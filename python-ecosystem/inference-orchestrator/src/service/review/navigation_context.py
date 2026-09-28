"""Model-facing graph navigation, separate from stored graph/tenant identity.

Every returned node, edge, plugin attribute and coverage/continuation fact stays
available. Host-only snapshot attestation and scoring implementation are omitted;
opaque storage IDs become short case-local handles understood by the tool host.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
from typing import Any


def _encoded(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


_UNIT_OCCURRENCE_FIELDS = frozenset({
    "depth", "impactScore", "connectionEvidenceId", "sourceEvidenceId", "reason", "continuation",
})


def _unit_definition(value: dict[str, Any]) -> dict[str, Any]:
    # A node/root/frontier occurrence may add traversal annotations to the same
    # source identity. Keep those at the occurrence while sharing its identity.
    return {key: item for key, item in value.items() if key not in _UNIT_OCCURRENCE_FIELDS}


def compact_navigation_result(result: dict[str, Any]) -> dict[str, Any]:
    """Factor exact repeated units; keep every graph record and source byte.

    References retain the actual unitId for direct structural reads. unitRef is
    a short definition key. Semantic comparison expands that key before comparing
    observations. Existing definition envelopes are left untouched.
    """
    if "unitDefinitions" in result:
        return result
    counts: Counter[str] = Counter()
    units: dict[str, dict[str, Any]] = {}
    reserved: set[str] = set()

    def count(value: Any) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("unitRef"), str):
                reserved.add(value["unitRef"])
            if isinstance(value.get("unitId"), str) and len(value) > 1 and "unitRef" not in value:
                definition = _unit_definition(value)
                identity = _encoded(definition)
                counts[identity] += 1
                units[identity] = definition
            for key, item in value.items():
                if key in _GRAPH_CONTAINERS:
                    count(item)
        elif isinstance(value, list):
            for item in value:
                count(item)

    count(result)
    references = {identity: f"definition@{index}"
                  for index, identity in enumerate(sorted(identity for identity, occurrences in counts.items()
                                                          if occurrences > 1), 1)}
    references = {identity: reference for identity, reference in references.items() if reference not in reserved}
    if not references:
        return result
    definitions: dict[str, dict[str, Any]] = {}

    def project(value: Any) -> Any:
        if isinstance(value, dict):
            if isinstance(value.get("unitId"), str) and "unitRef" not in value:
                identity = _encoded(_unit_definition(value))
                reference = references.get(identity)
                if reference is not None:
                    definitions[reference] = deepcopy(units[identity])
                    return {"unitId": value["unitId"], "unitRef": reference,
                            **{key: project(item) for key, item in value.items()
                               if key in _UNIT_OCCURRENCE_FIELDS}}
            return {key: project(item) if key in _GRAPH_CONTAINERS else deepcopy(item)
                    for key, item in value.items()}
        if isinstance(value, list):
            return [project(item) for item in value]
        return value

    projected = project(result)
    projected["unitDefinitions"] = definitions
    # Tiny unit records can cost more as references. Choose the smaller exact
    # representation; this does not impose a content, result, or token quota.
    return projected if len(_encoded(projected)) < len(_encoded(result)) else result



# These are checked by the host when binding the snapshot. The model cannot
# select a tenant/generation or use these attestation values in any tool.
_SNAPSHOT_HOST_FIELDS = frozenset({
    "revision", "generationManifestSha256", "baseRevision", "baseCollectionTarget",
    "baseGenerationManifestSha256", "sourceRevision", "targetSourceTreeSha256",
    "overlaySha256",
})
_UNIT_REFERENCE_FIELDS = frozenset({
    "unitId", "sourceUnitId", "targetUnitId", "fromUnitId", "toUnitId", "rootUnitId",
})
_EDGE_REFERENCE_FIELDS = frozenset({"evidenceId", "connectionEvidenceId", "sourceEvidenceId"})
_UNIT_REFERENCE_LISTS = frozenset({"selectedUnitIds", "omittedUnitIds"})
_EDGE_REFERENCE_LISTS = frozenset({"relationEvidenceIds"})
# Descend only through the neutral navigation contract. An unknown plugin field
# may contain keys such as unitId or content; those remain opaque literal data.
_GRAPH_CONTAINERS = frozenset({
    "results", "resolvedUnits", "candidates", "units", "nodes", "edges", "relationships",
    "frontier", "roots", "connections", "sourceWindows", "anchors", "symbols",
    "ambiguousTargets", "sourceUnit", "targetUnit", "unit", "coverage", "source",
})
_GRAPH_TARGET_ARGUMENTS = {
    "queryCodeGraph": ("target",), "getStructuralUnit": ("unitId",),
    "getImpactRadius": ("targets",), "traverseCodeGraph": ("start",),
    "getMinimalReviewContext": ("focusSymbols",),
}


class NavigationContext:
    """One evidence case's handles; never store these in the shared raw cache."""

    def __init__(self) -> None:
        self.identities: dict[str, str] = {}
        self.targets: dict[str, str] = {}
        self._counts = {"unit": 0, "edge": 0}

    def _register(self, value: Any, kind: str) -> None:
        if not isinstance(value, str) or not value or value in self.identities:
            return
        self._counts[kind] += 1
        handle = f"{kind}@{self._counts[kind]}"
        self.identities[value] = handle
        self.targets[handle] = value

    def resolve_arguments(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Only graph target parameters accept handles; source/grep text is literal."""
        fields = _GRAPH_TARGET_ARGUMENTS.get(name, ())
        result = dict(arguments)
        for field in fields:
            value = result.get(field)
            if isinstance(value, str):
                result[field] = self.targets.get(value, value)
            elif isinstance(value, list):
                result[field] = [self.targets.get(item, item) if isinstance(item, str) else item for item in value]
        return result

    def project(self, result: dict[str, Any]) -> dict[str, Any]:
        def collect(value: Any) -> None:
            if isinstance(value, list):
                for item in value:
                    collect(item)
            elif isinstance(value, dict):
                for key, item in value.items():
                    if key in _UNIT_REFERENCE_FIELDS:
                        self._register(item, "unit")
                    elif key in _EDGE_REFERENCE_FIELDS:
                        self._register(item, "edge")
                    elif key in _UNIT_REFERENCE_LISTS | _EDGE_REFERENCE_LISTS and isinstance(item, list):
                        for identity in item:
                            self._register(identity, "unit" if key in _UNIT_REFERENCE_LISTS else "edge")
                    elif key in _GRAPH_CONTAINERS:
                        collect(item)
                    elif key == "unitDefinitions" and isinstance(item, dict):
                        for definition in item.values():
                            collect(definition)

        collect(result)
        if isinstance(result.get("impactScores"), dict):
            for identity in result["impactScores"]:
                self._register(identity, "unit")

        def reference(value: Any) -> Any:
            return self.identities.get(value, value) if isinstance(value, str) else deepcopy(value)

        def references(value: Any) -> Any:
            return [reference(item) for item in value] if isinstance(value, list) else deepcopy(value)

        def continuation(value: Any) -> Any:
            if not isinstance(value, dict):
                return deepcopy(value)
            copied = deepcopy(value)
            arguments = copied.get("arguments")
            if isinstance(arguments, dict):
                for field in _GRAPH_TARGET_ARGUMENTS.get(copied.get("tool"), ()):
                    if field in arguments:
                        arguments[field] = references(arguments[field]) if isinstance(arguments[field], list) else reference(arguments[field])
            return copied

        def project(value: Any, *, envelope: bool = False) -> Any:
            if isinstance(value, list):
                return [project(item) for item in value]
            if not isinstance(value, dict):
                return deepcopy(value)
            projected = {}
            for key, item in value.items():
                if envelope and key == "scorePolicy":
                    continue
                if envelope and key == "snapshot" and isinstance(item, dict):
                    projected[key] = {field: deepcopy(detail) for field, detail in item.items()
                                      if field not in _SNAPSHOT_HOST_FIELDS}
                elif envelope and key == "impactScores" and isinstance(item, dict):
                    projected[key] = {reference(identity): deepcopy(score) for identity, score in item.items()}
                elif key in _UNIT_REFERENCE_FIELDS | _EDGE_REFERENCE_FIELDS:
                    projected[key] = reference(item)
                elif key in _UNIT_REFERENCE_LISTS | _EDGE_REFERENCE_LISTS:
                    projected[key] = references(item)
                elif envelope and key in {"targets", "unresolvedTargets", "focusSymbols"}:
                    projected[key] = references(item)
                elif envelope and key in {"target", "start"}:
                    projected[key] = reference(item)
                elif key == "continuation":
                    projected[key] = continuation(item)
                elif key == "continuations" and isinstance(item, list):
                    projected[key] = [continuation(operation) for operation in item]
                elif key == "unitDefinitions" and isinstance(item, dict):
                    projected[key] = {identity: project(definition) for identity, definition in item.items()}
                elif key in _GRAPH_CONTAINERS:
                    projected[key] = project(item)
                else:
                    projected[key] = deepcopy(item)
            return projected

        shown = project(result, envelope=True)
        raw_nodes = shown.get("nodes")
        nodes = {node.get("unitId"): node for node in (raw_nodes if isinstance(raw_nodes, list) else ())
                 if isinstance(node, dict) and isinstance(node.get("unitId"), str)}
        scores = shown.get("impactScores")
        if isinstance(scores, dict):
            remaining = {key: value for key, value in scores.items()
                         if key not in nodes or "impactScore" not in nodes[key] or nodes[key]["impactScore"] != value}
            if remaining:
                shown["impactScores"] = remaining
            else:
                shown.pop("impactScores", None)
        raw_connections = shown.get("connections")
        for connection in raw_connections if isinstance(raw_connections, list) else ():
            if not isinstance(connection, dict):
                continue
            # The path's result is already the returned score. Weight/decay are
            # implementation constants, not dependency or source evidence.
            connection.pop("edgeWeight", None)
            connection.pop("depthDecay", None)
            node = nodes.get(connection.get("unitId"), {})
            for key in ("depth", "impactScore"):
                if key in node and connection.get(key) == node[key]:
                    connection.pop(key, None)
        return compact_navigation_result(shown)


def expand_navigation_result(result: dict[str, Any]) -> dict[str, Any]:
    """Restore semantic records for host comparisons, not the model prompt."""
    definitions = result.get("unitDefinitions")
    if not isinstance(definitions, dict):
        return result

    def expand(value: Any) -> Any:
        if isinstance(value, dict):
            reference = value.get("unitRef")
            if isinstance(reference, str) and set(value) <= {"unitId", "unitRef"} | _UNIT_OCCURRENCE_FIELDS:
                definition = definitions.get(reference)
                if isinstance(definition, dict) and definition.get("unitId") == value["unitId"]:
                    return {**deepcopy(definition), **{key: expand(item) for key, item in value.items() if key != "unitRef"}}
            return {key: expand(item) if key in _GRAPH_CONTAINERS else deepcopy(item)
                    for key, item in value.items()}
        if isinstance(value, list):
            return [expand(item) for item in value]
        return value

    return {key: expand(value) if key in _GRAPH_CONTAINERS else deepcopy(value)
            for key, value in result.items() if key != "unitDefinitions"}
