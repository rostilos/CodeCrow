"""Content-addressed identity helpers for structural generations."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


GENERATION_MANIFEST_PAYLOAD_KEY = "repository_generation_manifest"
GENERATION_MEMBER_DIGEST_PAYLOAD_KEY = "generation_member_sha256"
GENERATION_SCHEMA = "codecrow.repository-index-generation"
INDEX_SELECTION_POLICY_SCHEMA = "codecrow.repository-index-selection"
GENERATION_MANIFEST_PATH = (
    "__analysis_state__/repository-generation-manifest/000000.state"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class GenerationManifestError(RuntimeError):
    """A repository generation seal is inconsistent."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_index_selection_policy(
    include_patterns: Sequence[str] | None,
    exclude_patterns: Sequence[str] | None,
) -> dict[str, Any]:
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
    return hashlib.sha256(_canonical_json(
        canonical_index_selection_policy(include_patterns, exclude_patterns)
    ).encode("utf-8")).hexdigest()


def compute_generation_member_digest(
    point_id: object,
    payload: Mapping[str, Any],
) -> str:
    content_payload = {
        key: value
        for key, value in payload.items()
        if key not in {GENERATION_MEMBER_DIGEST_PAYLOAD_KEY, "indexed_at"}
    }
    return hashlib.sha256(_canonical_json({
        "id": str(point_id),
        "payload": content_payload,
    }).encode("utf-8")).hexdigest()


def compute_generation_members_digest(
    members: Iterable[tuple[object, str]],
) -> str:
    normalized: list[tuple[str, str]] = []
    seen: set[str] = set()
    for point_id, digest in members:
        key = str(point_id)
        if key in seen or not is_sha256_hex(digest):
            raise GenerationManifestError("invalid repository generation member")
        seen.add(key)
        normalized.append((key, digest))
    hasher = hashlib.sha256()
    for point_id, digest in sorted(normalized):
        encoded = point_id.encode("utf-8")
        hasher.update(len(encoded).to_bytes(8, "big"))
        hasher.update(encoded)
        hasher.update(bytes.fromhex(digest))
    return hasher.hexdigest()


def is_sha256_hex(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None
