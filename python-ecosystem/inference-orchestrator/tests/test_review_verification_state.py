"""Concrete ledger regressions for source compaction, corrections, and progress."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from service.review.verification_state import VerificationState, source_discovery
from service.review.verifier import ReviewVerifier, _observations


def part(identifier="part-a", path="a.py"):
    return SimpleNamespace(id=identifier, path=path, side="proposed", anchors={1: "changed()"},
                           diff="@@ -1 +1 @@\n-before()\n+changed()\n")


def finding(value):
    return {"partId": value.id, "file": value.path, "line": 1,
            "title": "Supported failure", "reason": "Concrete trigger reaches the changed failure"}


def ledger():
    parts = [part(), part("part-b", "b.py")]
    state = VerificationState([finding(value) for value in parts], [], {value.id: value for value in parts})
    evidence_id, _ = state.add_evidence("getReviewDiff", {"status": "ready", "parts": [
        {"id": value.id, "path": value.path, "side": value.side, "diff": value.diff} for value in parts
    ]})
    state.visible.add(evidence_id)
    return state, evidence_id


def decision(candidate_id, verdict, refs, **extra):
    return {"candidateId": candidate_id, "verdict": verdict,
            "reason": "Source establishes this outcome", "evidenceIds": refs, **extra}


def test_late_caller_guard_can_correct_an_earlier_keep():
    state, changed = ledger()
    state.record(decisions=[decision("candidate-1", "keep", [changed])])
    guard, _ = state.add_evidence("readReviewFile", {
        "status": "ready", "path": "caller.py", "side": "proposed", "startLine": 1,
        "endLine": 1, "content": "if valid(value): changed(value)\n",
    })
    state.visible.add(guard)

    state.record(decisions=[decision("candidate-1", "dismiss", [changed, guard])])

    assert state.decisions["candidate-1"]["verdict"] == "dismiss"
    assert "candidate-1" not in ReviewVerifier._apply_decisions(state.candidates, state.decisions, [])


def test_later_supported_representative_can_replace_earlier_keep_without_duplicate_publication():
    state, changed = ledger()
    state.record(decisions=[decision("candidate-1", "keep", [changed])])
    state.record(decisions=[
        decision("candidate-2", "keep", [changed]),
        decision("candidate-1", "duplicate", [changed], duplicateOf="candidate-2"),
    ])

    assert state.decisions["candidate-1"]["verdict"] == "duplicate"
    assert set(ReviewVerifier._apply_decisions(state.candidates, state.decisions, [])) == {"candidate-2"}


def test_reason_only_rewrites_do_not_fund_more_model_turns():
    state, changed = ledger()
    value = decision("candidate-1", "keep", [changed])
    state.record(decisions=[value])
    revision = state.revision

    state.record(decisions=[{**value, "reason": "The same verdict explained in different words"}])

    assert state.revision == revision


def test_duplicate_of_dismissed_false_positive_is_not_resurrected():
    state, _ = ledger()
    decisions = {
        "candidate-1": {"verdict": "dismiss", "reason": "Caller already migrated"},
        "candidate-2": {"verdict": "duplicate", "duplicateOf": "candidate-1"},
    }

    assert ReviewVerifier._apply_decisions(state.candidates, decisions, []) == {}


def test_duplicate_cycle_without_confirmed_representative_stays_internal():
    state, _ = ledger()
    diagnostics = []
    decisions = {
        "candidate-1": {"verdict": "duplicate", "duplicateOf": "candidate-2"},
        "candidate-2": {"verdict": "duplicate", "duplicateOf": "candidate-1"},
    }

    assert ReviewVerifier._apply_decisions(state.candidates, decisions, diagnostics) == {}
    assert diagnostics


def test_contained_source_read_does_not_create_new_semantic_evidence():
    complete = {"status": "ready", "path": "a.py", "side": "proposed", "startLine": 1,
                "endLine": 3, "content": "first\nsecond\nthird\n"}
    contained = {**complete, "endLine": 2, "content": "first\nsecond\n"}

    original = _observations("readReviewFile", complete)
    repeated = _observations("readReviewFile", contained)

    assert repeated <= original
    changed = {**contained, "content": "first\nchanged\n"}
    assert _observations("readReviewFile", changed) - original
    assert _observations("readReviewFile", {**contained, "side": "target"}) - original


def response(**value):
    return SimpleNamespace(content=json.dumps(value))


@pytest.mark.asyncio
async def test_read_before_record_survives_another_related_source_turn(tmp_path):
    target, overlay = tmp_path / "target", tmp_path / "overlay"
    target.mkdir()
    overlay.mkdir()
    (overlay / "manifest.json").write_text(json.dumps({"changedFiles": [], "deletedFiles": []}))
    (target / "caller.py").write_text("if valid(value): changed(value)\n")
    (target / "contract.py").write_text("VALID_CONTRACT = True\n")
    parts = [part(), part("part-b", "a.py")]
    model = SimpleNamespace(ainvoke=AsyncMock(side_effect=[
        response(toolCalls=[
            {"name": "readReviewFile", "arguments": {"path": "caller.py"}},
            {"name": "recordReviewDecisions", "arguments": {
                "decisions": [decision("candidate-1", "keep", ["diff:part-a"])],
            }},
        ]),
        response(toolCalls=[{"name": "readReviewFile", "arguments": {"path": "contract.py"}}]),
        response(decisions=[decision("candidate-2", "dismiss", ["diff:part-b", "read-1"])]),
    ]))
    request = SimpleNamespace(aiProvider="openai", pullRequestId=1, projectRules=None, taskContext=None)
    result = await ReviewVerifier(None).verify(llm=model, request=request, findings=[finding(value) for value in parts],
                                               summaries=[], parts=parts,
                                               binding={"target_repo_path": str(target), "review_overlay_path": str(overlay)})
    third = str(model.ainvoke.call_args_list[2].args[0])
    assert third.count("if valid(value): changed(value)") == 1
    assert "VALID_CONTRACT = True" in third
    assert result.issues == [finding(parts[0])]
    assert not result.diagnostics


def test_additional_citations_update_provenance_without_funding_another_turn():
    state, changed = ledger()
    state.record(decisions=[decision("candidate-1", "keep", [changed])])
    revision = state.revision
    caller, _ = state.add_evidence("readReviewFile", {
        "status": "ready", "path": "caller.py", "side": "proposed", "startLine": 1,
        "endLine": 1, "content": "changed()\n",
    })
    state.visible.add(caller)

    state.record(decisions=[decision("candidate-1", "keep", [changed, caller])])

    assert state.decisions["candidate-1"]["evidenceIds"] == [changed, caller]
    assert state.revision == revision


def test_return_to_an_earlier_outcome_is_allowed_without_repeating_progress():
    state, changed = ledger()
    state.record(decisions=[decision("candidate-1", "keep", [changed])])
    state.record(decisions=[decision("candidate-1", "dismiss", [changed])])
    revision = state.revision

    state.record(decisions=[decision("candidate-1", "keep", [changed])])

    assert state.decisions["candidate-1"]["verdict"] == "keep"
    assert state.revision == revision


def test_later_source_can_resolve_an_uncertain_investigation_and_update_routing():
    state, changed = ledger()
    state.investigations["contract"] = {"question": "Does the caller use the changed contract?", "paths": ["caller.py"]}
    state.record(investigations=[{"id": "contract", "status": "uncertain", "reason": "Caller source not yet available"}])
    assert state.answers["contract"]["status"] == "uncertain"
    revision = state.revision

    state.record(investigations=[{"id": "contract", "status": "resolved", "reason": "Source establishes compatible use",
                                  "evidenceIds": [changed]}])

    assert "contract" in state.resolved
    assert state.revision == revision + 1
    routed = state.answers["contract"]
    assert state.investigations["contract"]["question"] == "Does the caller use the changed contract?"
    assert routed["status"] == "resolved"
    revision = state.revision
    state.record(investigations=[{"id": "contract", "status": "uncertain", "reason": "A related source premise remains open"}])
    assert "contract" not in state.resolved
    assert state.revision == revision


def test_invalid_json_fields_do_not_cancel_valid_sibling_decisions():
    state, changed = ledger()
    state.begin_turn()
    state.investigations["bad-question"] = {"question": "Incomplete answer"}
    state.investigations["good-question"] = {"question": "Supported answer"}

    result = state.record(
        decisions=[decision("candidate-1", "duplicate", [changed], duplicateOf={}),
                   decision("candidate-2", "keep", [changed])],
        investigations=[{"id": "bad-question", "status": ["resolved"], "reason": "malformed", "evidenceIds": [changed]},
                        {"id": "good-question", "status": "resolved", "reason": "source settles question", "evidenceIds": [changed]}],
    )

    assert "candidate-1" not in state.decisions
    assert state.decisions["candidate-2"]["verdict"] == "keep"
    assert state.resolved == {"good-question"}
    assert changed in state.visible
    assert result["rejected"]


def test_malformed_source_bounds_are_skipped_when_other_exact_evidence_is_available():
    state, changed = ledger()
    malformed, _ = state.add_evidence("getStructuralUnit", {
        "status": "ready", "path": "a.py", "side": "proposed", "startLine": "bad range",
        "endLine": float("inf"), "content": "changed()\n",
    })
    state.visible.add(malformed)

    state.record(decisions=[decision("candidate-1", "keep", [malformed, changed])])

    assert state.decisions["candidate-1"]["verdict"] == "keep"
    assert source_discovery({**finding(part()), "line": float("inf"), "evidenceIds": [changed]},
                                     state.parts, state.evidence) is None


def test_candidate_refinement_keeps_identity_and_advances_remaining_work():
    state, changed = ledger()
    state.record(findings=[{**finding(part()), "candidateId": "candidate-1",
                           "title": "Corrected mechanism", "evidenceIds": [changed]}])
    assert len(state.candidates) == 2
    assert state.candidates["candidate-1"]["title"] == "Corrected mechanism"
    assert state.decisions["candidate-1"]["verdict"] == "keep"
    assert state.revision == 1
    assert not state.complete
    state.record(findings=[{**finding(part()), "candidateId": "candidate-1", "evidenceIds": [changed]}])
    assert state.revision == 1


def test_corrected_report_does_not_send_disproved_discovery_premises_to_reconciliation():
    state, changed = ledger()
    state.candidates["candidate-1"].update(trigger="old trigger", failureMechanism="wrong mechanism",
                                          causalEvidence=[{"claim": "wrong premise"}], evidenceToCheck=["obsolete"])
    state.record(decisions=[decision("candidate-1", "keep", [changed], issue={
        "reason": "The supported missing wait violates completion ordering", "title": "Completion race"})])
    issue = state.candidates["candidate-1"]
    assert issue["reason"] == "The supported missing wait violates completion ordering"
    assert not {"trigger", "failureMechanism", "causalEvidence", "evidenceToCheck"} & issue.keys()


@pytest.mark.parametrize("identifier", [None, "mistyped-part-id"])
def test_exact_unambiguous_changed_location_recovers_missing_bookkeeping_id(identifier):
    state, changed = ledger()
    record = {**finding(part()), "partId": identifier, "evidenceIds": [changed]}
    issue = source_discovery(record, state.parts, state.evidence)
    assert issue["partId"] == "part-a"
    assert issue["line"] == 1
    assert source_discovery({**record, "line": 2}, state.parts, state.evidence) is None


def test_ambiguous_target_and_proposed_anchors_require_explicit_resolution():
    from service.review.change_context import resolve_changed_anchor
    proposed = part()
    target = part("target-part")
    target.side = "target"
    parts = {item.id: item for item in (proposed, target)}
    record = {"file": "a.py", "line": 1}
    assert resolve_changed_anchor(record, parts) is None
    assert resolve_changed_anchor({**record, "side": "proposed"}, parts) == (proposed, 1)
    assert resolve_changed_anchor({**record, "partId": "target-part"}, parts) == (target, 1)


def test_candidate_id_with_different_explicit_anchor_cannot_overwrite_original():
    value = part()
    value.anchors[5] = "second_failure()"
    original = finding(value)
    state = VerificationState([original], [], {value.id: value})
    key, _ = state.add_evidence("getReviewDiff", {"status": "ready", "parts": [{"id": value.id, "diff": value.diff}]})
    state.begin_turn()
    receipt = state.record(findings=[{**original, "candidateId": "candidate-1", "line": 5,
                                     "title": "Distinct second failure", "evidenceIds": [key]}])
    assert state.candidates["candidate-1"] == original
    assert "candidate-1" in receipt["remainingCandidateIds"]
    assert state.candidates["candidate-2"]["line"] == 5
    assert state.candidates["candidate-2"]["title"] == "Distinct second failure"
    assert state.decisions["candidate-2"]["verdict"] == "keep"
