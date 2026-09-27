"""Evidence cases for verification, following changed definitions and contracts.

A case owns related review questions, not an arbitrary number of tokens/files.
A question crossing definitions gets their complete hunks in its own case; it
cannot transitively drag every other PR question into one conversation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from service.review.planner import is_contract_relation


@dataclass
class VerificationCase:
    id: str
    part_ids: tuple[str, ...]
    findings: list[dict[str, Any]] = field(default_factory=list)
    investigations: list[dict[str, Any]] = field(default_factory=list)
    batch_ids: set[str] = field(default_factory=set)


def _owners(parts: Mapping[str, Any], graph_context: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    """Use a file-side scope when any of its changed lines lack ownership."""
    units_by_part = {}
    fallback = set()
    for part in parts.values():
        context = graph_context.get(part.id) or {}
        units = tuple(sorted(str(unit["unitId"]) for unit in context.get("units", []) if unit.get("unitId")))
        units_by_part[part.id] = units
        if not units or context.get("structuralOwnershipComplete") is False:
            fallback.add((part.path, part.side))
    return {part.id: ((f"file:{part.path}:{part.side}",) if (part.path, part.side) in fallback
                     else units_by_part[part.id]) for part in parts.values()}


def build_cases(findings: Sequence[dict[str, Any]], investigations: Sequence[dict[str, Any]],
                parts: Mapping[str, Any], graph_context: Mapping[str, Any]) -> list[VerificationCase]:
    owners = _owners(parts, graph_context)
    groups: dict[tuple[str, ...], VerificationCase] = {}

    def case_for(part_ids: Sequence[str], paths: Sequence[str], label: str) -> VerificationCase:
        selected = {key for key in part_ids if key in parts}
        if not selected:
            selected = {part.id for part in parts.values() if part.path in paths}
        scope = tuple(sorted({owner for key in selected for owner in owners[key]})) or (f"question:{label}",)
        if scope not in groups:
            # Include all changed hunks within the same affected definition.
            same_scope = {key for key, units in owners.items() if set(units) <= set(scope)}
            groups[scope] = VerificationCase(f"case-{len(groups) + 1}", tuple(sorted(selected | same_scope)))
        return groups[scope]

    for index, issue in enumerate(findings, 1):
        case = case_for([str(issue.get("partId") or "")], [str(issue.get("file") or "")], f"candidate-{index}")
        case.findings.append(dict(issue))
        case.batch_ids.update(str(key) for key in issue.get("batchIds", []) if isinstance(key, str))
    for index, question in enumerate(investigations, 1):
        case = case_for(question.get("partIds") or [], question.get("paths") or [], str(question.get("id") or index))
        case.investigations.append(dict(question))
        if question.get("origin"):
            case.batch_ids.add(str(question["origin"]))
    return list(groups.values())


def related_parts(case: VerificationCase, parts: Mapping[str, Any], graph_context: Mapping[str, Any]) -> list[Any]:
    """Preserve exact changed companions for the case's direct contracts."""
    selected = set(case.part_ids)
    unit_ids = {str(unit.get("unitId")) for key in case.part_ids
                for unit in (graph_context.get(key) or {}).get("units", []) if unit.get("unitId")}
    neighbor_ids: set[str] = set()
    neighbor_paths: set[str] = set()
    for key in case.part_ids:
        for relation in (graph_context.get(key) or {}).get("relations", []):
            if not is_contract_relation(relation):
                continue
            for name in ("source", "target"):
                endpoint = relation.get(name) or {}
                if endpoint.get("unitId") and str(endpoint["unitId"]) not in unit_ids:
                    neighbor_ids.add(str(endpoint["unitId"]))
                elif not endpoint.get("unitId") and endpoint.get("path"):
                    neighbor_paths.add(str(endpoint["path"]))
    for part in parts.values():
        owned = {str(unit.get("unitId")) for unit in (graph_context.get(part.id) or {}).get("units", [])}
        if owned & neighbor_ids or part.path in neighbor_paths:
            selected.add(part.id)
    owners = _owners(parts, graph_context)
    selected_owners = {owner for key in selected for owner in owners[key]}
    selected.update(key for key, owned in owners.items() if set(owned) <= selected_owners)
    return [part for key, part in parts.items() if key in selected]


def source_for_case(case_parts: Sequence[Any], sources: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Reuse complete source definitions already supplied to discovery."""
    selected = []
    for source in sources:
        if source.get("status") != "ready" or not isinstance(source.get("content"), str):
            continue
        try:
            start, end = int(source.get("startLine") or 1), int(source.get("endLine") or 0)
        except (ValueError, TypeError, OverflowError):
            continue
        if any(part.path == source.get("path") and part.side == source.get("side", "proposed")
               and any(start <= line <= end for line in part.anchors) for part in case_parts):
            value = dict(source)
            if value not in selected:
                selected.append(value)
    return selected
