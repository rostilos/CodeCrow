from __future__ import annotations

from dataclasses import replace

from .api import (
    Capability,
    FileArtifact,
    GraphFact,
    OutcomeStatus,
    PluginDiagnostic,
    ProjectCapabilities,
)
from .catalog import PluginCatalog
from .scope import (
    plugin_root_for_path,
    rebase_diagnostic,
    rebase_fact,
    relative_to_root,
)


class GraphFactComposer:
    """Merge complete plugin facts for storage, preserving contributor provenance.

    Indexing records repository evidence; query-time relevance determines which
    facts a review needs. Fact length, count, and serialized size do not decide
    whether that evidence is retained.
    """

    def __init__(self, catalog: PluginCatalog) -> None:
        self.catalog = catalog

    def graph_facts(
        self,
        artifact: FileArtifact,
        capabilities: ProjectCapabilities,
    ) -> tuple[tuple[GraphFact, ...], tuple[PluginDiagnostic, ...]]:
        contributions: list[tuple[GraphFact, ...]] = []
        diagnostics: list[PluginDiagnostic] = []
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
                    contributions.append(self._merge_semantic_facts(
                        tuple(rebase_fact(fact, plugin_root) for fact in outcome.value),
                        plugin_id,
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
        for contribution in contributions:
            for fact in contribution:
                current = facts.get(fact)
                facts[fact] = (
                    fact
                    if current is None
                    else self._merge_fact_contributors(
                        current,
                        fact.contributing_plugin_ids,
                    )
                )
        return tuple(sorted(facts.values())), tuple(diagnostics)

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
