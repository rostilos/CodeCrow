"""Weighted dependency impact propagation and induced subgraph selection.

Adapted portions retain their MIT attribution in this package's NOTICE.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .constants import (
    _IMPACT_DEFAULT_EDGE_DIRECTION,
    _IMPACT_DEFAULT_EDGE_WEIGHT,
    _IMPACT_DEPTH_DECAY,
    _IMPACT_EDGE_DIRECTIONS,
    _IMPACT_EDGE_WEIGHTS,
    _IMPACT_SCORE_FLOOR,
    _MAX_IMPACT_ROOTS,
    _MAX_IMPACT_WORK_NODES,
    _MAX_IMPACT_WORK_RELATIONS,
    _MAX_RELATIONS_PER_EXPANSION,
    _MAX_RETURNED_FRONTIER,
)
from .projection import (
    _canonical_relation_semantic,
    _compact_relation,
    _compact_unit,
    _unit_id,
)
from .source import (
    _bounded_source_windows,
    _source_evidence_by_unit,
)
from .lookup import (
    _ambiguous_target_response,
    _relation_pages,
    _resolve_targets,
)
from .walk import (
    _relation_neighbors,
)


def _impact_policy(relation: Mapping[str, Any]) -> tuple[float, str]:
    kind = _canonical_relation_semantic(relation)
    return (
        _IMPACT_EDGE_WEIGHTS.get(kind, _IMPACT_DEFAULT_EDGE_WEIGHT),
        _IMPACT_EDGE_DIRECTIONS.get(kind, _IMPACT_DEFAULT_EDGE_DIRECTION),
    )


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
