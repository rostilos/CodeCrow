"""Minimal review-context selection from neutral graph facts.

Adapted portions retain their MIT attribution in this package's NOTICE.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .constants import (
    _STOP_TERMS,
    _TASK_TERM,
)
from .projection import (
    _compact_relation,
    _compact_unit,
    _unit_id,
    _unit_key,
)
from .source import (
    _bounded_source_windows,
    _source_evidence_by_unit,
)
from .lookup import (
    _query_page,
)


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
    unresolved_symbols: list[str] = []
    for target in dict.fromkeys(focus_symbols):
        # Graph tools can route a previously observed exact unit identity here.
        # Name search intentionally does not index opaque unit IDs. Use the same
        # exact lookup as impact/traversal before falling back to symbol search,
        # and prioritize explicit roots over incidental changed-file anchors.
        detail = reader.get_unit(target)
        unit = detail.get("unit") if isinstance(detail, Mapping) else None
        if isinstance(unit, Mapping) and _unit_key(unit):
            units.setdefault(_unit_key(unit), dict(unit))
        else:
            unresolved_symbols.append(target)
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
    search_targets = list(dict.fromkeys((*unresolved_symbols, *task_terms)))
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
