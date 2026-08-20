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


def overlay_repository_facts(
    baseline: RepositoryFacts,
    repository_root: str | Path | None,
    revision: str,
    updated_paths: Iterable[str | Path],
    deleted_paths: Iterable[str | Path],
    registry: PluginRegistry,
    *,
    max_marker_bytes: int = 262_144,
    max_marker_files: int = 4_096,
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
            _applicable_pattern_markers(
                path,
                declared_pattern_markers,
                baseline.source_root,
            ),
        ))
    }
    retained_marker_bytes = sum(
        len(content.encode("utf-8"))
        for content in marker_contents.values()
    )

    inspected_bytes = 0
    inspected_files = 0
    skipped_inspection_bytes = 0
    skipped_inspection_files = 0
    skipped_unreadable = 0
    for marker_path in updated:
        if not _under_source_root(marker_path, baseline.source_root):
            marker_contents.pop(marker_path, None)
            continue
        applicable_exact_markers = tuple(
            marker
            for marker in declared_markers
            if marker_path == marker.path or marker_path.endswith("/" + marker.path)
        )
        applicable_patterns = tuple(_applicable_pattern_markers(
            marker_path,
            declared_pattern_markers,
            baseline.source_root,
        ))
        if not applicable_exact_markers and not applicable_patterns:
            continue
        if inspected_files >= max_marker_files:
            # This is reduced evidence, not proof that an already persisted
            # marker stopped matching. Keep the last reliable content so an
            # incremental update cannot deactivate a plugin merely because an
            # optional inspection budget was exhausted.
            skipped_inspection_files += 1
            continue
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
        if inspected_bytes + size > max_marker_bytes:
            skipped_inspection_bytes += 1
            continue
        inspected_bytes += size
        try:
            content = full_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            skipped_unreadable += 1
            continue
        if any(_matching_markers(
            marker_path,
            content,
            applicable_exact_markers,
            applicable_patterns,
        )):
            previous = marker_contents.get(marker_path)
            candidate_bytes = len(content.encode("utf-8"))
            previous_bytes = (
                len(previous.encode("utf-8"))
                if previous is not None
                else 0
            )
            candidate_total = (
                retained_marker_bytes - previous_bytes + candidate_bytes
            )
            if candidate_total > max_marker_bytes:
                # A matching update that cannot fit the persisted evidence
                # budget is also inconclusive. Preserve its previous proven
                # marker, if any, and omit a newly introduced marker.
                skipped_inspection_bytes += 1
            else:
                marker_contents[marker_path] = content
                retained_marker_bytes = candidate_total
        else:
            removed = marker_contents.pop(marker_path, None)
            if removed is not None:
                retained_marker_bytes -= len(removed.encode("utf-8"))

    if skipped_inspection_bytes:
        logger.warning(
            "Skipped %s updated plugin marker candidate(s) after reaching the "
            "%s-byte inspection budget; incremental indexing will continue with "
            "reduced automatic plugin-detection evidence",
            skipped_inspection_bytes,
            max_marker_bytes,
        )
    if skipped_inspection_files:
        logger.warning(
            "Skipped %s updated plugin marker candidate(s) after reaching the "
            "%s-file inspection budget; incremental indexing will continue with "
            "reduced automatic plugin-detection evidence",
            skipped_inspection_files,
            max_marker_files,
        )
    if skipped_unreadable:
        logger.warning(
            "Skipped %s unavailable, unsafe, or non-UTF-8 updated plugin "
            "marker candidate(s); incremental indexing will continue with "
            "reduced automatic plugin-detection evidence",
            skipped_unreadable,
        )

    return RepositoryFacts(
        revision=revision,
        paths=tuple(sorted(paths)),
        marker_contents=marker_contents,
        project_type=baseline.project_type,
        source_root=baseline.source_root,
    )
