"""Malformed output and unavailable graph routes do not lose required source reads."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from service.review.verification_state import VerificationState
from service.review.verification_tools import VerificationTools
from service.review.verifier import ReviewVerifier


def response(content=None, **fields):
    return SimpleNamespace(content=content if content is not None else json.dumps(fields))


def needs(name, arguments):
    return response(assessments=[{
        "workId": "work-1", "verdict": "needs_evidence", "reason": "Actual caller contract determines this candidate", "evidenceIds": [],
    }], evidenceRequests=[{
        "workIds": ["work-1"], "missingFact": "Does the actual caller guard the operation?",
        "calls": [{"name": name, "arguments": arguments}],
    }], findings=[])


def case(tmp_path):
    target, overlay = tmp_path / "repo", tmp_path / "overlay"
    target.mkdir()
    overlay.mkdir()
    (target / "caller.py").write_text("if valid(value): changed(value)\n")
    (target / "owner.py").write_text("def owner(): pass\n")
    (overlay / "manifest.json").write_text(json.dumps({"changedFiles": [], "deletedFiles": []}))
    part = SimpleNamespace(id="part", path="a.py", side="proposed", anchors={1: "changed(value)"},
        diff="@@ -1 +1 @@\n-old(value)\n+changed(value)\n")
    issue = {"partId": "part", "file": "a.py", "line": 1, "title": "Changed input failure", "reason": "An unguarded caller would violate the new input contract"}
    state = VerificationState([issue], [], {"part": part})
    state.evidence["diff:part"] = {"kind": "diff", "result": {
        "status": "ready", "partId": "part", "path": "a.py", "side": "proposed", "diff": part.diff,
    }}
    tools = VerificationTools(rag_client=None, binding={"target_repo_path": str(target), "review_overlay_path": str(overlay)}, parts=[part])
    return state, tools


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", ["malformed", "unavailable_graph_after_source"])
async def test_corrected_pending_work_can_follow_a_new_local_source_route(tmp_path, initial):
    state, tools = case(tmp_path)
    turns = ([response(content="I need to check the caller source.")] if initial == "malformed" else [
        needs("readReviewFile", {"path": "owner.py"}),
        needs("queryCodeGraph", {"pattern": "callers_of", "target": "changed"}),
    ])
    turns.append(needs("readReviewFile", {"path": "caller.py"}))
    caller_id = "read-1" if initial == "malformed" else "read-3"
    turns.append(response(assessments=[{
        "workId": "work-1", "verdict": "refuted", "reason": "The observed caller validates this input before calling",
        "evidenceIds": [caller_id],
    }], evidenceRequests=[], findings=[]))
    model = SimpleNamespace(ainvoke=AsyncMock(side_effect=turns))
    result = await ReviewVerifier(None)._verify_case(model, SimpleNamespace(aiProvider="openai", pullRequestId=1),
        state, tools, {"caseId": "recovery"}, [])
    assert model.ainvoke.await_count == len(turns)
    assert state.evidence[caller_id]["result"]["path"] == "caller.py"
    assert result.decisions[0]["verdict"] == "dismiss"
    assert result.issues == []
    assert not result.diagnostics
    last_prompt = str(model.ainvoke.call_args_list[-1].args[0])
    assert "if valid(value): changed(value)" in last_prompt
    assert "I need to check the caller source." not in last_prompt
