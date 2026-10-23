"""Replay Cal4's exact saved model verdict and source windows without inference."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from model.output_schemas import CodeReviewIssue
from service.review.candidate_ledger import CandidateEvidenceLedger
from service.review.inference_usage import InferenceUsage, _ACTIVE_USAGE
from service.review.orchestrator.stage_3_mcp_verification import (
    Stage3McpRuntime, execute_stage_3_mcp_verification,
)

FIXTURE = json.loads((Path(__file__).parent / "fixtures/cal4_verifier_status.json").read_text())


async def replay(*, fixed_observation, model_response=None, issues=None):
    candidate = CodeReviewIssue.model_validate(FIXTURE["actual_candidate_record"]["originalIssue"])
    issues = issues or {"issue_1": candidate}
    calls, prompts = [], []

    class Session:
        async def call_tool(self, name, arguments):
            calls.append((name, dict(arguments)))
            if fixed_observation:
                payload = FIXTURE["actual_observed_window"]
            else:
                lines = FIXTURE["exact_head_source"].splitlines()
                start, end = arguments["startLine"], min(arguments["endLine"], len(lines))
                payload = {"filePath": candidate.file, "fileContent": "\n".join(lines[start - 1:end]),
                           "startLine": start, "endLine": end, "totalLines": len(lines),
                           "completeFile": False, "exists": True, "source": "review-overlay"}
            return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(payload))])

    class Llm:
        def bind_tools(self, _tools):
            return self

        async def ainvoke(self, messages, **_kwargs):
            prompts.append(json.dumps(messages, default=str))
            return SimpleNamespace(tool_calls=[], response_metadata={},
                                   content=model_response or FIXTURE["actual_response_content"])

    async def no_fallback(*_args, **_kwargs):
        raise AssertionError("Saved successful model output needs no recovery")

    runtime = Stage3McpRuntime(lambda _: 100000, lambda *a, **k: 1,
                              lambda *a: [], no_fallback, lambda _: False)
    request = SimpleNamespace(projectVcsWorkspace="workspace", projectVcsRepoSlug="repo",
                              mcpLocalOnly=True, localRepoPath="/bound/base", localRepoRevision="base",
                              localReviewOverlayPath="/bound/head", localRepoTargetBranch="main")
    usage = InferenceUsage()
    token = _ACTIVE_USAGE.set(usage)
    try:
        result = await execute_stage_3_mcp_verification(
            Llm(), request, "saved Cal4 verification replay", SimpleNamespace(session=Session()),
            FIXTURE["head"], issues, runtime,
        )
    finally:
        _ACTIVE_USAGE.reset(token)
    return result, usage.decisions, calls, prompts


@pytest.mark.asyncio
async def test_actual_disproven_response_with_missing_primary_stays_effectively_unknown():
    result, decisions, _, _ = await replay(fixed_observation=True)
    # The source genuinely contains the quoted sort, but outside the primary anchor.
    assert result["dismissed_issue_keys"] == []
    assessment = result["verification_assessments"][0]
    assert assessment["verdict"] == "unknown"
    assert assessment["reasonCode"] == "dismissal_evidence_incomplete"
    assert assessment["completed"] is True
    assert assessment["contradictingEvidence"]  # Keep observed evidence for diagnosis.
    final = [d for d in decisions if d["kind"] == "final_verification" and d.get("verificationId") == "issue_1"]
    assert final and all(d["verdict"] == "unknown" for d in final)
    anchors = [d for d in decisions if d["kind"] == "final_verification_evidence"]
    assert anchors[0]["outcome"] == "anchor_unavailable"
    candidate = CodeReviewIssue.model_validate(FIXTURE["actual_candidate_record"]["originalIssue"])
    ledger = CandidateEvidenceLedger()
    ledger.register(candidate, stage="stage_1", source_key="saved-Cal4", review_unit_ids=["unit"],
                    prompt_hunk_ids=["hunk"], generation_prompt="saved candidate")
    ledger.assess(candidate, assessment)
    ledger.publish([candidate])
    assert ledger.verification_summary()["unknown"] == 1
    assert ledger.verification_summary()["disproven"] == 0
    # Raw inference evidence remains available even when its effective status changes.
    raw = json.loads(FIXTURE["actual_response_content"])
    assert next(a for a in raw["assessments"] if a["issue_id"] == "issue_1")["verdict"] == "disproven"


@pytest.mark.asyncio
async def test_host_prefetch_includes_primary_and_shares_window_with_nearby_candidate():
    candidate = CodeReviewIssue.model_validate(FIXTURE["actual_candidate_record"]["originalIssue"])
    nearby = candidate.model_copy(update={"line": 430, "relatedLocations": [], "title": "Independent nearby concern"})
    response = json.dumps({"report": "Unknown until semantics are assessed", "assessments": [
        {"issue_id": key, "verdict": "unknown", "reason": "No contradiction established"}
        for key in ("issue_1", "issue_2")
    ]})
    result, _, calls, prompts = await replay(fixed_observation=False, model_response=response,
                                            issues={"issue_1": candidate, "issue_2": nearby})
    assert len(calls) == 1
    assert calls[0][1]["startLine"] <= 421 <= calls[0][1]["endLine"]
    assert "getOutputRecurringSeatedBookings" in json.dumps(prompts)
    assert "return transformed;" in json.dumps(prompts)
    assert [a["verdict"] for a in result["verification_assessments"]] == ["unknown", "unknown"]


@pytest.mark.asyncio
async def test_base_side_candidate_does_not_prefetch_head_source():
    candidate = CodeReviewIssue.model_validate(FIXTURE["actual_candidate_record"]["originalIssue"])
    candidate.anchorSide = "BASE"
    response = json.dumps({"report": "Base source unresolved", "assessments": [
        {"issue_id": "issue_1", "verdict": "unknown", "reason": "No base evidence"}
    ]})
    result, _, calls, _ = await replay(fixed_observation=False, model_response=response,
                                      issues={"issue_1": candidate})
    assert calls == []
    assert result["dismissed_issue_keys"] == []
    assert result["verification_assessments"][0]["verdict"] == "unknown"
