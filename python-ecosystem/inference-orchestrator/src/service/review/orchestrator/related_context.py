"""Build a revision-safe related-context pack from RAG retrieval output."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from model.related_context import (
    ContextAnchorV1,
    ContextGapV1,
    ContextSnapshotReceiptV1,
    RelatedContextItemV1,
    RelatedContextPackV1,
)


_SHA_256 = re.compile(r"^[0-9a-f]{64}$")
_STRUCTURAL_TYPES = {"definition", "transitive_parent"}
_DIRECT_TYPES = {"changed_file", "class_context"}
_SELECTION_LIMITS = (8, 8, 4)


@dataclass(frozen=True)
class RelatedContextBuildResult:
    pack: RelatedContextPackV1
    accepted_chunks: List[Dict[str, Any]]


def manifest_anchor_digests(request: Any) -> Dict[str, str]:
    """Return exact changed-source digests from the validated input manifest."""
    manifest = getattr(request, "executionManifest", None)
    if manifest is None:
        return {}

    digests: Dict[str, str] = {}
    for artifact in getattr(manifest, "inputArtifacts", ()):
        if getattr(artifact, "kind", None) != "source-file":
            continue
        path = str(getattr(artifact, "contentKey", "") or "").lstrip("/")
        digest = getattr(artifact, "contentDigest", None)
        if path and isinstance(digest, str) and _SHA_256.fullmatch(digest):
            digests[path] = digest
    return digests


def flatten_deterministic_context(
    response: Optional[Mapping[str, Any]],
    *,
    max_chunks: int = 80,
) -> List[Dict[str, Any]]:
    """Flatten every deterministic group while retaining relationship labels."""
    if not isinstance(response, Mapping):
        return []
    nested = response.get("context")
    context = nested if isinstance(nested, Mapping) else response
    flattened: List[Dict[str, Any]] = []
    seen = set()

    def add(chunk: Any, relationship_type: str, group_key: str = "") -> None:
        if len(flattened) >= max_chunks or not isinstance(chunk, Mapping):
            return
        value = dict(chunk)
        metadata = value.get("metadata")
        metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
        content = value.get("text") or value.get("content") or ""
        path = str(
            metadata.get("path")
            or metadata.get("file_path")
            or value.get("path")
            or value.get("file_path")
            or ""
        ).lstrip("/")
        identity = (path, sha256(str(content).encode("utf-8")).hexdigest())
        if identity in seen:
            return
        seen.add(identity)
        value["text"] = str(content)
        value.setdefault("content", str(content))
        value["metadata"] = metadata
        value.setdefault("path", path)
        value.setdefault("file_path", path)
        value.setdefault("score", _deterministic_context_score(relationship_type))
        value["_source"] = "deterministic"
        value["_match_type"] = relationship_type
        if group_key:
            value["definition_name"] = group_key
        flattened.append(value)

    for relationship_type, group in (
        ("changed_file", context.get("changed_files", {})),
        ("definition", context.get("related_definitions", {})),
        ("class_context", context.get("class_context", {})),
        ("namespace_context", context.get("namespace_context", {})),
    ):
        if not isinstance(group, Mapping):
            continue
        for group_key, chunks in group.items():
            for chunk in chunks or []:
                add(chunk, relationship_type, str(group_key))
    for chunk in context.get("chunks", []) or []:
        relationship_type = (
            str(chunk.get("_match_type") or "deterministic")
            if isinstance(chunk, Mapping)
            else "deterministic"
        )
        add(chunk, relationship_type)
    return flattened


def _deterministic_context_score(relationship_type: str) -> float:
    if relationship_type in {"definition", "transitive_parent"}:
        return 0.95
    if relationship_type in {"changed_file", "class_context"}:
        return 0.92
    if relationship_type == "namespace_context":
        return 0.86
    return 0.84


def _snapshot_identity(snapshot: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(snapshot),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _metadata(chunk: Mapping[str, Any]) -> Dict[str, Any]:
    value = chunk.get("metadata")
    merged = dict(value) if isinstance(value, Mapping) else {}
    for key in (
        "path",
        "file_path",
        "branch",
        "snapshot_sha",
        "content_digest",
        "context_snapshot_id",
        "execution_id",
        "parser_version",
        "chunker_version",
        "embedding_version",
    ):
        if key not in merged and chunk.get(key) is not None:
            merged[key] = chunk.get(key)
    return merged


def _chunk_text(chunk: Mapping[str, Any]) -> str:
    value = chunk.get("text") or chunk.get("content") or ""
    return value if isinstance(value, str) else str(value)


def _chunk_path(chunk: Mapping[str, Any], metadata: Mapping[str, Any]) -> str:
    return str(
        metadata.get("path")
        or metadata.get("file_path")
        or chunk.get("path")
        or chunk.get("file_path")
        or ""
    ).lstrip("/")


def _line_number(metadata: Mapping[str, Any], *keys: str) -> Optional[int]:
    for key in keys:
        value = metadata.get(key)
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


def _score(chunk: Mapping[str, Any]) -> float:
    value = chunk.get("score", chunk.get("relevance_score", 0.0))
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _retrieval_method(chunk: Mapping[str, Any]) -> str:
    source = str(chunk.get("_source") or chunk.get("source") or "semantic")
    if source == "pr_indexed":
        return "pr_overlay"
    if source == "duplication":
        return "duplication"
    if source == "deterministic":
        return "deterministic"
    return "semantic"


def _relationship_type(chunk: Mapping[str, Any]) -> str:
    value = str(chunk.get("_match_type") or "").strip()
    if value:
        return value[:64]
    if _retrieval_method(chunk) == "duplication":
        return "possible_duplicate"
    if _retrieval_method(chunk) == "pr_overlay":
        return "changed_file"
    return "semantic_similarity"


def _direction(relationship_type: str) -> str:
    if relationship_type in _STRUCTURAL_TYPES:
        return "outbound_dependency"
    if relationship_type in _DIRECT_TYPES:
        return "local"
    if relationship_type == "namespace_context":
        return "peer"
    return "similarity"


def _selection_reason(
    chunk: Mapping[str, Any],
    relationship_type: str,
    method: str,
) -> str:
    reasons = {
        "definition": "Defines an identifier referenced by the changed code.",
        "transitive_parent": "Defines a parent in the changed code's inheritance chain.",
        "changed_file": "Provides exact post-change source for a reviewed file.",
        "class_context": "Shares the enclosing type with a changed symbol.",
        "namespace_context": "Shares the package or namespace with changed code.",
        "possible_duplicate": "May implement behavior similar to the changed code.",
        "semantic_similarity": "Matched diff-derived code through semantic retrieval.",
    }
    reason = reasons.get(
        relationship_type,
        f"Selected by {method} retrieval with relation {relationship_type}.",
    )
    query = chunk.get("_query")
    if method == "duplication" and isinstance(query, str) and query.strip():
        reason += f" Query evidence: {query.strip()[:400]}"
    return reason


def _exact_rejection_reason(
    *,
    chunk: Mapping[str, Any],
    metadata: Mapping[str, Any],
    text: str,
    snapshot: Mapping[str, Any],
    snapshot_id: str,
    execution_id: Optional[str],
    source_branch: Optional[str],
    base_branch: Optional[str],
) -> Optional[str]:
    revision = metadata.get("snapshot_sha")
    branch = metadata.get("branch") or chunk.get("branch")
    method = _retrieval_method(chunk)

    is_pr_overlay = method == "pr_overlay" or metadata.get("pr") is True
    if is_pr_overlay:
        if revision != snapshot["head_sha"]:
            return "pr_overlay_revision_mismatch"
        if not execution_id or metadata.get("execution_id") != execution_id:
            return "pr_overlay_execution_mismatch"
        if source_branch and branch != source_branch:
            return "pr_overlay_branch_mismatch"
    elif branch == source_branch and source_branch:
        if revision != snapshot["head_sha"]:
            return "source_revision_mismatch"
    elif branch == base_branch and base_branch:
        if revision != snapshot["base_sha"]:
            return "base_revision_mismatch"
    else:
        return "unknown_branch_coordinate"

    observed_snapshot_id = metadata.get("context_snapshot_id")
    if observed_snapshot_id is not None and observed_snapshot_id != snapshot_id:
        return "snapshot_receipt_mismatch"

    for key in ("parser_version", "chunker_version", "embedding_version"):
        if metadata.get(key) != snapshot[key]:
            return f"{key}_mismatch"

    content_digest = metadata.get("content_digest")
    if not isinstance(content_digest, str) or _SHA_256.fullmatch(content_digest) is None:
        return "content_digest_missing"
    if sha256(text.encode("utf-8")).hexdigest() != content_digest:
        return "content_digest_mismatch"
    return None


def _item(
    chunk: Dict[str, Any],
    *,
    metadata: Mapping[str, Any],
    text: str,
    exact: bool,
) -> RelatedContextItemV1:
    path = _chunk_path(chunk, metadata)
    relationship_type = _relationship_type(chunk)
    method = _retrieval_method(chunk)
    revision = metadata.get("snapshot_sha") if exact else None
    content_digest = metadata.get("content_digest") if exact else None
    start_line = _line_number(metadata, "start_line", "line_start", "startLine")
    end_line = _line_number(metadata, "end_line", "line_end", "endLine")
    if start_line and end_line and end_line < start_line:
        end_line = start_line
    symbol = (
        metadata.get("full_path")
        or metadata.get("primary_name")
        or chunk.get("definition_name")
    )
    identity = "|".join(
        str(value or "")
        for value in (
            path,
            revision,
            content_digest,
            start_line,
            end_line,
            relationship_type,
            method,
        )
    )
    exact_source = method == "pr_overlay" or relationship_type == "changed_file"
    structural = relationship_type in _STRUCTURAL_TYPES or method == "deterministic"
    return RelatedContextItemV1(
        item_id=sha256(identity.encode("utf-8")).hexdigest(),
        path=path,
        revision=revision,
        content_digest=content_digest,
        start_line=start_line,
        end_line=end_line,
        symbol=str(symbol)[:500] if symbol else None,
        relationship_type=relationship_type,
        direction=_direction(relationship_type),
        retrieval_method=method,
        score=_score(chunk),
        evidence_strength=(
            "exact_source"
            if exact_source
            else "structural_lead"
            if structural
            else "semantic_lead"
        ),
        selection_reason=_selection_reason(chunk, relationship_type, method),
        snapshot_verified=exact,
        content=text,
    )


def _select_with_tier_budget(
    candidates: Sequence[Tuple[RelatedContextItemV1, Dict[str, Any]]],
) -> Tuple[List[Tuple[RelatedContextItemV1, Dict[str, Any]]], int]:
    tiers: List[List[Tuple[RelatedContextItemV1, Dict[str, Any]]]] = [[], [], []]
    for candidate in candidates:
        item = candidate[0]
        if item.relationship_type in _STRUCTURAL_TYPES:
            tiers[0].append(candidate)
        elif (
            item.relationship_type in _DIRECT_TYPES
            or item.retrieval_method == "pr_overlay"
            or item.score >= 0.88
        ):
            tiers[1].append(candidate)
        else:
            tiers[2].append(candidate)

    selected: List[Tuple[RelatedContextItemV1, Dict[str, Any]]] = []
    carry = 0
    for tier, base_limit in zip(tiers, _SELECTION_LIMITS):
        limit = base_limit + carry
        selected.extend(tier[:limit])
        carry = max(0, limit - len(tier))
    return selected, max(0, len(candidates) - len(selected))


def build_related_context_pack(
    *,
    chunks: Iterable[Dict[str, Any]],
    anchor_paths: Sequence[str],
    snapshot: Optional[Mapping[str, Any]] = None,
    execution_id: Optional[str] = None,
    source_branch: Optional[str] = None,
    base_branch: Optional[str] = None,
    anchor_digests: Optional[Mapping[str, str]] = None,
    base_index_available: bool = True,
    additional_gaps: Sequence[ContextGapV1] = (),
) -> RelatedContextBuildResult:
    """Validate, deduplicate, budget, and explain retrieved context."""

    exact = snapshot is not None
    snapshot_value = dict(snapshot) if snapshot is not None else None
    snapshot_id = _snapshot_identity(snapshot_value) if snapshot_value else None
    anchors = [
        ContextAnchorV1(
            path=path,
            revision=snapshot_value["head_sha"] if snapshot_value else None,
            content_digest=(anchor_digests or {}).get(path),
        )
        for path in dict.fromkeys(path.lstrip("/") for path in anchor_paths if path)
    ]

    rejected = Counter()
    candidates: List[Tuple[RelatedContextItemV1, Dict[str, Any]]] = []
    seen = set()
    for raw_chunk in chunks:
        if not isinstance(raw_chunk, dict):
            rejected["malformed_chunk"] += 1
            continue
        metadata = _metadata(raw_chunk)
        text = _chunk_text(raw_chunk)
        path = _chunk_path(raw_chunk, metadata)
        if not path or not text.strip():
            rejected["missing_path_or_content"] += 1
            continue
        if exact:
            is_pr_overlay = (
                _retrieval_method(raw_chunk) == "pr_overlay"
                or metadata.get("pr") is True
            )
            if (
                not base_index_available
                and not is_pr_overlay
                and base_branch
                and metadata.get("branch") == base_branch
            ):
                rejected["base_index_not_selected"] += 1
                continue
            reason = _exact_rejection_reason(
                chunk=raw_chunk,
                metadata=metadata,
                text=text,
                snapshot=snapshot_value,
                snapshot_id=snapshot_id,
                execution_id=execution_id,
                source_branch=source_branch,
                base_branch=base_branch,
            )
            if reason:
                rejected[reason] += 1
                continue
        dedup_key = (path, metadata.get("content_digest") or sha256(text.encode()).hexdigest())
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        candidates.append(
            (
                _item(raw_chunk, metadata=metadata, text=text, exact=exact),
                raw_chunk,
            )
        )

    selected, truncated_count = _select_with_tier_budget(candidates)
    selected_items = [item for item, _ in selected]
    accepted_chunks = [chunk for _, chunk in selected]
    gaps: List[ContextGapV1] = list(additional_gaps)
    affected_paths = [anchor.path for anchor in anchors]
    if exact and not base_index_available:
        gaps.append(ContextGapV1(
            code="exact_base_index_unavailable",
            detail=(
                "No exact base-revision repository index was available. Context may "
                "contain the PR overlay but cannot establish unchanged dependency coverage."
            ),
            affected_paths=affected_paths,
        ))
    if rejected:
        detail = ", ".join(f"{reason}={count}" for reason, count in sorted(rejected.items()))
        gaps.append(ContextGapV1(
            code="context_receipt_rejected",
            detail=f"Rejected retrieved chunks that could not prove exact provenance: {detail}.",
            affected_paths=affected_paths,
        ))
    if truncated_count:
        gaps.append(ContextGapV1(
            code="context_budget_truncated",
            detail=f"Omitted {truncated_count} lower-priority context chunks after tier budgeting.",
            affected_paths=affected_paths,
        ))
    if not any(item.evidence_strength == "structural_lead" for item in selected_items):
        gaps.append(ContextGapV1(
            code="structural_context_missing",
            detail="No verified definition, inheritance, or deterministic relationship evidence was retrieved.",
            affected_paths=affected_paths,
        ))
    if not selected_items:
        gaps.append(ContextGapV1(
            code="related_context_empty",
            detail="No related-code chunk passed provenance and relevance assembly.",
            affected_paths=affected_paths,
        ))

    receipt = None
    if snapshot_value:
        receipt = ContextSnapshotReceiptV1(
            snapshot_id=snapshot_id,
            base_sha=snapshot_value["base_sha"],
            head_sha=snapshot_value["head_sha"],
            merge_base_sha=snapshot_value["merge_base_sha"],
            parser_version=snapshot_value["parser_version"],
            chunker_version=snapshot_value["chunker_version"],
            embedding_version=snapshot_value["embedding_version"],
        )
    pack = RelatedContextPackV1(
        mode="exact" if exact else "legacy",
        execution_id=execution_id,
        receipt=receipt,
        anchors=anchors,
        items=selected_items,
        gaps=gaps,
        rejected_chunk_count=sum(rejected.values()),
        truncated_chunk_count=truncated_count,
    )
    return RelatedContextBuildResult(pack=pack, accepted_chunks=accepted_chunks)
