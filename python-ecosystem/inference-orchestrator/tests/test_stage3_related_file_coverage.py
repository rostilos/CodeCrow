"""An observed related-file excerpt need not pretend to cover the entire file."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from model.output_schemas import CodeReviewIssue
from service.review.orchestrator import stage_3_mcp_verification as stage3
from service.review.orchestrator.stage_3_semantic_packing import _stage_3_mcp_continuation_messages
from utils.diff_processor import DiffProcessor


def test_recorded_discourse_url_disproof_uses_cited_related_head_source():
    fixture = json.loads((Path(__file__).parent / "fixtures/stage3_related_file_coverage.json").read_text())
    key = fixture["verification_id"]
    issue = CodeReviewIssue.model_validate(fixture["issue"])
    issues = {key: issue}
    executor = SimpleNamespace(verification_source={}, call_log=[])
    state = {"records": [], "token_target": 10000}
    runtime = stage3.Stage3McpRuntime(lambda _: 10000, lambda *args, **kwargs: 1,
        _stage_3_mcp_continuation_messages, None, lambda _: False)
    stage3._admit_request_head_hunks(DiffProcessor().process(fixture["request_head_diff"]),
        "Recorded finding", [{"role": "user", "content": "Recorded finding"}],
        executor, issues, fixture["review_revision"], runtime, state)
    primary = fixture["primary_observation"]
    executor.verification_source.setdefault((key, issue.file), []).append(primary["envelope"]["fileContent"])
    executor.call_log.append(primary["executor_receipt"])
    result = stage3._assessed_verification_result(json.dumps({"assessments": [fixture["raw_assessment"]]}),
        executor, issues, fixture["review_revision"], state)
    assert result["dismissed_issue_keys"] == [key]
    assert len(result["verification_assessments"][0]["contradictingEvidence"]) == 5


@pytest.mark.parametrize("source", ["head_hunk", "getReviewFileContent", "getBranchFileContent"])
@pytest.mark.parametrize("mismatch", [None, "uncited", "unobserved", "revision", "path", "invalid",
                                      "absent", "base", "primary_path_only", "related_range"])
def test_observed_head_related_file_excerpts_cover_path_only_locations(source, mismatch):
    issue = CodeReviewIssue(file="caller.py", line=10, severity="HIGH", category="BUG_RISK",
        reason="Caller bypasses the guard", suggestedFixDescription="Keep the guard",
        relatedLocations=["guard.py"], anchorSide="HEAD")
    executor = SimpleNamespace(call_log=[], verification_source={
        ("issue_0", "caller.py"): ["guard(request)"],
        ("issue_0", "guard.py"): ["if not allowed(request): raise Denied()"],
    })
    state = {"admitted_head_ranges": [("head", "caller.py", 9, 11)]}
    revision = "other" if mismatch == "revision" else "head"
    path = "other.py" if mismatch == "path" else "guard.py"
    start, end = (0, 0) if mismatch == "invalid" else (20, 25)
    if mismatch != "absent":
        if source == "head_hunk":
            state["admitted_head_ranges"].append((revision, path, start, end))
        else:
            executor.call_log.append({"tool": source, "success": True, "evidence_valid": True,
                "args": {"filePath": path, "branch": revision, "verificationId": "issue_0"},
                "evidence_revision": revision, "evidence_source_authority": "proposed_tree",
                "evidence_start_line": start, "evidence_end_line": end, "evidence_complete_file": False})
    if mismatch == "base":
        issue.anchorSide = "BASE"
    elif mismatch == "primary_path_only":
        issue.line = 0
    elif mismatch == "related_range":
        issue.relatedLocations = ["guard.py:20-30"]
    if mismatch == "unobserved":
        executor.verification_source.pop(("issue_0", "guard.py"))
    citations = [{"file_path": "caller.py", "snippet": "guard(request)"}]
    if mismatch != "uncited":
        citations.append({"file_path": "guard.py", "snippet": "if not allowed(request): raise Denied()"})
    result = stage3._assessed_verification_result(json.dumps({"assessments": [{
        "issue_id": "issue_0", "verdict": "disproven", "reason": "The caller invokes the guard",
        "contradicting_evidence": citations,
    }]}), executor, {"issue_0": issue}, "head", state)
    covered = mismatch in {None, "uncited"}
    assert result["dismissed_issue_keys"] == (["issue_0"] if covered else [])
    assert result["verification_assessments"][0]["verdict"] == ("disproven" if covered else "unknown")
