from __future__ import annotations

import json
from dataclasses import dataclass, replace

from .api import (
    Capability,
    FileArtifact,
    GraphFact,
    OutcomeStatus,
    PluginDiagnostic,
    PluginKind,
    ProjectCapabilities,
)
from .catalog import PluginCatalog
from .scope import (
    plugin_root_for_path,
    rebase_diagnostic,
    rebase_fact,
    relative_to_root,
)


@dataclass(frozen=True)
class GraphFactLimits:
    facts_per_file: int
    framework_facts_per_file: int
    string_length: int
    artifact_bytes: int


class GraphFactComposer:
    """Compose plugin-owned file facts and their recoverable diagnostics."""

    def __init__(self, catalog: PluginCatalog, limits: GraphFactLimits) -> None:
        self.catalog = catalog
        self.limits = limits

    def graph_facts(
        self,
        artifact: FileArtifact,
        capabilities: ProjectCapabilities,
    ) -> tuple[tuple[GraphFact, ...], tuple[PluginDiagnostic, ...]]:
        contributions: list[tuple[PluginKind, str, tuple[GraphFact, ...]]] = []
        diagnostics: list[PluginDiagnostic] = []
        rejected: dict[str, list[int]] = {}
        for plugin_id in capabilities.repository_plugins:
            descriptor = self.catalog.registry.descriptor(plugin_id)
            plugin_root = plugin_root_for_path(
                descriptor.kind,
                plugin_id,
                artifact.path,
                capabilities,
            )
            if plugin_root is None:
                continue
            if not ({Capability.INDEX, Capability.GRAPH} & set(descriptor.capabilities)):
                continue
            implementation = self.catalog.implementation(plugin_id)
            contributor = getattr(implementation, "index_file", None)
            if contributor is None:
                continue
            plugin_artifact = (
                artifact
                if not plugin_root
                else FileArtifact(
                    relative_to_root(artifact.path, plugin_root),
                    artifact.content,
                    artifact.deleted,
                )
            )
            try:
                outcome = contributor(plugin_artifact)
            except Exception as exception:
                diagnostics.append(
                    PluginDiagnostic(
                        code="plugin-index-exception",
                        message=f"{type(exception).__name__}: {exception}",
                        plugin_id=plugin_id,
                    )
                )
                continue
            try:
                if outcome.status is OutcomeStatus.FAILED:
                    diagnostics.append(rebase_diagnostic(
                        outcome.diagnostic,
                        plugin_root,
                    ))
                elif outcome.status is OutcomeStatus.HANDLED:
                    valid_facts = []
                    overlong_count = 0
                    for raw_fact in tuple(outcome.value):
                        fact = rebase_fact(raw_fact, plugin_root)
                        if self._fact_has_overlong_string(fact):
                            overlong_count += 1
                        else:
                            valid_facts.append(fact)
                    if overlong_count:
                        rejected.setdefault(plugin_id, [0, 0, 0])[0] += overlong_count
                    unique_facts = self._merge_semantic_facts(
                        tuple(valid_facts),
                        plugin_id,
                    )
                    contribution_limit = (
                        self.limits.framework_facts_per_file
                        if descriptor.kind is PluginKind.FRAMEWORK
                        else self.limits.facts_per_file
                    )
                    selected_facts = self._balanced_facts(
                        unique_facts,
                        contribution_limit,
                    )
                    if len(unique_facts) > len(selected_facts):
                        rejected.setdefault(plugin_id, [0, 0, 0])[2] += (
                            len(unique_facts) - len(selected_facts)
                        )
                    contributions.append((
                        descriptor.kind,
                        plugin_id,
                        selected_facts,
                    ))
            except Exception as exception:
                diagnostics.append(PluginDiagnostic(
                    code="plugin-index-invalid-result",
                    message=f"{type(exception).__name__}: {exception}",
                    plugin_id=plugin_id,
                    path=artifact.path,
                    recoverable=True,
                ))
        facts: dict[GraphFact, GraphFact] = {}
        serialized_bytes = 2  # Opening and closing brackets of the JSON array.
        for _, plugin_id, contribution in sorted(
            contributions,
            key=lambda item: (
                1 if item[0] is PluginKind.LANGUAGE else 0,
                item[1],
            ),
        ):
            for fact in contribution:
                if fact in facts:
                    current = facts[fact]
                    merged = self._merge_fact_contributors(
                        current,
                        fact.contributing_plugin_ids,
                    )
                    added_bytes = (
                        self._serialized_fact_bytes(merged)
                        - self._serialized_fact_bytes(current)
                    )
                    if (
                        serialized_bytes + added_bytes
                        > self.limits.artifact_bytes
                    ):
                        rejected.setdefault(plugin_id, [0, 0, 0])[1] += 1
                        continue
                    facts[fact] = merged
                    serialized_bytes += added_bytes
                    continue
                if len(facts) >= self.limits.facts_per_file:
                    rejected.setdefault(plugin_id, [0, 0, 0])[2] += 1
                    continue
                fact_bytes = self._serialized_fact_bytes(fact)
                added_bytes = fact_bytes + (1 if facts else 0)
                if (
                    serialized_bytes + added_bytes
                    > self.limits.artifact_bytes
                ):
                    rejected.setdefault(plugin_id, [0, 0, 0])[1] += 1
                    continue
                facts[fact] = fact
                serialized_bytes += added_bytes
        for plugin_id, (overlong_count, byte_count, count_limit) in sorted(
            rejected.items()
        ):
            reasons = []
            if overlong_count:
                reasons.append(
                    f"{overlong_count} fact(s) containing a string longer than "
                    f"{self.limits.string_length} characters"
                )
            if byte_count:
                reasons.append(
                    f"{byte_count} fact(s) exceeding the "
                    f"{self.limits.artifact_bytes}-byte artifact budget"
                )
            if count_limit:
                reasons.append(
                    f"{count_limit} fact(s) exceeding per-plugin or artifact "
                    "fact admission"
                )
            diagnostics.append(PluginDiagnostic(
                code="plugin-index-output-limit",
                message="graph output rejected " + " and ".join(reasons),
                plugin_id=plugin_id,
                path=artifact.path,
                recoverable=True,
            ))
        return tuple(sorted(facts.values())), tuple(diagnostics)

    def _fact_has_overlong_string(self, fact: GraphFact) -> bool:
        strings = (
            fact.kind,
            fact.source,
            fact.relation,
            fact.target,
            fact.path,
            *(value for attribute in fact.attributes for value in attribute),
            *fact.related_paths,
            *fact.contributing_plugin_ids,
        )
        return any(
            len(value) > self.limits.string_length
            for value in strings
        )

    @staticmethod
    def _serialized_fact_bytes(fact: GraphFact) -> int:
        return len(json.dumps(
            dict(fact.as_metadata()),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8"))

    @classmethod
    def _merge_semantic_facts(
        cls,
        facts: tuple[GraphFact, ...],
        contributing_plugin_id: str,
    ) -> tuple[GraphFact, ...]:
        merged: dict[GraphFact, GraphFact] = {}
        for fact in facts:
            attributed = cls._merge_fact_contributors(
                fact,
                (contributing_plugin_id,),
            )
            current = merged.get(attributed)
            merged[attributed] = (
                attributed
                if current is None
                else cls._merge_fact_contributors(
                    current,
                    attributed.contributing_plugin_ids,
                )
            )
        return tuple(sorted(merged.values()))

    @staticmethod
    def _merge_fact_contributors(
        fact: GraphFact,
        contributing_plugin_ids: tuple[str, ...],
    ) -> GraphFact:
        merged = tuple(sorted({
            *fact.contributing_plugin_ids,
            *contributing_plugin_ids,
        }))
        if merged == fact.contributing_plugin_ids:
            return fact
        return replace(fact, contributing_plugin_ids=merged)

    @staticmethod
    def _balanced_facts(
        facts: tuple[GraphFact, ...],
        limit: int,
    ) -> tuple[GraphFact, ...]:
        """Bound noisy contributors without starving a semantic fact kind."""
        by_kind: dict[str, list[GraphFact]] = {}
        for fact in sorted(set(facts)):
            by_kind.setdefault(fact.kind, []).append(fact)
        selected: list[GraphFact] = []
        offset = 0
        kinds = tuple(sorted(by_kind))
        while len(selected) < limit:
            added = False
            for kind in kinds:
                values = by_kind[kind]
                if offset < len(values):
                    selected.append(values[offset])
                    added = True
                    if len(selected) == limit:
                        break
            if not added:
                break
            offset += 1
        return tuple(selected)
