"""Atomic verification work with complete, reusable source scopes.

A source owner selects evidence, not which claims must share a conversation.
Each candidate or concrete question starts separately; the optional planner may
join repeated investigations of the same suspected defect across source owners.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from service.review.planner import is_contract_relation


@dataclass
class VerificationCase:
    id: str
    part_ids: tuple[str, ...]
    owner_ids: tuple[str, ...] = ()
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


def _anchor_owners(issue: Mapping[str, Any], part: Any, graph_context: Mapping[str, Any],
                   fallback: tuple[str, ...]) -> tuple[str, ...]:
    """A hunk can cross several definitions; the finding has one exact anchor."""
    if any(owner.startswith("file:") for owner in fallback):
        return fallback
    try:
        line = int(issue.get("line"))
    except (TypeError, ValueError, OverflowError):
        return fallback
    containing = []
    for unit in (graph_context.get(part.id) or {}).get("units", []):
        try:
            start, end = int(unit["startLine"]), int(unit["endLine"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if unit.get("unitId") and unit.get("path", part.path) == part.path and start <= line <= end:
            containing.append((start, end, str(unit["unitId"])))
    # Nested declarations use their most specific owner, while overlapping
    # non-nested owners remain together. Complete hunks are still supplied.
    return tuple(sorted(unit_id for start, end, unit_id in containing
                        if not any(start <= other_start <= other_end <= end
                                   and (start, end) != (other_start, other_end)
                                   for other_start, other_end, _ in containing))) or fallback


def build_cases(findings: Sequence[dict[str, Any]], investigations: Sequence[dict[str, Any]],
                parts: Mapping[str, Any], graph_context: Mapping[str, Any]) -> list[VerificationCase]:
    owners = _owners(parts, graph_context)
    cases: list[VerificationCase] = []

    def case_for(part_ids: Sequence[str], paths: Sequence[str], label: str,
                 anchor: Mapping[str, Any] | None = None) -> VerificationCase:
        selected = {key for key in part_ids if key in parts}
        if not selected:
            selected = {part.id for part in parts.values() if part.path in paths}
        scope = tuple(sorted({owner for key in selected
                              for owner in (_anchor_owners(anchor, parts[key], graph_context, owners[key])
                                            if anchor is not None else owners[key])})) or (f"question:{label}",)
        # Complete owner evidence may serve several independent investigations.
        # Sharing those hunks must not make their claims an indivisible worklist.
        same_scope = {key for key, units in owners.items() if set(units).intersection(scope)}
        case = VerificationCase(f"case-{len(cases) + 1}", tuple(sorted(selected | same_scope)), scope)
        cases.append(case)
        return case

    for index, issue in enumerate(findings, 1):
        case = case_for([str(issue.get("partId") or "")], [str(issue.get("file") or "")], f"candidate-{index}", issue)
        case.findings.append(dict(issue))
        case.batch_ids.update(str(key) for key in issue.get("batchIds", []) if isinstance(key, str))
    for index, question in enumerate(investigations, 1):
        case = case_for(question.get("partIds") or [], question.get("paths") or [], str(question.get("id") or index))
        case.investigations.append(dict(question))
        if question.get("origin"):
            case.batch_ids.add(str(question["origin"]))
    return cases


def related_parts(case: VerificationCase, parts: Mapping[str, Any], graph_context: Mapping[str, Any]) -> list[Any]:
    """Preserve exact changed companions for the case's direct contracts."""
    selected = set(case.part_ids)
    unit_ids = {owner for owner in case.owner_ids if not owner.startswith("file:")}
    if not unit_ids:
        unit_ids = {str(unit.get("unitId")) for key in case.part_ids
                    for unit in (graph_context.get(key) or {}).get("units", []) if unit.get("unitId")}
    neighbor_ids: set[str] = set()
    neighbor_paths: set[str] = set()
    for key in case.part_ids:
        for relation in (graph_context.get(key) or {}).get("relations", []):
            if not is_contract_relation(relation):
                continue
            endpoints = [relation.get(name) or {} for name in ("source", "target")]
            identified = {str(endpoint["unitId"]) for endpoint in endpoints if endpoint.get("unitId")}
            if identified and not identified.intersection(unit_ids):
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
    selected_owners = set(case.owner_ids) | {owner for key in selected - set(case.part_ids) for owner in owners[key]}
    selected.update(key for key, owned in owners.items() if set(owned) <= selected_owners)
    return [part for key, part in parts.items() if key in selected]


def source_for_case(case_parts: Sequence[Any], sources: Sequence[Mapping[str, Any]], *,
                    owner_ids: Sequence[str] = (), graph_context: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Reuse complete source definitions already supplied to discovery."""
    anchors_by_part = {}
    for part in case_parts:
        anchors = part.anchors
        owners = [unit for unit in ((graph_context or {}).get(part.id) or {}).get("units", [])
                  if str(unit.get("unitId")) in owner_ids]
        if owners and f"file:{part.path}:{part.side}" not in owner_ids:
            try:
                ranges = [(int(unit["startLine"]), int(unit["endLine"])) for unit in owners]
                scoped = [line for line in anchors if any(start <= line <= end for start, end in ranges)]
                if scoped:
                    anchors = scoped
            except (KeyError, TypeError, ValueError, OverflowError):
                pass  # Incomplete structural metadata retains complete fallback source.
        anchors_by_part[part.id] = anchors
    selected = []
    for source in sources:
        if source.get("status") != "ready" or not isinstance(source.get("content"), str):
            continue
        try:
            start, end = int(source.get("startLine") or 1), int(source.get("endLine") or 0)
        except (ValueError, TypeError, OverflowError):
            continue
        if any(part.path == source.get("path") and part.side == source.get("side", "proposed")
               and any(start <= line <= end for line in anchors_by_part[part.id]) for part in case_parts):
            value = dict(source)
            if value not in selected:
                selected.append(value)
    return selected
