"""Plan complete change ownership and explicit cross-batch review boundaries.

The planner co-owns directly coupled changes without transitively merging a whole
repository component. Remaining contract boundaries retain exact companion hunks;
summaries are routing information, never a substitute for changed source. Structural
ownership and file fallback preserve every complete hunk without token clipping.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence


class ChangePart(Protocol):
    id: str
    path: str
    anchors: Mapping[int, str]
    side: str


GraphReader = Callable[..., Awaitable[Sequence[Mapping[str, Any]]]]


@dataclass(frozen=True)
class ReviewBatch:
    id: str
    parts: tuple[ChangePart, ...]
    related_batch_ids: tuple[str, ...] = ()
    companion_parts: tuple[ChangePart, ...] = ()


@dataclass(frozen=True)
class CrossBatchScope:
    id: str
    batch_ids: tuple[str, ...]
    relations: tuple[dict[str, Any], ...]
    reason: str


@dataclass(frozen=True)
class ReviewPlan:
    batches: tuple[ReviewBatch, ...]
    graph_context: dict[str, dict[str, Any]]
    cross_batch_scopes: tuple[CrossBatchScope, ...]
    diagnostics: tuple[str, ...]


def _identity(prefix: str, values: Sequence[str]) -> str:
    encoded = json.dumps(sorted(values), ensure_ascii=False).encode("utf-8")
    return f"{prefix}-{sha256(encoded).hexdigest()[:16]}"


def _unit(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value[key]
        for key in (
            "unitId", "qualifiedName", "path", "kind", "startLine", "endLine",
        )
        if value.get(key) is not None
    }


def _relation(value: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        key: value[key] for key in ("kind", "relation", "origin", "confidence")
        if value.get(key) is not None
    }
    for name in ("source", "target"):
        endpoint = value.get(f"{name}Unit") or value.get(name)
        result[name] = _unit(endpoint) if isinstance(endpoint, Mapping) else {}
    return result


def _unique(values: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {json.dumps(value, sort_keys=True, ensure_ascii=False): value for value in values}
    return [by_key[key] for key in sorted(by_key)]


def _span(unit: Mapping[str, Any]) -> tuple[int, int] | None:
    try:
        start, end = int(unit.get("startLine", 0)), int(unit.get("endLine", 0))
    except (TypeError, ValueError):
        return None
    return (start, end) if 0 < start <= end else None


def _affected_units(part: ChangePart, units: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    # Graph coordinates describe the proposed tree. Removed lines use target
    # coordinates, so matching them here would attach unrelated proposed code.
    if part.side != "proposed":
        return [], False
    selected: list[dict[str, Any]] = []
    complete = True
    for line in part.anchors:
        covering = [
            (unit, span) for unit in units
            if (span := _span(unit)) is not None and span[0] <= line <= span[1]
        ]
        if not covering:
            complete = False
            continue
        smallest_span = min(end - start for _, (start, end) in covering)
        selected.extend(
            unit for unit, (start, end) in covering
            if end - start == smallest_span
        )
    return _unique(selected), complete and bool(part.anchors)


def is_contract_relation(relation: Mapping[str, Any]) -> bool:
    """Container/import membership alone does not establish a behavior contract."""
    labels = {str(relation.get(key) or "").upper() for key in ("kind", "relation")}
    return not labels.intersection({
        "CONTAINS", "CONTAIN", "BELONGS_TO", "IMPORTS", "IMPORT", "IMPORTS_FROM", "RESOLVES_IMPORT", "DECLARES",
    })


def _contract_groups(paths: Sequence[str], edges: Mapping[frozenset[str], int]) -> list[tuple[str, ...]]:
    """Complete-link grouping keeps call chains and shared hubs from becoming one batch.

    Every pair of files in a group must have a direct changed-code dependency.
    Edge multiplicity selects the strongest available grouping; stable names break
    ties. Group size follows the dependency structure, never a token/file quota.
    """
    groups = [(path,) for path in sorted(paths)]
    while True:
        choices = []
        for left_index, left in enumerate(groups):
            for right_index in range(left_index + 1, len(groups)):
                right = groups[right_index]
                weights = [edges.get(frozenset((a, b)), 0) for a in left for b in right]
                if weights and all(weights):
                    merged = tuple(sorted((*left, *right)))
                    choices.append((-sum(weights), merged, left_index, right_index))
        if not choices:
            return sorted(groups)
        _, merged, left_index, right_index = min(choices)
        groups = [group for index, group in enumerate(groups) if index not in (left_index, right_index)]
        groups.append(merged)
        groups.sort()


class ReviewPlanner:
    def __init__(self, graph_reader: GraphReader | None = None):
        self.graph_reader = graph_reader

    async def plan(self, parts: Sequence[ChangePart]) -> ReviewPlan:
        diagnostics: list[str] = []
        by_path: dict[str, list[ChangePart]] = defaultdict(list)
        for part in sorted(parts, key=lambda item: (item.path, min(item.anchors, default=0), item.id)):
            by_path[part.path].append(part)

        async def read(pattern: str, target: str, path: str) -> list[Mapping[str, Any]]:
            if self.graph_reader is None:
                return []
            try:
                values = await self.graph_reader(pattern=pattern, target=target, focus_path=path)
                return [value for value in values if isinstance(value, Mapping)]
            except Exception as error:
                diagnostics.append(f"Graph {pattern} unavailable for {path}: {error}")
                return []

        if parts and self.graph_reader is None:
            diagnostics.append("Graph planning unavailable; using complete file scopes.")

        units_by_part: dict[str, list[dict[str, Any]]] = {}
        ownership_complete: dict[str, bool] = {}
        units_by_id: dict[str, dict[str, Any]] = {}
        for path, path_parts in by_path.items():
            raw_units = await read("file_summary", path, path)
            units = _unique([
                _unit({**unit, "path": unit.get("path") or path})
                for unit in raw_units
                if unit.get("unitId") and (not unit.get("path") or unit.get("path") == path)
            ])
            for part in path_parts:
                affected, complete = _affected_units(part, units)
                units_by_part[part.id] = affected
                ownership_complete[part.id] = complete
                for unit in affected:
                    units_by_id[str(unit["unitId"])] = unit
            if self.graph_reader is not None and not units:
                diagnostics.append(f"No structural units available for {path}; using complete file scope.")
            elif self.graph_reader is not None and any(not ownership_complete[part.id] for part in path_parts):
                diagnostics.append(f"Changed lines lack proposed-tree structural ownership in {path}; using complete file scope.")

        relations_by_unit: dict[str, list[dict[str, Any]]] = {}
        for unit_id in sorted(units_by_id):
            unit = units_by_id[unit_id]
            relations_by_unit[unit_id] = _unique([
                _relation(relation)
                for relation in await read("relations_of", unit_id, str(unit["path"]))
            ])

        graph_context = {
            part.id: {
                "units": units_by_part[part.id],
                "structuralOwnershipComplete": ownership_complete[part.id],
                "relations": _unique([
                    relation for unit in units_by_part[part.id]
                    for relation in relations_by_unit.get(str(unit["unitId"]), [])
                ]),
            }
            for path_parts in by_path.values() for part in path_parts
        }
        all_relations = _unique([
            relation for relations in relations_by_unit.values() for relation in relations
            if is_contract_relation(relation)
        ])
        affected_paths = {unit_id: str(unit["path"]) for unit_id, unit in units_by_id.items()}
        direct_edges: dict[frozenset[str], int] = defaultdict(int)
        for relation in all_relations:
            endpoints = [relation[name] for name in ("source", "target")]
            # A unit elsewhere in a changed file is not a changed contract. A
            # path-only edge remains a cross-batch navigation hint below.
            endpoint_paths = [affected_paths.get(str(endpoint.get("unitId") or "")) for endpoint in endpoints]
            if all(endpoint_paths) and endpoint_paths[0] != endpoint_paths[1]:
                direct_edges[frozenset(endpoint_paths)] += 1
        batches = [ReviewBatch(
            _identity("batch", [part.id for path in paths for part in by_path[path]]),
            tuple(part for path in paths for part in by_path[path]),
        ) for paths in _contract_groups(list(by_path), direct_edges)]

        batches_by_unit: dict[str, set[str]] = defaultdict(set)
        batches_by_path: dict[str, set[str]] = defaultdict(set)
        for batch in batches:
            for part in batch.parts:
                batches_by_path[part.path].add(batch.id)
                for unit in units_by_part[part.id]:
                    batches_by_unit[str(unit["unitId"])].add(batch.id)

        scopes: list[CrossBatchScope] = []

        def add_scope(batch_ids: set[str], relations: list[dict[str, Any]], reason: str) -> None:
            if len(batch_ids) > 1:
                ids = tuple(sorted(batch_ids))
                scopes.append(CrossBatchScope(
                    _identity("scope", [reason, *ids]), ids, tuple(_unique(relations)), reason,
                ))

        incomplete_paths = {part.path for part in parts if not ownership_complete[part.id]}

        def endpoint_batches(endpoint: Mapping[str, Any]) -> set[str]:
            unit_id = str(endpoint.get("unitId") or "")
            if unit_id in batches_by_unit:
                return batches_by_unit[unit_id]
            path = str(endpoint.get("path") or "")
            # Known unchanged units in otherwise structurally owned files do
            # not make unrelated edits part of this contract. Path fallback is
            # for incomplete ownership (including deletions) or path-only edges.
            if unit_id and path not in incomplete_paths:
                return set()
            return batches_by_path.get(path, set())

        direct: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
        shared: dict[str, tuple[set[str], list[dict[str, Any]]]] = {}
        for relation in all_relations:
            source, target = relation["source"], relation["target"]
            left, right = endpoint_batches(source), endpoint_batches(target)
            if left and right:
                direct[tuple(sorted(left | right))].append(relation)
            elif left or right:
                unchanged = target if left else source
                unit_id = str(unchanged.get("unitId") or "")
                if unit_id:
                    neighbors, edges = shared.setdefault(unit_id, (set(), []))
                    neighbors.update(left or right)
                    edges.append(relation)
        for batch_ids, relations in sorted(direct.items()):
            add_scope(set(batch_ids), relations, "Changed structural dependency")
        for unit_id, (batch_ids, relations) in sorted(shared.items()):
            add_scope(batch_ids, relations, f"Shared unchanged structural dependency: {unit_id}")

        related: dict[str, set[str]] = defaultdict(set)
        for scope in scopes:
            for batch_id in scope.batch_ids:
                related[batch_id].update(set(scope.batch_ids) - {batch_id})
        companions: dict[str, dict[str, ChangePart]] = defaultdict(dict)
        for scope in scopes:
            if scope.reason != "Changed structural dependency":
                continue
            for batch in batches:
                if batch.id not in scope.batch_ids:
                    continue
                for other in batches:
                    if other.id == batch.id or other.id not in scope.batch_ids:
                        continue
                    endpoints = [relation[name] for relation in scope.relations for name in ("source", "target")]
                    for part in other.parts:
                        owned_ids = {str(unit["unitId"]) for unit in units_by_part[part.id]}
                        if any(str(endpoint.get("unitId") or "") in owned_ids
                               or (not endpoint.get("unitId") and endpoint.get("path") == part.path)
                               for endpoint in endpoints):
                            companions[batch.id][part.id] = part
        return ReviewPlan(
            batches=tuple(ReviewBatch(
                batch.id, batch.parts, tuple(sorted(related[batch.id])),
                tuple(companions[batch.id][key] for key in sorted(companions[batch.id])),
            ) for batch in batches),
            graph_context=graph_context,
            cross_batch_scopes=tuple(scopes),
            diagnostics=tuple(dict.fromkeys(diagnostics)),
        )
