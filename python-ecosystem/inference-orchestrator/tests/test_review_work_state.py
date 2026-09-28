"""Unified verification work and actual wrong-bucket receipt regressions."""
from types import SimpleNamespace

from service.review.verification_state import VerificationState


def state_with_work():
    part = SimpleNamespace(id="part", path="header.scss", side="proposed", anchors={3: "margin-left: auto;"},
                           diff="@@ -3 +3 @@\n-float: right;\n+margin-left: auto;\n")
    issue = {"partId": "part", "file": "header.scss", "line": 3, "title": "Panel layout", "reason": "Nested panel is not a flex item"}
    state = VerificationState([issue], [{"id": "batch:question:0", "question": "Is title still floated?", "paths": ["header.scss"]}], {"part": part})
    source, _ = state.add_evidence("getReviewDiff", {"status": "ready", "parts": [{"id": "part", "path": "header.scss", "side": "proposed", "diff": part.diff}]})
    state.begin_turn()
    return state, source


def assessment(work_id, verdict, source, **fields):
    return {"workId": work_id, "verdict": verdict, "reason": "Exact source establishes the outcome", "evidenceIds": [source], **fields}


def test_one_id_space_settles_candidates_and_questions_without_separate_buckets():
    state, source = state_with_work()
    assert [(item["id"], item["kind"]) for item in state.work_items()] == [("work-1", "candidate"), ("work-2", "investigation")]
    receipt = state.apply_assessments([assessment("work-1", "confirmed", source), assessment("work-2", "refuted", source)])
    assert receipt["pendingWorkIds"] == []
    assert receipt["acceptedWorkIds"] == ["work-1", "work-2"]
    assert state.decisions["candidate-1"]["verdict"] == "keep"
    assert state.resolved == {"batch:question:0"}


def test_settled_sibling_cannot_schedule_more_evidence_even_when_requested_later():
    state, source = state_with_work()
    receipt = state.apply_assessments([
        assessment("work-1", "needs_evidence", source),
        assessment("work-1", "refuted", source),
        assessment("work-2", "needs_evidence", source),
    ])
    assert receipt["evidenceWorkIds"] == ["work-2"]
    assert state.pending_work_ids() == ["work-2"]
    assert state.revision == 1
    next_receipt = state.apply_assessments([assessment("work-1", "needs_evidence", source)])
    assert not next_receipt["evidenceWorkIds"]
    assert next_receipt["rejected"]


def test_question_can_report_source_backed_new_issue_without_duplicate_reason():
    state, source = state_with_work()
    receipt = state.apply_assessments([assessment("work-2", "refuted", source, issue={
        "file": "header.scss", "line": 3, "title": "Nested panel loses right alignment", "severity": "LOW",
    })])
    assert receipt["rejected"] == []
    assert state.answers["batch:question:0"]["status"] == "resolved"
    assert state.candidates["candidate-2"]["reason"] == "Exact source establishes the outcome"
    assert state.candidates["candidate-2"]["line"] == 3
    assert state.decisions["candidate-2"]["verdict"] == "keep"
    assert state.work_target("work-3") == ("candidate", "candidate-2")


def test_unknown_work_and_candidate_ids_are_actionable_not_silently_accepted():
    state, source = state_with_work()
    receipt = state.apply_assessments([assessment("imagined-work", "confirmed", source), assessment("work-1", "confirmed", source)])
    assert receipt["acceptedWorkIds"] == ["work-1"]
    assert "unknown workId" in receipt["rejected"][0]
    receipt = state.record(decisions=[{"candidateId": "NEW-FINDING-PANEL", "verdict": "keep", "reason": "Real panel issue", "evidenceIds": [source]}])
    assert "unknown candidate ID" in receipt["rejected"][0]


def test_actual_wrong_question_bucket_is_normalized_without_guessing_a_new_finding():
    state, source = state_with_work()
    receipt = state.record(decisions=[
        {"candidateId": "batch:question:0", "verdict": "dismiss", "reason": "Title retains a float via span13", "evidenceIds": [source]},
        {"candidateId": "NEW-FINDING-PANEL", "verdict": "keep", "reason": "Panel is nested under row", "evidenceIds": [source]},
    ])
    assert receipt["answeredInvestigationIds"] == ["batch:question:0"]
    assert len(receipt["rejected"]) == 1
    assert len(state.candidates) == 1


def test_unknown_evidence_still_cannot_confirm_a_work_item():
    state, _ = state_with_work()
    receipt = state.apply_assessments([assessment("work-1", "confirmed", "future-read")])
    assert receipt["pendingWorkIds"] == ["work-1", "work-2"]
    assert receipt["rejected"]


def test_uncertainty_settles_work_without_manufacturing_a_finding():
    state, source = state_with_work()
    receipt = state.apply_assessments([assessment("work-2", "uncertain", source)])
    assert receipt["pendingWorkIds"] == ["work-1"]
    assert "batch:question:0" not in state.resolved
    assert state.answers["batch:question:0"]["status"] == "uncertain"


def test_duplicate_representatives_resolve_in_unified_id_space():
    state, source = state_with_work()
    state.apply_assessments([assessment("work-2", "confirmed", source, issue={"file": "header.scss", "line": 3, "title": "Same panel defect"})])
    receipt = state.apply_assessments([assessment("work-1", "duplicate", source, duplicateOf="work-2")])
    assert not receipt["rejected"]
    assert state.decisions["candidate-1"]["duplicateOf"] == "candidate-2"


def test_needs_evidence_does_not_advance_work_revision():
    state, source = state_with_work()
    first = state.apply_assessments([assessment("work-2", "needs_evidence", source)])
    second = state.apply_assessments([assessment("work-2", "needs_evidence", source, reason="A different way to ask the same question")])
    assert first["evidenceWorkIds"] == second["evidenceWorkIds"] == ["work-2"]
    assert state.revision == 0


def test_repeated_valid_question_issue_is_not_rejected_as_missing_source():
    state, source = state_with_work()
    value = assessment("work-2", "refuted", source, issue={"file": "header.scss", "line": 3, "title": "Nested panel"})
    state.apply_assessments([value])
    receipt = state.apply_assessments([value])
    assert not receipt["rejected"]
    assert len(state.candidates) == 2


def test_projected_outcomes_do_not_reintroduce_internal_identifier_spaces():
    state, source = state_with_work()
    state.apply_assessments([assessment("work-2", "refuted", source)])
    state.apply_assessments([assessment("work-1", "confirmed", source)])
    items = state.work_items()
    assert items[0]["outcome"]["verdict"] == "confirmed"
    assert "id" not in items[1]["outcome"]
    assert "status" not in items[1]["outcome"]
    assert items[1]["outcome"]["verdict"] == "refuted"


def test_rejected_question_issue_survives_empty_step_until_explicitly_repaired():
    state, source = state_with_work()
    state.apply_assessments([assessment("work-1", "refuted", source), assessment("work-2", "refuted", source, issue={
        "file": "header.scss", "line": 900, "title": "Panel defect",
    })])
    assert not state.complete
    assert state.pending_work_ids() == ["work-2"]
    assert state.work_items()[1]["pendingIssue"]["line"] == 900
    assert state.apply_assessments([])["rejected"]
    assert state.apply_assessments([assessment("work-2", "needs_evidence", source)])["evidenceWorkIds"] == ["work-2"]
    receipt = state.apply_assessments([assessment("work-2", "refuted", source, issue={
        "file": "header.scss", "line": 3, "title": "Panel defect",
    })])
    assert not receipt["rejected"]
    assert state.decisions["candidate-2"]["verdict"] == "keep"
    assert state.complete
    assert "pendingIssue" not in state.work_items()[1]


def test_explicit_withdrawal_clears_proposed_issue_correction_without_source_search():
    state, source = state_with_work()
    state.apply_assessments([assessment("work-2", "refuted", source, issue={"title": "Not anchored"})])
    receipt = state.apply_assessments([assessment("work-2", "refuted", source, issue=None,
        reason="The separate panel claim is unsupported; withdraw that proposed issue")])
    assert not receipt["rejected"]
    assert len(state.candidates) == 1


def test_candidate_location_is_corrected_when_observed_source_proves_new_active_anchor():
    state, source = state_with_work()
    state.parts["part"].anchors[8] = "other_panel()"
    source, _ = state.add_evidence("readReviewFile", {"status": "ready", "path": "header.scss", "side": "proposed",
        "startLine": 8, "endLine": 8, "content": "other_panel()\n"})
    state.begin_turn()
    receipt = state.apply_assessments([assessment("work-1", "confirmed", source, issue={
        "file": "header.scss", "line": 8, "title": "Correctly located panel defect",
    })])
    assert not receipt["rejected"]
    assert state.candidates["candidate-1"]["line"] == 8
    assert state.candidates["candidate-1"]["codeSnippet"] == "other_panel()"
    assert len(state.candidates) == 1
    assert state.decisions["candidate-1"]["verdict"] == "keep"


def test_unproved_candidate_location_is_retained_for_repair_without_overwriting_original():
    state, source = state_with_work()
    receipt = state.apply_assessments([assessment("work-1", "confirmed", source, issue={
        "file": "header.scss", "line": 999, "title": "Proposed corrected location",
    })])
    assert receipt["rejected"]
    assert state.candidates["candidate-1"]["line"] == 3
    assert "candidate-1" not in state.decisions
    assert state.work_items()[0]["pendingIssue"]["line"] == 999
    assert "work-1" in state.pending_work_ids()
    receipt = state.apply_assessments([assessment("work-1", "confirmed", source, issue={"line": 3, "title": "Correct panel location"})])
    assert not receipt["rejected"]
    assert "pendingIssue" not in state.work_items()[0]


def test_private_provenance_is_not_reused_in_model_work_items():
    state, source = state_with_work()
    state.candidates["candidate-1"]["_verificationEvidenceIds"] = ["previous-case-read"]
    assert "_verificationEvidenceIds" not in state.work_items()[0]


def test_an_unproved_relocation_does_not_republish_a_previously_confirmed_stale_location():
    state, source = state_with_work()
    state.apply_assessments([assessment("work-1", "confirmed", source)])
    receipt = state.apply_assessments([assessment("work-1", "confirmed", source, issue={
        "file": "header.scss", "line": 999, "title": "The actual failing location",
    })])
    assert receipt["rejected"]
    assert "candidate-1" not in state.decisions
    assert "work-1" in state.pending_work_ids()


def test_correcting_a_questions_issue_updates_its_existing_identity():
    state, source = state_with_work()
    state.apply_assessments([assessment("work-2", "refuted", source, issue={
        "file": "header.scss", "line": 3, "title": "Panel defect with secondary premise", "reason": "Initial report wording",
    })])
    receipt = state.apply_assessments([assessment("work-2", "refuted", source, issue={
        "file": "header.scss", "line": 3, "title": "Panel loses alignment", "reason": "The corrected source-supported mechanism",
    })])
    assert not receipt["rejected"]
    assert len(state.candidates) == 2
    assert state.candidates["candidate-2"]["reason"] == "The corrected source-supported mechanism"
    assert state.decisions["candidate-2"]["verdict"] == "keep"


def test_question_issue_can_correct_its_source_proven_anchor_without_leaving_old_copy():
    state, source = state_with_work()
    state.apply_assessments([assessment("work-2", "refuted", source, issue={"file": "header.scss", "line": 3, "title": "Panel defect"})])
    state.parts["part"].anchors[8] = "other_panel()"
    proof, _ = state.add_evidence("readReviewFile", {"status": "ready", "path": "header.scss", "side": "proposed",
        "startLine": 8, "endLine": 8, "content": "other_panel()\n"})
    state.begin_turn()
    receipt = state.apply_assessments([assessment("work-2", "refuted", proof, issue={
        "file": "header.scss", "line": 8, "title": "Corrected panel site",
    })])
    assert not receipt["rejected"]
    assert len(state.candidates) == 2
    assert state.candidates["candidate-2"]["line"] == 8
    assert state.candidates["candidate-2"]["title"] == "Corrected panel site"


def test_pending_relocation_does_not_reintroduce_previous_case_private_citations():
    state, source = state_with_work()
    state.candidates["candidate-1"]["_verificationEvidenceIds"] = ["old-case-read"]
    state.apply_assessments([assessment("work-1", "confirmed", source, issue={"line": 999, "title": "Corrected site"})])
    pending = state.work_items()[0]["pendingIssue"]
    assert pending["line"] == 999
    assert "_verificationEvidenceIds" not in pending


def test_missing_title_is_report_repair_and_title_only_patch_retains_verified_issue():
    state, source = state_with_work()
    verified_reason = "The requested panel is nested under a row, so the new flex margin cannot align it"
    receipt = state.apply_assessments([
        assessment("work-1", "refuted", source),
        assessment("work-2", "confirmed", source, reason=verified_reason, issue={
            "file": "header.scss", "line": 3, "severity": "LOW",
            "suggestedFixDescription": "Apply the layout rule to the row containing the panel",
        }),
    ])
    assert receipt["pendingWorkIds"] == ["work-2"]
    assert len(state.candidates) == 1  # A missing title is never invented from the question.
    pending = state.work_items()[1]
    assert pending["pendingIssue"]["reason"] == verified_reason
    assert any("issue.title is required" in error for error in pending["issueCorrections"])
    assert any("without rereading source" in error for error in pending["issueCorrections"])
    assert not any("correct the report location" in error for error in receipt["rejected"])

    receipt = state.apply_assessments([{
        "workId": "work-2", "verdict": "confirmed", "reason": verified_reason,
        "evidenceIds": [], "issue": {"title": "Nested panel loses alignment"},
    }])
    assert not receipt["rejected"]
    assert state.complete
    issue = state.candidates["candidate-2"]
    assert (issue["file"], issue["line"], issue["reason"], issue["severity"]) == (
        "header.scss", 3, verified_reason, "LOW")
    assert issue["suggestedFixDescription"] == "Apply the layout rule to the row containing the panel"
    assert state.decisions["candidate-2"]["evidenceIds"] == [source]
    assert "pendingIssue" not in state.work_items()[1]
    assert "issueCorrections" not in state.work_items()[1]


def test_report_repair_identifies_invalid_anchor_separately_from_missing_title():
    state, source = state_with_work()
    receipt = state.apply_assessments([assessment("work-2", "confirmed", source, issue={
        "file": "header.scss", "line": 999,
    })])
    assert any("issue.title is required" in error for error in receipt["rejected"])
    assert any("issue.file/line" in error for error in receipt["rejected"])
    assert not any("without rereading source" in error for error in receipt["rejected"])
    receipt = state.apply_assessments([assessment("work-2", "confirmed", source, issue={
        "line": 3, "title": "Panel defect",
    })])
    assert not receipt["rejected"]
    assert state.candidates["candidate-2"]["file"] == "header.scss"
    assert state.candidates["candidate-2"]["line"] == 3


def test_report_repair_can_replace_the_retained_explanation_explicitly():
    state, source = state_with_work()
    state.apply_assessments([assessment("work-2", "confirmed", source, issue={
        "file": "header.scss", "line": 3, "reason": "Original explanation",
    })])
    receipt = state.apply_assessments([assessment("work-2", "confirmed", source, issue={
        "title": "Panel defect", "reason": "Corrected narrower source-supported explanation",
    })])
    assert not receipt["rejected"]
    assert state.candidates["candidate-2"]["reason"] == "Corrected narrower source-supported explanation"


def test_incremental_candidate_repair_uses_current_assessment_reason_and_evidence():
    state, source = state_with_work()
    state.apply_assessments([assessment("work-1", "confirmed", source, reason="Superseded explanation", issue={
        "line": 999, "title": "Correct the panel location",
    })])
    caller, _ = state.add_evidence("readReviewFile", {"status": "ready", "path": "header.html", "side": "proposed",
        "startLine": 1, "endLine": 1, "content": '<div class="row"><div class="panel"></div></div>\n'})
    state.begin_turn()
    receipt = state.apply_assessments([assessment("work-1", "confirmed", caller,
        reason="The observed row nests the panel outside the flex container", issue={"line": 3})])
    assert not receipt["rejected"]
    assert state.candidates["candidate-1"]["reason"] == "The observed row nests the panel outside the flex container"
    assert state.decisions["candidate-1"]["evidenceIds"] == [caller, source]
    assert "Superseded explanation" not in str(state.work_items()[0])
