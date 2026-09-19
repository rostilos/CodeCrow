import hashlib
import json
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


def test_stage_2_candidate_uses_issue_specific_prompt_visibility():
    processed = DiffProcessor().process(TWO_HUNK_DIFF)
    first_hunk, second_hunk = processed.files[0].hunks
    issue = _issue(snippet="second_new()").model_copy(update={"id": "CROSS_001"})
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
        {first_hunk.id, second_hunk.id},
        {},
        {
            "issuePromptDigests": json.dumps({
                "CROSS_001": "sha256:" + "b" * 64,
            }),
            "issuePromptHunkIds": json.dumps({
                "CROSS_001": [first_hunk.id],
            }),
        },
    )

    assert apply_candidate_provenance_gate(
        [issue],
        _request(),
        processed,
        ledger,
        review_units.units_by_hunk,
    ) == []
    ledger.assert_terminal()


def test_stage_2_candidate_inherits_only_its_prompt_evidence_ids():
    processed = DiffProcessor().process(RAW_DIFF)
    hunk = processed.files[0].hunks[0]
    issue = _issue().model_copy(update={
        "id": "CROSS_001",
        "evidenceRefs": ["RAG-own-shard"],
    })
    review_units = Stage1ReviewUnitState(
        units_by_hunk={hunk.id: {"sha256:unit"}},
        unit_owner={"sha256:unit": 1},
        completed_unit_ids={"sha256:unit"},
        registered=True,
    )
    ledger = CandidateEvidenceLedger()

    _register_stage_2_candidates(
        [issue],
        _request(),
        processed,
        review_units,
        ledger,
        {hunk.id},
        {
            "RAG-own-shard": (),
            "RAG-other-shard": (),
        },
        {
            "issuePromptDigests": json.dumps({
                "CROSS_001": "sha256:" + "1" * 64,
            }),
            "issuePromptHunkIds": json.dumps({
                "CROSS_001": [hunk.id],
            }),
            "issuePromptEvidenceIds": json.dumps({
                "CROSS_001": ["RAG-own-shard"],
            }),
        },
    )

    assert apply_candidate_provenance_gate(
        [issue],
        _request(),
        processed,
        ledger,
        review_units.units_by_hunk,
    ) == [issue]
    record = ledger.summary()["records"][0]
    assert record["visibleEvidenceIds"] == ["RAG-own-shard"]


def test_stage_2_typed_candidate_cannot_cite_another_shards_evidence():
    processed = DiffProcessor().process(RAW_DIFF)
    hunk = processed.files[0].hunks[0]
    issue = _issue().model_copy(update={
        "id": "CROSS_001",
        "evidenceRefs": ["RAG-other-shard"],
        "claimKind": "python-call-contract",
    })
    review_units = Stage1ReviewUnitState(
        units_by_hunk={hunk.id: {"sha256:unit"}},
        unit_owner={"sha256:unit": 1},
        completed_unit_ids={"sha256:unit"},
        registered=True,
    )
    ledger = CandidateEvidenceLedger()

    _register_stage_2_candidates(
        [issue],
        _request(),
        processed,
        review_units,
        ledger,
        {hunk.id},
        {
            "RAG-own-shard": (),
            "RAG-other-shard": (),
        },
        {
            "issuePromptDigests": json.dumps({
                "CROSS_001": "sha256:" + "2" * 64,
            }),
            "issuePromptHunkIds": json.dumps({
                "CROSS_001": [hunk.id],
            }),
            "issuePromptEvidenceIds": json.dumps({
                "CROSS_001": ["RAG-own-shard"],
            }),
        },
    )

    assert apply_candidate_provenance_gate(
        [issue],
        _request(),
        processed,
        ledger,
        review_units.units_by_hunk,
    ) == []
    assert ledger.summary()["rejectionCounts"] == {
        "candidate_provenance:evidence_outside_generation_prompt": 1
    }


def test_stage_2_candidate_canonicalizes_prompt_visible_diff_marker_anchor():
    processed = DiffProcessor().process(TWO_HUNK_DIFF)
    _, second_hunk = processed.files[0].hunks
    issue = _issue(snippet="+second_new()").model_copy(update={
        "id": "CROSS_001",
        "line": 10,
    })
    review_units = Stage1ReviewUnitState(
        units_by_hunk={second_hunk.id: {"sha256:second-unit"}},
        unit_owner={"sha256:second-unit": 1},
        completed_unit_ids={"sha256:second-unit"},
        registered=True,
    )
    ledger = CandidateEvidenceLedger()

    _register_stage_2_candidates(
        [issue],
        _request(),
        processed,
        review_units,
        ledger,
        {second_hunk.id},
        {},
        {"generationPromptDigest": "sha256:" + "d" * 64},
    )

    assert issue.codeSnippet == "second_new()"
    assert apply_candidate_provenance_gate(
        [issue],
        _request(),
        processed,
        ledger,
        review_units.units_by_hunk,
    ) == [issue]


def test_stage_2_candidate_canonicalizes_missing_snippet_from_explicit_line():
    processed = DiffProcessor().process(RAW_DIFF)
    hunk = processed.files[0].hunks[0]
    issue = _issue(snippet="").model_copy(update={
        "id": "CROSS_001",
        "line": 1,
    })
    review_units = Stage1ReviewUnitState(
        units_by_hunk={hunk.id: {"sha256:unit"}},
        unit_owner={"sha256:unit": 1},
        completed_unit_ids={"sha256:unit"},
        registered=True,
    )
    ledger = CandidateEvidenceLedger()

    _register_stage_2_candidates(
        [issue],
        _request(),
        processed,
        review_units,
        ledger,
        {hunk.id},
        {},
        {"generationPromptDigest": "sha256:" + "e" * 64},
    )

    assert issue.codeSnippet == "dangerous()"
    assert apply_candidate_provenance_gate(
        [issue],
        _request(),
        processed,
        ledger,
        review_units.units_by_hunk,
    ) == [issue]


def test_stage_2_candidate_does_not_reanchor_conflicting_snippet_by_line():
    processed = DiffProcessor().process(RAW_DIFF)
    hunk = processed.files[0].hunks[0]
    issue = _issue(snippet="different_call()").model_copy(update={
        "id": "CROSS_001",
        "line": 1,
    })
    review_units = Stage1ReviewUnitState(
        units_by_hunk={hunk.id: {"sha256:unit"}},
        unit_owner={"sha256:unit": 1},
        completed_unit_ids={"sha256:unit"},
        registered=True,
    )
    ledger = CandidateEvidenceLedger()

    _register_stage_2_candidates(
        [issue],
        _request(),
        processed,
        review_units,
        ledger,
        {hunk.id},
        {},
        {"generationPromptDigest": "sha256:" + "f" * 64},
    )

    assert issue.codeSnippet == "different_call()"
    assert apply_candidate_provenance_gate(
        [issue],
        _request(),
        processed,
        ledger,
        review_units.units_by_hunk,
    ) == []


def test_stage_2_candidate_requires_issue_specific_hunk_provenance():
    processed = DiffProcessor().process(RAW_DIFF)
    issue = _issue().model_copy(update={"id": "CROSS_001"})

    with pytest.raises(RuntimeError, match="issue-specific visible-hunk"):
        _register_stage_2_candidates(
            [issue],
            _request(),
            processed,
            Stage1ReviewUnitState(
                units_by_hunk={},
                unit_owner={},
                completed_unit_ids=set(),
                registered=True,
            ),
            CandidateEvidenceLedger(),
            {processed.files[0].hunks[0].id},
            {},
            {
                "issuePromptDigests": json.dumps({
                    "CROSS_001": "sha256:" + "c" * 64,
                }),
                "issuePromptHunkIds": "{}",
            },
        )


def test_typed_candidate_cannot_cite_evidence_visible_only_to_another_prompt():
    processed = DiffProcessor().process(RAW_DIFF)
    hunk_id = processed.files[0].hunks[0].id
    issue = _issue().model_copy(update={
        "evidenceRefs": ["RAG-other-batch"],
        "claimKind": "python-call-contract",
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


def test_diff_grounded_generic_candidate_drops_unknown_optional_evidence():
    processed = DiffProcessor().process(RAW_DIFF)
    hunk_id = processed.files[0].hunks[0].id
    issue = _issue().model_copy(update={
        "evidenceRefs": ["RAG-visible", "RAG-unknown"],
        "claimKind": "",
    })
    ledger = CandidateEvidenceLedger()
    ledger.register(
        issue,
        stage="stage_2",
        source_key="cross-file:0",
        review_unit_ids=("sha256:unit",),
        prompt_hunk_ids=(hunk_id,),
        generation_prompt="stage 2 prompt",
        visible_evidence_by_id={"RAG-visible": ()},
    )

    kept = apply_candidate_provenance_gate(
        [issue],
        _request(),
        processed,
        ledger,
        {hunk_id: {"sha256:unit"}},
    )

    assert kept == [issue]
    assert issue.evidenceRefs == ["RAG-visible"]
    ledger.publish(kept)
    ledger.assert_terminal()
    assert ledger.summary()["records"][0]["evidenceRefs"] == [
        "RAG-visible"
    ]


def test_generic_candidate_cannot_gain_citations_during_reconciliation():
    processed = DiffProcessor().process(RAW_DIFF)
    hunk_id = processed.files[0].hunks[0].id
    issue = _issue().model_copy(update={
        "evidenceRefs": ["RAG-original"],
        "claimKind": "",
    })
    ledger = CandidateEvidenceLedger()
    ledger.register(
        issue,
        stage="stage_2",
        source_key="cross-file:0",
        review_unit_ids=("sha256:unit",),
        prompt_hunk_ids=(hunk_id,),
        generation_prompt="stage 2 prompt",
        visible_evidence_by_id={
            "RAG-original": (),
            "RAG-added-by-reconciliation": (),
        },
    )
    # A canonical object may be enriched while duplicate candidates reconcile,
    # but its ledger record still owns only the citations it generated.
    issue.evidenceRefs.append("RAG-added-by-reconciliation")

    kept = apply_candidate_provenance_gate(
        [issue],
        _request(),
        processed,
        ledger,
        {hunk_id: {"sha256:unit"}},
    )

    assert kept == [issue]
    assert issue.evidenceRefs == ["RAG-original"]
    assert ledger.summary()["records"][0]["evidenceRefs"] == [
        "RAG-original"
    ]


def test_stage_1_candidate_can_cite_search_evidence_visible_to_its_prompt():
    processed = DiffProcessor().process(RAW_DIFF)
    hunk_id = processed.files[0].hunks[0].id
    issue = _issue().model_copy(update={
        "evidenceRefs": ["RAG-search-result"],
    })
    ledger = CandidateEvidenceLedger()
    ledger.register(
        issue,
        stage="stage_1",
        source_key="batch-1:0",
        review_unit_ids=("sha256:unit",),
        prompt_hunk_ids=(hunk_id,),
        generation_prompt="stage 1 agent prompt",
        visible_evidence_by_id={"RAG-search-result": ()},
    )

    assert apply_candidate_provenance_gate(
        [issue],
        _request(),
        processed,
        ledger,
        {hunk_id: {"sha256:unit"}},
    ) == [issue]


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
            "exactEvidenceIds": 0,
        },
        "revisionBinding": {
            "pullRequestId": 12,
            "targetBranch": "main",
            "sourceRevision": "a" * 40,
            "baseRevision": "b" * 40,
            "baseGenerationManifestSha256": "c" * 64,
            "basePluginFingerprint": "sha256:" + "1" * 64,
            "basePluginDescriptorFingerprint": "sha256:" + "2" * 64,
            "basePluginImplementationFingerprint": "sha256:" + "3" * 64,
            "baseIndexRepresentationFingerprint": "sha256:" + "4" * 64,
        },
    })

    assert evidence["candidates"]["generated"] == 1
    assert evidence["candidates"]["published"] == 1
    assert evidence["candidates"]["records"][0]["anchorHunkIds"] == [hunk_id]
