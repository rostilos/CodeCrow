"""Read-only graph algorithms for exact proposed-tree review sessions.

The weighted impact-radius and bounded traversal operations in this module are
adapted from ``tirth8205/code-review-graph`` 2.3.8 at commit
``b58668751ab0c7670c078cf7cbd4d1f5b8e54f81`` by Tirth Kanani. CodeCrow's
minimal-context selector is native and informed by the upstream compact-context
surface. All operations read CodeCrow's neutral AST and plugin facts through a
supplied graph reader; they never open a repository or graph generation on their
own.

MIT License

Copyright (c) 2026 Tirth Kanani

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

from __future__ import annotations

from collections import deque
import json
import re
from typing import Any, Mapping, Sequence


_TASK_TERM = re.compile(r"[A-Za-z_][A-Za-z0-9_:$\\.\-/]{2,}")
_STOP_TERMS = {
    "about", "after", "again", "against", "analysis", "before", "being",
    "between", "change", "changed", "changes", "could", "during", "from",
    "have", "into", "might", "please", "review", "should", "that", "their",
    "there", "these", "this", "through", "what", "when", "where", "which",
    "with", "would",
}
_IMPACT_EDGE_WEIGHTS = {
    "CALLS": 1.0,
    "EXTENDS": 0.9,
    "INHERITANCE": 0.9,
    "INHERITS": 0.9,
    "IMPLEMENTS": 0.9,
    "OVERRIDES": 0.9,
    "TESTED_BY": 0.7,
    "REFERENCES": 0.6,
    "DEPENDS_ON": 0.6,
    "IMPORTS": 0.5,
    "IMPORTS_FROM": 0.5,
    "CONTAINS": 0.3,
}
_IMPACT_EDGE_DIRECTIONS = {
    "CALLS": "incoming",
    "EXTENDS": "incoming",
    "INHERITANCE": "incoming",
    "INHERITS": "incoming",
    "IMPLEMENTS": "incoming",
    "OVERRIDES": "incoming",
    "TESTED_BY": "outgoing",
    "REFERENCES": "incoming",
    "DEPENDS_ON": "incoming",
    "IMPORTS": "incoming",
    "IMPORTS_FROM": "incoming",
    "CONTAINS": "none",
}
_IMPACT_DEFAULT_EDGE_WEIGHT = 0.5
_IMPACT_DEFAULT_EDGE_DIRECTION = "incoming"
_IMPACT_DEPTH_DECAY = 0.6
_IMPACT_SCORE_FLOOR = 0.05
_MAX_SOURCE_WINDOW_CHARACTERS = 8000
_DEFAULT_TRAVERSAL_TOKEN_BUDGET = 2000
_MIN_TRAVERSAL_TOKEN_BUDGET = 512
_MAX_RELATIONS_PER_EXPANSION = 500
_MAX_IMPACT_ROOTS = 500
_MAX_IMPACT_WORK_NODES = 5000
_MAX_IMPACT_WORK_RELATIONS = 10000
_MAX_RETURNED_FRONTIER = 25
_SOURCE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cs", ".css", ".go", ".h", ".hpp", ".html",
    ".java", ".js", ".jsx", ".kt", ".kts", ".php", ".py", ".rb", ".rs",
    ".scala", ".sql", ".swift", ".ts", ".tsx", ".vue", ".xml", ".yaml",
    ".yml",
}

_RELATION_SEMANTIC_ALIASES = {
    "CALL": "CALLS",
    "CALLS": "CALLS",
    "CALLS_INSTANCE": "CALLS",
    "CALLS_INTRA_CLASS": "CALLS",
    "CALLS_RESOLVED_TARGET": "CALLS",
    "CALLS_STATIC": "CALLS",
    "CALLS_UNIQUE_CO_DECLARED_DEFINITION": "CALLS",
    "CONSUME": "CONSUMES",
    "CONSUMES": "CONSUMES",
    "CONTAIN": "CONTAINS",
    "CONTAINS": "CONTAINS",
    "DEPEND": "DEPENDS_ON",
    "DEPENDS": "DEPENDS_ON",
    "DEPENDS_ON": "DEPENDS_ON",
    "DEPENDS_ON_CONFIG_FIELD": "DEPENDS_ON",
    "DEPENDS_ON_INDEXER": "DEPENDS_ON",
    "DISPATCH": "DISPATCHES",
    "DISPATCHES": "DISPATCHES",
    "EXTEND": "EXTENDS",
    "EXTENDS": "EXTENDS",
    "HANDLE": "HANDLES",
    "HANDLES": "HANDLES",
    "IMPLEMENT": "IMPLEMENTS",
    "IMPLEMENTS": "IMPLEMENTS",
    "IMPORT": "IMPORTS",
    "IMPORTS": "IMPORTS",
    "IMPORTS_FROM": "IMPORTS_FROM",
    "INHERIT": "INHERITS",
    "INHERITANCE": "INHERITANCE",
    "INHERITS": "INHERITS",
    "LISTEN": "LISTENS",
    "LISTENS": "LISTENS",
    "OVERRIDE": "OVERRIDES",
    "OVERRIDES": "OVERRIDES",
    "PRODUCE": "PRODUCES",
    "PRODUCES": "PRODUCES",
    "PUBLISH": "PUBLISHES",
    "PUBLISHES": "PUBLISHES",
    "REFERENCE": "REFERENCES",
    "REFERENCES": "REFERENCES",
    "REFERENCES_DECLARED_FIELD": "REFERENCES",
    "REFERENCES_JSON_SCHEMA_TARGET": "REFERENCES",
    "RESOLVES_IMPORT": "IMPORTS",
    "RUNS_ON": "RUNS_ON",
    "TESTED_BY": "TESTED_BY",
    "TESTS": "TESTS",
    "TRIGGER": "TRIGGERS",
    "TRIGGERS": "TRIGGERS",
    "USES": "REFERENCES",
}


def _normalized_relation_token(value: Any) -> str:
    return str(value or "").strip().upper().replace("-", "_").replace(" ", "_")


def _canonical_relation_name(value: Any) -> str:
    normalized = _normalized_relation_token(value)
    return _RELATION_SEMANTIC_ALIASES.get(normalized, normalized)


def _canonical_relation_semantic(relation: Mapping[str, Any]) -> str:
    """Return a recognized semantic without discarding plugin provenance kinds."""

    normalized_kind = _normalized_relation_token(relation.get("kind"))
    normalized_relation = _normalized_relation_token(relation.get("relation"))
    for candidate in (normalized_kind, normalized_relation):
        semantic = _RELATION_SEMANTIC_ALIASES.get(candidate)
        if semantic:
            return semantic
    return normalized_kind or normalized_relation


def _unit_id(unit: Mapping[str, Any] | None) -> str:
    return str(unit.get("unitId") or "") if isinstance(unit, Mapping) else ""


def _unit_key(unit: Mapping[str, Any]) -> str:
    return _unit_id(unit) or "|".join((
        str(unit.get("path") or ""),
        str(unit.get("qualifiedName") or unit.get("name") or ""),
        str(unit.get("startLine") or ""),
    ))


def _compact_unit(
    unit: Mapping[str, Any],
    *,
    detail_level: str,
    depth: int | None = None,
    source_evidence_id: str | None = None,
) -> dict[str, Any]:
    fields = ("unitId", "path", "kind", "name")
    if detail_level == "standard":
        fields += (
            "qualifiedName", "startLine", "endLine", "language", "recordType",
        )
    result = {
        field: unit.get(field)
        for field in fields
        if unit.get(field) is not None and unit.get(field) != ""
    }
    if depth is not None:
        result["depth"] = depth
    if source_evidence_id:
        result["sourceEvidenceId"] = source_evidence_id
    return result


def _compact_relation(
    relation: Mapping[str, Any],
    *,
    detail_level: str,
    depth: int | None = None,
) -> dict[str, Any]:
    source_unit = relation.get("sourceUnit")
    target_unit = relation.get("targetUnit")
    result: dict[str, Any] = {
        "evidenceId": relation.get("evidenceId"),
        "kind": relation.get("kind"),
        "source": relation.get("source"),
        "target": relation.get("target"),
        "sourceUnitId": _unit_id(source_unit),
        "targetUnitId": _unit_id(target_unit),
    }
    if detail_level == "standard":
        result.update({
            "relation": relation.get("relation"),
            "origin": dict(relation.get("origin") or {}),
            "relatedPaths": list(relation.get("relatedPaths") or ()),
        })
        attributes = relation.get("attributes")
        if isinstance(attributes, Mapping) and attributes:
            result["attributes"] = dict(attributes)
    if depth is not None:
        result["depth"] = depth
    return {
        key: value
        for key, value in result.items()
        if value is not None and value != "" and value != [] and value != {}
    }


def _source_detail(reader, unit: Mapping[str, Any]) -> Mapping[str, Any] | None:
    resolver = getattr(reader, "source_detail_for_manifest", None)
    if callable(resolver):
        detail = resolver(unit)
    else:
        unit_id = _unit_id(unit)
        detail = reader.get_unit(unit_id) if unit_id else None
    return detail if isinstance(detail, Mapping) and detail.get("sourceEvidence") else None


def _bounded_source_windows(
    reader,
    units: Sequence[Mapping[str, Any]],
    relations: Sequence[Mapping[str, Any]],
    *,
    changed_paths: Sequence[str],
    max_source_windows: int,
    max_source_characters: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    changed = set(changed_paths)
    relation_ids_by_unit: dict[str, list[str]] = {}
    for relation in relations:
        evidence_id = str(relation.get("evidenceId") or "")
        if not evidence_id:
            continue
        for endpoint in (relation.get("sourceUnit"), relation.get("targetUnit")):
            endpoint_id = _unit_id(endpoint)
            if endpoint_id:
                relation_ids_by_unit.setdefault(endpoint_id, []).append(evidence_id)

    windows: list[dict[str, Any]] = []
    window_by_source_id: dict[str, dict[str, Any]] = {}
    available_source_ids: set[str] = set()
    omitted_source_ids: list[str] = []
    remaining = max_source_characters
    # Stage 1 already contains bounded current source for the host-owned changed
    # paths. Prefer related unchanged units so an explicit includeSource request
    # fills a real cross-file context gap before duplicating the prompt.
    ordered_candidates = [
        candidate
        for _index, candidate in sorted(
            enumerate(units),
            key=lambda item: (
                str(item[1].get("path") or "") in changed,
                item[0],
            ),
        )
    ]
    for candidate in ordered_candidates:
        detail = _source_detail(reader, candidate)
        source = detail.get("unit") if isinstance(detail, Mapping) else None
        if not isinstance(source, Mapping):
            continue
        source_id = _unit_id(source)
        if not source_id:
            continue
        content = source.get("content")
        if not isinstance(content, str) or not content:
            continue
        available_source_ids.add(source_id)
        candidate_id = _unit_id(candidate)
        relation_ids = [
            *relation_ids_by_unit.get(candidate_id, ()),
            *relation_ids_by_unit.get(source_id, ()),
        ]
        existing_window = window_by_source_id.get(source_id)
        if existing_window is not None:
            existing_window["selectedUnitIds"] = list(dict.fromkeys(
                unit_id
                for unit_id in (
                    *existing_window.get("selectedUnitIds", ()),
                    candidate_id,
                    source_id,
                )
                if unit_id
            ))
            existing_window["relationEvidenceIds"] = list(dict.fromkeys((
                *existing_window.get("relationEvidenceIds", ()),
                *relation_ids,
            )))
            continue
        if len(windows) >= max_source_windows or remaining <= 0:
            omitted_source_ids.append(source_id)
            continue
        ceiling = min(remaining, _MAX_SOURCE_WINDOW_CHARACTERS)
        selected = content[:ceiling]
        truncated = len(content) > ceiling
        if truncated and "\n" in selected:
            selected = selected.rsplit("\n", 1)[0] + "\n"
        if not selected:
            continue
        remaining -= len(selected)
        start_line = max(1, int(source.get("startLine") or 1))
        end_line = start_line + selected.count("\n")
        if not selected.endswith("\n"):
            end_line += 1
        path = str(source.get("path") or "")
        window = {
            "evidenceId": "source:" + source_id,
            "unitId": source_id,
            "selectedUnitIds": list(dict.fromkeys(
                unit_id for unit_id in (candidate_id, source_id) if unit_id
            )),
            "path": path,
            "startLine": start_line,
            "endLine": max(start_line, end_line - 1),
            "content": selected,
            "contentSha256": source.get("contentSha256"),
            "changedFile": path in changed,
            "truncated": truncated,
            "relationEvidenceIds": list(dict.fromkeys(relation_ids)),
        }
        windows.append(window)
        window_by_source_id[source_id] = window
    truncated_windows = sum(bool(window["truncated"]) for window in windows)
    omitted_unique = list(dict.fromkeys(omitted_source_ids))
    bounded = bool(omitted_unique or truncated_windows)
    return windows, {
        "state": "bounded" if bounded else "complete",
        "truncated": bounded,
        "availableSourceUnits": len(available_source_ids),
        "returnedSourceUnits": len(windows),
        "omittedSourceUnits": len(omitted_unique),
        "truncatedSourceWindows": truncated_windows,
        "omittedUnitIds": omitted_unique[:20],
        "maxSourceWindows": max_source_windows,
        "maxSourceCharacters": max_source_characters,
        "returnedSourceCharacters": sum(
            len(str(window.get("content") or "")) for window in windows
        ),
    }


def _source_evidence_by_unit(
    windows: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    evidence_by_unit: dict[str, str] = {}
    for window in windows:
        evidence_id = str(window.get("evidenceId") or "")
        if not evidence_id:
            continue
        for unit_id in (
            window.get("unitId"),
            *(window.get("selectedUnitIds") or ()),
        ):
            normalized = str(unit_id or "")
            if normalized:
                evidence_by_unit[normalized] = evidence_id
    return evidence_by_unit


def _json_char_length(value: Any) -> int:
    return len(json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ))


def _looks_like_path(target: str) -> bool:
    final = target.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    suffix = "." + final.rsplit(".", 1)[-1].casefold() if "." in final else ""
    return "/" in target or "\\" in target or suffix in _SOURCE_SUFFIXES


def _query_page(
    reader,
    pattern: str,
    target: str,
    *,
    max_results: int,
    cursor: int = 0,
) -> dict[str, Any]:
    """Read one deterministic page while retaining compatibility test readers."""

    try:
        return reader.query_graph(
            pattern,
            target,
            max_results=max_results,
            cursor=cursor,
        )
    except TypeError as exception:
        if cursor or "cursor" not in str(exception):
            raise
        return reader.query_graph(pattern, target, max_results=max_results)


def _relation_pages(
    reader,
    target: str,
    *,
    max_results: int,
) -> tuple[list[dict[str, Any]], bool, int | None]:
    """Read a bounded relation branch and expose a resumable cursor."""

    relations: list[dict[str, Any]] = []
    seen: set[str] = set()
    cursor = 0
    next_cursor: int | None = None
    while len(relations) < max_results:
        page_size = min(100, max_results - len(relations))
        response = _query_page(
            reader,
            "relations_of",
            target,
            max_results=page_size,
            cursor=cursor,
        )
        page = [
            dict(relation)
            for relation in response.get("results") or ()
            if isinstance(relation, Mapping)
        ]
        for relation in page:
            evidence_id = str(relation.get("evidenceId") or "")
            key = evidence_id or json.dumps(
                relation,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            if key in seen:
                continue
            seen.add(key)
            relations.append(relation)
        truncated = bool(response.get("truncated"))
        if not truncated:
            return relations, False, None
        proposed_cursor = response.get("nextCursor")
        try:
            proposed_cursor = int(proposed_cursor)
        except (TypeError, ValueError):
            proposed_cursor = cursor + len(page)
        if not page or proposed_cursor <= cursor:
            return relations, True, cursor or None
        cursor = proposed_cursor
        next_cursor = cursor
    return relations, bool(next_cursor), next_cursor


def _resolve_targets(
    reader,
    targets: Sequence[str],
    *,
    max_results: int,
) -> tuple[
    list[dict[str, Any]],
    list[str],
    bool,
    list[dict[str, Any]],
]:
    units: list[dict[str, Any]] = []
    unresolved: list[str] = []
    ambiguous: list[dict[str, Any]] = []
    seen: set[str] = set()
    truncated = False
    for raw_target in targets:
        target = str(raw_target or "").strip()
        if not target:
            continue
        remaining = max_results - len(units)
        if remaining <= 0:
            truncated = True
            break
        candidates: list[Mapping[str, Any]] = []
        detail = reader.get_unit(target)
        if isinstance(detail, Mapping) and isinstance(detail.get("unit"), Mapping):
            candidates.append(detail["unit"])
        if not candidates:
            pattern = "file_summary" if _looks_like_path(target) else "symbol_search"
            resolved_exactly = False
            if pattern == "symbol_search":
                # Named relation queries use the reader's conservative target
                # resolver. Probe it before fuzzy symbol search so traversal
                # and impact never silently choose one of several symbols (or
                # widen a single symbolic root into several unrelated roots).
                resolution = _query_page(
                    reader,
                    "relations_of",
                    target,
                    max_results=1,
                )
                if resolution.get("status") == "ambiguous":
                    ambiguous.append({
                        "target": target,
                        "error": resolution.get("error"),
                        "candidates": list(resolution.get("candidates") or ()),
                        "candidateCount": resolution.get(
                            "candidateCount",
                            len(resolution.get("candidates") or ()),
                        ),
                        "candidatesTruncated": bool(
                            resolution.get("candidatesTruncated")
                            or resolution.get("truncated")
                        ),
                        "cursor": int(resolution.get("cursor") or 0),
                        "nextCursor": resolution.get("nextCursor"),
                        **(
                            {"hint": resolution.get("hint")}
                            if resolution.get("hint")
                            else {}
                        ),
                    })
                    continue
                resolved_units = [
                    item
                    for item in resolution.get("resolvedUnits") or ()
                    if isinstance(item, Mapping)
                ]
                if resolved_units:
                    truncated = truncated or len(resolved_units) > remaining
                    candidates.extend(resolved_units[:remaining])
                    resolved_exactly = True
            cursor = 0
            while remaining > 0 and not resolved_exactly:
                page_size = min(100, remaining)
                response = _query_page(
                    reader,
                    pattern,
                    target,
                    max_results=page_size,
                    cursor=cursor,
                )
                page = [
                    item
                    for item in response.get("results") or ()
                    if isinstance(item, Mapping)
                ]
                candidates.extend(page)
                remaining -= len(page)
                page_truncated = bool(response.get("truncated"))
                if not page_truncated or pattern != "file_summary" or not page:
                    truncated = truncated or page_truncated
                    break
                proposed_cursor = response.get("nextCursor")
                try:
                    proposed_cursor = int(proposed_cursor)
                except (TypeError, ValueError):
                    proposed_cursor = cursor + len(page)
                if proposed_cursor <= cursor:
                    truncated = True
                    break
                cursor = proposed_cursor
            if (
                not resolved_exactly
                and remaining <= 0
                and bool(response.get("truncated"))
            ):
                truncated = True
            if not candidates and pattern == "file_summary":
                response = _query_page(
                    reader,
                    "symbol_search",
                    target,
                    max_results=min(100, max(1, remaining)),
                )
                candidates.extend(
                    item
                    for item in response.get("results") or ()
                    if isinstance(item, Mapping)
                )
                truncated = truncated or bool(response.get("truncated"))
        matched = False
        for candidate_index, candidate in enumerate(candidates):
            key = _unit_key(candidate)
            if not key or key in seen:
                continue
            matched = True
            seen.add(key)
            units.append(dict(candidate))
            if len(units) >= max_results:
                remaining_keys = {
                    _unit_key(remaining_candidate)
                    for remaining_candidate in candidates[candidate_index + 1:]
                    if _unit_key(remaining_candidate)
                }
                truncated = truncated or bool(remaining_keys.difference(seen))
                break
        if not matched:
            unresolved.append(target)
    return units, unresolved, truncated, ambiguous


def _ambiguous_target_response(
    reader,
    *,
    operation: str,
    targets: Sequence[str],
    ambiguous: Sequence[Mapping[str, Any]],
    include_source: bool = False,
) -> dict[str, Any]:
    values = [dict(value) for value in ambiguous]
    response: dict[str, Any] = {
        "status": "ambiguous",
        "operation": operation,
        "error": "A symbolic graph root resolves to multiple structural units",
        "snapshot": reader.snapshot(),
        "targets": list(targets),
        "ambiguousTargets": values,
        "results": [],
        "sourceWindows": [],
        "coverage": {
            "state": "bounded",
            "truncated": True,
            "partialReasons": ["ambiguous_target"],
            "returnedResults": 0,
            "sourceIncluded": include_source,
            "source": {
                "state": "unavailable" if include_source else "not_requested",
                "truncated": False,
                "availableSourceUnits": 0,
                "returnedSourceUnits": 0,
                "omittedSourceUnits": 0,
                "truncatedSourceWindows": 0,
            },
        },
    }
    if len(values) == 1:
        response.update({
            "target": values[0].get("target"),
            "candidates": values[0].get("candidates", []),
            "candidateCount": values[0].get("candidateCount", 0),
            "candidatesTruncated": values[0].get(
                "candidatesTruncated",
                False,
            ),
            "cursor": int(values[0].get("cursor") or 0),
            "nextCursor": values[0].get("nextCursor"),
        })
        if values[0].get("hint"):
            response["hint"] = values[0]["hint"]
    return response


def _relation_neighbors(
    relation: Mapping[str, Any],
    current_unit_id: str,
    *,
    direction: str,
) -> list[dict[str, Any]]:
    source = relation.get("sourceUnit")
    target = relation.get("targetUnit")
    source_id = _unit_id(source)
    target_id = _unit_id(target)
    neighbors: list[dict[str, Any]] = []
    if (
        direction in {"outgoing", "both"}
        and source_id == current_unit_id
        and isinstance(target, Mapping)
        and target_id
    ):
        neighbors.append(dict(target))
    if (
        direction in {"incoming", "both"}
        and target_id == current_unit_id
        and isinstance(source, Mapping)
        and source_id
    ):
        neighbors.append(dict(source))
    return neighbors


def _impact_policy(relation: Mapping[str, Any]) -> tuple[float, str]:
    kind = _canonical_relation_semantic(relation)
    return (
        _IMPACT_EDGE_WEIGHTS.get(kind, _IMPACT_DEFAULT_EDGE_WEIGHT),
        _IMPACT_EDGE_DIRECTIONS.get(kind, _IMPACT_DEFAULT_EDGE_DIRECTION),
    )


def _walk(
    reader,
    roots: Sequence[Mapping[str, Any]],
    *,
    strategy: str,
    direction: str,
    relation_kinds: Sequence[str],
    max_depth: int,
    max_results: int,
) -> dict[str, Any]:
    pending: deque[tuple[dict[str, Any], int]] = deque(
        (dict(unit), 0) for unit in roots if _unit_id(unit)
    )
    visited: dict[str, tuple[dict[str, Any], int]] = {}
    relations: dict[str, tuple[dict[str, Any], int]] = {}
    frontier: dict[str, tuple[dict[str, Any], int, str, int | None]] = {}
    requested_kinds = {
        _canonical_relation_name(kind)
        for kind in relation_kinds
        if str(kind).strip()
    }
    partial_reasons: list[str] = []
    depth_reached = 0
    max_relation_results = min(
        _MAX_RELATIONS_PER_EXPANSION,
        max(100, max_results * 4),
    )

    while pending:
        unit, depth = pending.popleft() if strategy == "bfs" else pending.pop()
        current_id = _unit_id(unit)
        if not current_id or current_id in visited:
            continue
        if len(visited) >= max_results:
            frontier[current_id] = (unit, depth, "result_limit", None)
            partial_reasons.append("result_limit")
            continue
        visited[current_id] = (unit, depth)
        depth_reached = max(depth_reached, depth)
        if depth >= max_depth:
            frontier[current_id] = (unit, depth, "depth_limit", None)
            if depth == max_depth:
                partial_reasons.append("depth_limit")
            continue

        branch_relations, branch_truncated, next_cursor = _relation_pages(
            reader,
            current_id,
            max_results=max_relation_results,
        )
        if branch_truncated:
            frontier[current_id] = (
                unit,
                depth,
                "branch_result_limit",
                next_cursor,
            )
            partial_reasons.append("branch_result_limit")
        candidates: list[tuple[dict[str, Any], int]] = []
        for relation_value in branch_relations:
            if not isinstance(relation_value, Mapping):
                continue
            relation = dict(relation_value)
            relation_kind = _canonical_relation_semantic(relation)
            relation_filter_names = {
                relation_kind,
                _normalized_relation_token(relation.get("kind")),
                _normalized_relation_token(relation.get("relation")),
            }
            if requested_kinds and requested_kinds.isdisjoint(relation_filter_names):
                continue
            neighbors = _relation_neighbors(
                relation,
                current_id,
                direction=direction,
            )
            if not neighbors:
                continue
            evidence_id = str(relation.get("evidenceId") or "")
            if evidence_id and evidence_id not in relations:
                if len(relations) >= max_relation_results:
                    frontier[current_id] = (
                        unit,
                        depth,
                        "relation_result_limit",
                        None,
                    )
                    partial_reasons.append("relation_result_limit")
                    break
                relations[evidence_id] = (relation, depth + 1)
            for neighbor in neighbors:
                neighbor_id = _unit_id(neighbor)
                if neighbor_id and neighbor_id not in visited:
                    candidates.append((neighbor, depth + 1))
        if strategy == "dfs":
            candidates.reverse()
        pending.extend(candidates)

    return {
        "visited": visited,
        "relations": relations,
        "frontier": frontier,
        "depthReached": depth_reached,
        "partialReasons": list(dict.fromkeys(partial_reasons)),
    }


def minimal_review_context(
    reader,
    *,
    question: str,
    focus_paths: Sequence[str],
    focus_symbols: Sequence[str] = (),
    changed_paths: Sequence[str] = (),
    max_relations: int = 25,
    detail_level: str = "minimal",
    include_source: bool = False,
    max_source_windows: int = 4,
    max_source_characters: int = 8000,
) -> dict[str, Any]:
    """Return a small starting graph without embedding unrequested source."""

    # Reserve distinct budgets for changed-path anchors, their direct
    # neighborhood, and a second hop. Letting the path lookup consume the
    # entire response made the advertised continuation depth unreachable on
    # relation-dense files.
    anchor_limit = max(1, max_relations // 3)
    direct_limit = max(anchor_limit, max_relations * 2 // 3)
    anchors = reader.relations_for_paths(
        focus_paths,
        max_relations=anchor_limit,
    )
    units: dict[str, dict[str, Any]] = {}
    for anchor in anchors.get("anchors") or ():
        for unit in anchor.get("symbols") or ():
            if isinstance(unit, Mapping) and _unit_key(unit):
                units.setdefault(_unit_key(unit), dict(unit))
    relations: dict[str, dict[str, Any]] = {
        str(relation.get("evidenceId")): dict(relation)
        for relation in anchors.get("relations") or ()
        if isinstance(relation, Mapping) and relation.get("evidenceId")
    }
    relation_depths: dict[str, int] = {
        evidence_id: 0 for evidence_id in relations
    }
    graph_query_truncated = False
    continuations: list[dict[str, Any]] = []

    task_terms = [
        term
        for term in dict.fromkeys(
            match.group(0) for match in _TASK_TERM.finditer(question)
        )
        if term.casefold() not in _STOP_TERMS
    ][:8]
    search_targets = list(dict.fromkeys((*focus_symbols, *task_terms)))
    for target in search_targets:
        if len(units) >= max_relations:
            break
        for unit in reader.search_units(
            target,
            max_results=min(5, max_relations - len(units)),
        ):
            key = _unit_key(unit)
            if key:
                units.setdefault(key, dict(unit))

    frontier = list(units.values())
    visited_units: set[str] = set()
    depth_reached = 0
    for depth, relation_limit in ((1, direct_limit), (2, max_relations)):
        if not frontier or len(relations) >= relation_limit:
            continue
        next_frontier: dict[str, dict[str, Any]] = {}
        eligible_frontier = [
            unit for unit in frontier
            if _unit_id(unit) and _unit_id(unit) not in visited_units
        ]
        for index, unit in enumerate(eligible_frontier):
            if len(relations) >= relation_limit:
                break
            unit_id = _unit_id(unit)
            visited_units.add(unit_id)
            remaining = relation_limit - len(relations)
            remaining_roots = max(1, len(eligible_frontier) - index)
            # Round-robin capacity across roots before any root receives the
            # remaining dense tail.
            per_root_limit = max(1, (remaining + remaining_roots - 1) // remaining_roots)
            response = _query_page(
                reader,
                "relations_of",
                unit_id,
                max_results=min(100, per_root_limit),
            )
            if response.get("truncated"):
                graph_query_truncated = True
                next_cursor = response.get("nextCursor")
                if next_cursor is None:
                    next_cursor = len(response.get("results") or ())
                continuations.append({
                    "tool": "queryCodeGraph",
                    "arguments": {
                        "pattern": "relations_of",
                        "target": unit_id,
                        "cursor": next_cursor,
                        "maxResults": min(100, max_relations),
                        "detailLevel": detail_level,
                    },
                })
            for relation in response.get("results") or ():
                if (
                    not isinstance(relation, Mapping)
                    or not relation.get("evidenceId")
                ):
                    continue
                evidence_id = str(relation["evidenceId"])
                if evidence_id not in relations:
                    if len(relations) >= relation_limit:
                        break
                    relations[evidence_id] = dict(relation)
                    relation_depths[evidence_id] = depth
                    depth_reached = max(depth_reached, depth)
                else:
                    relation_depths[evidence_id] = min(
                        relation_depths.get(evidence_id, depth),
                        depth,
                    )
                for endpoint in (
                    relation.get("sourceUnit"),
                    relation.get("targetUnit"),
                ):
                    if not isinstance(endpoint, Mapping):
                        continue
                    key = _unit_key(endpoint)
                    if key and len(units) < max_relations * 2:
                        units.setdefault(key, dict(endpoint))
                    endpoint_id = _unit_id(endpoint)
                    if endpoint_id and endpoint_id not in visited_units:
                        next_frontier.setdefault(endpoint_id, dict(endpoint))
        frontier = list(next_frontier.values())

    unit_values = list(units.values())[:max_relations]
    relation_values = list(relations.values())[:max_relations]
    windows, source_coverage = (
        _bounded_source_windows(
            reader,
            unit_values,
            relation_values,
            changed_paths=changed_paths,
            max_source_windows=max_source_windows,
            max_source_characters=max_source_characters,
        )
        if include_source
        else ([], {
            "state": "not_requested",
            "truncated": False,
            "availableSourceUnits": 0,
            "returnedSourceUnits": 0,
            "omittedSourceUnits": 0,
            "truncatedSourceWindows": 0,
        })
    )
    source_by_unit = _source_evidence_by_unit(windows)
    anchor_coverage = anchors.get("coverage") or {}
    partial_reasons: list[str] = []
    if (
        anchor_coverage.get("omittedRelations")
        or anchor_coverage.get("omittedSymbols")
    ):
        partial_reasons.append("focus_relation_limit")
    if (
        graph_query_truncated
        or len(units) > max_relations
        or (len(relations) >= max_relations and bool(frontier))
    ):
        partial_reasons.append("graph_relation_limit")
    if source_coverage.get("truncated"):
        partial_reasons.append("source_window_limit")
    partial_reasons = list(dict.fromkeys(partial_reasons))
    truncated = bool(partial_reasons)
    return {
        "status": "ready",
        "operation": "minimal_review_context",
        "snapshot": reader.snapshot(),
        "question": question,
        "focusPaths": list(focus_paths),
        "focusSymbols": list(focus_symbols),
        "summary": (
            f"{len(unit_values)} structural unit(s) and "
            f"{len(relation_values)} relation(s) selected for the review task."
        ),
        "nodes": [
            _compact_unit(
                unit,
                detail_level=detail_level,
                source_evidence_id=source_by_unit.get(_unit_id(unit)),
            )
            for unit in unit_values
        ],
        "edges": [
            _compact_relation(
                relation,
                detail_level=detail_level,
                depth=relation_depths.get(
                    str(relation.get("evidenceId") or ""),
                    0,
                ),
            )
            for relation in relation_values
        ],
        "sourceWindows": windows,
        "coverage": {
            "state": "bounded" if truncated else "complete",
            "truncated": truncated,
            "returnedNodes": len(unit_values),
            "returnedRelations": len(relation_values),
            "depthReached": depth_reached,
            "relationsByDepth": {
                str(depth): sum(
                    1
                    for relation in relation_values
                    if relation_depths.get(
                        str(relation.get("evidenceId") or ""),
                        0,
                    ) == depth
                )
                for depth in sorted(set(relation_depths.values()))
            },
            "sourceIncluded": include_source,
            "partialReasons": partial_reasons,
            "source": source_coverage,
        },
        "nextOperations": [
            "queryCodeGraph", "getImpactRadius", "traverseCodeGraph",
            "getStructuralUnit",
        ],
        "continuations": continuations[:10],
    }


def _weighted_impact(
    reader,
    roots: Sequence[Mapping[str, Any]],
    *,
    max_depth: int,
    max_results: int,
) -> dict[str, Any]:
    """Run code-review-graph's bounded best-score relaxation through a reader.

    Upstream executes the relaxation in SQLite (or NetworkX). CodeCrow instead
    asks its exact proposed-generation reader for each frontier node so proposed-file facts
    shadow stale base facts throughout the same scoring algorithm.
    """

    roots_by_id = {
        _unit_id(unit): dict(unit)
        for unit in roots
        if _unit_id(unit)
    }
    best: dict[str, dict[str, Any]] = {
        unit_id: {
            "unit": unit,
            "score": 1.0,
            "depth": 0,
            "connection": None,
        }
        for unit_id, unit in roots_by_id.items()
    }
    frontier = dict(best)
    bounded_frontier: dict[
        str,
        tuple[dict[str, Any], int, str, int | None],
    ] = {}
    partial_reasons: list[str] = []
    discovered_ids = set(roots_by_id)
    traversed_relations: dict[str, dict[str, Any]] = {}
    depth_reached = 0
    max_work_nodes = min(
        _MAX_IMPACT_WORK_NODES,
        max(500, max_results * 20),
    )
    max_branch_relations = min(
        _MAX_RELATIONS_PER_EXPANSION,
        max(100, max_results * 4),
    )

    for depth in range(1, max_depth + 1):
        if not frontier:
            break
        candidates: dict[str, dict[str, Any]] = {}
        for current_id in sorted(frontier):
            current = frontier[current_id]
            branch_relations, branch_truncated, next_cursor = _relation_pages(
                reader,
                current_id,
                max_results=max_branch_relations,
            )
            if branch_truncated:
                bounded_frontier[current_id] = (
                    current["unit"],
                    int(current["depth"]),
                    "branch_result_limit",
                    next_cursor,
                )
                partial_reasons.append("branch_result_limit")
            for raw_relation in branch_relations:
                if not isinstance(raw_relation, Mapping):
                    continue
                relation = dict(raw_relation)
                evidence_id = str(relation.get("evidenceId") or "")
                if evidence_id and evidence_id not in traversed_relations:
                    if len(traversed_relations) < _MAX_IMPACT_WORK_RELATIONS:
                        traversed_relations[evidence_id] = relation
                    else:
                        partial_reasons.append("relation_work_limit")
                weight, direction = _impact_policy(relation)
                if direction == "none":
                    continue
                for neighbor in _relation_neighbors(
                    relation,
                    current_id,
                    direction=direction,
                ):
                    neighbor_id = _unit_id(neighbor)
                    if not neighbor_id or neighbor_id in roots_by_id:
                        continue
                    score = float(current["score"]) * weight * _IMPACT_DEPTH_DECAY
                    if score <= _IMPACT_SCORE_FLOOR:
                        continue
                    discovered_ids.add(neighbor_id)
                    connection = {
                        "unitId": neighbor_id,
                        "fromUnitId": current_id,
                        "evidenceId": relation.get("evidenceId"),
                        "kind": relation.get("kind"),
                        "direction": direction,
                        "edgeWeight": weight,
                        "depthDecay": _IMPACT_DEPTH_DECAY,
                        "depth": depth,
                        "impactScore": round(score, 4),
                        "relation": relation,
                    }
                    candidate = {
                        "unit": neighbor,
                        "score": score,
                        "depth": depth,
                        "connection": connection,
                    }
                    existing = candidates.get(neighbor_id) or best.get(neighbor_id)
                    if existing is None or score > float(existing["score"]):
                        candidates[neighbor_id] = candidate
                    elif score == float(existing["score"]):
                        existing_id = str(
                            (existing.get("connection") or {}).get("evidenceId") or ""
                        )
                        candidate_id = str(connection.get("evidenceId") or "")
                        if candidate_id and (not existing_id or candidate_id < existing_id):
                            candidates[neighbor_id] = candidate

        improved = {
            unit_id: candidate
            for unit_id, candidate in candidates.items()
            if float(candidate["score"]) > float(best.get(unit_id, {}).get("score", 0.0))
        }
        if not improved:
            frontier = {}
            break
        best.update(improved)
        ranked_impacted = sorted(
            (
                (unit_id, state)
                for unit_id, state in best.items()
                if unit_id not in roots_by_id
            ),
            key=lambda item: (
                -float(item[1]["score"]),
                str(item[1]["unit"].get("qualifiedName") or item[0]),
            ),
        )
        kept_ids = {
            unit_id for unit_id, _state in ranked_impacted[:max_work_nodes]
        }
        if len(ranked_impacted) > max_work_nodes:
            partial_reasons.append("work_node_limit")
            for unit_id, state in ranked_impacted[max_work_nodes:]:
                bounded_frontier[unit_id] = (
                    state["unit"],
                    int(state["depth"]),
                    "work_node_limit",
                    None,
                )
                best.pop(unit_id, None)
        frontier = {
            unit_id: state
            for unit_id, state in improved.items()
            if unit_id in kept_ids
        }
        depth_reached = depth

    if frontier and depth_reached >= max_depth:
        partial_reasons.append("depth_limit")
        for unit_id, state in frontier.items():
            bounded_frontier.setdefault(
                unit_id,
                (state["unit"], int(state["depth"]), "depth_limit", None),
            )

    ranked_impacted = sorted(
        (
            (unit_id, state)
            for unit_id, state in best.items()
            if unit_id not in roots_by_id
        ),
        key=lambda item: (
            -float(item[1]["score"]),
            str(item[1]["unit"].get("qualifiedName") or item[0]),
        ),
    )
    if len(ranked_impacted) > max_results:
        partial_reasons.append("result_limit")
        for unit_id, state in ranked_impacted[max_results:]:
            bounded_frontier.setdefault(
                unit_id,
                (state["unit"], int(state["depth"]), "result_limit", None),
            )
    impacted = ranked_impacted[:max_results]
    return {
        "roots": roots_by_id,
        "impacted": impacted,
        # Internal response-assembly state. Keeping the winning predecessor
        # chain lets the public envelope include every root and edge referenced
        # by a returned impact result without changing the upstream score cap.
        "states": best,
        "frontier": bounded_frontier,
        "partialReasons": list(dict.fromkeys(partial_reasons)),
        "depthReached": depth_reached,
        "totalDiscovered": len(discovered_ids - set(roots_by_id)),
        "totalScored": len(ranked_impacted),
        "relations": traversed_relations,
        "maxWorkNodes": max_work_nodes,
    }


def _impact_root_ids(
    impact: Mapping[str, Any],
) -> tuple[set[str], bool]:
    """Return the root closure required by the selected impact paths."""

    roots = impact.get("roots") or {}
    states = impact.get("states") or {}
    required: set[str] = set()
    incomplete = False
    for unit_id, _state in impact.get("impacted") or ():
        current_id = str(unit_id or "")
        seen: set[str] = set()
        while current_id and current_id not in seen:
            seen.add(current_id)
            if current_id in roots:
                required.add(current_id)
                break
            current = states.get(current_id)
            connection = (
                current.get("connection")
                if isinstance(current, Mapping)
                else None
            )
            parent_id = str(
                connection.get("fromUnitId") or ""
                if isinstance(connection, Mapping)
                else ""
            )
            if not parent_id:
                incomplete = True
                break
            current_id = parent_id
        else:
            if current_id:
                incomplete = True
    return required, incomplete


def _induced_impact_relations(
    reader,
    *,
    unit_ids: set[str],
    required_relations: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], bool, int]:
    """Collect a bounded induced subgraph, prioritizing winning path edges.

    The pinned upstream implementation issues one store-level induced-edge
    query after ranking. Readers may expose the equivalent optimized method;
    the compatibility fallback pages every retained node and applies the same
    endpoint predicate.
    """

    by_id: dict[str, dict[str, Any]] = {}
    required_ids: list[str] = []
    for relation_value in required_relations:
        relation = dict(relation_value)
        source_id = _unit_id(relation.get("sourceUnit"))
        target_id = _unit_id(relation.get("targetUnit"))
        evidence_id = str(relation.get("evidenceId") or "")
        if (
            evidence_id
            and source_id in unit_ids
            and target_id in unit_ids
        ):
            by_id.setdefault(evidence_id, relation)
            required_ids.append(evidence_id)

    truncated = False
    scanned_ids: set[str] = set(by_id)
    optimized = getattr(reader, "relations_among", None)
    if callable(optimized):
        response = optimized(
            sorted(unit_ids),
            max_results=_MAX_IMPACT_WORK_RELATIONS,
        )
        candidates = response.get("results") or ()
        truncated = bool(response.get("truncated"))
    else:
        candidates = []
        for unit_id in sorted(unit_ids):
            branch, branch_truncated, _next_cursor = _relation_pages(
                reader,
                unit_id,
                max_results=_MAX_RELATIONS_PER_EXPANSION,
            )
            truncated = truncated or branch_truncated
            candidates.extend(branch)
            for relation in branch:
                evidence_id = str(relation.get("evidenceId") or "")
                if evidence_id:
                    scanned_ids.add(evidence_id)
            if len(scanned_ids) >= _MAX_IMPACT_WORK_RELATIONS:
                truncated = True
                break

    for relation_value in candidates:
        if not isinstance(relation_value, Mapping):
            continue
        relation = dict(relation_value)
        evidence_id = str(relation.get("evidenceId") or "")
        if not evidence_id or evidence_id in by_id:
            continue
        source_id = _unit_id(relation.get("sourceUnit"))
        target_id = _unit_id(relation.get("targetUnit"))
        if source_id not in unit_ids or target_id not in unit_ids:
            continue
        if len(by_id) >= _MAX_IMPACT_WORK_RELATIONS:
            truncated = True
            break
        by_id[evidence_id] = relation

    ordered_ids = list(dict.fromkeys(required_ids))
    alternate_ids = sorted(set(by_id).difference(ordered_ids))
    return (
        [by_id[evidence_id] for evidence_id in (*ordered_ids, *alternate_ids)],
        truncated,
        len(set(required_ids)),
    )


def review_impact_radius(
    reader,
    *,
    targets: Sequence[str],
    changed_paths: Sequence[str] = (),
    max_depth: int = 2,
    max_results: int = 100,
    detail_level: str = "standard",
    include_source: bool = False,
    max_source_windows: int = 6,
    max_source_characters: int = 12000,
) -> dict[str, Any]:
    """Find bounded dependents and test relations from exact proposed-tree roots."""

    roots, unresolved, root_truncated, ambiguous = _resolve_targets(
        reader,
        targets,
        # Like upstream, changed roots do not consume the impacted-node result
        # limit. CodeCrow retains an explicit work cap and reports it rather
        # than silently treating a partial seed set as complete.
        max_results=_MAX_IMPACT_ROOTS,
    )
    if ambiguous:
        return _ambiguous_target_response(
            reader,
            operation="review_impact_radius",
            targets=targets,
            ambiguous=ambiguous,
            include_source=include_source,
        )
    impact = _weighted_impact(
        reader,
        roots,
        max_depth=max_depth,
        max_results=max_results,
    )
    impacted = impact["impacted"]
    required_root_ids, connection_path_incomplete = _impact_root_ids(impact)
    required_roots = [
        unit for unit in roots if _unit_id(unit) in required_root_ids
    ]
    optional_roots = [
        unit for unit in roots if _unit_id(unit) not in required_root_ids
    ]
    # Roots do not consume the impacted-result budget upstream. The response
    # still bounds a potentially broad file target, but always retains the
    # root(s) referenced by the selected winning paths before filling the
    # remaining root envelope deterministically.
    returned_roots = [
        *required_roots,
        *optional_roots[:max(0, max_results - len(required_roots))],
    ]
    units = [
        *returned_roots,
        *(state["unit"] for _unit_id_value, state in impacted),
    ]
    connections = [
        state["connection"]
        for _unit_id_value, state in impacted
        if isinstance(state.get("connection"), Mapping)
    ]
    returned_unit_ids = {
        _unit_id(unit) for unit in returned_roots if _unit_id(unit)
    } | {unit_id_value for unit_id_value, _state in impacted}
    node_depth_by_id = {
        _unit_id(unit): 0
        for unit in returned_roots
        if _unit_id(unit)
    }
    node_depth_by_id.update({
        unit_id_value: int(state["depth"])
        for unit_id_value, state in impacted
    })
    required_relations = [
        connection["relation"]
        for connection in connections
        if isinstance(connection.get("relation"), Mapping)
    ]
    induced_relations, induced_scan_truncated, required_relation_count = (
        _induced_impact_relations(
            reader,
            unit_ids=returned_unit_ids,
            required_relations=required_relations,
        )
    )
    max_returned_relations = min(500, max(100, max_results * 2))
    relation_response_truncated = (
        induced_scan_truncated
        or len(induced_relations) > max_returned_relations
    )
    relation_values = induced_relations[:max_returned_relations]
    relations_by_id = {
        str(relation.get("evidenceId")): dict(relation)
        for relation in relation_values
        if relation.get("evidenceId")
    }
    windows, source_coverage = (
        _bounded_source_windows(
            reader,
            units,
            relation_values,
            changed_paths=changed_paths,
            max_source_windows=max_source_windows,
            max_source_characters=max_source_characters,
        )
        if include_source
        else ([], {
            "state": "not_requested",
            "truncated": False,
            "availableSourceUnits": 0,
            "returnedSourceUnits": 0,
            "omittedSourceUnits": 0,
            "truncatedSourceWindows": 0,
        })
    )
    source_by_unit = _source_evidence_by_unit(windows)
    partial_reasons = list(impact["partialReasons"])
    if root_truncated:
        partial_reasons.append("root_result_limit")
    if len(returned_roots) < len(roots):
        partial_reasons.append("root_response_limit")
    if connection_path_incomplete:
        partial_reasons.append("connection_path_incomplete")
    if relation_response_truncated:
        partial_reasons.append("relation_response_limit")
    if source_coverage.get("truncated"):
        partial_reasons.append("source_window_limit")
    partial_reasons = list(dict.fromkeys(partial_reasons))
    nodes = [
        {
            **_compact_unit(
                unit,
                detail_level=detail_level,
                depth=0,
                source_evidence_id=source_by_unit.get(_unit_id(unit)),
            ),
            "impactScore": 1.0,
        }
        for unit in returned_roots
    ]
    for unit_id_value, state in impacted:
        connection = state.get("connection") or {}
        nodes.append({
            **_compact_unit(
                state["unit"],
                detail_level=detail_level,
                depth=int(state["depth"]),
                source_evidence_id=source_by_unit.get(unit_id_value),
            ),
            "impactScore": round(float(state["score"]), 4),
            "connectionEvidenceId": connection.get("evidenceId"),
        })
    return {
        "status": "ready",
        "operation": "review_impact_radius",
        "snapshot": reader.snapshot(),
        "targets": list(targets),
        "unresolvedTargets": list(unresolved),
        "roots": [
            _compact_unit(unit, detail_level=detail_level, depth=0)
            for unit in returned_roots
        ],
        "nodes": nodes,
        "edges": [
            _compact_relation(
                relation,
                detail_level=detail_level,
                depth=max(
                    node_depth_by_id.get(_unit_id(relation.get("sourceUnit")), 0),
                    node_depth_by_id.get(_unit_id(relation.get("targetUnit")), 0),
                ),
            )
            for evidence_id, relation in relations_by_id.items()
        ],
        "connections": [
            {
                key: value
                for key, value in connection.items()
                if key != "relation"
            }
            for connection in connections
        ],
        "impactScores": {
            unit_id_value: round(float(state["score"]), 4)
            for unit_id_value, state in impacted
        },
        "impactedFiles": sorted({
            str(state["unit"].get("path"))
            for _unit_id_value, state in impacted
            if state["unit"].get("path")
        }),
        "frontier": [
            {
                **_compact_unit(unit, detail_level="minimal", depth=depth),
                "reason": reason,
                **(
                    {
                        "continuation": {
                            "tool": "queryCodeGraph",
                            "arguments": {
                                "pattern": "relations_of",
                                "target": _unit_id(unit),
                                "cursor": next_cursor,
                                "maxResults": min(100, max_results),
                                "detailLevel": detail_level,
                            },
                        }
                    }
                    if next_cursor is not None and _unit_id(unit)
                    else {}
                ),
            }
            for unit, depth, reason, next_cursor in list(
                impact["frontier"].values()
            )[:_MAX_RETURNED_FRONTIER]
        ],
        "sourceWindows": windows,
        "coverage": {
            "state": "bounded" if partial_reasons else "complete",
            "truncated": bool(partial_reasons),
            "partialReasons": partial_reasons,
            "maxDepth": max_depth,
            "depthReached": impact["depthReached"],
            "maxResults": max_results,
            "returnedNodes": len(nodes),
            "resolvedRoots": len(roots),
            "returnedRoots": len(returned_roots),
            "returnedImpacted": len(impacted),
            "totalDiscovered": impact["totalDiscovered"],
            "totalScored": impact["totalScored"],
            "returnedRelations": len(relations_by_id),
            "inducedRelationsFound": len(induced_relations),
            "omittedRelations": max(
                0,
                len(induced_relations) - len(relations_by_id),
            ),
            "requiredConnectionRelations": required_relation_count,
            "returnedConnectionRelations": sum(
                1
                for relation in relation_values
                if str(relation.get("evidenceId") or "")
                in {
                    str(connection.get("evidenceId") or "")
                    for connection in connections
                }
            ),
            "omittedConnectionRelations": max(
                0,
                required_relation_count
                - sum(
                    1
                    for relation in relation_values
                    if str(relation.get("evidenceId") or "")
                    in {
                        str(connection.get("evidenceId") or "")
                        for connection in connections
                    }
                ),
            ),
            "omittedRoots": max(0, len(roots) - len(returned_roots)),
            "omittedFrontier": max(
                0,
                len(impact["frontier"]) - _MAX_RETURNED_FRONTIER,
            ),
            "maxWorkNodes": impact["maxWorkNodes"],
            "sourceIncluded": include_source,
            "source": source_coverage,
        },
        "scorePolicy": {
            "edgeWeights": dict(_IMPACT_EDGE_WEIGHTS),
            "edgeDirections": dict(_IMPACT_EDGE_DIRECTIONS),
            "defaultEdgeDirection": _IMPACT_DEFAULT_EDGE_DIRECTION,
            "defaultEdgeWeight": _IMPACT_DEFAULT_EDGE_WEIGHT,
            "depthDecay": _IMPACT_DEPTH_DECAY,
            "scoreFloor": _IMPACT_SCORE_FLOOR,
        },
    }


def traverse_review_graph(
    reader,
    *,
    start: str,
    strategy: str = "bfs",
    direction: str = "both",
    relation_kinds: Sequence[str] = (),
    changed_paths: Sequence[str] = (),
    max_depth: int = 3,
    max_results: int = 100,
    token_budget: int = _DEFAULT_TRAVERSAL_TOKEN_BUDGET,
    detail_level: str = "standard",
    include_source: bool = False,
    max_source_windows: int = 6,
    max_source_characters: int = 12000,
) -> dict[str, Any]:
    """Traverse exact AST/plugin relations using deterministic BFS or DFS."""

    # The API applies the same cardinality limit. Keep the algorithm itself
    # robust for direct callers and prevent invalid, arbitrarily long filter
    # labels from dominating the bounded response envelope.
    bounded_relation_kinds = tuple(dict.fromkeys(
        str(kind).strip()[:128]
        for kind in relation_kinds[:50]
        if str(kind).strip()
    ))

    roots, unresolved, root_truncated, ambiguous = _resolve_targets(
        reader,
        [start],
        max_results=1,
    )
    if ambiguous:
        return _ambiguous_target_response(
            reader,
            operation="traverse_review_graph",
            targets=[start],
            ambiguous=ambiguous,
            include_source=include_source,
        )
    walked = _walk(
        reader,
        roots,
        strategy=strategy,
        direction=direction,
        relation_kinds=bounded_relation_kinds,
        max_depth=max_depth,
        max_results=max_results,
    )
    return _walk_response(
        reader,
        operation="traverse_review_graph",
        targets=[start],
        roots=roots,
        unresolved=unresolved,
        root_truncated=root_truncated,
        walked=walked,
        changed_paths=changed_paths,
        max_depth=max_depth,
        max_results=max_results,
        token_budget=token_budget,
        detail_level=detail_level,
        include_source=include_source,
        max_source_windows=max_source_windows,
        max_source_characters=max_source_characters,
        extra={
            "strategy": strategy,
            "direction": direction,
            "relationKinds": list(bounded_relation_kinds),
        },
    )


def _walk_response(
    reader,
    *,
    operation: str,
    targets: Sequence[str],
    roots: Sequence[Mapping[str, Any]],
    unresolved: Sequence[str],
    root_truncated: bool,
    walked: Mapping[str, Any],
    changed_paths: Sequence[str],
    max_depth: int,
    max_results: int,
    token_budget: int,
    detail_level: str,
    include_source: bool,
    max_source_windows: int,
    max_source_characters: int,
    extra: Mapping[str, Any],
) -> dict[str, Any]:
    visited = walked["visited"]
    relations = walked["relations"]
    token_budget = max(
        _MIN_TRAVERSAL_TOKEN_BUDGET,
        min(int(token_budget), 16000),
    )
    response_character_budget = token_budget * 4
    graph_character_budget = (
        response_character_budget
        if not include_source
        else max(512, response_character_budget * 2 // 3)
    )

    selected_units: list[tuple[str, dict[str, Any], int]] = []
    selected_unit_ids: set[str] = set()
    selected_relations: list[tuple[dict[str, Any], int]] = []
    selected_relation_ids: set[str] = set()
    token_omitted_units: list[tuple[dict[str, Any], int]] = []
    token_omitted_relation_ids: set[str] = set()

    for unit_id, (unit, depth) in visited.items():
        compact = _compact_unit(unit, detail_level=detail_level, depth=depth)
        candidate_nodes = [
            *(
                _compact_unit(
                    selected_unit,
                    detail_level=detail_level,
                    depth=selected_depth,
                )
                for _selected_id, selected_unit, selected_depth in selected_units
            ),
            compact,
        ]
        if selected_units and _json_char_length({
            "nodes": candidate_nodes,
            "edges": [
                _compact_relation(
                    relation,
                    detail_level=detail_level,
                    depth=relation_depth,
                )
                for relation, relation_depth in selected_relations
            ],
        }) > graph_character_budget:
            token_omitted_units.append((dict(unit), depth))
            continue
        selected_units.append((unit_id, dict(unit), depth))
        selected_unit_ids.add(unit_id)

        for evidence_id, (relation, relation_depth) in relations.items():
            if evidence_id in selected_relation_ids:
                continue
            if not {
                _unit_id(relation.get("sourceUnit")),
                _unit_id(relation.get("targetUnit")),
            }.issubset(selected_unit_ids):
                continue
            candidate_relations = [
                *selected_relations,
                (dict(relation), relation_depth),
            ]
            if _json_char_length({
                "nodes": candidate_nodes,
                "edges": [
                    _compact_relation(
                        candidate_relation,
                        detail_level=detail_level,
                        depth=candidate_depth,
                    )
                    for candidate_relation, candidate_depth in candidate_relations
                ],
            }) > graph_character_budget:
                token_omitted_relation_ids.add(evidence_id)
                continue
            selected_relation_ids.add(evidence_id)
            token_omitted_relation_ids.discard(evidence_id)
            selected_relations.append((dict(relation), relation_depth))

    unit_values = [unit for _unit_id_value, unit, _depth in selected_units]
    relation_values = [relation for relation, _depth in selected_relations]
    graph_payload_characters = _json_char_length({
        "nodes": [
            _compact_unit(unit, detail_level=detail_level, depth=depth)
            for _unit_id_value, unit, depth in selected_units
        ],
        "edges": [
            _compact_relation(relation, detail_level=detail_level, depth=depth)
            for relation, depth in selected_relations
        ],
    })
    source_character_budget = min(
        max_source_characters,
        max(0, response_character_budget - graph_payload_characters),
    )
    windows, source_coverage = (
        _bounded_source_windows(
            reader,
            unit_values,
            relation_values,
            changed_paths=changed_paths,
            max_source_windows=max_source_windows,
            max_source_characters=source_character_budget,
        )
        if include_source
        else ([], {
            "state": "not_requested",
            "truncated": False,
            "availableSourceUnits": 0,
            "returnedSourceUnits": 0,
            "omittedSourceUnits": 0,
            "truncatedSourceWindows": 0,
        })
    )

    def evidence_payload_characters() -> int:
        return _json_char_length({
            "nodes": [
                _compact_unit(unit, detail_level=detail_level, depth=depth)
                for _unit_id_value, unit, depth in selected_units
            ],
            "edges": [
                _compact_relation(relation, detail_level=detail_level, depth=depth)
                for relation, depth in selected_relations
            ],
            "sourceWindows": windows,
        })

    while windows and evidence_payload_characters() > response_character_budget:
        overflow = evidence_payload_characters() - response_character_budget
        window = windows[-1]
        content = str(window.get("content") or "")
        if len(content) > overflow + 64:
            selected = content[:len(content) - overflow - 64]
            if "\n" in selected:
                selected = selected.rsplit("\n", 1)[0] + "\n"
            window["content"] = selected
            window["truncated"] = True
            start_line = max(1, int(window.get("startLine") or 1))
            window["endLine"] = max(
                start_line,
                start_line + selected.count("\n") - int(selected.endswith("\n")),
            )
            break
        removed = windows.pop()
        omitted_ids = list(source_coverage.get("omittedUnitIds") or ())
        omitted_ids.append(str(removed.get("unitId") or ""))
        source_coverage["omittedUnitIds"] = [
            unit_id for unit_id in dict.fromkeys(omitted_ids) if unit_id
        ][:20]

    if include_source:
        source_coverage.update({
            "returnedSourceUnits": len(windows),
            "omittedSourceUnits": max(
                int(source_coverage.get("omittedSourceUnits") or 0),
                int(source_coverage.get("availableSourceUnits") or 0) - len(windows),
            ),
            "truncatedSourceWindows": sum(
                bool(window.get("truncated")) for window in windows
            ),
            "returnedSourceCharacters": sum(
                len(str(window.get("content") or "")) for window in windows
            ),
        })
        source_coverage["truncated"] = bool(
            source_coverage["omittedSourceUnits"]
            or source_coverage["truncatedSourceWindows"]
        )
        source_coverage["state"] = (
            "bounded" if source_coverage["truncated"] else "complete"
        )

    source_by_unit = _source_evidence_by_unit(windows)
    partial_reasons = list(walked["partialReasons"])
    if root_truncated:
        partial_reasons.append("root_result_limit")
    if token_omitted_units or token_omitted_relation_ids:
        partial_reasons.append("token_budget")
    if source_coverage.get("truncated"):
        partial_reasons.append("source_window_limit")
    partial_reasons = list(dict.fromkeys(partial_reasons))
    frontier_values = [
        (unit, depth, "token_budget", None)
        for unit, depth in token_omitted_units
    ]
    if token_omitted_relation_ids and not frontier_values and selected_units:
        _unit_id_value, unit, depth = selected_units[-1]
        frontier_values.append((unit, depth, "token_budget", None))
    frontier_values.extend(walked["frontier"].values())
    frontier_response = []
    seen_frontier: set[tuple[str, str]] = set()

    def traversal_continuation_arguments(
        unit: Mapping[str, Any],
        depth: int,
    ) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "start": _unit_id(unit),
            "strategy": extra.get("strategy", "bfs"),
            "direction": extra.get("direction", "both"),
            "maxDepth": max(0, max_depth - depth),
            "maxResults": max_results,
            "tokenBudget": token_budget,
            "detailLevel": detail_level,
            "includeSource": include_source,
            "maxSourceWindows": max_source_windows,
            "maxSourceCharacters": max_source_characters,
        }
        if extra.get("relationKinds"):
            arguments["relationKinds"] = list(extra["relationKinds"])
        return arguments

    for unit, depth, reason, next_cursor in frontier_values:
        frontier_key = (_unit_id(unit), reason)
        if frontier_key in seen_frontier:
            continue
        seen_frontier.add(frontier_key)
        item = {
            **_compact_unit(unit, detail_level="minimal", depth=depth),
            "reason": reason,
        }
        if next_cursor is not None and _unit_id(unit):
            item["continuation"] = {
                "tool": "queryCodeGraph",
                "arguments": {
                    "pattern": "relations_of",
                    "target": _unit_id(unit),
                    "cursor": next_cursor,
                    "maxResults": min(100, max_results),
                    "detailLevel": detail_level,
                },
            }
        elif reason == "token_budget" and _unit_id(unit):
            item["continuation"] = {
                "tool": "traverseCodeGraph",
                "arguments": traversal_continuation_arguments(unit, depth),
            }
        frontier_response.append(item)

    response = {
        "status": "ready",
        "operation": operation,
        "snapshot": reader.snapshot(),
        "targets": list(targets),
        "unresolvedTargets": list(unresolved),
        "roots": [
            _compact_unit(unit, detail_level=detail_level, depth=0)
            for unit in roots
        ],
        "nodes": [
            _compact_unit(
                unit,
                detail_level=detail_level,
                depth=depth,
                source_evidence_id=source_by_unit.get(unit_id),
            )
            for unit_id, unit, depth in selected_units
        ],
        "edges": [
            _compact_relation(relation, detail_level=detail_level, depth=depth)
            for relation, depth in selected_relations
        ],
        "frontier": frontier_response[:_MAX_RETURNED_FRONTIER],
        "sourceWindows": windows,
        "coverage": {
            "state": "bounded" if partial_reasons else "complete",
            "truncated": bool(partial_reasons),
            "partialReasons": partial_reasons,
            "maxDepth": max_depth,
            "depthReached": walked["depthReached"],
            "maxResults": max_results,
            "tokenBudget": token_budget,
            "serializedCharacters": 0,
            "estimatedTokens": 0,
            "discoveredNodes": len(visited),
            "returnedNodes": len(selected_units),
            "omittedNodes": max(0, len(visited) - len(selected_units)),
            "discoveredRelations": len(relations),
            "returnedRelations": len(selected_relations),
            "omittedRelations": len(relations) - len(selected_relations),
            "omittedFrontier": max(
                0,
                len(frontier_response) - _MAX_RETURNED_FRONTIER,
            ),
            "sourceIncluded": include_source,
            "source": source_coverage,
        },
        **dict(extra),
    }

    total_frontier = len(frontier_response)

    def mark_token_bounded() -> None:
        coverage = response["coverage"]
        reasons = coverage["partialReasons"]
        if "token_budget" not in reasons:
            reasons.append("token_budget")
        coverage["state"] = "bounded"
        coverage["truncated"] = True

    def refresh_source_coverage() -> None:
        source = response["coverage"]["source"]
        returned_windows = response["sourceWindows"]
        if not include_source:
            return
        source.update({
            "returnedSourceUnits": len(returned_windows),
            "omittedSourceUnits": max(
                int(source.get("omittedSourceUnits") or 0),
                int(source.get("availableSourceUnits") or 0)
                - len(returned_windows),
            ),
            "truncatedSourceWindows": sum(
                bool(window.get("truncated"))
                for window in returned_windows
            ),
            "returnedSourceCharacters": sum(
                len(str(window.get("content") or ""))
                for window in returned_windows
            ),
        })
        source["truncated"] = bool(
            source["omittedSourceUnits"]
            or source["truncatedSourceWindows"]
        )
        source["state"] = "bounded" if source["truncated"] else "complete"
        if source["truncated"]:
            reasons = response["coverage"]["partialReasons"]
            if "source_window_limit" not in reasons:
                reasons.append("source_window_limit")
            response["coverage"]["state"] = "bounded"
            response["coverage"]["truncated"] = True
        valid_evidence_ids = {
            str(window.get("evidenceId") or "")
            for window in returned_windows
        }
        for node in response["nodes"]:
            if node.get("sourceEvidenceId") not in valid_evidence_ids:
                node.pop("sourceEvidenceId", None)

    def refresh_response_counts() -> None:
        coverage = response["coverage"]
        coverage["returnedNodes"] = len(response["nodes"])
        coverage["omittedNodes"] = max(
            0,
            int(coverage["discoveredNodes"]) - len(response["nodes"]),
        )
        coverage["returnedRelations"] = len(response["edges"])
        coverage["omittedRelations"] = max(
            0,
            int(coverage["discoveredRelations"]) - len(response["edges"]),
        )
        coverage["omittedFrontier"] = max(
            0,
            total_frontier - len(response["frontier"]),
        )

    def update_serialized_size() -> int:
        coverage = response["coverage"]
        for _iteration in range(8):
            serialized_characters = _json_char_length(response)
            estimated_tokens = (serialized_characters + 3) // 4
            previous = (
                coverage["serializedCharacters"],
                coverage["estimatedTokens"],
            )
            coverage["serializedCharacters"] = serialized_characters
            coverage["estimatedTokens"] = estimated_tokens
            if previous == (serialized_characters, estimated_tokens):
                break
        return int(coverage["serializedCharacters"])

    refresh_source_coverage()
    refresh_response_counts()
    serialized_characters = update_serialized_size()
    if serialized_characters > response_character_budget:
        mark_token_bounded()
        if not any(
            item.get("reason") == "token_budget"
            for item in response["frontier"]
        ) and selected_units:
            _frontier_id, frontier_unit, frontier_depth = selected_units[-1]
            response["frontier"].insert(0, {
                **_compact_unit(
                    frontier_unit,
                    detail_level="minimal",
                    depth=frontier_depth,
                ),
                "reason": "token_budget",
                "continuation": {
                    "tool": "traverseCodeGraph",
                    "arguments": traversal_continuation_arguments(
                        frontier_unit,
                        frontier_depth,
                    ),
                },
            })
            total_frontier += 1

    for _iteration in range(1000):
        refresh_source_coverage()
        refresh_response_counts()
        serialized_characters = update_serialized_size()
        if serialized_characters <= response_character_budget:
            break
        mark_token_bounded()

        if len(response["frontier"]) > 1:
            response["frontier"].pop()
            continue
        if response["sourceWindows"]:
            overflow = serialized_characters - response_character_budget
            window = response["sourceWindows"][-1]
            content = str(window.get("content") or "")
            if len(content) > overflow + 64:
                selected = content[:len(content) - overflow - 64]
                if "\n" in selected:
                    selected = selected.rsplit("\n", 1)[0] + "\n"
                window["content"] = selected
                window["truncated"] = True
                start_line = max(1, int(window.get("startLine") or 1))
                window["endLine"] = max(
                    start_line,
                    start_line
                    + selected.count("\n")
                    - int(selected.endswith("\n")),
                )
            else:
                removed = response["sourceWindows"].pop()
                omitted_ids = list(
                    response["coverage"]["source"].get("omittedUnitIds")
                    or ()
                )
                omitted_ids.append(str(removed.get("unitId") or ""))
                response["coverage"]["source"]["omittedUnitIds"] = [
                    unit_id
                    for unit_id in dict.fromkeys(omitted_ids)
                    if unit_id
                ][:20]
            continue
        if response["edges"]:
            response["edges"].pop()
            continue
        if len(response["nodes"]) > 1:
            removed_node = response["nodes"].pop()
            removed_unit_id = str(removed_node.get("unitId") or "")
            response["edges"] = [
                edge
                for edge in response["edges"]
                if removed_unit_id not in {
                    str(edge.get("sourceUnitId") or ""),
                    str(edge.get("targetUnitId") or ""),
                }
            ]
            continue
        if response["frontier"] and response["frontier"][0].get("continuation"):
            response["frontier"][0].pop("continuation", None)
            continue

        # Evidence has already been reduced as far as possible. Bound echoed
        # request controls and descriptive identity fields as a final envelope
        # step; callers already own the original request, so repeating an
        # arbitrarily long symbolic label must never defeat tokenBudget.
        echo_changed = False
        for key in ("targets", "unresolvedTargets"):
            values = response.get(key)
            if not isinstance(values, list):
                continue
            bounded_values = [
                str(value)[:160]
                for value in values[:4]
            ]
            if bounded_values != values:
                response[key] = bounded_values
                echo_changed = True
        if echo_changed:
            continue
        if response.get("relationKinds"):
            response["relationKinds"].pop()
            continue

        identity_changed = False
        for key in ("frontier", "roots", "nodes"):
            values = response.get(key)
            if not isinstance(values, list):
                continue
            compact_values = []
            for value in values:
                if not isinstance(value, Mapping):
                    continue
                compact = {
                    field: value.get(field)
                    for field in (
                        "unitId",
                        "depth",
                        "reason",
                        "sourceEvidenceId",
                    )
                    if value.get(field) is not None
                }
                if isinstance(compact.get("unitId"), str):
                    compact["unitId"] = compact["unitId"][:256]
                compact_values.append(compact)
            if compact_values != values:
                response[key] = compact_values
                identity_changed = True
        if identity_changed:
            continue

        snapshot = response.get("snapshot")
        if isinstance(snapshot, Mapping):
            bounded_snapshot = {
                str(key)[:80]: (
                    str(value)[:256] if isinstance(value, str) else value
                )
                for key, value in snapshot.items()
            }
            if bounded_snapshot != snapshot:
                response["snapshot"] = bounded_snapshot
                continue

        source_coverage_value = response["coverage"].get("source")
        if (
            isinstance(source_coverage_value, dict)
            and source_coverage_value.get("omittedUnitIds")
        ):
            source_coverage_value.pop("omittedUnitIds", None)
            continue
        if response.get("unresolvedTargets"):
            response["unresolvedTargets"].pop()
            continue
        if response.get("targets"):
            response["targets"].pop()
            continue
        break

    refresh_source_coverage()
    refresh_response_counts()
    serialized_characters = update_serialized_size()
    if serialized_characters > response_character_budget:
        # Preserve a small, fail-open diagnostic envelope if mandatory metadata
        # ever grows beyond the requested estimate. The original request is
        # already known to the caller; evidence counts and snapshot identity are
        # more useful here than failing an otherwise optional graph operation.
        previous_coverage = response["coverage"]
        snapshot_value = response.get("snapshot")
        compact_snapshot = {}
        if isinstance(snapshot_value, Mapping):
            compact_snapshot = {
                key: str(snapshot_value[key])[:160]
                for key in (
                    "kind",
                    "branch",
                    "revision",
                    "generationManifestSha256",
                )
                if snapshot_value.get(key) is not None
            }
        response = {
            "status": "ready",
            "operation": operation,
            "snapshot": compact_snapshot,
            "roots": [],
            "nodes": [],
            "edges": [],
            "frontier": [],
            "sourceWindows": [],
            "coverage": {
                "state": "bounded",
                "truncated": True,
                "partialReasons": ["token_budget"],
                "tokenBudget": token_budget,
                "serializedCharacters": 0,
                "estimatedTokens": 0,
                "discoveredNodes": previous_coverage["discoveredNodes"],
                "returnedNodes": 0,
                "omittedNodes": previous_coverage["discoveredNodes"],
                "discoveredRelations": previous_coverage[
                    "discoveredRelations"
                ],
                "returnedRelations": 0,
                "omittedRelations": previous_coverage[
                    "discoveredRelations"
                ],
                "sourceIncluded": include_source,
                "source": {
                    "state": "bounded" if include_source else "not_requested",
                    "truncated": include_source,
                    "returnedSourceUnits": 0,
                },
            },
            "strategy": extra.get("strategy", "bfs"),
            "direction": extra.get("direction", "both"),
        }
        update_serialized_size()
    return response


def query_review_graph(
    reader,
    *,
    pattern: str,
    target: str,
    changed_paths: Sequence[str] = (),
    max_results: int = 25,
    cursor: int = 0,
    detail_level: str = "standard",
    include_source: bool = False,
    max_source_windows: int = 6,
    max_source_characters: int = 12000,
) -> dict[str, Any]:
    """Run one exact proposed-tree query, preserving full facts in standard mode."""

    exact = _query_page(
        reader,
        pattern,
        target,
        max_results=max_results,
        cursor=cursor,
    )
    if exact.get("status") == "error":
        return {
            **dict(exact),
            "operation": "query_review_graph",
            "snapshot": reader.snapshot(),
        }
    if exact.get("status") == "ambiguous":
        source_coverage = {
            "state": "unavailable" if include_source else "not_requested",
            "truncated": False,
            "availableSourceUnits": 0,
            "returnedSourceUnits": 0,
            "omittedSourceUnits": 0,
            "truncatedSourceWindows": 0,
        }
        return {
            **dict(exact),
            "operation": "query_review_graph",
            "snapshot": reader.snapshot(),
            "sourceWindows": [],
            "coverage": {
                "state": "bounded",
                "truncated": True,
                "partialReasons": ["ambiguous_target"],
                "maxResults": max_results,
                "returnedResults": 0,
                "sourceIncluded": include_source,
                "source": source_coverage,
            },
        }
    values = [item for item in exact.get("results") or () if isinstance(item, Mapping)]
    resolved_units = [
        dict(item)
        for item in exact.get("resolvedUnits") or ()
        if isinstance(item, Mapping)
    ]
    unit_results = pattern.strip().casefold() in {
        "symbol_search", "symbols", "file_summary",
    }
    units: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    if unit_results:
        units = [dict(item) for item in values]
    else:
        relations = [dict(item) for item in values]
        for relation in relations:
            for endpoint in (relation.get("sourceUnit"), relation.get("targetUnit")):
                if isinstance(endpoint, Mapping):
                    units.append(dict(endpoint))
    windows, source_coverage = (
        _bounded_source_windows(
            reader,
            units,
            relations,
            changed_paths=changed_paths,
            max_source_windows=max_source_windows,
            max_source_characters=max_source_characters,
        )
        if include_source
        else ([], {
            "state": "not_requested",
            "truncated": False,
            "availableSourceUnits": 0,
            "returnedSourceUnits": 0,
            "omittedSourceUnits": 0,
            "truncatedSourceWindows": 0,
        })
    )
    if detail_level == "minimal":
        response_results = (
            [_compact_unit(item, detail_level="minimal") for item in units]
            if unit_results
            else [_compact_relation(item, detail_level="minimal") for item in relations]
        )
    else:
        response_results = values
    response_resolved_units = (
        [
            _compact_unit(item, detail_level="minimal")
            for item in resolved_units
        ]
        if detail_level == "minimal"
        else resolved_units
    )
    partial_reasons = []
    if exact.get("truncated"):
        partial_reasons.append("result_limit")
    if source_coverage.get("truncated"):
        partial_reasons.append("source_window_limit")
    return {
        "status": "ready",
        "operation": "query_review_graph",
        "snapshot": reader.snapshot(),
        "pattern": exact.get("pattern") or pattern,
        "target": target,
        "cursor": int(exact.get("cursor") or cursor),
        "nextCursor": exact.get("nextCursor"),
        "resolvedUnits": response_resolved_units,
        "results": response_results,
        "sourceWindows": windows,
        "coverage": {
            "state": "bounded" if partial_reasons else "complete",
            "truncated": bool(partial_reasons),
            "partialReasons": partial_reasons,
            "maxResults": max_results,
            "returnedResults": len(response_results),
            "sourceIncluded": include_source,
            "source": source_coverage,
        },
    }


def get_review_structural_unit(
    reader,
    *,
    unit_id: str,
    offset: int = 0,
    max_characters: int = 12000,
) -> dict[str, Any] | None:
    """Read one bounded exact-content window from an AST or plugin unit."""

    detail = reader.get_unit(unit_id)
    if detail is None:
        return None
    response = dict(detail)
    unit = response.get("unit")
    if isinstance(unit, Mapping):
        bounded_unit = dict(unit)
        content = bounded_unit.get("content")
        if isinstance(content, str):
            offset = max(0, int(offset))
            max_characters = max(1, min(int(max_characters), 60000))
            total_characters = len(content)
            bounded_offset = min(offset, total_characters)
            end_offset = min(
                total_characters,
                bounded_offset + max_characters,
            )
            bounded_unit["content"] = content[bounded_offset:end_offset]
            bounded_unit["contentWindow"] = {
                "offset": bounded_offset,
                "endOffset": end_offset,
                "totalCharacters": total_characters,
                "truncated": end_offset < total_characters,
                "nextOffset": end_offset if end_offset < total_characters else None,
            }
            response["unit"] = bounded_unit
    return {
        **response,
        "status": "ready",
        "operation": "get_review_structural_unit",
        # An unchanged unit may physically come from the sealed base reader, but
        # the observable snapshot is always the composed proposed-tree session.
        "snapshot": reader.snapshot(),
    }
