"""Dify6 live capture had no candidates but requested issue_0 source anyway."""
from types import SimpleNamespace
import json
from unittest.mock import AsyncMock, Mock

import pytest

from model.multi_stage import CrossFileAnalysisResult, ReviewPlan
from model.output_schemas import CodeReviewIssue
from service.review.orchestrator import stage_3_aggregation as stage3
from tests.test_quality_capture import _request, capture_environment


@pytest.mark.asyncio
@pytest.mark.parametrize("resolved", [False, True])
async def test_empty_active_inventory_needs_no_model_or_repository_call(monkeypatch, resolved):
    request = SimpleNamespace(
        projectVcsRepoSlug="dify", pullRequestId=6, prTitle="SMTP hostname",
        taskContext=None, changedFiles=["api/libs/smtp.py"], prAuthor="author",
        currentCommitHash="6facade85c2723292b4d8585dfa587921059a5b7",
        previousCodeAnalysisIssues=[], mcpLocalOnly=True,
    )
    plan = ReviewPlan(analysis_summary="SMTP configuration change", file_groups=[])
    result = CrossFileAnalysisResult(
        pr_risk_level="LOW", cross_file_issues=[], pr_recommendation="PASS", confidence="MEDIUM",
    )
    issues = [CodeReviewIssue(file="api/libs/smtp.py", line=12, title="Resolved typing",
        reason="Resolved", severity="LOW", category="CODE_QUALITY", isResolved=True,
        suggestedFixDescription="Already fixed")] if resolved else []
    mcp_verifier = AsyncMock(return_value={"report": "phantom issue_0", "verification_assessments": []})
    plain_report = AsyncMock(return_value={"report": "unnecessary model synthesis"})
    decision = Mock()
    monkeypatch.setattr(stage3, "_stage_3_with_mcp", mcp_verifier)
    monkeypatch.setattr(stage3, "_invoke_stage_3_report", plain_report)
    monkeypatch.setattr(stage3, "record_compute_decision", decision, raising=False)
    response = await stage3.execute_stage_3_aggregation(
        object(), request, plan, issues, result, is_incremental=resolved,
        mcp_client=object(), use_mcp_tools=True,
    )
    mcp_verifier.assert_not_awaited()
    plain_report.assert_not_awaited()
    assert response["verification_assessments"] == []
    assert response["dismissed_issue_ids"] == []
    assert response["stage_3_prompt_provenance"] == {"inputShards": [], "synthesisShards": []}
    assert "No active findings" in response["report"]
    assert "MEDIUM" in response["report"]
    if resolved:
        assert "Resolved findings: 1" in response["report"]
    decision.assert_called_once_with("final_verification", outcome="no_candidates", candidates=0)


@pytest.mark.asyncio
async def test_cross_file_context_survives_empty_active_issue_inventory(monkeypatch):
    request = SimpleNamespace(
        projectVcsRepoSlug="repo", pullRequestId=1, prTitle="Cross-file change",
        taskContext=None, changedFiles=["caller.py", "callee.py"], prAuthor="author",
        currentCommitHash="head", previousCodeAnalysisIssues=[], mcpLocalOnly=True,
    )
    plan = ReviewPlan(analysis_summary="Cross-file change", file_groups=[])
    result = CrossFileAnalysisResult(pr_risk_level="HIGH", pr_recommendation="FAIL", confidence="LOW",
        cross_file_issues=[dict(id="cross-1", title="Caller contract", severity="HIGH", category="BUG_RISK",
            affected_files=["caller.py", "callee.py"], description="Caller omits required argument",
            evidence="callee requires it", business_impact="call fails", suggestion="Supply argument")])
    verifier = AsyncMock(return_value={"report": "Cross-file evidence preserved", "verification_assessments": []})
    monkeypatch.setattr(stage3, "_stage_3_with_mcp", verifier)
    response = await stage3.execute_stage_3_aggregation(
        object(), request, plan, [], result, mcp_client=object(), use_mcp_tools=True,
    )
    verifier.assert_awaited_once()
    assert "Caller omits required argument" in verifier.await_args.args[2]
    assert response["report"] == "Cross-file evidence preserved"
    assert response["stage_3_prompt_provenance"]["inputShards"]


@pytest.mark.asyncio
async def test_empty_stage_records_real_compute_decision_in_service_response_and_capture(
    monkeypatch, capture_environment,
):
    from service.review.review_service import ReviewService
    from service.review.candidate_ledger import CandidateEvidenceLedger

    ledger = CandidateEvidenceLedger()
    ledger.publish([])
    expected_task_evidence = {"taskKey": "TASK-1", "scope": "unchanged auxiliary payload"}

    async def review(_self, *, request, repo_path, event_callback, quality_capture):
        result = await stage3.execute_stage_3_aggregation(
            object(), request, ReviewPlan(analysis_summary="Complete", file_groups=[]), [],
            CrossFileAnalysisResult(pr_risk_level="LOW", cross_file_issues=[],
                                    pr_recommendation="PASS", confidence="MEDIUM"),
            mcp_client=object(), use_mcp_tools=True,
        )
        return {"result": {"comment": result["report"], "issues": [],
                           "taskEvidence": expected_task_evidence}}

    monkeypatch.setattr(ReviewService, "_process_review", review)
    result = await ReviewService().process_review_request(_request())
    usage = result["inferenceUsage"]
    assert usage["attempts"] == 0
    assert usage["stages"] == {}
    assert usage["decisions"] == [{"kind": "final_verification", "outcome": "no_candidates", "candidates": 0}]
    assert result["result"]["taskEvidence"] == expected_task_evidence
    capture = json.loads(next(capture_environment.glob("*.json")).read_text())
    assert capture["result"]["inferenceUsage"]["decisions"] == usage["decisions"]
    assert capture["calls"] == []
    assert ledger.verification_summary() == {
        "candidates": 0, "attempted": 0, "completed": 0, "supported": 0,
        "disproven": 0, "unknown": 0, "incomplete": 0, "reasonCounts": {},
    }
