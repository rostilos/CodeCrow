from __future__ import annotations

from .api import (
    ArchitecturePacket,
    CandidateClaim,
    Capability,
    FileArtifact,
    FileDisposition,
    GraphFact,
    OutcomeStatus,
    PluginDiagnostic,
    PluginKind,
    ProjectCapabilities,
    RepositoryAnalysis,
    RepositoryAnalysisMode,
    RepositoryContext,
    RepositorySnapshot,
    ReviewContribution,
    SyntaxContribution,
    ValidationResult,
)
from .catalog import PluginCatalog


class PluginRuntime:
    """Host-side composition. Implementations return data; the host owns policy."""

    MAX_FACTS_PER_FILE = 200
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
        mode: RepositoryAnalysisMode = RepositoryAnalysisMode.FULL_INDEX,
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
                configure_mode = getattr(
                    outcome.value,
                    "set_analysis_mode",
                    None,
                )
                if configure_mode is not None:
                    try:
                        configure_mode(mode)
                    except Exception as exception:
                        diagnostics.append(PluginDiagnostic(
                            code="plugin-repository-mode-exception",
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
            if Capability.FILE_POLICY not in descriptor.capabilities:
                continue
            implementation = self.catalog.implementation(plugin_id)
            contributor = getattr(implementation, "file_disposition", None)
            if contributor is None:
                continue
            outcome = contributor(path)
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

    def graph_facts(
        self,
        artifact: FileArtifact,
        capabilities: ProjectCapabilities,
    ) -> tuple[tuple[GraphFact, ...], tuple[PluginDiagnostic, ...]]:
        contributions: list[tuple[PluginKind, str, tuple[GraphFact, ...]]] = []
        diagnostics: list[PluginDiagnostic] = []
        for plugin_id in capabilities.repository_plugins:
            descriptor = self.catalog.registry.descriptor(plugin_id)
            if not ({Capability.INDEX, Capability.GRAPH} & set(descriptor.capabilities)):
                continue
            implementation = self.catalog.implementation(plugin_id)
            contributor = getattr(implementation, "index_file", None)
            if contributor is None:
                continue
            try:
                outcome = contributor(artifact)
            except Exception as exception:
                diagnostics.append(
                    PluginDiagnostic(
                        code="plugin-index-exception",
                        message=f"{type(exception).__name__}: {exception}",
                        plugin_id=plugin_id,
                    )
                )
                continue
            if outcome.status is OutcomeStatus.FAILED:
                diagnostics.append(outcome.diagnostic)
            elif outcome.status is OutcomeStatus.HANDLED:
                contributions.append((
                    descriptor.kind,
                    plugin_id,
                    self._balanced_facts(tuple(outcome.value), self.MAX_FACTS_PER_FILE),
                ))
        facts: set[GraphFact] = set()
        for _, _, contribution in sorted(
            contributions,
            key=lambda item: (
                1 if item[0] is PluginKind.LANGUAGE else 0,
                item[1],
            ),
        ):
            remaining = self.MAX_FACTS_PER_FILE - len(facts)
            if remaining <= 0:
                break
            facts.update(contribution[:remaining])
        return tuple(sorted(facts)), tuple(diagnostics)

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

    @staticmethod
    def _balanced_facts(facts: tuple[GraphFact, ...], limit: int) -> tuple[GraphFact, ...]:
        """Bound noisy contributors without starving any semantic fact kind."""
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
            implementation = self.catalog.implementation(plugin_id)
            contributor = getattr(implementation, "review", None)
            if contributor is None:
                continue
            try:
                outcome = contributor(paths)
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
        return (
            ReviewContribution(
                rules=tuple(sorted(rules)[: self.MAX_RULES]),
                evidence_requests=self._balanced_evidence_requests(
                    tuple(requests),
                    self.MAX_EVIDENCE_REQUESTS,
                ),
                group_paths=tuple(sorted(groups)),
            ),
            tuple(diagnostics),
        )

    @staticmethod
    def _balanced_evidence_requests(
        requests: tuple,
        limit: int,
    ) -> tuple:
        """Cap exact requests without allowing one plugin evidence kind to starve another."""
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


class RepositoryAnalysisHandle:
    """Host-owned streaming composition of repository semantic contributors."""

    def __init__(
        self,
        runtime: PluginRuntime,
        sessions: list[tuple[str, object]],
        diagnostics: list[PluginDiagnostic],
    ) -> None:
        self._runtime = runtime
        self._sessions = sessions
        self._diagnostics = diagnostics
        self._finished = False

    @property
    def active(self) -> bool:
        return bool(self._sessions)

    def ingest(self, artifacts: tuple[FileArtifact, ...]) -> None:
        if self._finished:
            raise RuntimeError("repository analysis is already finished")
        if tuple(sorted(artifact.path for artifact in artifacts)) != tuple(
            artifact.path for artifact in artifacts
        ):
            raise ValueError("repository artifacts must be path-sorted")
        retained: list[tuple[str, object]] = []
        for plugin_id, session in self._sessions:
            try:
                session.ingest(artifacts)
                retained.append((plugin_id, session))
            except Exception as exception:
                self._diagnostics.append(PluginDiagnostic(
                    code="plugin-repository-ingest-exception",
                    message=f"{type(exception).__name__}: {exception}",
                    plugin_id=plugin_id,
                ))
        self._sessions = retained

    def finish(self) -> tuple[RepositoryAnalysis, tuple[PluginDiagnostic, ...]]:
        if self._finished:
            raise RuntimeError("repository analysis is already finished")
        self._finished = True
        symbols = set()
        packets: dict[tuple[str, str, str], ArchitecturePacket] = {}
        snapshots: dict[tuple[str, str], RepositorySnapshot] = {}
        contexts: dict[tuple[str, str, str], RepositoryContext] = {}
        current = RepositoryAnalysis()
        for plugin_id, session in self._sessions:
            try:
                outcome = session.finish(current)
            except Exception as exception:
                self._diagnostics.append(PluginDiagnostic(
                    code="plugin-repository-finish-exception",
                    message=f"{type(exception).__name__}: {exception}",
                    plugin_id=plugin_id,
                ))
                continue
            if outcome.status is OutcomeStatus.FAILED:
                self._diagnostics.append(outcome.diagnostic)
                continue
            if outcome.status is not OutcomeStatus.HANDLED:
                continue
            contribution = outcome.value
            if not isinstance(contribution, RepositoryAnalysis):
                self._diagnostics.append(PluginDiagnostic(
                    code="plugin-repository-invalid-result",
                    message="repository contributor returned an invalid result",
                    plugin_id=plugin_id,
                ))
                continue
            symbols.update(contribution.symbols)
            if len(symbols) > self._runtime.MAX_REPOSITORY_SYMBOLS:
                self._diagnostics.append(PluginDiagnostic(
                    code="plugin-repository-symbol-limit",
                    message=(
                        f"repository analysis produced more than "
                        f"{self._runtime.MAX_REPOSITORY_SYMBOLS} symbols"
                    ),
                    plugin_id=plugin_id,
                ))
            for packet in contribution.packets:
                key = (packet.plugin_id, packet.kind, packet.key)
                if key in packets and packets[key] != packet:
                    self._diagnostics.append(PluginDiagnostic(
                        code="plugin-repository-packet-conflict",
                        message=f"conflicting architecture packet {key}",
                        plugin_id=plugin_id,
                    ))
                    continue
                packets[key] = packet
            for snapshot in contribution.snapshots:
                key = (snapshot.plugin_id, snapshot.kind)
                if key in snapshots and snapshots[key] != snapshot:
                    self._diagnostics.append(PluginDiagnostic(
                        code="plugin-repository-snapshot-conflict",
                        message=f"conflicting repository snapshot {key}",
                        plugin_id=plugin_id,
                    ))
                    continue
                snapshots[key] = snapshot
            for context in contribution.contexts:
                key = (context.plugin_id, context.kind, context.path)
                if key in contexts and contexts[key] != context:
                    self._diagnostics.append(PluginDiagnostic(
                        code="plugin-repository-context-conflict",
                        message=f"conflicting repository context {key}",
                        plugin_id=plugin_id,
                    ))
                    continue
                contexts[key] = context
            if len(packets) > self._runtime.MAX_ARCHITECTURE_PACKETS:
                self._diagnostics.append(PluginDiagnostic(
                    code="plugin-repository-packet-limit",
                    message=(
                        f"repository analysis produced more than "
                        f"{self._runtime.MAX_ARCHITECTURE_PACKETS} architecture packets"
                    ),
                    plugin_id=plugin_id,
                ))
            current = RepositoryAnalysis(
                symbols=tuple(sorted(symbols)[: self._runtime.MAX_REPOSITORY_SYMBOLS]),
                packets=tuple(sorted(packets.values())[: self._runtime.MAX_ARCHITECTURE_PACKETS]),
                snapshots=tuple(sorted(snapshots.values())),
                contexts=tuple(sorted(contexts.values())),
            )
        return current, tuple(self._diagnostics)
