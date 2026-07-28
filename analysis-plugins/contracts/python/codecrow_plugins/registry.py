from __future__ import annotations

import hashlib
import heapq
import json
from collections import defaultdict
from typing import Iterable

from .api import Capability, CodeCrowPlugin, PluginDescriptor, PluginKind


class PluginRegistry:
    """Validated, immutable registry with dependency-stable ordering."""

    def __init__(self, descriptors: Iterable[PluginDescriptor]):
        by_id: dict[str, PluginDescriptor] = {}
        for descriptor in descriptors:
            if descriptor.id in by_id:
                raise ValueError(f"duplicate plugin id: {descriptor.id}")
            by_id[descriptor.id] = descriptor
        self._by_id = dict(sorted(by_id.items()))
        self._validate_requirements()
        self._ordered_ids = self._topological_order()
        self._validate_framework_dependencies()
        self._fingerprint = self._calculate_fingerprint()

    @classmethod
    def from_plugins(cls, plugins: Iterable[CodeCrowPlugin]) -> "PluginRegistry":
        return cls(plugin.descriptor for plugin in plugins)

    @property
    def ordered_ids(self) -> tuple[str, ...]:
        return self._ordered_ids

    @property
    def descriptors(self) -> tuple[PluginDescriptor, ...]:
        return tuple(self._by_id[plugin_id] for plugin_id in self._ordered_ids)

    @property
    def fingerprint(self) -> str:
        return self._fingerprint

    def fingerprint_for(self, plugin_ids: Iterable[str]) -> str:
        """Fingerprint only the dependency-closed descriptors in use."""
        return self._calculate_fingerprint(self.resolve(plugin_ids))

    def descriptor(self, plugin_id: str) -> PluginDescriptor:
        try:
            return self._by_id[plugin_id]
        except KeyError as exception:
            raise KeyError(f"unknown plugin id: {plugin_id}") from exception

    def contains(self, plugin_id: str) -> bool:
        return plugin_id in self._by_id

    def resolve(self, requested_ids: Iterable[str]) -> tuple[PluginDescriptor, ...]:
        closure: set[str] = set()

        def include(plugin_id: str) -> None:
            if plugin_id not in self._by_id:
                raise ValueError(f"unknown requested plugin: {plugin_id}")
            if plugin_id in closure:
                return
            for requirement in self._by_id[plugin_id].requires:
                include(requirement)
            closure.add(plugin_id)

        for plugin_id in requested_ids:
            include(plugin_id)
        return tuple(self._by_id[plugin_id] for plugin_id in self._ordered_ids if plugin_id in closure)

    def for_capability(
        self,
        capability: Capability,
        active_ids: Iterable[str] | None = None,
    ) -> tuple[PluginDescriptor, ...]:
        descriptors = self.descriptors if active_ids is None else self.resolve(active_ids)
        return tuple(descriptor for descriptor in descriptors if capability in descriptor.capabilities)

    def _validate_requirements(self) -> None:
        for descriptor in self._by_id.values():
            missing = [required for required in descriptor.requires if required not in self._by_id]
            if missing:
                raise ValueError(f"plugin {descriptor.id} requires missing plugins: {missing}")

    def _topological_order(self) -> tuple[str, ...]:
        indegree = {plugin_id: 0 for plugin_id in self._by_id}
        dependants: dict[str, list[str]] = defaultdict(list)
        for descriptor in self._by_id.values():
            indegree[descriptor.id] = len(descriptor.requires)
            for requirement in descriptor.requires:
                dependants[requirement].append(descriptor.id)
        queue = [plugin_id for plugin_id, degree in indegree.items() if degree == 0]
        heapq.heapify(queue)
        ordered: list[str] = []
        while queue:
            plugin_id = heapq.heappop(queue)
            ordered.append(plugin_id)
            for dependant in sorted(dependants.get(plugin_id, ())):
                indegree[dependant] -= 1
                if indegree[dependant] == 0:
                    heapq.heappush(queue, dependant)
        if len(ordered) != len(self._by_id):
            cycle_ids = sorted(plugin_id for plugin_id, degree in indegree.items() if degree > 0)
            raise ValueError(f"plugin dependency cycle: {cycle_ids}")
        return tuple(ordered)

    def _validate_framework_dependencies(self) -> None:
        def has_language_dependency(plugin_id: str, visited: set[str]) -> bool:
            if plugin_id in visited:
                return False
            visited.add(plugin_id)
            descriptor = self._by_id[plugin_id]
            return any(
                self._by_id[required].kind is PluginKind.LANGUAGE
                or has_language_dependency(required, visited)
                for required in descriptor.requires
            )

        for descriptor in self._by_id.values():
            if descriptor.kind is PluginKind.FRAMEWORK and not has_language_dependency(descriptor.id, set()):
                raise ValueError(f"framework plugin {descriptor.id} must depend on a language plugin")

    def _calculate_fingerprint(
        self,
        descriptors: Iterable[PluginDescriptor] | None = None,
    ) -> str:
        projection = []
        for descriptor in descriptors if descriptors is not None else self.descriptors:
            projection.append(
                {
                    "id": descriptor.id,
                    "kind": descriptor.kind.value,
                    "requires": list(descriptor.requires),
                    "capabilities": [value.value for value in descriptor.capabilities],
                    "detection": {
                        "extensions": list(descriptor.detection.extensions),
                        "filesAll": list(descriptor.detection.files_all),
                        "filesAny": list(descriptor.detection.files_any),
                        "contentMarkers": [
                            {"path": marker.path, "contains": marker.contains}
                            for marker in descriptor.detection.content_markers
                        ],
                        "alternatives": [
                            dict({
                                "filesAll": list(alternative.files_all),
                                "filesAny": list(alternative.files_any),
                                "pathPatternsAll": list(alternative.path_patterns_all),
                                "pathPatternsAny": list(alternative.path_patterns_any),
                                "contentMarkers": [
                                    {"path": marker.path, "contains": marker.contains}
                                    for marker in alternative.content_markers
                                ],
                            }, **({"contentPatternMarkers": [
                                    {
                                        "pathPattern": marker.path_pattern,
                                        "contains": marker.contains,
                                    }
                                    for marker in alternative.content_pattern_markers
                                ]} if alternative.content_pattern_markers else {}))
                            for alternative in descriptor.detection.alternatives
                        ],
                    },
                    "entrypoints": dict(descriptor.entrypoints),
                }
            )
        encoded = json.dumps(projection, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
