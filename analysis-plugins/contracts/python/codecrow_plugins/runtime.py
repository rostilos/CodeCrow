from __future__ import annotations

from .api import (
    CandidateClaim,
    Capability,
    FileArtifact,
    FileDisposition,
    GraphFact,
    OutcomeStatus,
    PluginDiagnostic,
    ProjectCapabilities,
    RepositorySnapshot,
    ReviewContribution,
    SyntaxContribution,
    ValidationResult,
)
from .catalog import PluginCatalog
from .graph_composition import GraphFactComposer
from .repository_runtime import RepositoryAnalysisHandle
from .scope import plugin_root_for_path, relative_to_root


class PluginRuntime:
    """Host-side composition. Implementations return data; the host owns policy."""

    MAX_REVIEW_PATHS_PER_PLUGIN = 80
    MAX_RULES = 40
    MAX_EVIDENCE_REQUESTS = 80
    MAX_REPOSITORY_SYMBOLS = 250_000
    MAX_ARCHITECTURE_PACKETS = 100_000

    def __init__(self, catalog: PluginCatalog):
        self.catalog = catalog

    def repository_analysis_plugins(
        self,
        capabilities: ProjectCapabilities,
    ) -> tuple[str, ...]:
        """Return selected plugins that own repository-scoped analysis state."""
        selected = []
        for plugin_id in capabilities.repository_plugins:
            implementation = self.catalog.implementation(plugin_id)
            if (
                callable(getattr(implementation, "start_repository_analysis", None))
                or callable(getattr(implementation, "restore_repository_analysis", None))
            ):
                selected.append(plugin_id)
        return tuple(selected)

    def start_repository_analysis(
        self,
        capabilities: ProjectCapabilities,
        revision: str,
        snapshots: tuple[RepositorySnapshot, ...] = (),
        source_root: str | None = None,
    ) -> "RepositoryAnalysisHandle":
        sessions: list[tuple[str, object]] = []
        diagnostics: list[PluginDiagnostic] = []
        for plugin_id in capabilities.repository_plugins:
            implementation = self.catalog.implementation(plugin_id)
            plugin_snapshots = tuple(
                snapshot for snapshot in snapshots
                if snapshot.plugin_id == plugin_id
            )
            starter = (
                getattr(implementation, "restore_repository_analysis", None)
                if plugin_snapshots
                else None
            ) or getattr(implementation, "start_repository_analysis", None)
            if starter is None:
                continue
            try:
                outcome = (
                    starter(revision, plugin_snapshots)
                    if plugin_snapshots
                    else starter(revision)
                )
            except Exception as exception:
                diagnostics.append(PluginDiagnostic(
                    code="plugin-repository-start-exception",
                    message=f"{type(exception).__name__}: {exception}",
                    plugin_id=plugin_id,
                ))
                continue
            if outcome.status is OutcomeStatus.FAILED:
                diagnostics.append(outcome.diagnostic)
            elif outcome.status is OutcomeStatus.HANDLED:
                configure_root = getattr(outcome.value, "set_source_root", None)
                if configure_root is not None:
                    try:
                        configure_root(source_root)
                    except Exception as exception:
                        diagnostics.append(PluginDiagnostic(
                            code="plugin-repository-root-exception",
                            message=f"{type(exception).__name__}: {exception}",
                            plugin_id=plugin_id,
                        ))
                        continue
                sessions.append((plugin_id, outcome.value))
        return RepositoryAnalysisHandle(self, sessions, diagnostics)

    def file_disposition(
        self,
        path: str,
        capabilities: ProjectCapabilities,
    ) -> FileDisposition:
        """Compose framework file policies without exposing implementations to hosts."""
        disposition = FileDisposition.FULL
        for plugin_id in capabilities.repository_plugins:
            descriptor = self.catalog.registry.descriptor(plugin_id)
            plugin_root = plugin_root_for_path(
                descriptor.kind,
                plugin_id,
                path,
                capabilities,
            )
            if plugin_root is None:
                continue
            if Capability.FILE_POLICY not in descriptor.capabilities:
                continue
            implementation = self.catalog.implementation(plugin_id)
            contributor = getattr(implementation, "file_disposition", None)
            if contributor is None:
                continue
            outcome = contributor(relative_to_root(path, plugin_root))
            if outcome.status is OutcomeStatus.FAILED:
                raise RuntimeError(
                    f"plugin file policy failed for {path}: {outcome.diagnostic.code}"
                )
            if outcome.status is not OutcomeStatus.HANDLED:
                continue
            if not isinstance(outcome.value, FileDisposition):
                raise TypeError(f"plugin {plugin_id} returned an invalid file disposition")
            if outcome.value is FileDisposition.EXCLUDED:
                return FileDisposition.EXCLUDED
            if outcome.value is FileDisposition.GENERATED:
                disposition = FileDisposition.GENERATED
            if outcome.value is FileDisposition.ARCHITECTURE_ONLY:
                if disposition is FileDisposition.FULL:
                    disposition = FileDisposition.ARCHITECTURE_ONLY
        return disposition

    def syntax_contribution(
        self,
        path: str,
        capabilities: ProjectCapabilities,
    ) -> tuple[SyntaxContribution | None, tuple[PluginDiagnostic, ...]]:
        """Resolve one selected plugin-owned syntax declaration for a file."""
        # File assignments are the authoritative language dispatch boundary.
        # A repository can select several language plugins while still
        # containing extensionless, configuration, generated, or otherwise
        # unsupported files. Those paths have no entry and must use the neutral
        # host fallback; offering every repository language here makes any
        # polyglot repository an artificial parser conflict.
        selected = capabilities.file_plugins.get(path, ())
        contributions: list[SyntaxContribution] = []
        diagnostics: list[PluginDiagnostic] = []
        for plugin_id in selected:
            descriptor = self.catalog.registry.descriptor(plugin_id)
            if Capability.SYNTAX not in descriptor.capabilities:
                continue
            implementation = self.catalog.implementation(plugin_id)
            contributor = getattr(implementation, "syntax", None)
            if contributor is None:
                continue
            try:
                outcome = contributor()
            except Exception as exception:
                diagnostics.append(PluginDiagnostic(
                    code="plugin-syntax-exception",
                    message=f"{type(exception).__name__}: {exception}",
                    plugin_id=plugin_id,
                ))
                continue
            if outcome.status is OutcomeStatus.FAILED:
                diagnostics.append(outcome.diagnostic)
                continue
            if outcome.status is not OutcomeStatus.HANDLED:
                continue
            contribution = outcome.value
            if not isinstance(contribution, SyntaxContribution):
                raise TypeError(
                    f"plugin {plugin_id} returned an invalid syntax contribution"
                )
            if contribution.plugin_id != plugin_id:
                raise ValueError(
                    f"plugin {plugin_id} returned syntax for "
                    f"{contribution.plugin_id}"
                )
            contributions.append(contribution)
        if len(contributions) > 1:
            raise RuntimeError(
                "conflicting syntax contributions for "
                f"{path}: "
                + ", ".join(
                    item.plugin_id for item in contributions
                )
            )
        return (
            contributions[0] if contributions else None,
            tuple(diagnostics),
        )

    def review_contribution(
        self,
        paths: tuple[str, ...],
        capabilities: ProjectCapabilities,
    ) -> tuple[ReviewContribution, tuple[PluginDiagnostic, ...]]:
        rules: set[str] = set()
        requests = set()
        groups = set()
        diagnostics: list[PluginDiagnostic] = []
        for plugin_id in capabilities.repository_plugins:
            descriptor = self.catalog.registry.descriptor(plugin_id)
            owned_paths = tuple(
                path for path in paths
                if plugin_root_for_path(
                    descriptor.kind,
                    plugin_id,
                    path,
                    capabilities,
                ) is not None
            )
            if not owned_paths:
                continue
            implementation = self.catalog.implementation(plugin_id)
            contributor = getattr(implementation, "review", None)
            if contributor is None:
                continue
            review_paths = owned_paths[: self.MAX_REVIEW_PATHS_PER_PLUGIN]
            if len(owned_paths) > len(review_paths):
                diagnostics.append(PluginDiagnostic(
                    code="plugin-review-input-limit",
                    message=(
                        f"review contribution admitted {len(review_paths)} of "
                        f"{len(owned_paths)} owned paths"
                    ),
                    plugin_id=plugin_id,
                    recoverable=True,
                ))
            try:
                outcome = contributor(review_paths)
            except Exception as exception:
                diagnostics.append(
                    PluginDiagnostic(
                        code="plugin-review-exception",
                        message=f"{type(exception).__name__}: {exception}",
                        plugin_id=plugin_id,
                    )
                )
                continue
            if outcome.status is OutcomeStatus.FAILED:
                diagnostics.append(outcome.diagnostic)
            elif outcome.status is OutcomeStatus.HANDLED:
                rules.update(outcome.value.rules)
                requests.update(outcome.value.evidence_requests)
                groups.update(outcome.value.group_paths)
        selected_rules = tuple(sorted(rules)[: self.MAX_RULES])
        selected_requests = self._balanced_evidence_requests(
            tuple(requests),
            self.MAX_EVIDENCE_REQUESTS,
        )
        omitted_rules = len(rules) - len(selected_rules)
        omitted_requests = len(requests) - len(selected_requests)
        if omitted_rules or omitted_requests:
            diagnostics.append(PluginDiagnostic(
                code="plugin-review-output-limit",
                message=(
                    f"review contribution omitted {omitted_rules} rule(s) and "
                    f"{omitted_requests} evidence request(s) beyond the "
                    "aggregate admission"
                ),
                recoverable=True,
            ))
        return (
            ReviewContribution(
                rules=selected_rules,
                evidence_requests=selected_requests,
                group_paths=tuple(sorted(groups)),
            ),
            tuple(diagnostics),
        )

    @staticmethod
    def _balanced_evidence_requests(
        requests: tuple,
        limit: int,
    ) -> tuple:
        """Bound exact requests without allowing one kind to starve another."""
        by_kind: dict[str, list] = {}
        for request in sorted(set(requests)):
            by_kind.setdefault(request.kind, []).append(request)
        selected = []
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
        return tuple(sorted(selected))

    def validate(
        self,
        claim: CandidateClaim,
        capabilities: ProjectCapabilities,
    ) -> tuple[ValidationResult, ...]:
        return self.validate_with_diagnostics(claim, capabilities)[0]

    def validate_with_diagnostics(
        self,
        claim: CandidateClaim,
        capabilities: ProjectCapabilities,
    ) -> tuple[tuple[ValidationResult, ...], tuple[PluginDiagnostic, ...]]:
        results: list[ValidationResult] = []
        diagnostics: list[PluginDiagnostic] = []
        for plugin_id in capabilities.repository_plugins:
            descriptor = self.catalog.registry.descriptor(plugin_id)
            if plugin_root_for_path(
                descriptor.kind,
                plugin_id,
                claim.path,
                capabilities,
            ) is None:
                continue
            implementation = self.catalog.implementation(plugin_id)
            validator = getattr(implementation, "validate", None)
            if validator is None:
                continue
            try:
                outcome = validator(claim)
            except Exception as exception:
                diagnostics.append(PluginDiagnostic(
                    code="plugin-validation-exception",
                    message=f"{type(exception).__name__}: {exception}",
                    plugin_id=plugin_id,
                ))
                continue
            if outcome.status is OutcomeStatus.FAILED:
                diagnostics.append(outcome.diagnostic)
            if outcome.status is OutcomeStatus.HANDLED:
                results.append(outcome.value)
        return tuple(results), tuple(diagnostics)

    def graph_facts(
        self,
        artifact: FileArtifact,
        capabilities: ProjectCapabilities,
    ) -> tuple[tuple[GraphFact, ...], tuple[PluginDiagnostic, ...]]:
        composer = GraphFactComposer(self.catalog)
        return composer.graph_facts(artifact, capabilities)
