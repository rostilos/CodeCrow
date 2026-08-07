"""Deterministic identity for an exact pull-request overlay generation."""

from __future__ import annotations

import hashlib
from typing import Iterable, Sequence


ZERO_FINGERPRINT = "sha256:" + "0" * 64


def _write_field(digest, name: str, value: str) -> None:
    name_bytes = name.encode("utf-8")
    value_bytes = value.encode("utf-8")
    digest.update(len(name_bytes).to_bytes(4, "big"))
    digest.update(name_bytes)
    digest.update(len(value_bytes).to_bytes(8, "big"))
    digest.update(value_bytes)


def _content_digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _content_state(file_info: object) -> str:
    state = getattr(file_info, "content_state", "complete")
    return state if state in {"complete", "partial_diff"} else "complete"


def pr_overlay_generation_fingerprint(
    *,
    workspace: str,
    project: str,
    pr_number: int,
    branch: str,
    base_branch: str,
    source_revision: str,
    base_revision: str,
    files: Iterable[object],
    requested_plugin_ids: Sequence[str],
    repository_plugin_ids: Sequence[str],
    request_plugin_fingerprint: str,
    target_plugin_fingerprint: str,
    capability_fingerprint: str,
    descriptor_fingerprint: str,
    implementation_fingerprint: str,
    index_representation_fingerprint: str,
    pr_overlay_representation_fingerprint: str,
    snapshots: Iterable[object],
    base_generation_manifest_sha256: str = "",
) -> str:
    """Hash every input that can change persisted semantic or plugin context."""
    digest = hashlib.sha256()
    _write_field(digest, "domain", "codecrow-pr-overlay")
    for name, value in (
        ("workspace", workspace),
        ("project", project),
        ("pr_number", str(pr_number)),
        ("branch", branch),
        ("base_branch", base_branch),
        ("source_revision", source_revision),
        ("base_revision", base_revision),
        (
            "base_generation_manifest_sha256",
            base_generation_manifest_sha256,
        ),
        ("request_plugin_fingerprint", request_plugin_fingerprint),
        ("target_plugin_fingerprint", target_plugin_fingerprint),
        ("capability_fingerprint", capability_fingerprint),
        ("descriptor_fingerprint", descriptor_fingerprint),
        ("implementation_fingerprint", implementation_fingerprint),
        ("index_representation_fingerprint", index_representation_fingerprint),
        (
            "pr_overlay_representation_fingerprint",
            pr_overlay_representation_fingerprint,
        ),
    ):
        _write_field(digest, name, value or "")

    for plugin_id in sorted(requested_plugin_ids):
        _write_field(digest, "requested_plugin", plugin_id)
    for plugin_id in sorted(repository_plugin_ids):
        _write_field(digest, "repository_plugin", plugin_id)

    normalized_files = sorted(
        (
            str(getattr(file_info, "path")),
            str(getattr(file_info, "change_type")),
            _content_state(file_info),
            _content_digest(str(getattr(file_info, "content"))),
        )
        for file_info in files
    )
    for path, change_type, content_state, content_digest in normalized_files:
        _write_field(digest, "file_path", path)
        _write_field(digest, "file_change_type", change_type)
        _write_field(digest, "file_content_state", content_state)
        _write_field(digest, "file_content_sha256", content_digest)

    normalized_snapshots = sorted(
        (
            str(getattr(snapshot, "plugin_id")),
            str(getattr(snapshot, "kind")),
            _content_digest(str(getattr(snapshot, "content"))),
        )
        for snapshot in snapshots
    )
    for plugin_id, kind, content_digest in normalized_snapshots:
        _write_field(digest, "snapshot_plugin", plugin_id)
        _write_field(digest, "snapshot_kind", kind)
        _write_field(digest, "snapshot_content_sha256", content_digest)

    return "sha256:" + digest.hexdigest()


def is_complete_reusable_generation(
    points: Sequence[object],
    expected_fingerprint: str,
) -> bool:
    """Accept only a non-empty generation whose every point has one identity."""
    if not points or not expected_fingerprint:
        return False
    return all(
        (getattr(point, "payload", None) or {}).get(
            "pr_generation_fingerprint"
        )
        == expected_fingerprint
        for point in points
    )
