"""Exact graph payload accounting without reserializing accepted evidence."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .projection import _compact_relation, _compact_unit, _json_char_length, _unit_id


@dataclass
class GraphEvidenceSelection:
    units: list[tuple[str, dict[str, Any], int]] = field(default_factory=list)
    relations: list[tuple[dict[str, Any], int]] = field(default_factory=list)
    omitted_units: list[tuple[dict[str, Any], int]] = field(default_factory=list)
    omitted_relation_ids: set[str] = field(default_factory=set)
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    serialized_characters: int = field(default_factory=lambda: _json_char_length({"nodes": [], "edges": []}))


def select_graph_evidence(
    visited: Mapping[str, tuple[Mapping[str, Any], int]],
    relations: Mapping[str, tuple[Mapping[str, Any], int]],
    *,
    detail_level: str,
    character_budget: int | None,
) -> GraphEvidenceSelection:
    """Keep the original ordered selection policy with linear-sized work.

    JSON list growth is exactly the new serialized item plus a comma when the
    list is nonempty. Relations become eligible only when both endpoints are
    accepted, and a rejected relation cannot fit after this payload grows.
    With no character budget, preserve the entire semantically selected graph.
    """
    result = GraphEvidenceSelection()
    waiting: dict[str, list[str]] = {}
    missing: dict[str, set[str]] = {}
    order: dict[str, int] = {}
    for index, (evidence_id, (relation, _depth)) in enumerate(relations.items()):
        endpoints = {
            _unit_id(relation.get("sourceUnit")),
            _unit_id(relation.get("targetUnit")),
        }
        if "" in endpoints:
            continue
        order[evidence_id] = index
        missing[evidence_id] = endpoints
        for endpoint in endpoints:
            waiting.setdefault(endpoint, []).append(evidence_id)

    for unit_id, (unit, depth) in visited.items():
        compact = _compact_unit(unit, detail_level=detail_level, depth=depth)
        added_characters = _json_char_length(compact) + bool(result.nodes)
        if (character_budget is not None and result.units
                and result.serialized_characters + added_characters > character_budget):
            result.omitted_units.append((dict(unit), depth))
            continue
        result.units.append((unit_id, dict(unit), depth))
        result.nodes.append(compact)
        result.serialized_characters += added_characters
        ready = []
        for evidence_id in waiting.pop(unit_id, ()):
            missing[evidence_id].discard(unit_id)
            if not missing[evidence_id]:
                ready.append(evidence_id)
        for evidence_id in sorted(ready, key=order.__getitem__):
            relation, relation_depth = relations[evidence_id]
            compact_relation = _compact_relation(
                relation, detail_level=detail_level, depth=relation_depth,
            )
            added_characters = _json_char_length(compact_relation) + bool(result.edges)
            if (character_budget is not None
                    and result.serialized_characters + added_characters > character_budget):
                result.omitted_relation_ids.add(evidence_id)
                continue
            result.relations.append((dict(relation), relation_depth))
            result.edges.append(compact_relation)
            result.serialized_characters += added_characters
    return result
