from __future__ import annotations

import logging
from pathlib import Path
from pathlib import PurePosixPath
from typing import Iterable

from .api import RepositoryFacts, normalize_path
from .registry import PluginRegistry

logger = logging.getLogger(__name__)


def _declared_markers(registry: PluginRegistry):
    exact = tuple(sorted({
        marker
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


def _under_source_root(path: str, source_root: str | None) -> bool:
    return (
        source_root is None
        or path == source_root
        or path.startswith(source_root + "/")
    )


def _matching_markers(path, content, exact_markers, pattern_markers):
    matching_exact = {
        marker
        for marker in exact_markers
        if (path == marker.path or path.endswith("/" + marker.path))
        and marker.contains in content
    }
    matching_patterns = {
        marker
        for marker in pattern_markers
        if PurePosixPath(path).match(marker.path_pattern)
        and marker.contains in content
    }
    return matching_exact, matching_patterns


def build_repository_facts(
    repository_root: str | Path,
    revision: str,
    paths: Iterable[str | Path],
    registry: PluginRegistry,
    *,
    max_marker_bytes: int = 262_144,
    project_type: str | None = None,
    source_root: str | None = None,
) -> RepositoryFacts:
    """Read only statically declared markers from an already pinned checkout."""
    root = Path(repository_root).resolve(strict=True)
    normalized_paths = tuple(sorted({normalize_path(Path(path).as_posix()) for path in paths}))
    available = set(normalized_paths)
    if project_type and project_type.strip().casefold() != "auto":
        return RepositoryFacts(
            revision=revision,
            paths=normalized_paths,
            marker_contents={},
            project_type=project_type,
            source_root=source_root,
        )

    declared_markers, declared_pattern_markers = _declared_markers(registry)
    declared_marker_paths = tuple(sorted({marker.path for marker in declared_markers}))

    marker_contents: dict[str, str] = {}
    consumed_bytes = 0
    matched_pattern_markers = set()
    pattern_candidates = tuple(
        path for path in normalized_paths
        if _under_source_root(path, source_root)
        and (
            any(PurePosixPath(path).match(marker.path_pattern) for marker in declared_pattern_markers)
            or any(
                path == marker_path or path.endswith("/" + marker_path)
                for marker_path in declared_marker_paths
            )
        )
    )
    skipped_for_bytes = 0
    for marker_path in tuple(dict.fromkeys((*declared_marker_paths, *pattern_candidates))):
        if (
            marker_path not in available
            or not _under_source_root(marker_path, source_root)
        ):
            continue
        applicable_exact_markers = {
            marker
            for marker in declared_markers
            if marker_path == marker.path or marker_path.endswith("/" + marker.path)
        }
        applicable_pattern_markers = {
            marker for marker in declared_pattern_markers
            if PurePosixPath(marker_path).match(marker.path_pattern)
        }
        if (
            not applicable_exact_markers
            and applicable_pattern_markers.issubset(matched_pattern_markers)
        ):
            continue
        full_path = (root / marker_path).resolve(strict=True)
        if root not in full_path.parents:
            raise ValueError("plugin marker escaped the repository root")
        size = full_path.stat().st_size
        if consumed_bytes + size > max_marker_bytes:
            skipped_for_bytes += 1
            continue
        content = full_path.read_text(encoding="utf-8")
        matching_exact_markers, matching_pattern_markers = _matching_markers(
            marker_path,
            content,
            applicable_exact_markers,
            applicable_pattern_markers - matched_pattern_markers,
        )
        if not matching_exact_markers and not matching_pattern_markers:
            continue
        consumed_bytes += len(content.encode("utf-8"))
        marker_contents[marker_path] = content
        matched_pattern_markers.update(matching_pattern_markers)

    if skipped_for_bytes:
        logger.warning(
            "Skipped %s plugin marker candidate(s) after reaching the %s-byte "
            "content budget; repository indexing will continue with reduced "
            "automatic plugin-detection evidence",
            skipped_for_bytes,
            max_marker_bytes,
        )

    return RepositoryFacts(
        revision=revision,
        paths=normalized_paths,
        marker_contents=marker_contents,
        project_type=project_type,
        source_root=source_root,
    )


def overlay_repository_facts(
    baseline: RepositoryFacts,
    repository_root: str | Path | None,
    revision: str,
    updated_paths: Iterable[str | Path],
    deleted_paths: Iterable[str | Path],
    registry: PluginRegistry,
    *,
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
        if baseline.project_type is None:
            raise ValueError("updated repository facts require a repository root")
    root = (
        Path(repository_root).resolve(strict=True)
        if repository_root is not None
        else None
    )

    paths = (set(baseline.paths) - set(deleted)) | set(updated)
    if baseline.project_type is not None:
        return RepositoryFacts(
            revision=revision,
            paths=tuple(sorted(paths)),
            marker_contents={},
            project_type=baseline.project_type,
            source_root=baseline.source_root,
        )

    declared_markers, declared_pattern_markers = _declared_markers(registry)
    marker_contents = {
        path: content
        for path, content in sorted(baseline.marker_contents.items())
        if path in paths
        and _under_source_root(path, baseline.source_root)
        and any(_matching_markers(
            path,
            content,
            declared_markers,
            declared_pattern_markers,
        ))
    }

    for marker_path in updated:
        if not _under_source_root(marker_path, baseline.source_root):
            marker_contents.pop(marker_path, None)
            continue
        applicable_exact_markers = tuple(
            marker
            for marker in declared_markers
            if marker_path == marker.path or marker_path.endswith("/" + marker.path)
        )
        applicable_patterns = tuple(
            marker
            for marker in declared_pattern_markers
            if PurePosixPath(marker_path).match(marker.path_pattern)
        )
        if not applicable_exact_markers and not applicable_patterns:
            continue
        full_path = (root / marker_path).resolve(strict=True)
        if root not in full_path.parents:
            raise ValueError("plugin marker escaped the repository root")
        content = full_path.read_text(encoding="utf-8")
        if any(_matching_markers(
            marker_path,
            content,
            applicable_exact_markers,
            applicable_patterns,
        )):
            marker_contents[marker_path] = content
        else:
            marker_contents.pop(marker_path, None)

    bounded_marker_contents: dict[str, str] = {}
    consumed_bytes = 0
    skipped_for_bytes = 0
    for path, content in sorted(marker_contents.items()):
        size = len(content.encode("utf-8"))
        if consumed_bytes + size > max_marker_bytes:
            skipped_for_bytes += 1
            continue
        bounded_marker_contents[path] = content
        consumed_bytes += size
    if skipped_for_bytes:
        logger.warning(
            "Skipped %s persisted plugin marker file(s) after reaching the "
            "%s-byte content budget during incremental update; indexing will "
            "continue with reduced automatic plugin-detection evidence",
            skipped_for_bytes,
            max_marker_bytes,
        )

    return RepositoryFacts(
        revision=revision,
        paths=tuple(sorted(paths)),
        marker_contents=bounded_marker_contents,
        project_type=baseline.project_type,
        source_root=baseline.source_root,
    )
