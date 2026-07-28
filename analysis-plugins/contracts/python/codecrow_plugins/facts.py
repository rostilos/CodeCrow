from __future__ import annotations

from pathlib import Path
from pathlib import PurePosixPath
from typing import Iterable

from .api import RepositoryFacts, normalize_path
from .registry import PluginRegistry


def _declared_markers(registry: PluginRegistry):
    exact = tuple(sorted({
        marker.path
        for descriptor in registry.descriptors
        for marker in (
            *descriptor.detection.content_markers,
            *(
                marker
                for alternative in descriptor.detection.alternatives
                for marker in alternative.content_markers
            ),
        )
    }))
    patterns = tuple(
        marker
        for descriptor in registry.descriptors
        for alternative in descriptor.detection.alternatives
        for marker in alternative.content_pattern_markers
    )
    return exact, patterns


def build_repository_facts(
    repository_root: str | Path,
    revision: str,
    paths: Iterable[str | Path],
    registry: PluginRegistry,
    *,
    max_marker_files: int = 16,
    max_marker_bytes: int = 262_144,
) -> RepositoryFacts:
    """Read only statically declared markers from an already pinned checkout."""
    root = Path(repository_root).resolve(strict=True)
    normalized_paths = tuple(sorted({normalize_path(Path(path).as_posix()) for path in paths}))
    available = set(normalized_paths)
    declared_markers, declared_pattern_markers = _declared_markers(registry)
    if len(declared_markers) > max_marker_files:
        raise ValueError("declared plugin marker files exceed the host budget")

    marker_contents: dict[str, str] = {}
    consumed_bytes = 0
    matched_pattern_markers = set()
    pattern_candidates = tuple(
        path for path in normalized_paths
        if any(PurePosixPath(path).match(marker.path_pattern) for marker in declared_pattern_markers)
    )
    for marker_path in (*declared_markers, *pattern_candidates):
        if marker_path not in available:
            continue
        applicable_pattern_markers = {
            marker for marker in declared_pattern_markers
            if PurePosixPath(marker_path).match(marker.path_pattern)
        }
        if marker_path not in declared_markers and applicable_pattern_markers.issubset(matched_pattern_markers):
            continue
        full_path = (root / marker_path).resolve(strict=True)
        if root not in full_path.parents:
            raise ValueError("plugin marker escaped the repository root")
        size = full_path.stat().st_size
        content = full_path.read_text(encoding="utf-8")
        matching_pattern_markers = {
            marker for marker in applicable_pattern_markers
            if marker not in matched_pattern_markers
            and marker.contains in content
        }
        if marker_path not in declared_markers and not matching_pattern_markers:
            continue
        if marker_path not in marker_contents and len(marker_contents) >= max_marker_files:
            raise ValueError("declared plugin marker files exceed the host budget")
        if consumed_bytes + size > max_marker_bytes:
            raise ValueError("plugin marker contents exceed the host byte budget")
        consumed_bytes += len(content.encode("utf-8"))
        marker_contents[marker_path] = content
        matched_pattern_markers.update(matching_pattern_markers)

    return RepositoryFacts(
        revision=revision,
        paths=normalized_paths,
        marker_contents=marker_contents,
    )


def overlay_repository_facts(
    baseline: RepositoryFacts,
    repository_root: str | Path | None,
    revision: str,
    updated_paths: Iterable[str | Path],
    deleted_paths: Iterable[str | Path],
    registry: PluginRegistry,
    *,
    max_marker_files: int = 16,
    max_marker_bytes: int = 262_144,
) -> RepositoryFacts:
    """Apply one exact commit change set to persisted neutral detection facts.

    Only statically declared marker files are read from the pinned changed-file
    checkout. Path-only detection is recomputed from the complete persisted
    inventory, so language/framework activation changes cannot be silently
    missed by an incremental branch update.
    """
    updated = tuple(sorted({
        normalize_path(Path(path).as_posix()) for path in updated_paths
    }))
    deleted = tuple(sorted({
        normalize_path(Path(path).as_posix()) for path in deleted_paths
    }))
    overlap = sorted(set(updated).intersection(deleted))
    if overlap:
        raise ValueError(
            "repository fact changes cannot update and delete the same path: "
            + ", ".join(overlap[:10])
        )
    if updated and repository_root is None:
        raise ValueError("updated repository facts require a repository root")
    root = (
        Path(repository_root).resolve(strict=True)
        if repository_root is not None
        else None
    )

    paths = (set(baseline.paths) - set(deleted)) | set(updated)
    marker_contents = {
        path: content
        for path, content in baseline.marker_contents.items()
        if path in paths
    }
    declared_markers, declared_pattern_markers = _declared_markers(registry)
    exact_markers = set(declared_markers)

    for marker_path in updated:
        applicable_patterns = tuple(
            marker
            for marker in declared_pattern_markers
            if PurePosixPath(marker_path).match(marker.path_pattern)
        )
        if marker_path not in exact_markers and not applicable_patterns:
            continue
        full_path = (root / marker_path).resolve(strict=True)
        if root not in full_path.parents:
            raise ValueError("plugin marker escaped the repository root")
        content = full_path.read_text(encoding="utf-8")
        if (
            marker_path in exact_markers
            or any(marker.contains in content for marker in applicable_patterns)
        ):
            marker_contents[marker_path] = content
        else:
            marker_contents.pop(marker_path, None)

    if len(marker_contents) > max_marker_files:
        raise ValueError("declared plugin marker files exceed the host budget")
    consumed_bytes = sum(
        len(content.encode("utf-8"))
        for content in marker_contents.values()
    )
    if consumed_bytes > max_marker_bytes:
        raise ValueError("plugin marker contents exceed the host byte budget")

    return RepositoryFacts(
        revision=revision,
        paths=tuple(sorted(paths)),
        marker_contents=marker_contents,
    )
