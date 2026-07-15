"""P0-02 characterization of planner exclusion and producer ordering."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from model.dtos import ReviewRequestDto
from model.multi_stage import (
    CrossFileAnalysisResult,
    CrossFileIssue,
    FileToSkip,
    ReviewPlan,
)
from model.output_schemas import CodeReviewIssue
from service.review.orchestrator.orchestrator import MultiStageReviewOrchestrator
from service.review.telemetry import (
    ExecutionIdentity,
    ExecutionTelemetryRecorder,
    MemoryTelemetrySink,
    VersionAttribution,
)


pytestmark = pytest.mark.legacy_defect


def _request():
    return ReviewRequestDto(
        projectId=1,
        projectVcsWorkspace="offline-workspace",
        projectVcsRepoSlug="offline-repository",
        projectWorkspace="offline-workspace",
        projectNamespace="offline-project",
        aiProvider="offline-fake",
        aiModel="offline-fake-model",
        aiApiKey="not-a-live-key",
        pullRequestId=42,
        sourceBranchName="feature",
        targetBranchName="main",
        commitHash="head-a",
        changedFiles=[],
        deletedFiles=[],
        previousCodeAnalysisIssues=[],
    )


def _stage_one_issue():
    return CodeReviewIssue(
        id="stage-one",
        severity="HIGH",
        category="BUG_RISK",
        file="src/a.py",
        line=10,
        reason="Stage 1 candidate",
        suggestedFixDescription="Fix it",
        codeSnippet="unsafe_a()",
    )


def test_legacy_defect_planner_skip_is_counted_as_planned_but_has_no_review_unit():
    orchestrator = MultiStageReviewOrchestrator.__new__(MultiStageReviewOrchestrator)
    plan = ReviewPlan(
        analysis_summary="legacy",
        file_groups=[],
        files_to_skip=[FileToSkip(path="src/skipped.py", reason="planner considered it low risk")],
        cross_file_concerns=[],
    )

    result = orchestrator._ensure_all_files_planned(plan, ["src/skipped.py"])

    assert result.file_groups == []
    assert orchestrator._count_files(result) == 0
    assert [item.path for item in result.files_to_skip] == ["src/skipped.py"]


@pytest.mark.asyncio(loop_scope="function")
async def test_legacy_defect_stage_two_candidate_is_absent_from_llm_verifier_inputs():
    sequence = []
    verifier_input_ids = []
    stage_one = _stage_one_issue()
    stage_two = CrossFileIssue(
        id="stage-two",
        severity="HIGH",
        category="ARCHITECTURE",
        title="Late cross-file candidate",
        primary_file="src/b.py",
        line=20,
        codeSnippet="unsafe_b()",
        affected_files=["src/a.py", "src/b.py"],
        description="Created after the LLM verifier has already returned.",
        evidence="The two files disagree.",
        business_impact="Incorrect state can publish.",
        suggestion="Unify the state contract.",
    )
    cross_file_result = CrossFileAnalysisResult(
        pr_risk_level="HIGH",
        cross_file_issues=[stage_two],
        data_flow_concerns=[],
        pr_recommendation="REQUEST_CHANGES",
        confidence="HIGH",
    )
    empty_plan = ReviewPlan(
        analysis_summary="legacy",
        file_groups=[],
        cross_file_concerns=[],
    )
    profile = SimpleNamespace(
        fast_check_enabled=False,
        describe=lambda: "offline characterization",
    )

    async def verify(_llm, issues, _request, _processed_diff):
        sequence.append("llm-verifier")
        verifier_input_ids.extend(issue.id for issue in issues)
        return issues

    async def produce_stage_two(*_args, **_kwargs):
        sequence.append("stage-two-producer")
        return cross_file_result

    telemetry = ExecutionTelemetryRecorder(
        identity=ExecutionIdentity(
            execution_id="stage-order-test",
            base_revision="a" * 40,
            head_revision="b" * 40,
        ),
        versions=VersionAttribution(
            provider="scripted",
            model="fixture-v1",
            prompt_version="prompt-v1",
            rules_version="rules-v1",
            policy_version="policy-v1",
            index_version="rag-commit-" + "c" * 40,
        ),
        sink=MemoryTelemetrySink(),
    )

    orchestrator = MultiStageReviewOrchestrator(
        llm=MagicMock(name="offline_llm"),
        mcp_client=None,
        rag_client=None,
        telemetry=telemetry,
    )

    module = "service.review.orchestrator.orchestrator"
    with patch.object(orchestrator, "_index_pr_files", new_callable=AsyncMock), \
            patch(f"{module}.build_review_inference_profile", return_value=profile), \
            patch(f"{module}.with_stage_output_cap", side_effect=lambda llm, *_args: llm), \
            patch(f"{module}.execute_stage_0_planning", new_callable=AsyncMock, return_value=empty_plan), \
            patch(f"{module}.execute_stage_1_file_reviews", new_callable=AsyncMock, return_value=[stage_one]), \
            patch(f"{module}.deduplicate_cross_batch_issues", side_effect=lambda issues: issues), \
            patch(f"{module}.run_verification_agent", side_effect=verify), \
            patch(f"{module}.should_run_stage_2", return_value=(True, "legacy characterization")), \
            patch(f"{module}.execute_stage_2_cross_file", side_effect=produce_stage_two), \
            patch(f"{module}.run_deterministic_evidence_gate", side_effect=lambda issues, *_args: issues), \
            patch(f"{module}.should_use_fast_dedup", return_value=True), \
            patch(f"{module}.deduplicate_final_issues", side_effect=lambda issues: issues), \
            patch(
                f"{module}.execute_stage_3_aggregation",
                new_callable=AsyncMock,
                return_value={"report": "legacy output", "dismissed_issue_ids": []},
            ), \
            patch(f"{module}.VERIFICATION_ENABLED", True):
        result = await orchestrator.orchestrate_review(_request())

    assert sequence == ["llm-verifier", "stage-two-producer"]
    assert verifier_input_ids == ["stage-one"]
    assert [item["id"] for item in result["issues"]] == ["stage-one", "stage-two"]
    assert [(stage.name, stage.producer) for stage in telemetry.stages] == [
        ("planning", "stage_0"),
        ("retrieval", "pr_index"),
        ("generation", "stage_1"),
        ("pre_dedup", "cross_batch_dedup"),
        ("reconciliation", "previous_issue_reconciliation"),
        ("verification", "verification_agent"),
        ("generation", "stage_2"),
        ("verification", "deterministic_evidence_gate"),
        ("post_dedup", "final_dedup"),
        ("aggregation", "stage_3"),
    ]
    assert [lineage.producer for lineage in telemetry.lineage] == [
        "stage_1",
        "cross_batch_dedup",
        "verification_agent",
        "stage_2",
        "deterministic_evidence_gate",
        "final_dedup",
        "stage_3",
    ]
