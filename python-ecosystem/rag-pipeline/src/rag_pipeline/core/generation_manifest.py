"""Content-addressed membership manifests for repository index generations."""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from llama_index.core.schema import TextNode
from qdrant_client.models import FieldCondition, Filter, MatchValue, PointStruct


GENERATION_MANIFEST_PAYLOAD_KEY = "repository_generation_manifest"
GENERATION_MEMBER_DIGEST_PAYLOAD_KEY = "generation_member_sha256"
GENERATION_SCHEMA = "codecrow.repository-index-generation"
INDEX_SELECTION_POLICY_SCHEMA = "codecrow.repository-index-selection"
GENERATION_MANIFEST_PATH = (
    "__analysis_state__/repository-generation-manifest/000000.state"
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class GenerationManifestError(RuntimeError):
    """A repository generation is incomplete or its seal is inconsistent."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _canonical_vector(value: Any) -> Any:
    """Canonicalize cosine vectors as Qdrant stores them.

    Qdrant normalizes cosine vectors and persists float32 values. Rounding the
    normalized representation avoids treating harmless transport precision as
    a generation-integrity failure while still binding every vector value.
    """
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_vector(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        values = list(value)
        if values and all(isinstance(item, (int, float)) for item in values):
            norm = math.sqrt(sum(float(item) ** 2 for item in values))
            if norm:
                return [round(float(item) / norm, 7) for item in values]
            return [0.0 for _ in values]
        return [_canonical_vector(item) for item in values]
    return value


def canonical_index_selection_policy(
    include_patterns: Sequence[str] | None,
    exclude_patterns: Sequence[str] | None,
) -> dict[str, Any]:
    """Return the order-independent effective repository selection policy."""

    for label, patterns in (
        ("include", include_patterns),
        ("exclude", exclude_patterns),
    ):
        if patterns is not None and (
            isinstance(patterns, (str, bytes))
            or not all(isinstance(pattern, str) for pattern in patterns)
        ):
            raise GenerationManifestError(
                f"repository index {label} patterns are invalid"
            )
    return {
        "schema": INDEX_SELECTION_POLICY_SCHEMA,
        "includePatterns": sorted(set(include_patterns or ())),
        "excludePatterns": sorted(set(exclude_patterns or ())),
    }


def compute_index_selection_policy_sha256(
    include_patterns: Sequence[str] | None,
    exclude_patterns: Sequence[str] | None,
) -> str:
    """Digest the canonical effective repository selection policy."""

    policy = canonical_index_selection_policy(
        include_patterns,
        exclude_patterns,
    )
    return hashlib.sha256(_canonical_json(policy).encode("utf-8")).hexdigest()


def compute_generation_member_digest(
    point_id: object,
    payload: Mapping[str, Any],
    vector: object,
) -> str:
    """Bind one persisted point's deterministic identity, payload and vector.

    ``indexed_at`` is operational metadata, not representation content. It is
    excluded so rebuilding identical repository content can produce the same
    generation identity. The digest field itself is also excluded to avoid a
    recursive projection.
    """
    content_payload = {
        key: value
        for key, value in payload.items()
        if key not in {
            GENERATION_MEMBER_DIGEST_PAYLOAD_KEY,
            "indexed_at",
        }
    }
    encoded = _canonical_json({
        "id": str(point_id),
        "payload": content_payload,
        "vector": _canonical_vector(vector),
    }).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def compute_generation_members_digest(
    members: Iterable[tuple[object, str]],
) -> str:
    """Return the order-independent aggregate identity for generation members."""
    normalized = []
    seen_ids = set()
    for point_id, member_digest in members:
        normalized_id = str(point_id)
        if normalized_id in seen_ids:
            raise GenerationManifestError(
                "repository generation contains duplicate point identities"
            )
        if not isinstance(member_digest, str) or not _SHA256_RE.fullmatch(
            member_digest
        ):
            raise GenerationManifestError(
                "repository generation member is missing a valid content digest"
            )
        seen_ids.add(normalized_id)
        normalized.append((normalized_id, member_digest))

    hasher = hashlib.sha256()
    for point_id, member_digest in sorted(normalized):
        encoded_id = point_id.encode("utf-8")
        hasher.update(len(encoded_id).to_bytes(8, "big"))
        hasher.update(encoded_id)
        hasher.update(bytes.fromhex(member_digest))
    return hasher.hexdigest()


def verified_generation_member(point) -> tuple[object, str]:
    """Recompute and verify one persisted generation member's full content."""
    payload = point.payload or {}
    stored_digest = payload.get(GENERATION_MEMBER_DIGEST_PAYLOAD_KEY)
    if not is_sha256_hex(stored_digest):
        raise GenerationManifestError(
            "repository generation member is missing a valid content digest"
        )
    computed_digest = compute_generation_member_digest(
        point.id,
        payload,
        point.vector,
    )
    if computed_digest != stored_digest:
        raise GenerationManifestError(
            "repository generation member content digest does not match its "
            "persisted payload and vector"
        )
    return point.id, computed_digest


def _generation_filter(branch: str, commit: str) -> Filter:
    return Filter(
        must=[
            FieldCondition(key="branch", match=MatchValue(value=branch)),
            FieldCondition(key="commit", match=MatchValue(value=commit)),
        ],
        must_not=[
            FieldCondition(key="pr", match=MatchValue(value=True)),
        ],
    )


def collect_generation_members(
    client,
    collection_name: str,
    branch: str,
    commit: str,
) -> list[tuple[object, str]]:
    """Read all unsealed members of one exact pending generation."""
    members = []
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection_name,
            scroll_filter=_generation_filter(branch, commit),
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        for point in points:
            payload = point.payload or {}
            if payload.get(GENERATION_MANIFEST_PAYLOAD_KEY) is True:
                raise GenerationManifestError(
                    "pending repository generation already contains a manifest"
                )
            members.append(verified_generation_member(point))
        if offset is None:
            break
    return members


def seal_generation_members(
    client,
    collection_name: str,
    branch: str,
    commit: str,
) -> int:
    """Persist member digests against Qdrant's actual stored vectors.

    Qdrant may normalize cosine vectors and reduce their precision.  A digest
    made from the embedding request is therefore not necessarily a digest of
    the representation which will later be verified.  Seal after every member
    is stored, then collect/verify the same representation before publishing a
    generation manifest.
    """
    offset = None
    sealed = 0
    while True:
        points, offset = client.scroll(
            collection_name=collection_name,
            scroll_filter=_generation_filter(branch, commit),
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        replacements = []
        for point in points:
            payload = dict(point.payload or {})
            if payload.get(GENERATION_MANIFEST_PAYLOAD_KEY) is True:
                raise GenerationManifestError(
                    "pending repository generation already contains a manifest"
                )
            payload[GENERATION_MEMBER_DIGEST_PAYLOAD_KEY] = (
                compute_generation_member_digest(point.id, payload, point.vector)
            )
            replacements.append(PointStruct(
                id=point.id,
                vector=point.vector,
                payload=payload,
            ))
        if replacements:
            client.upsert(
                collection_name=collection_name,
                points=replacements,
                wait=True,
            )
            sealed += len(replacements)
        if offset is None:
            break
    return sealed


def generation_manifest_content(
    *,
    workspace: str,
    project: str,
    branch: str,
    commit: str,
    member_count: int,
    members_sha256: str,
    source_tree_sha256: str,
    index_include_patterns: Sequence[str],
    index_exclude_patterns: Sequence[str],
    index_selection_policy_sha256: str,
) -> str:
    """Serialize the immutable generation seal content."""
    selection_policy = canonical_index_selection_policy(
        index_include_patterns,
        index_exclude_patterns,
    )
    return _canonical_json({
        "branch": branch,
        "commit": commit,
        "indexSelectionPolicy": selection_policy,
        "indexSelectionPolicySha256": index_selection_policy_sha256,
        "memberCount": member_count,
        "membersSha256": members_sha256,
        "project": project,
        "schema": GENERATION_SCHEMA,
        "sourceTreeSha256": source_tree_sha256,
        "workspace": workspace,
    })


def build_generation_manifest_node(
    *,
    workspace: str,
    project: str,
    branch: str,
    commit: str,
    member_count: int,
    members_sha256: str,
    source_tree_sha256: str,
    index_include_patterns: Sequence[str],
    index_exclude_patterns: Sequence[str],
    identity_metadata: Mapping[str, Any],
) -> TextNode:
    """Build the single zero-vector state node that seals a generation."""
    if member_count < 1:
        raise GenerationManifestError(
            "repository generation cannot be sealed without members"
        )
    if not _SHA256_RE.fullmatch(members_sha256):
        raise GenerationManifestError(
            "repository generation aggregate digest is invalid"
        )
    if not _SHA256_RE.fullmatch(source_tree_sha256):
        raise GenerationManifestError(
            "repository generation source-tree digest is invalid"
        )
    selection_policy = canonical_index_selection_policy(
        index_include_patterns,
        index_exclude_patterns,
    )
    selection_policy_sha256 = compute_index_selection_policy_sha256(
        selection_policy["includePatterns"],
        selection_policy["excludePatterns"],
    )
    content = generation_manifest_content(
        workspace=workspace,
        project=project,
        branch=branch,
        commit=commit,
        member_count=member_count,
        members_sha256=members_sha256,
        source_tree_sha256=source_tree_sha256,
        index_include_patterns=selection_policy["includePatterns"],
        index_exclude_patterns=selection_policy["excludePatterns"],
        index_selection_policy_sha256=selection_policy_sha256,
    )
    content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return TextNode(
        text=content,
        metadata={
            "workspace": workspace,
            "project": project,
            "branch": branch,
            "commit": commit,
            "path": GENERATION_MANIFEST_PATH,
            "language": "repository-state",
            "filetype": "state",
            GENERATION_MANIFEST_PAYLOAD_KEY: True,
            "generation_schema": GENERATION_SCHEMA,
            "generation_member_count": member_count,
            "generation_members_sha256": members_sha256,
            "generation_manifest_sha256": content_sha256,
            "source_tree_sha256": source_tree_sha256,
            "index_include_patterns": selection_policy["includePatterns"],
            "index_exclude_patterns": selection_policy["excludePatterns"],
            "index_selection_policy_sha256": selection_policy_sha256,
            **dict(identity_metadata),
        },
    )


def is_sha256_hex(value: object) -> bool:
    """Return whether ``value`` is one canonical lower-case SHA-256 digest."""
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def generation_manifest_point_id(
    workspace: str,
    project: str,
    branch: str,
) -> str:
    """Return the deterministic storage ID of a repository generation seal."""
    key = f"{workspace}:{project}:{branch}:{GENERATION_MANIFEST_PATH}:0"
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, key))
