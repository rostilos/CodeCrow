"""Deterministic breadth/depth graph traversal.

Adapted portions retain their MIT attribution in this package's NOTICE.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Mapping, Sequence

from .constants import (
    _MAX_RELATIONS_PER_EXPANSION,
)
from .projection import (
    _canonical_relation_name,
    _canonical_relation_semantic,
    _normalized_relation_token,
    _unit_id,
)
from .lookup import (
    _relation_pages,
)


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
