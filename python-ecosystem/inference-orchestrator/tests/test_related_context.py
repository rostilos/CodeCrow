from hashlib import sha256

from service.review.orchestrator.context_helpers import format_rag_context
from service.review.orchestrator.related_context import (
    build_related_context_pack,
    flatten_deterministic_context,
)


BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
SNAPSHOT = {
    "schema_version": 1,
    "base_sha": BASE_SHA,
    "head_sha": HEAD_SHA,
    "merge_base_sha": "c" * 40,
    "parser_version": "tree-sitter-v1",
    "chunker_version": "ast-code-splitter-v1",
    "embedding_version": "configured-v1",
}


def _exact_chunk(
    text: str = "class Dependency: pass",
    *,
    revision: str = BASE_SHA,
    branch: str = "main",
    source: str = "deterministic",
    execution_id: str | None = None,
):
    metadata = {
        "path": "src/dependency.py",
        "branch": branch,
        "snapshot_sha": revision,
        "content_digest": sha256(text.encode("utf-8")).hexdigest(),
        "parser_version": SNAPSHOT["parser_version"],
        "chunker_version": SNAPSHOT["chunker_version"],
        "embedding_version": SNAPSHOT["embedding_version"],
        "primary_name": "Dependency",
        "start_line": 4,
        "end_line": 8,
    }
    if execution_id:
        metadata["execution_id"] = execution_id
    return {
        "text": text,
        "score": 0.95,
        "metadata": metadata,
        "_source": source,
        "_match_type": "definition",
    }


def test_exact_pack_hash_checks_and_explains_structural_context():
    result = build_related_context_pack(
        chunks=[_exact_chunk()],
        anchor_paths=["src/changed.py"],
        snapshot=SNAPSHOT,
        execution_id="execution-1",
        source_branch="feature",
        base_branch="main",
        base_index_available=True,
    )

    assert result.pack.mode == "exact"
    assert result.pack.rejected_chunk_count == 0
    assert len(result.pack.items) == 1
    item = result.pack.items[0]
    assert item.snapshot_verified is True
    assert item.revision == BASE_SHA
    assert item.relationship_type == "definition"
    assert item.evidence_strength == "structural_lead"
    assert item.start_line == 4 and item.end_line == 8
    assert not any(gap.code == "structural_context_missing" for gap in result.pack.gaps)


def test_exact_pack_rejects_mixed_revision_and_tampered_content():
    mixed_revision = _exact_chunk(revision="d" * 40)
    tampered = _exact_chunk(text="trusted")
    tampered["text"] = "changed after digest"

    result = build_related_context_pack(
        chunks=[mixed_revision, tampered],
        anchor_paths=["src/changed.py"],
        snapshot=SNAPSHOT,
        execution_id="execution-1",
        source_branch="feature",
        base_branch="main",
        base_index_available=False,
    )

    assert result.accepted_chunks == []
    assert result.pack.rejected_chunk_count == 2
    codes = {gap.code for gap in result.pack.gaps}
    assert "context_receipt_rejected" in codes
    assert "exact_base_index_unavailable" in codes
    assert "related_context_empty" in codes


def test_disabled_frozen_base_index_rejects_base_chunks_that_appear_later():
    result = build_related_context_pack(
        chunks=[_exact_chunk()],
        anchor_paths=["src/changed.py"],
        snapshot=SNAPSHOT,
        execution_id="execution-1",
        source_branch="feature",
        base_branch="main",
        base_index_available=False,
    )

    assert result.accepted_chunks == []
    assert result.pack.rejected_chunk_count == 1
    rejection_gap = next(
        gap for gap in result.pack.gaps if gap.code == "context_receipt_rejected"
    )
    assert "base_index_not_selected=1" in rejection_gap.detail


def test_pr_overlay_must_match_head_and_execution():
    accepted = _exact_chunk(
        revision=HEAD_SHA,
        branch="feature",
        source="pr_indexed",
        execution_id="execution-1",
    )
    rejected = _exact_chunk(
        text="other execution",
        revision=HEAD_SHA,
        branch="feature",
        source="pr_indexed",
        execution_id="execution-2",
    )

    result = build_related_context_pack(
        chunks=[accepted, rejected],
        anchor_paths=["src/changed.py"],
        snapshot=SNAPSHOT,
        execution_id="execution-1",
        source_branch="feature",
        base_branch="main",
    )

    assert len(result.pack.items) == 1
    assert result.pack.items[0].retrieval_method == "pr_overlay"
    assert result.pack.items[0].evidence_strength == "exact_source"
    assert result.pack.rejected_chunk_count == 1


def test_duplication_labeled_pr_overlay_cannot_bypass_execution_binding():
    accepted = _exact_chunk(
        revision=HEAD_SHA,
        branch="feature",
        source="duplication",
        execution_id="execution-1",
    )
    accepted["metadata"]["pr"] = True
    rejected = _exact_chunk(
        text="same revision from another execution",
        revision=HEAD_SHA,
        branch="feature",
        source="duplication",
        execution_id="execution-2",
    )
    rejected["metadata"]["pr"] = True

    result = build_related_context_pack(
        chunks=[accepted, rejected],
        anchor_paths=["src/changed.py"],
        snapshot=SNAPSHOT,
        execution_id="execution-1",
        source_branch="feature",
        base_branch="main",
    )

    assert len(result.pack.items) == 1
    assert result.pack.items[0].retrieval_method == "duplication"
    assert result.pack.rejected_chunk_count == 1
    assert "pr_overlay_execution_mismatch=1" in next(
        gap.detail for gap in result.pack.gaps if gap.code == "context_receipt_rejected"
    )


def test_formatter_exposes_receipt_reason_strength_and_gaps():
    result = build_related_context_pack(
        chunks=[_exact_chunk()],
        anchor_paths=["src/changed.py"],
        snapshot=SNAPSHOT,
        execution_id="execution-1",
        source_branch="feature",
        base_branch="main",
        base_index_available=True,
    )
    rendered = format_rag_context({
        "related_context_pack_v1": result.pack.model_dump(mode="json")
    })

    assert "RELATED CONTEXT PACK V1" in rendered
    assert result.pack.receipt.snapshot_id in rendered
    assert "Why selected: Defines an identifier" in rendered
    assert "strength=structural_lead" in rendered


def test_legacy_pack_does_not_claim_snapshot_verification():
    result = build_related_context_pack(
        chunks=[{
            "text": "legacy code",
            "score": 0.8,
            "metadata": {"path": "legacy.py"},
        }],
        anchor_paths=["changed.py"],
    )

    assert result.pack.mode == "legacy"
    assert result.pack.receipt is None
    assert result.pack.items[0].snapshot_verified is False
    assert result.pack.items[0].revision is None


def test_deterministic_groups_keep_structural_relationship_labels():
    definition = _exact_chunk()
    definition["_match_type"] = "transitive_parent"
    flattened = flatten_deterministic_context({
        "context": {
            "changed_files": {},
            "related_definitions": {"Dependency": [definition]},
            "class_context": {},
            "namespace_context": {},
            "chunks": [definition],
        }
    })

    assert len(flattened) == 1
    assert flattened[0]["_source"] == "deterministic"
    assert flattened[0]["_match_type"] == "definition"
    assert flattened[0]["definition_name"] == "Dependency"
