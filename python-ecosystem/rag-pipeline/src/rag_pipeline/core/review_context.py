"""Exact proposed-tree structural generations and bounded review evidence.

The target archive and proposed-file overlay are review-host inputs. This
module materializes their exact composition only long enough to update the
sealed target-head graph through its repository-delta contract. Review tools
read the resulting complete proposed generation; model-controlled tool
arguments never select local repository roots or tenant bindings.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, Sequence

from .exact_index import (
    ExactIndexPreconditionError,
    RepositoryDeltaRebuildRequired,
)
from .generation_manifest import canonical_index_selection_policy
from .review_graph_tools import (
    get_review_structural_unit as read_review_structural_unit,
    minimal_review_context as build_minimal_review_context,
    query_review_graph as run_review_graph_query,
    review_impact_radius as build_review_impact_radius,
    traverse_review_graph as run_review_graph_traversal,
)
from .source_tree import (
    attest_repository_source_tree,
    read_repository_file_bytes,
    require_repository_source_tree_unchanged,
)


_QUESTION_TERM = re.compile(r"[A-Za-z_][A-Za-z0-9_:$\\.\-/]{2,}")
_TEST_PATH = re.compile(r"(^|/)(tests?|specs?|__tests__)(/|$)|(?:test|spec)\.[^.]+$", re.I)
_STOP_TERMS = {
    "about", "after", "again", "against", "analysis", "before", "being",
    "between", "change", "changed", "changes", "could", "during", "from",
    "have", "into", "might", "please", "review", "should", "that", "their",
    "there", "these", "this", "through", "what", "when", "where", "which",
    "with", "would",
}
_MAX_TERM_SEARCHES = 8
_MAX_ANCHOR_GRAPH_TARGETS = 12
_MAX_SOURCE_WINDOW_CHARACTERS = 8000
_REVIEW_GRAPH_COMPOSITION = "exact_policy_selected_proposed_tree"
_DIRECT_GRAPH_PATTERNS = (
    "callers_of",
    "callees_of",
    "references_to",
    "importers_of",
    "inheritors_of",
    "tests_for",
    "framework_relations",
)


class ProposedTreeUnavailableError(RuntimeError):
    """The overlay cannot represent the complete proposed tree exactly."""


@dataclass(frozen=True)
class ReviewOverlay:
    root: Path
    files_root: Path
    changed_paths: tuple[str, ...]
    deleted_paths: tuple[str, ...]
    file_sha256_by_path: Mapping[str, str]
    fingerprint: str


@dataclass(frozen=True)
class ProposedTreeGeneration:
    collection_target: str
    receipt: Mapping[str, Any]
    target_source_tree_sha256: str
    overlay_sha256: str
    proposed_source_tree_sha256: str
    representation_identity: str
    changed_paths: tuple[str, ...]
    deleted_paths: tuple[str, ...]
    cache_hit: bool


@dataclass
class _PreparationFlight:
    """One in-process proposed-tree build shared by concurrent HTTP workers."""

    completed: threading.Event
    result: ProposedTreeGeneration | None = None
    error: BaseException | None = None


@dataclass(frozen=True)
class ProposedTreeReadSession:
    """One complete reader held open for one exact proposed-tree operation."""

    reader: Any
    generation: ProposedTreeGeneration


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _normalize_path(value: object) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    candidate = PurePosixPath(raw)
    if (
        not raw
        or candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise ProposedTreeUnavailableError(
            f"review overlay contains an invalid repository path: {value!r}"
        )
    return candidate.as_posix()


def _string_paths(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ProposedTreeUnavailableError(
            f"review overlay manifest field {field} must be a path list"
        )
    return tuple(sorted({_normalize_path(item) for item in value}))


def load_review_overlay(path: str | Path) -> ReviewOverlay:
    """Load and attest the host-created proposed-file overlay."""
    root = Path(path).resolve()
    manifest_path = root / "manifest.json"
    files_root = root / "files"
    try:
        if not root.is_dir() or root.is_symlink():
            raise ProposedTreeUnavailableError("review overlay root is unavailable")
        if not files_root.is_dir() or files_root.is_symlink():
            raise ProposedTreeUnavailableError("review overlay files root is unavailable")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ProposedTreeUnavailableError:
        raise
    except (OSError, ValueError, TypeError) as exception:
        raise ProposedTreeUnavailableError(
            "review overlay manifest is unavailable or malformed"
        ) from exception
    if not isinstance(manifest, dict):
        raise ProposedTreeUnavailableError("review overlay manifest must be an object")

    changed_paths = _string_paths(manifest.get("changedFiles"), "changedFiles")
    deleted_paths = _string_paths(manifest.get("deletedFiles"), "deletedFiles")
    changed = set(changed_paths)
    deleted = set(deleted_paths)
    changed.update(deleted)
    changed_paths = tuple(sorted(changed))

    file_sha256_by_path: dict[str, str] = {}
    unavailable: list[str] = []
    for relative_path in changed_paths:
        if relative_path in deleted:
            continue
        candidate = files_root / relative_path
        try:
            candidate_stat = candidate.lstat()
            if not stat.S_ISREG(candidate_stat.st_mode):
                raise OSError("not a regular file")
            content = read_repository_file_bytes(files_root, relative_path)
        except (OSError, RuntimeError):
            unavailable.append(relative_path)
            continue
        file_sha256_by_path[relative_path] = hashlib.sha256(content).hexdigest()
    if unavailable:
        preview = ", ".join(unavailable[:5])
        suffix = "" if len(unavailable) <= 5 else f" (+{len(unavailable) - 5} more)"
        raise ProposedTreeUnavailableError(
            "exact proposed tree is unavailable because changed file bodies are "
            f"missing: {preview}{suffix}"
        )

    fingerprint = _sha256_json({
        "changedFiles": list(changed_paths),
        "deletedFiles": list(deleted_paths),
        "fileSha256ByPath": file_sha256_by_path,
    })
    return ReviewOverlay(
        root=root,
        files_root=files_root,
        changed_paths=changed_paths,
        deleted_paths=deleted_paths,
        file_sha256_by_path=file_sha256_by_path,
        fingerprint=fingerprint,
    )


def _destination_without_symlink_ancestor(root: Path, relative_path: str) -> Path:
    destination = root.joinpath(*PurePosixPath(relative_path).parts)
    current = root
    for component in PurePosixPath(relative_path).parts[:-1]:
        current = current / component
        try:
            current_stat = current.lstat()
        except FileNotFoundError:
            current.mkdir()
            continue
        if stat.S_ISLNK(current_stat.st_mode) or not stat.S_ISDIR(current_stat.st_mode):
            raise ProposedTreeUnavailableError(
                "proposed-tree path crosses a non-directory repository entry: "
                + relative_path
            )
    return destination


def _remove_destination(destination: Path) -> None:
    try:
        destination_stat = destination.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISDIR(destination_stat.st_mode) and not stat.S_ISLNK(destination_stat.st_mode):
        shutil.rmtree(destination)
    else:
        destination.unlink()


def _link_or_copy(source: str, destination: str) -> str:
    try:
        os.link(source, destination, follow_symlinks=False)
        return destination
    except OSError:
        return shutil.copy2(source, destination, follow_symlinks=False)


def materialize_proposed_tree(
    target_root: Path,
    overlay: ReviewOverlay,
    destination: Path,
) -> None:
    """Compose target + changed bodies - deletions without mutating the target."""
    shutil.copytree(
        target_root,
        destination,
        symlinks=True,
        copy_function=_link_or_copy,
    )
    for relative_path in overlay.deleted_paths:
        target = _destination_without_symlink_ancestor(destination, relative_path)
        _remove_destination(target)
    for relative_path, expected_sha256 in overlay.file_sha256_by_path.items():
        target = _destination_without_symlink_ancestor(destination, relative_path)
        _remove_destination(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        content = read_repository_file_bytes(
            overlay.files_root,
            relative_path,
            expected_sha256=expected_sha256,
        )
        target.write_bytes(content)


def _review_identity(
    *,
    workspace: str,
    project: str,
    target_branch: str,
    base_revision: str,
    base_collection_target: str,
    base_generation_manifest_sha256: str,
    source_revision: str,
    target_source_tree_sha256: str,
    overlay_sha256: str,
    representation: Mapping[str, Any],
    include_patterns: Sequence[str] | None,
    exclude_patterns: Sequence[str] | None,
    project_type: str | None,
    source_root: str | None,
) -> tuple[str, dict[str, Any]]:
    identity = {
        "workspace": workspace,
        "project": project,
        "targetBranch": target_branch,
        "baseRevision": base_revision,
        "baseCollectionTarget": base_collection_target,
        "baseGenerationManifestSha256": base_generation_manifest_sha256,
        "sourceRevision": source_revision,
        "targetSourceTreeSha256": target_source_tree_sha256,
        "overlaySha256": overlay_sha256,
        "representationIdentity": representation["representation_identity"],
        "selection": canonical_index_selection_policy(
            include_patterns,
            exclude_patterns,
        ),
        "projectType": project_type,
        "sourceRoot": source_root,
        "graphComposition": _REVIEW_GRAPH_COMPOSITION,
    }
    digest = _sha256_json(identity)
    return f"cc_review_g_{digest}", identity


def _question_terms(question: str, focus_symbols: Sequence[str]) -> list[str]:
    result: list[str] = []
    for value in (*focus_symbols, *_QUESTION_TERM.findall(question or "")):
        normalized = value.strip()
        if not normalized or normalized.casefold() in _STOP_TERMS:
            continue
        if normalized not in result:
            result.append(normalized)
        if len(result) >= _MAX_TERM_SEARCHES:
            break
    return result


def _fair_anchor_target_groups(
    anchors: Sequence[Mapping[str, Any]],
    *,
    max_targets: int,
) -> list[tuple[str, tuple[str, ...]]]:
    """Choose graph targets without letting a dense changed path crowd out peers."""

    candidates: list[tuple[str, list[str]]] = []
    for anchor in sorted(
        anchors,
        key=lambda item: str(item.get("path") or ""),
    ):
        path = str(anchor.get("path") or "")
        targets = list(dict.fromkeys(
            str(unit.get("qualifiedName") or unit.get("name") or unit.get("unitId"))
            for unit in anchor.get("symbols") or ()
            if isinstance(unit, Mapping)
            and (unit.get("qualifiedName") or unit.get("name") or unit.get("unitId"))
        ))
        if path and targets:
            candidates.append((path, targets))

    selected: list[list[str]] = [[] for _candidate in candidates]
    selected_count = 0
    depth = 0
    while selected_count < max_targets:
        added = False
        for index, (_path, targets) in enumerate(candidates):
            if depth >= len(targets):
                continue
            selected[index].append(targets[depth])
            selected_count += 1
            added = True
            if selected_count >= max_targets:
                break
        if not added:
            break
        depth += 1
    return [
        (candidates[index][0], tuple(targets))
        for index, targets in enumerate(selected)
        if targets
    ]


def _fair_direct_query_lanes(
    target_groups: Sequence[tuple[str, Sequence[str]]],
) -> Iterator[tuple[str, str, str]]:
    """Interleave changed roots and relation categories deterministically."""

    if not target_groups:
        return
    max_depth = max(len(targets) for _path, targets in target_groups)
    pattern_count = len(_DIRECT_GRAPH_PATTERNS)
    for depth in range(max_depth):
        for pattern_offset in range(pattern_count):
            for root_index, (path, targets) in enumerate(target_groups):
                if depth >= len(targets):
                    continue
                pattern = _DIRECT_GRAPH_PATTERNS[
                    (pattern_offset + root_index) % pattern_count
                ]
                yield path, targets[depth], pattern


def _select_fair_direct_relations(
    reader,
    target_groups: Sequence[tuple[str, Sequence[str]]],
    *,
    existing_evidence_ids: set[str],
    max_relations: int,
) -> tuple[list[dict[str, Any]], bool]:
    """Select a bounded direct slice with one result per root/category lane first."""

    if max_relations <= 0:
        return [], False
    selected: list[dict[str, Any]] = []
    deferred: list[list[dict[str, Any]]] = []
    seen = set(existing_evidence_ids)
    truncated = False
    for _path, target, pattern in _fair_direct_query_lanes(target_groups):
        remaining = max_relations - len(selected)
        if remaining <= 0:
            break
        graph = reader.query_graph(
            pattern,
            target,
            max_results=min(3, remaining),
        )
        truncated = truncated or bool(graph.get("truncated"))
        candidates: list[dict[str, Any]] = []
        for relation in graph.get("results") or ():
            if not isinstance(relation, Mapping):
                continue
            evidence_id = str(relation.get("evidenceId") or "")
            if not evidence_id or evidence_id in seen:
                continue
            seen.add(evidence_id)
            candidates.append(dict(relation))
        if not candidates:
            continue
        selected.append(candidates[0])
        if len(candidates) > 1:
            deferred.append(candidates[1:])

    # Sparse graphs may expose useful evidence through only one lane. Fill the
    # remaining budget from those already-bounded query pages, still taking one
    # result from each lane per pass.
    while deferred and len(selected) < max_relations:
        next_deferred: list[list[dict[str, Any]]] = []
        for candidates in deferred:
            if len(selected) >= max_relations:
                break
            selected.append(candidates[0])
            if len(candidates) > 1:
                next_deferred.append(candidates[1:])
        deferred = next_deferred
    return selected, truncated


def _relation_kind(relation: Mapping[str, Any]) -> str:
    return " ".join((
        str(relation.get("kind") or ""),
        str(relation.get("relation") or ""),
    )).upper().replace("-", "_")


def _relation_paths(relation: Mapping[str, Any]) -> set[str]:
    result = {
        str(path)
        for path in relation.get("relatedPaths") or ()
        if isinstance(path, str) and path
    }
    origin = relation.get("origin") or {}
    if isinstance(origin, Mapping) and isinstance(origin.get("path"), str):
        result.add(origin["path"])
    for endpoint in (relation.get("sourceUnit"), relation.get("targetUnit")):
        if isinstance(endpoint, Mapping) and isinstance(endpoint.get("path"), str):
            result.add(endpoint["path"])
    return result


def _source_detail_for_manifest(reader, unit: Mapping[str, Any]) -> dict[str, Any] | None:
    manifest_resolver = getattr(reader, "source_detail_for_manifest", None)
    if callable(manifest_resolver):
        return manifest_resolver(unit)
    detail = reader.get_unit(str(unit.get("unitId") or ""))
    if detail and detail.get("sourceEvidence"):
        return detail
    path = unit.get("path")
    if not isinstance(path, str) or not path:
        return None
    try:
        line = max(1, int(unit.get("startLine") or 1))
    except (TypeError, ValueError):
        line = 1
    row = reader.connection.execute(
        "SELECT unit_id FROM units WHERE path = ? "
        "AND record_type IN ('source_unit', 'plugin_context') "
        "ORDER BY CASE WHEN start_line <= ? AND end_line >= ? THEN 0 ELSE 1 END, "
        "abs(start_line - ?), start_line, unit_id LIMIT 1",
        (path, line, line, line),
    ).fetchone()
    return reader.get_unit(row["unit_id"]) if row is not None else None


def _bounded_source_window(
    detail: Mapping[str, Any],
    *,
    remaining_characters: int,
    changed_paths: set[str],
    relation_evidence_ids: Sequence[str],
) -> dict[str, Any] | None:
    unit = detail.get("unit")
    if not isinstance(unit, Mapping):
        return None
    content = unit.get("content")
    if not isinstance(content, str) or not content or remaining_characters <= 0:
        return None
    ceiling = min(remaining_characters, _MAX_SOURCE_WINDOW_CHARACTERS)
    truncated = len(content) > ceiling
    selected = content[:ceiling]
    if truncated and "\n" in selected:
        selected = selected.rsplit("\n", 1)[0] + "\n"
    if not selected:
        return None
    start_line = max(1, int(unit.get("startLine") or 1))
    end_line = start_line + selected.count("\n")
    if not selected.endswith("\n"):
        end_line += 1
    path = str(unit.get("path") or "")
    return {
        "evidenceId": "source:" + str(unit.get("unitId")),
        "unitId": unit.get("unitId"),
        "path": path,
        "startLine": start_line,
        "endLine": max(start_line, end_line - 1),
        "content": selected,
        "contentSha256": unit.get("contentSha256"),
        "changedFile": path in changed_paths,
        "truncated": truncated,
        "relationEvidenceIds": list(dict.fromkeys(relation_evidence_ids)),
    }


def _compact_graph_evidence(
    relations: Sequence[Mapping[str, Any]],
    relation_hops: Mapping[str, int],
    source_windows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalize repeated endpoint manifests into one node table."""

    source_window_by_unit = {
        str(window.get("unitId")): str(window.get("evidenceId"))
        for window in source_windows
        if window.get("unitId") and window.get("evidenceId")
    }
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    for relation in relations:
        endpoint_ids: dict[str, str | None] = {}
        for key in ("sourceUnit", "targetUnit"):
            unit = relation.get(key)
            unit_id = (
                str(unit.get("unitId") or "")
                if isinstance(unit, Mapping)
                else ""
            )
            endpoint_ids[key] = unit_id or None
            if not unit_id or not isinstance(unit, Mapping) or unit_id in nodes:
                continue
            node = {
                "unitId": unit_id,
                "path": unit.get("path"),
                "kind": unit.get("kind"),
                "name": unit.get("name"),
                "qualifiedName": unit.get("qualifiedName"),
                "startLine": unit.get("startLine"),
                "endLine": unit.get("endLine"),
                "language": unit.get("language"),
            }
            source_evidence_id = source_window_by_unit.get(unit_id)
            if source_evidence_id:
                node["sourceEvidenceId"] = source_evidence_id
            nodes[unit_id] = {
                key: value
                for key, value in node.items()
                if value is not None and value != ""
            }

        evidence_id = str(relation.get("evidenceId") or "")
        origin = relation.get("origin") or {}
        compact_origin = (
            {
                key: value
                for key, value in origin.items()
                if key in {"path", "line", "extractor", "plugin", "plugins"}
                and value is not None
                and value != ""
                and value != ()
                and value != []
            }
            if isinstance(origin, Mapping)
            else {}
        )
        edge = {
            "evidenceId": evidence_id,
            "hop": int(relation_hops.get(evidence_id, 0)),
            "kind": relation.get("kind"),
            "source": relation.get("source"),
            "relation": relation.get("relation"),
            "target": relation.get("target"),
            "origin": compact_origin,
            "sourceUnitId": endpoint_ids["sourceUnit"],
            "targetUnitId": endpoint_ids["targetUnit"],
            "relatedPaths": list(dict.fromkeys(
                path
                for path in relation.get("relatedPaths") or ()
                if isinstance(path, str) and path
            )),
        }
        attributes = relation.get("attributes")
        if isinstance(attributes, Mapping) and attributes:
            edge["attributes"] = dict(attributes)
        edges.append({
            key: value
            for key, value in edge.items()
            if value is not None
            and value != ""
            and value != ()
            and value != []
        })
    return list(nodes.values()), edges


class LayeredReviewGraphReader:
    """Legacy compatibility reader retained for layered regression fixtures.

    Production review operations read one complete sealed proposed-tree
    generation. This class preserves the former base-plus-overlay visibility
    semantics for tests and compatibility callers while they are retired.
    """

    def __init__(self, base_reader, overlay_reader, changed_paths: Sequence[str]):
        self.base_reader = base_reader
        self.overlay_reader = overlay_reader
        self.changed_paths = frozenset(changed_paths)
        self._endpoint_units: dict[
            tuple[str, bool], dict[str, Any] | None
        ] = {}

    def snapshot(self) -> dict[str, Any]:
        return self.overlay_reader.snapshot()

    def _base_unit_visible(self, unit: Mapping[str, Any] | None) -> bool:
        return bool(
            isinstance(unit, Mapping)
            and unit.get("path") not in self.changed_paths
        )

    def _base_relation_visible(self, relation: Mapping[str, Any]) -> bool:
        origin = relation.get("origin") or {}
        origin_path = origin.get("path") if isinstance(origin, Mapping) else None
        if origin_path in self.changed_paths:
            return False
        if (
            isinstance(origin, Mapping)
            and origin.get("extractor") == "plugin"
            and _relation_paths(relation).intersection(self.changed_paths)
        ):
            return False
        return True

    def _resolve_endpoint(
        self,
        endpoint: object,
        *,
        overlay_only: bool = False,
    ) -> dict[str, Any] | None:
        target = str(endpoint or "").strip()
        if not target:
            return None
        cache_key = (target, overlay_only)
        if cache_key in self._endpoint_units:
            return self._endpoint_units[cache_key]
        candidates = list(
            self.overlay_reader.search_units(target, max_results=5)
        )
        if not overlay_only:
            candidates.extend(
                unit
                for unit in self.base_reader.search_units(target, max_results=5)
                if self._base_unit_visible(unit)
            )
        target_folded = target.casefold()
        selected = next(
            (
                unit
                for unit in candidates
                if str(unit.get("qualifiedName") or "").casefold() == target_folded
                or str(unit.get("name") or "").casefold() == target_folded
                or str(unit.get("unitId") or "").casefold() == target_folded
            ),
            None,
        )
        self._endpoint_units[cache_key] = selected
        return selected

    def _enrich_relation(
        self,
        relation: Mapping[str, Any],
        *,
        base_relation: bool,
    ) -> dict[str, Any] | None:
        result = dict(relation)
        for endpoint_key, unit_key in (
            ("source", "sourceUnit"),
            ("target", "targetUnit"),
        ):
            current = result.get(unit_key)
            changed_base_endpoint = bool(
                base_relation
                and isinstance(current, Mapping)
                and current.get("path") in self.changed_paths
            )
            if changed_base_endpoint:
                # An unchanged base-origin relation can remain valid when its
                # endpoint's symbol survives in a changed file. Rebind it only to
                # an exact overlay symbol; a fuzzy or missing match would expose a
                # renamed/deleted target-head unit as proposed topology.
                resolved = self._resolve_endpoint(
                    current.get("qualifiedName")
                    or current.get("name")
                    or result.get(endpoint_key),
                    overlay_only=True,
                )
                if resolved is None:
                    return None
                result[unit_key] = resolved
            elif not isinstance(current, Mapping):
                resolved = self._resolve_endpoint(result.get(endpoint_key))
                if resolved is not None:
                    result[unit_key] = resolved
        return result

    def _enrich_relations(
        self,
        relations: Sequence[Mapping[str, Any]],
        *,
        base_relation: bool,
    ) -> list[dict[str, Any]]:
        enriched: list[dict[str, Any]] = []
        for relation in relations:
            item = self._enrich_relation(
                relation,
                base_relation=base_relation,
            )
            if item is not None:
                enriched.append(item)
        return enriched

    @staticmethod
    def _deduplicate_relations(
        relations: Sequence[Mapping[str, Any]],
        max_results: int,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for relation in relations:
            evidence_id = str(relation.get("evidenceId") or "")
            if not evidence_id or evidence_id in seen:
                continue
            seen.add(evidence_id)
            result.append(dict(relation))
            if len(result) >= max_results:
                break
        return result

    @staticmethod
    def _deduplicate_units(
        units: Sequence[Mapping[str, Any]],
        max_results: int,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for unit in units:
            unit_id = str(unit.get("unitId") or "")
            if not unit_id or unit_id in seen:
                continue
            seen.add(unit_id)
            result.append(dict(unit))
            if len(result) >= max_results:
                break
        return result

    def relations_for_paths(
        self,
        paths: Sequence[str],
        *,
        max_relations: int = 80,
    ) -> dict[str, Any]:
        normalized = tuple(dict.fromkeys(_normalize_path(path) for path in paths))
        overlay_paths = [path for path in normalized if path in self.changed_paths]
        base_paths = [path for path in normalized if path not in self.changed_paths]
        overlay = (
            self.overlay_reader.relations_for_paths(
                overlay_paths,
                max_relations=max_relations,
            )
            if overlay_paths
            else {"anchors": [], "relations": [], "coverage": {}}
        )
        remaining = max(1, max_relations - len(overlay["relations"]))
        base = (
            self.base_reader.relations_for_paths(
                base_paths,
                max_relations=remaining,
            )
            if base_paths
            else {"anchors": [], "relations": [], "coverage": {}}
        )
        base_relations = [
            relation
            for relation in base["relations"]
            if self._base_relation_visible(relation)
        ]
        relations = self._deduplicate_relations(
            [
                *self._enrich_relations(
                    overlay["relations"],
                    base_relation=False,
                ),
                *self._enrich_relations(
                    base_relations,
                    base_relation=True,
                ),
            ],
            max_relations,
        )
        anchors_by_path = {
            str(anchor.get("path")): anchor
            for anchor in (*overlay["anchors"], *base["anchors"])
        }
        anchors = [
            anchors_by_path.get(path, {
                "path": path,
                "symbols": [],
                "omittedSymbols": 0,
            })
            for path in normalized
        ]
        overlay_coverage = overlay.get("coverage") or {}
        base_coverage = base.get("coverage") or {}
        total_relations = int(overlay_coverage.get("totalRelations") or 0) + int(
            base_coverage.get("totalRelations") or 0
        )
        omitted_symbols = int(overlay_coverage.get("omittedSymbols") or 0) + int(
            base_coverage.get("omittedSymbols") or 0
        )
        return {
            "snapshot": self.snapshot(),
            "anchors": anchors,
            "relations": relations,
            "coverage": {
                "state": (
                    "complete"
                    if total_relations <= max_relations and omitted_symbols == 0
                    else "bounded"
                ),
                "totalRelations": total_relations,
                "omittedRelations": max(0, total_relations - len(relations)),
                "preloadedSymbols": sum(
                    len(anchor.get("symbols") or ()) for anchor in anchors
                ),
                "omittedSymbols": omitted_symbols,
            },
        }

    def get_unit(self, unit_id: str) -> dict[str, Any] | None:
        detail = self.overlay_reader.get_unit(unit_id)
        if detail is not None:
            return detail
        detail = self.base_reader.get_unit(unit_id)
        unit = detail.get("unit") if isinstance(detail, Mapping) else None
        return detail if self._base_unit_visible(unit) else None

    def source_detail_for_manifest(
        self,
        unit: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        path = unit.get("path")
        readers = (
            (self.overlay_reader,)
            if path in self.changed_paths
            else (self.overlay_reader, self.base_reader)
        )
        for reader in readers:
            detail = _source_detail_for_manifest(reader, unit)
            if detail is not None:
                return detail
        return None

    def query_graph(
        self,
        pattern: str,
        target: str,
        *,
        max_results: int = 25,
        cursor: int = 0,
    ) -> dict[str, Any]:
        normalized_pattern = str(pattern or "").strip().casefold()
        cursor = max(0, int(cursor))
        required = cursor + max_results + 1

        def collect(reader) -> dict[str, Any]:
            values: list[dict[str, Any]] = []
            page_cursor = 0
            last: dict[str, Any] = {}
            while len(values) < required:
                page_size = min(100, required - len(values))
                try:
                    page = reader.query_graph(
                        pattern,
                        target,
                        max_results=page_size,
                        cursor=page_cursor,
                    )
                except TypeError as exception:
                    if page_cursor or "cursor" not in str(exception):
                        raise
                    page = reader.query_graph(
                        pattern,
                        target,
                        max_results=required,
                    )
                last = dict(page)
                page_values = [
                    dict(item)
                    for item in page.get("results") or ()
                    if isinstance(item, Mapping)
                ]
                values.extend(page_values)
                if page.get("status") == "error" or not page.get("truncated"):
                    break
                next_cursor = page.get("nextCursor")
                try:
                    next_cursor = int(next_cursor)
                except (TypeError, ValueError):
                    next_cursor = page_cursor + len(page_values)
                if not page_values or next_cursor <= page_cursor:
                    break
                page_cursor = next_cursor
            last["results"] = values
            last["truncated"] = bool(last.get("truncated"))
            return last

        overlay = collect(self.overlay_reader)
        base = collect(self.base_reader)
        if overlay.get("status") in {"error", "ambiguous"}:
            return overlay
        if base.get("status") in {"error", "ambiguous"}:
            return {
                **base,
                "snapshot": self.snapshot(),
            }
        if normalized_pattern in {"symbol_search", "symbols", "file_summary"}:
            overlay_results = list(overlay.get("results") or ())
            base_results = [
                item
                for item in base.get("results") or ()
                if self._base_unit_visible(item)
            ]
            combined_units = [*overlay_results, *base_results]
            combined_results = self._deduplicate_units(combined_units, required)
            results = combined_results[cursor:cursor + max_results]
            truncated = len(combined_results) > cursor + len(results) or bool(
                overlay.get("truncated") or base.get("truncated")
            )
            return {
                "snapshot": self.snapshot(),
                "pattern": overlay.get("pattern") or normalized_pattern,
                "target": target,
                "results": results,
                "cursor": cursor,
                "nextCursor": cursor + len(results) if truncated else None,
                "truncated": truncated,
                "resultCount": len(results),
            }
        base_results = [
            item
            for item in base.get("results") or ()
            if self._base_relation_visible(item)
        ]
        combined = [
            *self._enrich_relations(
                overlay.get("results") or (),
                base_relation=False,
            ),
            *self._enrich_relations(
                base_results,
                base_relation=True,
            ),
        ]
        combined_results = self._deduplicate_relations(combined, required)
        results = combined_results[cursor:cursor + max_results]
        truncated = len(combined_results) > cursor + len(results) or bool(
            overlay.get("truncated") or base.get("truncated")
        )
        return {
            "snapshot": self.snapshot(),
            "pattern": pattern,
            "target": target,
            "results": results,
            "cursor": cursor,
            "nextCursor": cursor + len(results) if truncated else None,
            "truncated": truncated,
            "resultCount": len(results),
        }

    def search_units(
        self,
        query: str,
        *,
        max_results: int = 25,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        offset = max(0, int(offset))
        required = offset + max_results
        overlay = self.overlay_reader.search_units(query, max_results=required)
        base = [
            unit
            for unit in self.base_reader.search_units(query, max_results=required)
            if self._base_unit_visible(unit)
        ]
        combined = self._deduplicate_units((*overlay, *base), required)
        return combined[offset:offset + max_results]


class ProposedTreeReviewContextService:
    """Build/cache exact proposed topology and project bounded review evidence."""

    _preparation_lock = threading.Lock()
    _preparation_flights: dict[tuple[str, ...], _PreparationFlight] = {}

    def __init__(self, index_manager):
        self.index_manager = index_manager

    def prepare_generation_singleflight(
        self,
        **arguments: Any,
    ) -> ProposedTreeGeneration:
        """Build one exact review generation and share it with concurrent callers.

        Stage 1 prepares before batch fan-out, but duplicate delivery and parallel
        review requests can still target the same immutable overlay. Followers
        wait for the leader's result instead of entering index mutation and
        receiving a 409 conflict.
        """

        overlay = load_review_overlay(str(arguments["review_overlay_path"]))
        representation = self.index_manager.current_representation_identity()
        key = (
            str(id(self.index_manager)),
            str(arguments["workspace"]),
            str(arguments["project"]),
            str(arguments["target_branch"]),
            str(arguments["base_revision"]),
            str(arguments["source_revision"]),
            str(arguments.get("base_collection_target") or ""),
            str(arguments.get("base_generation_manifest_sha256") or ""),
            overlay.fingerprint,
            str(representation["representation_identity"]),
        )
        with self._preparation_lock:
            flight = self._preparation_flights.get(key)
            leader = flight is None
            if flight is None:
                flight = _PreparationFlight(completed=threading.Event())
                self._preparation_flights[key] = flight

        if not leader:
            try:
                wait_seconds = max(
                    30.0,
                    float(os.environ.get(
                        "RAG_REVIEW_PREPARATION_WAIT_SECONDS",
                        "900",
                    )),
                )
            except (TypeError, ValueError):
                wait_seconds = 900.0
            if not flight.completed.wait(wait_seconds):
                raise ProposedTreeUnavailableError(
                    "timed out waiting for the shared proposed-tree generation"
                )
            if flight.error is not None:
                raise ProposedTreeUnavailableError(
                    "shared proposed-tree generation failed: "
                    f"{type(flight.error).__name__}: {flight.error}"
                ) from flight.error
            if flight.result is None:
                raise ProposedTreeUnavailableError(
                    "shared proposed-tree generation completed without a receipt"
                )
            return replace(flight.result, cache_hit=True)

        try:
            result = self.prepare_generation(**arguments)
            flight.result = result
            return result
        except BaseException as error:
            flight.error = error
            raise
        finally:
            flight.completed.set()
            with self._preparation_lock:
                self._preparation_flights.pop(key, None)

    def prepare_generation(
        self,
        *,
        target_repo_path: str,
        review_overlay_path: str,
        workspace: str,
        project: str,
        target_branch: str,
        base_revision: str,
        source_revision: str,
        base_collection_target: str | None = None,
        base_generation_manifest_sha256: str | None = None,
        include_patterns: Sequence[str] | None = None,
        exclude_patterns: Sequence[str] | None = None,
        project_type: str | None = None,
        source_root: str | None = None,
    ) -> ProposedTreeGeneration:
        if not base_collection_target or not base_generation_manifest_sha256:
            raise ExactIndexPreconditionError(
                "proposed-tree review context requires an exact sealed "
                "target-head generation"
            )
        target_root = Path(target_repo_path).resolve()
        overlay = load_review_overlay(review_overlay_path)
        representation = self.index_manager.current_representation_identity()
        base_receipt = self.index_manager.get_revision_preflight(
            workspace,
            project,
            target_branch,
            base_revision,
            collection_target=base_collection_target,
        )
        if (
            base_receipt is None
            or base_receipt.get("generation_manifest_sha256")
            != base_generation_manifest_sha256
        ):
            raise ExactIndexPreconditionError(
                "exact sealed target-head generation is unavailable"
            )
        target_source_tree_sha256 = str(
            base_receipt.get("source_tree_sha256") or ""
        )
        if base_receipt.get("index_representation_fingerprint") != (
            representation["index_representation_fingerprint"]
        ):
            raise ExactIndexPreconditionError(
                "sealed target-head generation uses a different structural "
                "representation"
            )
        with self.index_manager.open_reader(
            workspace=workspace,
            project=project,
            branch=target_branch,
            revision=base_revision,
            generation_manifest_sha256=str(
                base_generation_manifest_sha256
            ),
            collection_target=str(base_collection_target),
        ) as sealed_base_reader:
            base_repository_facts = sealed_base_reader.repository_facts()
        if base_repository_facts.get("revision") != base_revision:
            raise ExactIndexPreconditionError(
                "sealed target-head repository facts have a different revision"
            )
        project_type = (
            str(base_repository_facts["projectType"])
            if base_repository_facts.get("projectType")
            else None
        )
        source_root = (
            str(base_repository_facts["sourceRoot"])
            if base_repository_facts.get("sourceRoot")
            else None
        )
        # Proposed-tree indexing remains bound to the exact selection policy of
        # its sealed base. Request-time profile fields cannot widen or narrow it.
        include_patterns = tuple(
            base_receipt.get("index_include_patterns") or ()
        ) or None
        exclude_patterns = tuple(
            base_receipt.get("index_exclude_patterns") or ()
        ) or None
        selected_overlay_paths = tuple(
            path.as_posix()
            for path in self.index_manager.loader.iter_repository_files(
                overlay.files_root,
                list(include_patterns) if include_patterns else None,
                list(exclude_patterns) if exclude_patterns else None,
                expected_file_sha256=overlay.file_sha256_by_path,
            )
        )
        collection_target, _ = _review_identity(
            workspace=workspace,
            project=project,
            target_branch=target_branch,
            base_revision=base_revision,
            base_collection_target=base_collection_target,
            base_generation_manifest_sha256=(
                base_generation_manifest_sha256
            ),
            source_revision=source_revision,
            target_source_tree_sha256=target_source_tree_sha256,
            overlay_sha256=overlay.fingerprint,
            representation=representation,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            project_type=project_type,
            source_root=source_root,
        )
        snapshot_metadata = {
            "kind": "proposed_tree",
            "storage_mode": _REVIEW_GRAPH_COMPOSITION,
            "base_revision": base_revision,
            "base_collection_target": base_collection_target,
            "base_generation_manifest_sha256": (
                base_generation_manifest_sha256
            ),
            "base_index_selection_policy_sha256": base_receipt.get(
                "index_selection_policy_sha256"
            ),
            "source_revision": source_revision,
            "target_source_tree_sha256": target_source_tree_sha256,
            "overlay_sha256": overlay.fingerprint,
            "changed_paths": list(overlay.changed_paths),
            "deleted_paths": list(overlay.deleted_paths),
            "selected_overlay_paths": list(selected_overlay_paths),
            "project_type": project_type,
            "source_root": source_root,
            "representation_identity": representation["representation_identity"],
        }
        receipt = self.index_manager.get_revision_preflight(
            workspace,
            project,
            target_branch,
            source_revision,
            collection_target=collection_target,
        )
        if receipt is not None:
            if receipt.get("snapshot_metadata") != snapshot_metadata:
                raise ExactIndexPreconditionError(
                    "cached proposed-tree generation has incompatible provenance"
                )
            return self._generation(
                receipt,
                collection_target,
                target_source_tree_sha256,
                overlay,
                representation,
                cache_hit=True,
            )

        target_source = attest_repository_source_tree(target_root, base_revision)
        if target_source_tree_sha256 != target_source.tree_sha256:
            raise ExactIndexPreconditionError(
                "sealed target-head generation does not match the supplied "
                "repository archive"
            )
        with tempfile.TemporaryDirectory(prefix="codecrow-review-tree-") as temporary:
            proposed_root = Path(temporary) / "repository"
            materialize_proposed_tree(target_root, overlay, proposed_root)
            require_repository_source_tree_unchanged(target_root, target_source)
            reloaded_overlay = load_review_overlay(review_overlay_path)
            if reloaded_overlay.fingerprint != overlay.fingerprint:
                raise ProposedTreeUnavailableError(
                    "review overlay changed while the proposed tree was materialized"
                )
            deleted_path_set = set(overlay.deleted_paths)
            changed_paths = tuple(
                path
                for path in overlay.changed_paths
                if path not in deleted_path_set
            )
            try:
                self.index_manager.index_proposed_tree_delta(
                    repo_path=str(proposed_root),
                    workspace=workspace,
                    project=project,
                    branch=target_branch,
                    base_revision=base_revision,
                    commit=source_revision,
                    changed_paths=changed_paths,
                    deleted_paths=overlay.deleted_paths,
                    # The manager performs the sole authoritative attestation
                    # of this exclusively-owned temporary composition.
                    source_tree_sha256=None,
                    collection_target=collection_target,
                    base_collection_target=base_collection_target,
                    base_generation_manifest_sha256=(
                        base_generation_manifest_sha256
                    ),
                    project_type=project_type,
                    source_root=source_root,
                    snapshot_metadata=snapshot_metadata,
                    source_tree_exclusively_owned=True,
                )
            except RepositoryDeltaRebuildRequired:
                # Repository-aware plugin selection changed, or a custom plugin
                # cannot restore its sealed base state. The already-materialized
                # full proposed tree is the exact, bounded fallback.
                self.index_manager.index_repository(
                    repo_path=str(proposed_root),
                    workspace=workspace,
                    project=project,
                    branch=target_branch,
                    commit=source_revision,
                    include_patterns=(
                        list(include_patterns) if include_patterns else None
                    ),
                    exclude_patterns=(
                        list(exclude_patterns) if exclude_patterns else None
                    ),
                    collection_target=collection_target,
                    project_type=project_type,
                    source_root=source_root,
                    snapshot_metadata=snapshot_metadata,
                    source_tree_exclusively_owned=True,
                )
        receipt = self.index_manager.get_revision_preflight(
            workspace,
            project,
            target_branch,
            source_revision,
            collection_target=collection_target,
        )
        if receipt is None or receipt.get("snapshot_metadata") != snapshot_metadata:
            raise ExactIndexPreconditionError(
                "proposed-tree structural generation was not sealed exactly"
            )
        return self._generation(
            receipt,
            collection_target,
            target_source_tree_sha256,
            overlay,
            representation,
            cache_hit=False,
        )

    @staticmethod
    def _generation(
        receipt: Mapping[str, Any],
        collection_target: str,
        target_source_tree_sha256: str,
        overlay: ReviewOverlay,
        representation: Mapping[str, Any],
        *,
        cache_hit: bool,
    ) -> ProposedTreeGeneration:
        return ProposedTreeGeneration(
            collection_target=collection_target,
            receipt=dict(receipt),
            target_source_tree_sha256=target_source_tree_sha256,
            overlay_sha256=overlay.fingerprint,
            proposed_source_tree_sha256=str(receipt["source_tree_sha256"]),
            representation_identity=str(representation["representation_identity"]),
            changed_paths=overlay.changed_paths,
            deleted_paths=overlay.deleted_paths,
            cache_hit=cache_hit,
        )

    def load_prepared_generation(
        self,
        *,
        review_overlay_path: str,
        workspace: str,
        project: str,
        target_branch: str,
        base_revision: str,
        source_revision: str,
        base_collection_target: str | None,
        base_generation_manifest_sha256: str | None,
        review_collection_target: str | None,
        review_generation_manifest_sha256: str | None,
    ) -> ProposedTreeGeneration:
        """Verify and load a sealed review generation without mutating storage."""

        if not base_collection_target or not base_generation_manifest_sha256:
            raise ExactIndexPreconditionError(
                "prepared proposed-tree queries require an exact sealed base"
            )
        if not review_collection_target or not review_generation_manifest_sha256:
            raise ExactIndexPreconditionError(
                "proposed-tree query requires a sealed review-generation receipt"
            )
        overlay = load_review_overlay(review_overlay_path)
        representation = self.index_manager.current_representation_identity()
        receipt = self.index_manager.get_revision_preflight(
            workspace,
            project,
            target_branch,
            source_revision,
            collection_target=review_collection_target,
        )
        if receipt is None:
            raise ExactIndexPreconditionError(
                "prepared proposed-tree generation is unavailable"
            )
        if receipt.get("generation_manifest_sha256") != (
            review_generation_manifest_sha256
        ):
            raise ExactIndexPreconditionError(
                "prepared proposed-tree generation receipt does not match"
            )
        if receipt.get("index_representation_fingerprint") != (
            representation["index_representation_fingerprint"]
        ):
            raise ExactIndexPreconditionError(
                "prepared proposed-tree generation uses a different "
                "structural representation"
            )
        metadata = receipt.get("snapshot_metadata")
        if not isinstance(metadata, Mapping):
            raise ExactIndexPreconditionError(
                "prepared proposed-tree generation has no provenance"
            )
        expected_metadata = {
            "kind": "proposed_tree",
            "storage_mode": _REVIEW_GRAPH_COMPOSITION,
            "base_revision": base_revision,
            "base_collection_target": base_collection_target,
            "base_generation_manifest_sha256": (
                base_generation_manifest_sha256
            ),
            "source_revision": source_revision,
            "overlay_sha256": overlay.fingerprint,
            "representation_identity": (
                representation["representation_identity"]
            ),
        }
        mismatches = sorted(
            key
            for key, expected in expected_metadata.items()
            if metadata.get(key) != expected
        )
        if mismatches:
            raise ExactIndexPreconditionError(
                "prepared proposed-tree generation has incompatible "
                "provenance: " + ", ".join(mismatches)
            )
        if tuple(metadata.get("changed_paths") or ()) != overlay.changed_paths:
            raise ExactIndexPreconditionError(
                "prepared proposed-tree changed paths do not match the overlay"
            )
        if tuple(metadata.get("deleted_paths") or ()) != overlay.deleted_paths:
            raise ExactIndexPreconditionError(
                "prepared proposed-tree deleted paths do not match the overlay"
            )
        target_source_tree_sha256 = str(
            metadata.get("target_source_tree_sha256") or ""
        )
        if not target_source_tree_sha256:
            raise ExactIndexPreconditionError(
                "prepared proposed-tree generation lacks target source identity"
            )
        return self._generation(
            receipt,
            review_collection_target,
            target_source_tree_sha256,
            overlay,
            representation,
            cache_hit=True,
        )

    @contextmanager
    def open_read_session(
        self,
        *,
        target_repo_path: str,
        review_overlay_path: str,
        workspace: str,
        project: str,
        target_branch: str,
        base_revision: str,
        source_revision: str,
        base_collection_target: str | None = None,
        base_generation_manifest_sha256: str | None = None,
        review_collection_target: str | None = None,
        review_generation_manifest_sha256: str | None = None,
        include_patterns: Sequence[str] | None = None,
        exclude_patterns: Sequence[str] | None = None,
        project_type: str | None = None,
        source_root: str | None = None,
    ) -> Iterator[ProposedTreeReadSession]:
        """Open the complete exact proposed-tree generation.

        Keeping generation preparation and the physical reader inside this
        context manager prevents a later operation from accidentally reopening
        the target-head generation or observing an already-closed generation.
        """

        generation = self.load_prepared_generation(
            review_overlay_path=review_overlay_path,
            workspace=workspace,
            project=project,
            target_branch=target_branch,
            base_revision=base_revision,
            source_revision=source_revision,
            base_collection_target=base_collection_target,
            base_generation_manifest_sha256=(
                base_generation_manifest_sha256
            ),
            review_collection_target=review_collection_target,
            review_generation_manifest_sha256=(
                review_generation_manifest_sha256
            ),
        )
        with self.index_manager.open_reader(
            workspace=workspace,
            project=project,
            branch=target_branch,
            revision=source_revision,
            generation_manifest_sha256=str(
                generation.receipt["generation_manifest_sha256"]
            ),
            collection_target=generation.collection_target,
        ) as proposed_reader:
            yield ProposedTreeReadSession(
                reader=proposed_reader,
                generation=generation,
            )

    def review_context(
        self,
        *,
        target_repo_path: str,
        review_overlay_path: str,
        workspace: str,
        project: str,
        target_branch: str,
        base_revision: str,
        source_revision: str,
        base_collection_target: str | None = None,
        base_generation_manifest_sha256: str | None = None,
        review_collection_target: str | None = None,
        review_generation_manifest_sha256: str | None = None,
        focus_paths: Sequence[str],
        question: str,
        focus_symbols: Sequence[str] = (),
        include_patterns: Sequence[str] | None = None,
        exclude_patterns: Sequence[str] | None = None,
        project_type: str | None = None,
        source_root: str | None = None,
        max_relations: int = 32,
        max_source_windows: int = 6,
        max_source_characters: int = 12000,
    ) -> dict[str, Any]:
        normalized_focus_paths = tuple(dict.fromkeys(
            _normalize_path(path) for path in focus_paths
        ))
        with self.open_read_session(
            target_repo_path=target_repo_path,
            review_overlay_path=review_overlay_path,
            workspace=workspace,
            project=project,
            target_branch=target_branch,
            base_revision=base_revision,
            source_revision=source_revision,
            base_collection_target=base_collection_target,
            base_generation_manifest_sha256=base_generation_manifest_sha256,
            review_collection_target=review_collection_target,
            review_generation_manifest_sha256=(
                review_generation_manifest_sha256
            ),
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            project_type=project_type,
            source_root=source_root,
        ) as session:
            return self._read_context(
                session.reader,
                session.generation,
                normalized_focus_paths,
                question,
                focus_symbols,
                max_relations=max_relations,
                max_source_windows=max_source_windows,
                max_source_characters=max_source_characters,
            )

    def minimal_review_context(
        self,
        *,
        target_repo_path: str,
        review_overlay_path: str,
        workspace: str,
        project: str,
        target_branch: str,
        base_revision: str,
        source_revision: str,
        focus_paths: Sequence[str],
        question: str,
        focus_symbols: Sequence[str] = (),
        base_collection_target: str | None = None,
        base_generation_manifest_sha256: str | None = None,
        review_collection_target: str | None = None,
        review_generation_manifest_sha256: str | None = None,
        include_patterns: Sequence[str] | None = None,
        exclude_patterns: Sequence[str] | None = None,
        project_type: str | None = None,
        source_root: str | None = None,
        max_relations: int = 25,
        detail_level: str = "minimal",
        include_source: bool = True,
        max_source_windows: int = 4,
        max_source_characters: int = 8000,
    ) -> dict[str, Any]:
        normalized_paths = tuple(dict.fromkeys(
            _normalize_path(path) for path in focus_paths
        ))
        with self.open_read_session(
            target_repo_path=target_repo_path,
            review_overlay_path=review_overlay_path,
            workspace=workspace,
            project=project,
            target_branch=target_branch,
            base_revision=base_revision,
            source_revision=source_revision,
            base_collection_target=base_collection_target,
            base_generation_manifest_sha256=base_generation_manifest_sha256,
            review_collection_target=review_collection_target,
            review_generation_manifest_sha256=(
                review_generation_manifest_sha256
            ),
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            project_type=project_type,
            source_root=source_root,
        ) as session:
            return build_minimal_review_context(
                session.reader,
                question=question,
                focus_paths=normalized_paths,
                focus_symbols=focus_symbols,
                changed_paths=session.generation.changed_paths,
                max_relations=max_relations,
                detail_level=detail_level,
                include_source=include_source,
                max_source_windows=max_source_windows,
                max_source_characters=max_source_characters,
            )

    def review_impact_radius(
        self,
        *,
        target_repo_path: str,
        review_overlay_path: str,
        workspace: str,
        project: str,
        target_branch: str,
        base_revision: str,
        source_revision: str,
        focus_paths: Sequence[str],
        targets: Sequence[str] = (),
        base_collection_target: str | None = None,
        base_generation_manifest_sha256: str | None = None,
        review_collection_target: str | None = None,
        review_generation_manifest_sha256: str | None = None,
        include_patterns: Sequence[str] | None = None,
        exclude_patterns: Sequence[str] | None = None,
        project_type: str | None = None,
        source_root: str | None = None,
        max_depth: int = 2,
        max_results: int = 100,
        detail_level: str = "standard",
        include_source: bool = True,
        max_source_windows: int = 6,
        max_source_characters: int = 12000,
    ) -> dict[str, Any]:
        normalized_paths = tuple(dict.fromkeys(
            _normalize_path(path) for path in focus_paths
        ))
        requested_targets = tuple(dict.fromkeys(
            str(target).strip()
            for target in targets
            if str(target).strip()
        ))
        root_targets = requested_targets or normalized_paths
        with self.open_read_session(
            target_repo_path=target_repo_path,
            review_overlay_path=review_overlay_path,
            workspace=workspace,
            project=project,
            target_branch=target_branch,
            base_revision=base_revision,
            source_revision=source_revision,
            base_collection_target=base_collection_target,
            base_generation_manifest_sha256=base_generation_manifest_sha256,
            review_collection_target=review_collection_target,
            review_generation_manifest_sha256=(
                review_generation_manifest_sha256
            ),
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            project_type=project_type,
            source_root=source_root,
        ) as session:
            return build_review_impact_radius(
                session.reader,
                targets=root_targets,
                changed_paths=session.generation.changed_paths,
                max_depth=max_depth,
                max_results=max_results,
                detail_level=detail_level,
                include_source=include_source,
                max_source_windows=max_source_windows,
                max_source_characters=max_source_characters,
            )

    def traverse_review_graph(
        self,
        *,
        target_repo_path: str,
        review_overlay_path: str,
        workspace: str,
        project: str,
        target_branch: str,
        base_revision: str,
        source_revision: str,
        focus_paths: Sequence[str],
        start: str,
        strategy: str = "bfs",
        direction: str = "both",
        relation_kinds: Sequence[str] = (),
        base_collection_target: str | None = None,
        base_generation_manifest_sha256: str | None = None,
        review_collection_target: str | None = None,
        review_generation_manifest_sha256: str | None = None,
        include_patterns: Sequence[str] | None = None,
        exclude_patterns: Sequence[str] | None = None,
        project_type: str | None = None,
        source_root: str | None = None,
        max_depth: int = 3,
        max_results: int = 100,
        token_budget: int = 2000,
        detail_level: str = "standard",
        include_source: bool = True,
        max_source_windows: int = 6,
        max_source_characters: int = 12000,
    ) -> dict[str, Any]:
        with self.open_read_session(
            target_repo_path=target_repo_path,
            review_overlay_path=review_overlay_path,
            workspace=workspace,
            project=project,
            target_branch=target_branch,
            base_revision=base_revision,
            source_revision=source_revision,
            base_collection_target=base_collection_target,
            base_generation_manifest_sha256=base_generation_manifest_sha256,
            review_collection_target=review_collection_target,
            review_generation_manifest_sha256=(
                review_generation_manifest_sha256
            ),
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            project_type=project_type,
            source_root=source_root,
        ) as session:
            return run_review_graph_traversal(
                session.reader,
                start=start,
                strategy=strategy,
                direction=direction,
                relation_kinds=relation_kinds,
                changed_paths=session.generation.changed_paths,
                max_depth=max_depth,
                max_results=max_results,
                token_budget=token_budget,
                detail_level=detail_level,
                include_source=include_source,
                max_source_windows=max_source_windows,
                max_source_characters=max_source_characters,
            )

    def query_review_graph(
        self,
        *,
        target_repo_path: str,
        review_overlay_path: str,
        workspace: str,
        project: str,
        target_branch: str,
        base_revision: str,
        source_revision: str,
        focus_paths: Sequence[str],
        pattern: str,
        target: str,
        base_collection_target: str | None = None,
        base_generation_manifest_sha256: str | None = None,
        review_collection_target: str | None = None,
        review_generation_manifest_sha256: str | None = None,
        include_patterns: Sequence[str] | None = None,
        exclude_patterns: Sequence[str] | None = None,
        project_type: str | None = None,
        source_root: str | None = None,
        max_results: int = 25,
        cursor: int = 0,
        detail_level: str = "standard",
        include_source: bool = True,
        max_source_windows: int = 6,
        max_source_characters: int = 12000,
    ) -> dict[str, Any]:
        with self.open_read_session(
            target_repo_path=target_repo_path,
            review_overlay_path=review_overlay_path,
            workspace=workspace,
            project=project,
            target_branch=target_branch,
            base_revision=base_revision,
            source_revision=source_revision,
            base_collection_target=base_collection_target,
            base_generation_manifest_sha256=base_generation_manifest_sha256,
            review_collection_target=review_collection_target,
            review_generation_manifest_sha256=(
                review_generation_manifest_sha256
            ),
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            project_type=project_type,
            source_root=source_root,
        ) as session:
            return run_review_graph_query(
                session.reader,
                pattern=pattern,
                target=target,
                changed_paths=session.generation.changed_paths,
                max_results=max_results,
                cursor=cursor,
                detail_level=detail_level,
                include_source=include_source,
                max_source_windows=max_source_windows,
                max_source_characters=max_source_characters,
            )

    def get_review_structural_unit(
        self,
        *,
        target_repo_path: str,
        review_overlay_path: str,
        workspace: str,
        project: str,
        target_branch: str,
        base_revision: str,
        source_revision: str,
        focus_paths: Sequence[str],
        unit_id: str,
        offset: int = 0,
        max_characters: int = 12000,
        base_collection_target: str | None = None,
        base_generation_manifest_sha256: str | None = None,
        review_collection_target: str | None = None,
        review_generation_manifest_sha256: str | None = None,
        include_patterns: Sequence[str] | None = None,
        exclude_patterns: Sequence[str] | None = None,
        project_type: str | None = None,
        source_root: str | None = None,
    ) -> dict[str, Any] | None:
        with self.open_read_session(
            target_repo_path=target_repo_path,
            review_overlay_path=review_overlay_path,
            workspace=workspace,
            project=project,
            target_branch=target_branch,
            base_revision=base_revision,
            source_revision=source_revision,
            base_collection_target=base_collection_target,
            base_generation_manifest_sha256=base_generation_manifest_sha256,
            review_collection_target=review_collection_target,
            review_generation_manifest_sha256=(
                review_generation_manifest_sha256
            ),
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            project_type=project_type,
            source_root=source_root,
        ) as session:
            return read_review_structural_unit(
                session.reader,
                unit_id=unit_id,
                offset=offset,
                max_characters=max_characters,
            )

    @staticmethod
    def _read_context(
        reader,
        generation: ProposedTreeGeneration,
        focus_paths: Sequence[str],
        question: str,
        focus_symbols: Sequence[str],
        *,
        max_relations: int,
        max_source_windows: int,
        max_source_characters: int,
    ) -> dict[str, Any]:
        # Reserve the response budget across anchor, direct, and second-hop
        # evidence. Letting anchor relations consume the whole budget made the
        # nominal second-hop traversal unreachable on relation-dense files.
        anchor_budget = max(1, min(max_relations, max_relations // 3))
        direct_limit = max(anchor_budget, max_relations * 2 // 3)
        anchors = reader.relations_for_paths(
            focus_paths,
            max_relations=anchor_budget,
        )
        changed_units: list[dict[str, Any]] = []
        represented_focus_paths: set[str] = set()
        for anchor in anchors["anchors"]:
            symbols = anchor.get("symbols") or ()
            changed_units.extend(symbols)
            if symbols and isinstance(anchor.get("path"), str):
                represented_focus_paths.add(anchor["path"])

        relation_by_id: dict[str, dict[str, Any]] = {}
        relation_hops: dict[str, int] = {}
        truncated_graph_query = False

        def add_relations(
            relations: Sequence[Mapping[str, Any]],
            *,
            hop: int,
            limit: int,
        ) -> None:
            for relation in relations:
                evidence_id = str(relation.get("evidenceId") or "")
                if not evidence_id:
                    continue
                if evidence_id in relation_by_id:
                    relation_hops[evidence_id] = min(
                        relation_hops[evidence_id],
                        hop,
                    )
                    continue
                if len(relation_by_id) >= limit:
                    return
                relation_by_id[evidence_id] = dict(relation)
                relation_hops[evidence_id] = hop

        add_relations(
            anchors["relations"],
            hop=0,
            limit=anchor_budget,
        )
        anchor_target_groups = _fair_anchor_target_groups(
            anchors["anchors"],
            max_targets=_MAX_ANCHOR_GRAPH_TARGETS,
        )
        anchor_targets: list[str] = []
        target_depth = 0
        while any(
            target_depth < len(targets)
            for _path, targets in anchor_target_groups
        ):
            anchor_targets.extend(
                targets[target_depth]
                for _path, targets in anchor_target_groups
                if target_depth < len(targets)
            )
            target_depth += 1
        direct_relations, direct_truncated = _select_fair_direct_relations(
            reader,
            anchor_target_groups,
            existing_evidence_ids=set(relation_by_id),
            max_relations=max(0, direct_limit - len(relation_by_id)),
        )
        truncated_graph_query = truncated_graph_query or direct_truncated
        add_relations(
            direct_relations,
            hop=1,
            limit=direct_limit,
        )

        focus_matches: list[dict[str, Any]] = []
        for term in _question_terms(question, focus_symbols):
            for unit in reader.search_units(term, max_results=5):
                unit_id = str(unit.get("unitId") or "")
                if unit_id and all(
                    existing.get("unitId") != unit_id for existing in focus_matches
                ):
                    focus_matches.append(unit)
            if len(focus_matches) >= 20:
                break

        for unit in focus_matches[:5]:
            if len(relation_by_id) >= direct_limit:
                break
            target = str(
                unit.get("qualifiedName")
                or unit.get("name")
                or unit.get("path")
                or ""
            )
            if not target:
                continue
            remaining = direct_limit - len(relation_by_id)
            graph = reader.query_graph(
                "relations_of",
                target,
                max_results=min(5, remaining),
            )
            truncated_graph_query = truncated_graph_query or bool(
                graph.get("truncated")
            )
            add_relations(
                graph.get("results") or (),
                hop=1,
                limit=direct_limit,
            )

        second_hop_targets: list[str] = []
        for relation in tuple(relation_by_id.values()):
            if "CALL" not in _relation_kind(relation):
                continue
            for endpoint in (relation.get("source"), relation.get("target")):
                value = str(endpoint or "")
                if value and value not in anchor_targets and value not in second_hop_targets:
                    second_hop_targets.append(value)
        for target in second_hop_targets[:6]:
            if len(relation_by_id) >= max_relations:
                break
            for pattern in ("callers_of", "callees_of"):
                remaining = max_relations - len(relation_by_id)
                if remaining <= 0:
                    break
                graph = reader.query_graph(
                    pattern,
                    target,
                    max_results=min(2, remaining),
                )
                truncated_graph_query = truncated_graph_query or bool(
                    graph.get("truncated")
                )
                add_relations(
                    graph.get("results") or (),
                    hop=2,
                    limit=max_relations,
                )

        changed_set = set(generation.changed_paths)
        focus_set = set(focus_paths)
        relations = list(relation_by_id.values())
        call_ids: list[str] = []
        blast_ids: list[str] = []
        test_ids: list[str] = []
        framework_ids: list[str] = []
        for relation in relations:
            evidence_id = relation["evidenceId"]
            kind = _relation_kind(relation)
            relation_paths = _relation_paths(relation)
            if "CALL" in kind:
                call_ids.append(evidence_id)
            if relation_paths - focus_set or (
                relation_paths.intersection(focus_set)
                and "CONTAINS" not in kind
            ):
                blast_ids.append(evidence_id)
            if "TEST" in kind or any(_TEST_PATH.search(path) for path in relation_paths):
                test_ids.append(evidence_id)
            origin = relation.get("origin") or {}
            if isinstance(origin, Mapping) and (
                origin.get("extractor") == "plugin"
                or origin.get("plugin")
                or origin.get("plugins")
            ):
                framework_ids.append(evidence_id)

        endpoint_candidates: list[tuple[dict[str, Any], str]] = []
        for category in (test_ids, blast_ids, call_ids):
            for evidence_id in category:
                relation = relation_by_id[evidence_id]
                for endpoint in (relation.get("sourceUnit"), relation.get("targetUnit")):
                    if isinstance(endpoint, dict):
                        endpoint_candidates.append((endpoint, evidence_id))
        for unit in focus_matches:
            endpoint_candidates.append((unit, "focus:" + str(unit.get("unitId"))))

        source_windows: list[dict[str, Any]] = []
        seen_source_units: set[str] = set()
        omitted_source_paths: list[str] = []
        source_evidence_by_unit: dict[str, list[str]] = {}
        for unit, evidence_id in endpoint_candidates:
            unit_id = str(unit.get("unitId") or "")
            if unit_id:
                source_evidence_by_unit.setdefault(unit_id, []).append(evidence_id)
        for unit, evidence_id in endpoint_candidates:
            if len(source_windows) >= max_source_windows:
                path = unit.get("path")
                if isinstance(path, str) and path not in omitted_source_paths:
                    omitted_source_paths.append(path)
                continue
            if unit.get("path") in focus_set:
                continue
            detail = _source_detail_for_manifest(reader, unit)
            detail_unit = detail.get("unit") if detail else None
            source_unit_id = (
                str(detail_unit.get("unitId") or "")
                if isinstance(detail_unit, Mapping)
                else ""
            )
            if not source_unit_id or source_unit_id in seen_source_units:
                continue
            used_characters = sum(len(item["content"]) for item in source_windows)
            window = _bounded_source_window(
                detail,
                remaining_characters=max_source_characters - used_characters,
                changed_paths=changed_set,
                relation_evidence_ids=(
                    source_evidence_by_unit.get(str(unit.get("unitId") or ""))
                    or [evidence_id]
                ),
            )
            if window is None:
                path = unit.get("path")
                if (
                    detail is not None
                    and isinstance(path, str)
                    and path not in omitted_source_paths
                ):
                    omitted_source_paths.append(path)
                continue
            source_windows.append(window)
            seen_source_units.add(source_unit_id)

        graph_nodes, compact_relations = _compact_graph_evidence(
            relations,
            relation_hops,
            source_windows,
        )
        frontier_symbols = list(dict.fromkeys(
            str(endpoint or "")
            for relation in relations
            if relation_hops.get(str(relation.get("evidenceId") or ""), 0) >= 2
            for endpoint in (relation.get("source"), relation.get("target"))
            if endpoint
            and str(endpoint) not in anchor_targets
        ))[:8]

        partial_reasons: list[str] = []
        unrepresented_focus_paths = sorted(
            set(focus_paths)
            - set(generation.deleted_paths)
            - represented_focus_paths
        )
        anchor_coverage = anchors.get("coverage") or {}
        if anchor_coverage.get("state") != "complete":
            partial_reasons.append("focus_relation_limit")
        if truncated_graph_query or len(relation_by_id) >= max_relations:
            partial_reasons.append("graph_relation_limit")
        if omitted_source_paths or any(item["truncated"] for item in source_windows):
            partial_reasons.append("source_window_limit")
        if int(generation.receipt.get("skipped_file_count") or 0) > 0:
            partial_reasons.append("index_skipped_files")
        if unrepresented_focus_paths:
            partial_reasons.append("focus_paths_without_structural_units")
        partial_reasons = list(dict.fromkeys(partial_reasons))

        omitted_followups: list[dict[str, Any]] = []
        if "focus_relation_limit" in partial_reasons or "graph_relation_limit" in partial_reasons:
            omitted_followups.append({
                "tool": "exploreReviewContext",
                "reason": "The bounded structural slice omitted additional relations.",
                "arguments": {
                    "question": "Narrow the investigation to one named symbol or dependency.",
                    "focusSymbols": frontier_symbols[:5] or anchor_targets[:5],
                    "maxRelations": max_relations,
                },
            })
        if omitted_source_paths:
            omitted_followups.append({
                "tool": "getReviewFileContent",
                "reason": "Related exact source windows exceeded the response budget.",
                "paths": omitted_source_paths[:10],
            })
        if unrepresented_focus_paths:
            omitted_followups.append({
                "tool": "getReviewFileContent",
                "reason": (
                    "These changed paths have exact proposed bytes but no "
                    "structural units in the current index representation."
                ),
                "paths": unrepresented_focus_paths[:10],
            })

        snapshot = reader.snapshot()
        snapshot["baseRevision"] = generation.receipt["snapshot_metadata"][
            "base_revision"
        ]
        snapshot["sourceRevision"] = generation.receipt["snapshot_metadata"][
            "source_revision"
        ]
        return {
            "status": "ready",
            "snapshot": snapshot,
            "freshness": {
                "state": "exact_proposed_tree",
                "baseRevision": snapshot["baseRevision"],
                "sourceRevision": snapshot["sourceRevision"],
                "targetSourceTreeSha256": generation.target_source_tree_sha256,
                "overlaySha256": generation.overlay_sha256,
                "proposedSourceTreeSha256": generation.proposed_source_tree_sha256,
            },
            "changed": {
                "paths": list(generation.changed_paths),
                "focusPaths": list(focus_paths),
                "deletedPaths": list(generation.deleted_paths),
                "units": changed_units,
                "focusMatches": focus_matches,
            },
            "evidence": {
                "format": "normalized_nodes_edges",
                "nodes": graph_nodes,
                "relations": compact_relations,
                "frontier": [
                    {
                        "symbol": symbol,
                        "next": {
                            "tool": "exploreReviewContext",
                            "arguments": {
                                "question": (
                                    "Continue the current review investigation "
                                    f"through {symbol}."
                                ),
                                "focusSymbols": [symbol],
                            },
                        },
                    }
                    for symbol in frontier_symbols
                ],
                "callPaths": call_ids,
                "blastRadius": blast_ids,
                "tests": test_ids,
                "framework": framework_ids,
            },
            "sourceWindows": source_windows,
            "coverage": {
                "treeState": "exact",
                "graphState": "bounded" if partial_reasons else "complete_for_query",
                "partialReasons": partial_reasons,
                "changedFileBodiesComplete": True,
                "focusPathCount": len(focus_paths),
                "changedUnitCount": len(changed_units),
                "relationCount": len(relations),
                "sourceWindowCount": len(source_windows),
                "omittedRelationCount": int(
                    anchor_coverage.get("omittedRelations") or 0
                ),
                "omittedSymbolCount": int(
                    anchor_coverage.get("omittedSymbols") or 0
                ),
                "indexSkippedFileCount": int(
                    generation.receipt.get("skipped_file_count") or 0
                ),
                "unrepresentedFocusPaths": unrepresented_focus_paths,
            },
            "provenance": {
                "targetSourceTreeSha256": generation.target_source_tree_sha256,
                "overlaySha256": generation.overlay_sha256,
                "proposedSourceTreeSha256": generation.proposed_source_tree_sha256,
                "representationIdentity": generation.representation_identity,
                "indexRepresentationFingerprint": generation.receipt.get(
                    "index_representation_fingerprint"
                ),
                "collectionTarget": generation.collection_target,
                "cacheHit": generation.cache_hit,
            },
            "omittedFollowups": omitted_followups,
        }
