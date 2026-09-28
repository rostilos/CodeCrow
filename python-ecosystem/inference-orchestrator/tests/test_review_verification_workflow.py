"""Captured source-request failures: offline host behavior, not model quality."""
from types import SimpleNamespace

import pytest

from service.review.verification_state import VerificationState
from service.review.verification_workflow import VerificationWorkflow


def ledger():
    part = SimpleNamespace(id="part", path="src/changed.ts", side="proposed", anchors={2: "changed();"})
    findings = [{"partId": "part", "file": part.path, "line": 2, "title": "Potential failure",
                 "reason": "Discovery claim depends on an unseen setting", "trigger": "Unverified premise",
                 "evidenceToCheck": ["Read configuration"], "suggestedFixDescription": "Await completion"},
                {"partId": "part", "file": part.path, "line": 2, "title": "Independent failure",
                 "reason": "Separate demonstrated failure"}]
    questions = [{"id": "question", "question": "Does the caller compensate?", "partIds": ["part"]}]
    state = VerificationState(findings, questions, {"part": part})
    state.evidence["diff:part"] = {"kind": "diff", "result": {
        "status": "ready", "partId": "part", "path": part.path,
        "diff": "@@ -1,2 +1,2 @@\n context\n-before();\n+changed();\n"}}
    state.begin_turn(state.evidence)
    return state


def outcome(work="work-1", verdict="confirmed", **extra):
    return {"workId": work, "verdict": verdict, "reason": "Source proves the changed failure",
            "evidenceIds": ["diff:part"], **extra}


def request(*ids):
    return {"workIds": list(ids or ["work-1"]), "missingFact": "Does compiler configuration reject this import?",
            "calls": [{"name": "readReviewFile", "arguments": {"path": "tsconfig.json"}}]}


def step(assessments=(), requests=(), findings=()):
    return {"assessments": list(assessments), "evidenceRequests": list(requests), "findings": list(findings)}


def test_pending_request_without_repeated_needs_evidence_executes():
    # 114 captured requests were skipped because the same response did not also
    # repeat a needs_evidence assessment for its already-pending work.
    state = ledger()
    receipt = VerificationWorkflow(state).apply([step(requests=[request()])])
    assert receipt["requests"] == [request()]
    assert not receipt["rejected"]
    assert "work-1" in receipt["pendingWorkIds"]


@pytest.mark.parametrize("verdict", ["confirmed", "refuted", "uncertain", "duplicate", "needs_evidence"])
def test_requested_counterevidence_defers_that_outcome_and_keeps_siblings(verdict):
    # Unused-import capture: confirmed plus tsconfig read published immediately.
    state = ledger()
    provisional = outcome(verdict=verdict, duplicateOf="work-2",
                          issue={"title": "Compiler rejects import", "reason": "Conditional compiler claim"})
    receipt = VerificationWorkflow(state).apply([step([provisional, outcome("work-2")], [request()])])
    assert receipt["acceptedWorkIds"] == ["work-2"]
    assert "candidate-1" not in state.decisions
    assert state.decisions["candidate-2"]["verdict"] == "keep"
    assert state.candidates["candidate-1"]["title"] == "Potential failure"
    assert state.work_items()[0]["pendingAssessment"] == provisional
    assert receipt["requests"] == [request()]
    assert not receipt["rejected"]


def test_sibling_native_calls_are_normalized_as_one_submission():
    state = ledger()
    receipt = VerificationWorkflow(state).apply([step([outcome()]), step(requests=[request()])])
    assert "candidate-1" not in state.decisions
    assert receipt["requests"] == [request()]


def test_observed_counterevidence_refutes_provisional_report_and_removes_hypothesis():
    state = ledger()
    workflow = VerificationWorkflow(state)
    workflow.apply([step([outcome()], [request()])])
    key, _ = state.add_evidence("readReviewFile", {"status": "ready", "path": "tsconfig.json", "side": "proposed",
        "startLine": 1, "endLine": 1, "content": '{"compilerOptions":{"noUnusedLocals":false}}'})
    state.begin_turn()
    receipt = workflow.apply([step([outcome(verdict="refuted", reason="Unused imports are permitted", evidenceIds=[key])])])
    assert receipt["acceptedWorkIds"] == ["work-1"]
    assert state.decisions["candidate-1"]["verdict"] == "dismiss"
    assert "pendingAssessment" not in state.work_items()[0]


def test_question_uncertainty_with_explicit_source_lead_stays_pending():
    state = ledger()
    receipt = VerificationWorkflow(state).apply([step([outcome("work-3", "uncertain")], [request("work-3")])])
    assert "question" not in state.answers
    assert "question" not in state.resolved
    assert "work-3" in receipt["pendingWorkIds"]
    assert receipt["requests"][0]["workIds"] == ["work-3"]


def test_settled_work_does_not_reopen_from_an_unrelated_late_read():
    state = ledger()
    workflow = VerificationWorkflow(state)
    workflow.apply([step([outcome()])])
    receipt = workflow.apply([step(requests=[request()])])
    assert not receipt["requests"]
    assert receipt["rejected"]
    assert state.decisions["candidate-1"]["verdict"] == "keep"


def test_explicit_reassessment_with_new_source_lead_reopens_settled_work():
    state = ledger()
    workflow = VerificationWorkflow(state)
    workflow.apply([step([outcome()])])
    receipt = workflow.apply([step([outcome(verdict="uncertain")], [request()])])
    assert receipt["requests"] == [request()]
    assert "candidate-1" not in state.decisions
    assert "work-1" in receipt["pendingWorkIds"]


def test_malformed_or_unknown_calls_do_not_defer_valid_outcomes():
    state = ledger()
    invalid = request()
    invalid["calls"] = [{"name": "writeFile", "arguments": {}}]
    receipt = VerificationWorkflow(state, tool_names={"readReviewFile"}).apply([step([outcome()], [invalid])])
    assert not receipt["requests"]
    assert receipt["rejected"]
    assert state.decisions["candidate-1"]["verdict"] == "keep"


def test_unknown_work_does_not_discard_known_sibling_request():
    state = ledger()
    receipt = VerificationWorkflow(state).apply([step(requests=[request("unknown", "work-3")])])
    assert receipt["requests"][0]["workIds"] == ["work-3"]
    assert receipt["rejected"] == ["unknown: unknown workId in evidence request"]


def test_confirmed_publication_uses_verified_reason_without_duplicate_issue_object():
    state = ledger()
    VerificationWorkflow(state).apply([step([outcome(reason="The observed caller omits the required wait")])])
    issue = state.candidates["candidate-1"]
    assert issue["reason"] == "The observed caller omits the required wait"
    assert issue["suggestedFixDescription"] == "Await completion"
    assert "trigger" not in issue and "evidenceToCheck" not in issue


def test_explicit_verified_issue_reason_takes_precedence():
    state = ledger()
    VerificationWorkflow(state).apply([step([outcome(issue={"reason": "Public causal explanation", "title": "Lost completion"})])])
    assert state.candidates["candidate-1"]["reason"] == "Public causal explanation"
    assert state.candidates["candidate-1"]["title"] == "Lost completion"


def test_unusable_model_records_do_not_break_sibling_assessment():
    state = ledger()
    receipt = VerificationWorkflow(state).apply([step([{"workId": [], "verdict": []}, outcome("work-2")])])
    assert receipt["acceptedWorkIds"] == ["work-2"]
    assert receipt["rejected"]


def test_reopened_question_withdraws_old_finding_until_its_answer_is_reestablished():
    state = ledger()
    workflow = VerificationWorkflow(state)
    question_answer = outcome("work-3", issue={"file": "src/changed.ts", "line": 2,
        "title": "Question discovered defect", "reason": "Source-backed new defect"})
    workflow.apply([step([question_answer])])
    representative = state._work_representatives["work-3"]
    assert state.decisions[representative]["verdict"] == "keep"
    workflow.apply([step([outcome("work-3", "uncertain")], [request("work-3")])])
    assert representative not in state.decisions
    assert state._work_ids[("candidate", representative)] in state.pending_work_ids()
    assert "question" not in state.resolved
    workflow.apply([step([question_answer])])
    assert state.decisions[representative]["verdict"] == "keep"
    assert "question" in state.resolved
    representative_work = state._work_ids[("candidate", representative)]
    assert "pendingAssessment" not in next(item for item in state.work_items() if item["id"] == representative_work)


def test_reason_correction_keeps_outcome_and_publication_consistent_without_progress():
    state = ledger()
    workflow = VerificationWorkflow(state)
    workflow.apply([step([outcome()])])
    revision = state.revision
    workflow.apply([step([outcome(reason="Corrected causal explanation")])])
    assert state.revision == revision
    assert state.candidates["candidate-1"]["reason"] == "Corrected causal explanation"
    assert state.decisions["candidate-1"]["reason"] == "Corrected causal explanation"


@pytest.mark.parametrize("reverse", [False, True])
def test_conflicting_terminal_outcomes_do_not_publish_by_native_call_order(reverse):
    state = ledger()
    conflicting = [outcome(verdict="confirmed"), outcome(verdict="refuted", reason="Observed guard prevents failure")]
    if reverse:
        conflicting.reverse()
    receipt = VerificationWorkflow(state).apply([
        step([conflicting[0], outcome("work-2")]), step([conflicting[1]])])
    assert receipt["acceptedWorkIds"] == ["work-2"]
    assert "candidate-1" not in state.decisions
    assert state.decisions["candidate-2"]["verdict"] == "keep"
    assert state.work_items()[0]["pendingAssessment"]["conflictingAssessments"] == conflicting
    assert "work-1" in receipt["pendingWorkIds"]
    assert receipt["rejected"] == ["work-1: conflicting outcomes in the same response; reconcile the proposed assessments using observed evidence"]


def test_conflicting_outcome_repair_reuses_evidence_and_preserves_independent_result():
    state = ledger()
    workflow = VerificationWorkflow(state)
    workflow.apply([step([outcome(), outcome(verdict="uncertain"), outcome("work-2")])])
    revision = state.revision
    receipt = workflow.apply([step([outcome(verdict="refuted", reason="Caller guard prevents the proposed failure")])])
    assert receipt["acceptedWorkIds"] == ["work-1"]
    assert not receipt["rejected"]
    assert state.decisions["candidate-1"]["verdict"] == "dismiss"
    assert state.decisions["candidate-2"]["verdict"] == "keep"
    assert state.revision == revision + 1
    assert "pendingAssessment" not in state.work_items()[0]


@pytest.mark.parametrize("reverse", [False, True])
def test_provisional_and_terminal_status_without_read_is_not_a_conflict(reverse):
    state = ledger()
    values = [outcome(verdict="needs_evidence"), outcome(verdict="confirmed")]
    if reverse:
        values.reverse()
    receipt = VerificationWorkflow(state).apply([step(values)])
    assert not receipt["rejected"]
    assert receipt["acceptedWorkIds"] == ["work-1"]
    assert state.decisions["candidate-1"]["verdict"] == "keep"


def test_conflicting_question_outcomes_stay_pending_while_requested_source_executes():
    state = ledger()
    values = [outcome("work-3", "confirmed"), outcome("work-3", "refuted")]
    receipt = VerificationWorkflow(state).apply([step(values, [request("work-3")])])
    assert receipt["requests"] == [request("work-3")]
    assert "question" not in state.answers
    assert "question" not in state.resolved
    assert state.work_items()[2]["pendingAssessment"]["conflictingAssessments"] == values


def test_repeated_identical_terminal_disposition_is_not_a_conflict():
    state = ledger()
    receipt = VerificationWorkflow(state).apply([step([outcome(), outcome(reason="The same disposition explained precisely")])])
    assert not receipt["rejected"]
    assert receipt["acceptedWorkIds"] == ["work-1"]
    assert state.decisions["candidate-1"]["verdict"] == "keep"


def established_question_finding():
    state = ledger()
    workflow = VerificationWorkflow(state)
    answer = outcome("work-3", issue={"file": "src/changed.ts", "line": 2,
        "title": "Question discovered defect", "reason": "Source-backed new defect"})
    workflow.apply([step([outcome(), outcome("work-2"), answer])])
    representative = state._work_representatives["work-3"]
    representative_work = state._work_ids[("candidate", representative)]
    assert state.complete
    return state, workflow, representative, representative_work


@pytest.mark.parametrize("question_verdict", ["confirmed", "refuted"])
def test_resolving_reopened_question_without_report_leaves_derived_finding_pending(question_verdict):
    state, workflow, representative, representative_work = established_question_finding()
    workflow.apply([step([outcome("work-3", "needs_evidence")], [request("work-3")])])
    prior = next(item for item in state.work_items() if item["id"] == representative_work)
    assert "outcome" not in prior
    assert prior["pendingAssessment"]["evidenceIds"] == ["diff:part"]
    workflow.apply([step([outcome("work-3", question_verdict)])])
    assert "question" in state.resolved
    assert state.pending_work_ids() == [representative_work]
    assert not state.complete
    assert representative not in state.decisions
    workflow.apply([step([outcome(representative_work, "refuted", reason="Source disproves the derived finding")])])
    assert state.complete
    assert state.decisions[representative]["verdict"] == "dismiss"


def test_explicit_derived_finding_assessment_settles_alongside_reopened_question_read():
    state, workflow, representative, representative_work = established_question_finding()
    receipt = workflow.apply([step([
        outcome("work-3", "needs_evidence"),
        outcome(representative_work, "confirmed", reason="Independent observed source establishes this defect"),
    ], [request("work-3")])])
    assert receipt["acceptedWorkIds"] == [representative_work]
    assert state.decisions[representative]["verdict"] == "keep"
    assert state.pending_work_ids() == ["work-3"]
    workflow.apply([step([outcome("work-3", "refuted")])])
    assert state.complete
    assert state.decisions[representative]["verdict"] == "keep"
    assert state.candidates[representative]["reason"] == "Independent observed source establishes this defect"
