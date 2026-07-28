import hashlib
from types import SimpleNamespace

import pytest

from model.output_schemas import CodeReviewIssue
from service.review.candidate_ledger import CandidateEvidenceLedger
from service.review.orchestrator.verification_agent import (
    apply_candidate_provenance_gate,
)
from service.review.orchestrator.orchestrator import (
    _register_stage_2_candidates,
)
from service.review.orchestrator.stage_1_file_review import (
    Stage1ReviewUnitState,
)
from service.review.quality_capture import _terminal_pipeline_evidence
from utils.diff_processor import DiffProcessor


RAW_DIFF = """diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1 +1 @@
-safe()
+dangerous()
"""

TWO_HUNK_DIFF = """diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1 +1 @@
-first_old()
+first_new()
@@ -10 +10 @@
-second_old()
+second_new()
"""


def _issue(*, snippet: str = "dangerous()") -> CodeReviewIssue:
    return CodeReviewIssue(
        severity="HIGH",
        category="BUG_RISK",
        file="src/app.py",
        line=1,
        title="Unsafe call remains",
        reason="The changed call still fails for the supplied input.",
        suggestedFixDescription="Use the safe call.",
        codeSnippet=snippet,
    )


def _request():
    return SimpleNamespace(
        previousCodeAnalysisIssues=[],
        enrichmentData=None,
    )


def test_candidate_provenance_binds_anchor_to_owning_review_unit():
    processed = DiffProcessor().process(RAW_DIFF)
    hunk_id = processed.files[0].hunks[0].id
    issue = _issue()
    ledger = CandidateEvidenceLedger()
    ledger.register(
        issue,
        stage="stage_1",
        source_key="batch-1:0",
        review_unit_ids=("sha256:unit",),
        prompt_hunk_ids=(hunk_id,),
        generation_prompt="stage 1 prompt",
    )

    kept = apply_candidate_provenance_gate(
        [issue],
        _request(),
        processed,
        ledger,
        {hunk_id: {"sha256:unit"}},
    )

    assert kept == [issue]
    record = ledger.record_for(issue)
    assert record.anchor_hunk_ids == (hunk_id,)
    assert record.generation_prompt_digest == (
        "sha256:"
        + hashlib.sha256(b"stage 1 prompt").hexdigest()
    )
    ledger.publish(kept)
    ledger.assert_terminal()


def test_candidate_requires_exact_generation_prompt_provenance():
    with pytest.raises(ValueError, match="prompt digest is required"):
        CandidateEvidenceLedger().register(
            _issue(),
            stage="stage_1",
            source_key="batch-1:0",
            review_unit_ids=("sha256:unit",),
            prompt_hunk_ids=("sha256:hunk",),
        )


def test_candidate_provenance_rejects_anchor_outside_generation_unit():
    processed = DiffProcessor().process(RAW_DIFF)
    issue = _issue()
    ledger = CandidateEvidenceLedger()
    ledger.register(
        issue,
        stage="stage_1",
        source_key="batch-1:0",
        review_unit_ids=("sha256:unit",),
        prompt_hunk_ids=("sha256:not-the-source-hunk",),
        generation_prompt="stage 1 prompt",
    )

    assert apply_candidate_provenance_gate(
        [issue],
        _request(),
        processed,
        ledger,
        {},
    ) == []
    ledger.assert_terminal()
    assert ledger.summary()["rejectionCounts"] == {
        "candidate_provenance:anchor_outside_generation_unit": 1
    }


def test_fresh_unregistered_candidate_fails_closed():
    processed = DiffProcessor().process(RAW_DIFF)

    with pytest.raises(RuntimeError, match="no generation provenance"):
        apply_candidate_provenance_gate(
            [_issue()],
            _request(),
            processed,
            CandidateEvidenceLedger(),
            {},
        )


def test_stage_2_candidate_cannot_claim_a_hunk_omitted_from_its_prompt():
    processed = DiffProcessor().process(TWO_HUNK_DIFF)
    first_hunk, second_hunk = processed.files[0].hunks
    issue = _issue(snippet="second_new()")
    review_units = Stage1ReviewUnitState(
        units_by_hunk={
            first_hunk.id: {"sha256:first-unit"},
            second_hunk.id: {"sha256:second-unit"},
        },
        unit_owner={
            "sha256:first-unit": 1,
            "sha256:second-unit": 2,
        },
        completed_unit_ids={
            "sha256:first-unit",
            "sha256:second-unit",
        },
        registered=True,
    )
    ledger = CandidateEvidenceLedger()

    _register_stage_2_candidates(
        [issue],
        _request(),
        processed,
        review_units,
        ledger,
        {first_hunk.id},
        {},
        {"generationPromptDigest": "sha256:" + "a" * 64},
    )

    assert apply_candidate_provenance_gate(
        [issue],
        _request(),
        processed,
        ledger,
        review_units.units_by_hunk,
    ) == []
    ledger.assert_terminal()
    assert ledger.summary()["rejectionCounts"] == {
        "candidate_provenance:unbound_review_unit": 1
    }


def test_candidate_cannot_cite_evidence_visible_only_to_another_prompt():
    processed = DiffProcessor().process(RAW_DIFF)
    hunk_id = processed.files[0].hunks[0].id
    issue = _issue().model_copy(update={
        "evidenceRefs": ["RAG-other-batch"],
    })
    ledger = CandidateEvidenceLedger()
    ledger.register(
        issue,
        stage="stage_1",
        source_key="batch-1:0",
        review_unit_ids=("sha256:unit",),
        prompt_hunk_ids=(hunk_id,),
        generation_prompt="stage 1 prompt",
        visible_evidence_by_id={"RAG-this-batch": ()},
    )

    assert apply_candidate_provenance_gate(
        [issue],
        _request(),
        processed,
        ledger,
        {hunk_id: {"sha256:unit"}},
    ) == []
    ledger.assert_terminal()
    assert ledger.summary()["rejectionCounts"] == {
        "candidate_provenance:evidence_outside_generation_prompt": 1
    }
    candidate_id = ledger.summary()["records"][0]["candidateId"]
    assert ledger.hunk_receipts(((hunk_id, "src/app.py"),)) == [{
        "hunkId": hunk_id,
        "path": "src/app.py",
        "promptCandidateIds": [candidate_id],
        "anchoredCandidateIds": [candidate_id],
        "publishedCandidateIds": [],
        "rejectedCandidateIds": [candidate_id],
        "outcome": "rejected",
    }]


def test_terminal_capture_accepts_deterministic_candidate_ledger():
    processed = DiffProcessor().process(RAW_DIFF)
    hunk_id = processed.files[0].hunks[0].id
    issue = _issue()
    ledger = CandidateEvidenceLedger()
    ledger.register(
        issue,
        stage="stage_1",
        source_key="batch-1:0",
        review_unit_ids=("sha256:unit",),
        prompt_hunk_ids=(hunk_id,),
        generation_prompt="stage 1 prompt",
    )
    ledger.confirm_anchor_hunks(issue, (hunk_id,))
    ledger.publish([issue])
    evidence = _terminal_pipeline_evidence({
        "state": "review_evidence_completed",
        "hunkCoverage": {
            "ingested": 0,
            "planned": 0,
            "reviewed": 0,
            "validated": 0,
            "completed": 1,
            "excluded": 0,
        },
        "reviewUnits": {"registered": 1, "completed": 1},
        "candidates": ledger.summary(),
        "hunkReceipts": ledger.hunk_receipts(
            ((hunk_id, "src/app.py"),)
        ),
        "retrieval": {
            "deterministicStates": ["complete"],
            "semanticFailures": 0,
            "semanticDisabled": False,
            "exactEvidenceIds": 0,
        },
    })

    assert evidence["candidates"]["generated"] == 1
    assert evidence["candidates"]["published"] == 1
    assert evidence["candidates"]["records"][0]["anchorHunkIds"] == [hunk_id]
