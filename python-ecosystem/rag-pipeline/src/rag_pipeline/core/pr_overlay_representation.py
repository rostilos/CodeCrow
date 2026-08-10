"""Content-derived identity for neutral pull-request overlay behavior."""

from __future__ import annotations

import hashlib
import json
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Mapping

from .index_representation import index_representation_fingerprint


PR_OVERLAY_REPRESENTATION_PAYLOAD_KEY = (
    "pr_overlay_representation_fingerprint"
)

_PR_OVERLAY_SOURCE_PATHS = (
    "api/models.py",
    "api/routers/pr.py",
    "api/routers/query.py",
    "core/index_manager/manager.py",
    "core/pr_overlay_identity.py",
    "core/pr_overlay_manifest.py",
    "core/pr_overlay_representation.py",
    "core/revision_binding.py",
    "core/revision_preflight.py",
    "core/review_grouping.py",
    "services/base.py",
    "services/deterministic_context.py",
    "services/pr_context.py",
    "services/query_service.py",
    "services/semantic_search.py",
)

_PR_OVERLAY_DEPENDENCIES = (
    "fastapi",
    "pydantic",
)


def _installed_dependency_versions() -> dict[str, str]:
    versions = {}
    for distribution in _PR_OVERLAY_DEPENDENCIES:
        try:
            versions[distribution] = importlib_metadata.version(distribution)
        except importlib_metadata.PackageNotFoundError:
            versions[distribution] = "absent"
    return versions


def compute_pr_overlay_representation_fingerprint(
    package_root: str | Path,
    *,
    branch_representation_fingerprint: str,
    dependency_versions: Mapping[str, str],
) -> str:
    """Hash PR-only behavior without widening branch reindex scope."""
    root = Path(package_root).resolve(strict=True)
    projection = {
        "branch_representation_fingerprint": branch_representation_fingerprint,
        "dependencies": {
            name: str(dependency_versions.get(name, "absent"))
            for name in _PR_OVERLAY_DEPENDENCIES
        },
        "sources": [
            {
                "path": relative_path,
                "sha256": hashlib.sha256(
                    (root / relative_path).read_bytes()
                ).hexdigest(),
            }
            for relative_path in _PR_OVERLAY_SOURCE_PATHS
        ],
    }
    encoded = json.dumps(
        projection,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def pr_overlay_representation_fingerprint(config=None) -> str:
    """Return the PR-overlay build identity composed with branch identity."""
    package_root = Path(__file__).resolve().parents[1]
    return compute_pr_overlay_representation_fingerprint(
        package_root,
        branch_representation_fingerprint=(
            index_representation_fingerprint(config)
        ),
        dependency_versions=_installed_dependency_versions(),
    )
