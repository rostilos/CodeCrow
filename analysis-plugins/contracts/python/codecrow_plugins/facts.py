from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from .api import RepositoryFacts, normalize_path
from .plugin_glob import plugin_glob_matches
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
    pattern_markers = tuple(sorted({
        marker
        for descriptor in registry.descriptors
        for alternative in descriptor.detection.alternatives
        for marker in alternative.content_pattern_markers
    }))
    return exact, pattern_markers


def _under_source_root(path: str, source_root: str | None) -> bool:
    return (
        source_root is None
        or path == source_root
        or path.startswith(source_root + "/")
    )


def _potential_root_relative_paths(path: str, source_root: str | None):
    if source_root is not None:
        if path == source_root:
            yield ""
        elif path.startswith(source_root + "/"):
            yield path[len(source_root) + 1:]
        return
    yield path
    offset = path.find("/")
    while offset >= 0:
        yield path[offset + 1:]
        offset = path.find("/", offset + 1)


def _applicable_pattern_markers(
    path: str,
    pattern_markers,
    source_root: str | None,
):
    relative_paths = tuple(_potential_root_relative_paths(path, source_root))
    return {
        marker
        for marker in pattern_markers
        if any(
            plugin_glob_matches(marker.path_pattern, relative)
            for relative in relative_paths
        )
    }


def _fair_candidate_paths(
    paths: tuple[str, ...],
    exact_marker_paths: tuple[str, ...],
    pattern_markers,
    source_root: str | None,
):
    def exact_lane(marker_path):
        return (
            path for path in paths
            if path == marker_path or path.endswith("/" + marker_path)
        )

    def pattern_lane(marker):
        return (
            path for path in paths
            if marker in _applicable_pattern_markers(
                path, (marker,), source_root,
            )
        )

    lanes = [
        exact_lane(marker_path)
        for marker_path in exact_marker_paths
    ]
    lanes.extend(
        pattern_lane(marker)
        for marker in pattern_markers
    )
    seen: set[str] = set()
    progressed = True
    while progressed:
        progressed = False
        for lane in lanes:
            path = next((candidate for candidate in lane if candidate not in seen), None)
            if path is None:
                continue
            seen.add(path)
            progressed = True
            yield path


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
        if marker.contains in content
    }
    return matching_exact, matching_patterns


def build_repository_facts(
    repository_root: str | Path,
    revision: str,
    paths: Iterable[str | Path],
    registry: PluginRegistry,
    *,
    max_marker_bytes: int = 262_144,
    max_marker_files: int = 4_096,
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
    marker_candidates = _fair_candidate_paths(
        tuple(
            path for path in normalized_paths
            if _under_source_root(path, source_root)
        ),
        declared_marker_paths,
        declared_pattern_markers,
        source_root,
    )
    skipped_for_bytes = 0
    skipped_for_files = 0
    skipped_unreadable = 0
    inspected_files = 0
    for marker_path in marker_candidates:
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
        applicable_pattern_markers = _applicable_pattern_markers(
            marker_path,
            declared_pattern_markers,
            source_root,
        )
        if not applicable_exact_markers and not applicable_pattern_markers:
            continue
        if inspected_files >= max_marker_files:
            skipped_for_files = 1
            break
        inspected_files += 1
        try:
            full_path = (root / marker_path).resolve(strict=True)
            if root not in full_path.parents:
                skipped_unreadable += 1
                continue
            size = full_path.stat().st_size
        except (OSError, RuntimeError):
            skipped_unreadable += 1
            continue
        if consumed_bytes + size > max_marker_bytes:
            skipped_for_bytes += 1
            continue
        consumed_bytes += size
        try:
            content = full_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            skipped_unreadable += 1
            continue
        matching_exact_markers, matching_pattern_markers = _matching_markers(
            marker_path,
            content,
            applicable_exact_markers,
            applicable_pattern_markers,
        )
        if not matching_exact_markers and not matching_pattern_markers:
            continue
        marker_contents[marker_path] = content

    if skipped_for_bytes:
        logger.warning(
            "Skipped %s plugin marker candidate(s) after reaching the %s-byte "
            "content budget; repository indexing will continue with reduced "
            "automatic plugin-detection evidence",
            skipped_for_bytes,
            max_marker_bytes,
        )
    if skipped_for_files:
        logger.warning(
            "Skipped %s plugin marker candidate(s) after reaching the %s-file "
            "inspection budget; repository indexing will continue with reduced "
            "automatic plugin-detection evidence",
            skipped_for_files,
            max_marker_files,
        )
    if skipped_unreadable:
        logger.warning(
            "Skipped %s unavailable, unsafe, or non-UTF-8 plugin marker "
            "candidate(s); repository indexing will continue with reduced "
            "automatic plugin-detection evidence",
            skipped_unreadable,
        )

    return RepositoryFacts(
        revision=revision,
        paths=normalized_paths,
        marker_contents=marker_contents,
        project_type=project_type,
        source_root=source_root,
    )
