"""Bound request HEAD source must satisfy the same coverage it already cites."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from model.output_schemas import CodeReviewIssue
from service.review.orchestrator import stage_3_mcp_verification as stage3
from service.review.orchestrator.stage_3_semantic_packing import _stage_3_mcp_continuation_messages
from utils.diff_processor import DiffProcessor, DiffChangeType

FIXTURE = json.loads((Path(__file__).parent / "fixtures/stage3_admitted_head_coverage.json").read_text())


def replay(omit=None):
    issue = CodeReviewIssue.model_validate(FIXTURE["issue"])
    issues = {FIXTURE["verification_id"]: issue}
    diff = DiffProcessor().process(FIXTURE["request_head_diff"])
    if omit in {"deleted", "incomplete", "not_changed_source"}:
        for file in diff.files:
            if omit == "deleted":
                file.change_type = DiffChangeType.DELETED
            else:
                file.hunks = [replace(hunk, **(
                    {"new_count": hunk.new_count + 1} if omit == "incomplete"
                    else {"evidence_kind": "untrusted_prose"})) for hunk in file.hunks]
    executor = SimpleNamespace(verification_source={}, call_log=[])
    state = {"records": [], "token_target": 10000}
    runtime = stage3.Stage3McpRuntime(lambda _: 10000,
        lambda *args, **kwargs: 10001 if omit == "capped" else 1,
        _stage_3_mcp_continuation_messages, None, lambda _: False)
    prompt = stage3._admit_request_head_hunks(diff, "Recorded public findings",
        [{"role": "user", "content": "Recorded public findings"}], executor, issues,
        FIXTURE["review_revision"], runtime, state)
    raw = json.dumps({"report": "Recorded verdict", "assessments": [FIXTURE["raw_assessment"]]})
    return issues, executor, state, prompt, raw


def test_actual_visible_source_and_citation_validation():
    issues, executor, state, prompt, raw = replay()
    for block in FIXTURE["actually_visible_source_blocks"]:
        assert block["body"] in prompt
        assert block["body"] in executor.verification_source[("issue_13", block["path"])]
    _, assessments = stage3.extract_verification_assessments(raw, executor, issues)
    assert assessments["issue_13"]["verdict"] == "disproven"
    assert len(assessments["issue_13"]["contradictingEvidence"]) == 4
    assert not executor.call_log  # Do not counterfeit additional MCP receipts.


def test_required_head_anchors_already_observed_should_allow_recorded_falsification():
    issues, executor, state, prompt, raw = replay()
    result = stage3._assessed_verification_result(raw, executor, issues,
        FIXTURE["review_revision"], state)
    assert result["dismissed_issue_keys"] == ["issue_13"]


@pytest.mark.parametrize("omit", ["capped", "deleted", "incomplete", "not_changed_source"])
def test_unadmitted_source_cannot_authorize_a_dismissal(omit):
    issues, executor, state, prompt, raw = replay(omit)
    assert not executor.verification_source
    assert not state.get("admitted_head_ranges")
    result = stage3._assessed_verification_result(raw, executor, issues,
        FIXTURE["review_revision"], state)
    assert result["dismissed_issue_keys"] == []
    assert result["verification_assessments"][0]["verdict"] == "unknown"


@pytest.mark.parametrize("mismatch", ["revision", "path", "range", "base", "path_only", "absent"])
def test_quotes_without_matching_head_anchor_coverage_remain_unknown(mismatch):
    issues, executor, state, prompt, raw = replay()
    if mismatch == "base":
        issues["issue_13"].anchorSide = "BASE"
    elif mismatch == "path_only":
        issues["issue_13"].line = 0
    elif mismatch == "absent":
        state.pop("admitted_head_ranges")
    else:
        state["admitted_head_ranges"] = [
            ("other" if mismatch == "revision" else revision,
             "unrelated.rb" if mismatch == "path" else path,
             1000 if mismatch == "range" else start,
             2000 if mismatch == "range" else end)
            for revision, path, start, end in state["admitted_head_ranges"]
        ]
    # Quotes are still exact; coverage alone must reject these substitutions.
    assert stage3.extract_verification_assessments(raw, executor, issues)[1]["issue_13"]["verdict"] == "disproven"
    result = stage3._assessed_verification_result(raw, executor, issues,
        FIXTURE["review_revision"], state)
    assert result["dismissed_issue_keys"] == []
    assert result["verification_assessments"][0]["reasonCode"] == "dismissal_evidence_incomplete"


@pytest.mark.asyncio
@pytest.mark.parametrize("fits", [True, False])
async def test_timeout_fallback_uses_only_preserved_complete_source(fits):
    issues, executor, state, prompt, raw = replay()
    executor.review_revision = FIXTURE["review_revision"]
    state["executor"] = executor
    state["records"] = [{"iteration": 0, "toolResults": [{
        "name": "getReviewFileContent", "result": FIXTURE["unrelated_prefetch"]}]}]
    calls = []

    async def final_response(_llm, final_prompt, **kwargs):
        assert kwargs["allow_retry"] is False
        for source in FIXTURE["actually_visible_source_blocks"]:
            assert source["body"] in final_prompt
        calls.append(final_prompt)
        return {"report": raw}

    runtime = stage3.Stage3McpRuntime(lambda _: 10000,
        lambda *args, **kwargs: 1 if fits else 10001,
        _stage_3_mcp_continuation_messages, final_response, lambda _: False)
    result = await stage3._verification_report_fallback(None, "original unaugmented prompt",
        runtime, state, issues, "verification_timeout")
    assert len(calls) == 1
    assert result["dismissed_issue_keys"] == (["issue_13"] if fits else [])
