from __future__ import annotations

import json
import time
from typing import Callable

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
    MAX_GRAPH_FACT_STRING_LENGTH = 4_096
    MAX_GRAPH_FACT_BYTES_PER_ARTIFACT = 262_144
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
            plugin_root = self._plugin_root_for_path(
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
            outcome = contributor(self._relative_to_root(path, plugin_root))
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
        rejected: dict[str, list[int]] = {}
        for plugin_id in capabilities.repository_plugins:
            descriptor = self.catalog.registry.descriptor(plugin_id)
            plugin_root = self._plugin_root_for_path(
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
                    self._relative_to_root(artifact.path, plugin_root),
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
            if outcome.status is OutcomeStatus.FAILED:
                diagnostics.append(self._rebase_diagnostic(
                    outcome.diagnostic,
                    plugin_root,
                ))
            elif outcome.status is OutcomeStatus.HANDLED:
                valid_facts = []
                overlong_count = 0
                for raw_fact in tuple(outcome.value):
                    fact = self._rebase_fact(raw_fact, plugin_root)
                    if self._fact_has_overlong_string(fact):
                        overlong_count += 1
                    else:
                        valid_facts.append(fact)
                if overlong_count:
                    rejected.setdefault(plugin_id, [0, 0])[0] += overlong_count
                contributions.append((
                    descriptor.kind,
                    plugin_id,
                    self._balanced_facts(
                        tuple(valid_facts),
                        self.MAX_FACTS_PER_FILE,
                    ),
                ))
        facts: set[GraphFact] = set()
        serialized_bytes = 2  # The opening and closing brackets of the JSON array.
        for _, plugin_id, contribution in sorted(
            contributions,
            key=lambda item: (
                1 if item[0] is PluginKind.LANGUAGE else 0,
                item[1],
            ),
        ):
            if len(facts) >= self.MAX_FACTS_PER_FILE:
                break
            for fact in contribution:
                if len(facts) >= self.MAX_FACTS_PER_FILE:
                    break
                if fact in facts:
                    continue
                fact_bytes = self._serialized_fact_bytes(fact)
                added_bytes = fact_bytes + (1 if facts else 0)
                if (
                    serialized_bytes + added_bytes
                    > self.MAX_GRAPH_FACT_BYTES_PER_ARTIFACT
                ):
                    rejected.setdefault(plugin_id, [0, 0])[1] += 1
                    continue
                facts.add(fact)
                serialized_bytes += added_bytes
        for plugin_id, (overlong_count, byte_count) in sorted(rejected.items()):
            reasons = []
            if overlong_count:
                reasons.append(
                    f"{overlong_count} fact(s) containing a string longer than "
                    f"{self.MAX_GRAPH_FACT_STRING_LENGTH} characters"
                )
            if byte_count:
                reasons.append(
                    f"{byte_count} fact(s) exceeding the "
                    f"{self.MAX_GRAPH_FACT_BYTES_PER_ARTIFACT}-byte artifact budget"
                )
            diagnostics.append(PluginDiagnostic(
                code="plugin-index-output-limit",
                message="graph output rejected " + " and ".join(reasons),
                plugin_id=plugin_id,
                path=artifact.path,
                recoverable=True,
            ))
        return tuple(sorted(facts)), tuple(diagnostics)

    def _fact_has_overlong_string(self, fact: GraphFact) -> bool:
        strings = (
            fact.kind,
            fact.source,
            fact.relation,
            fact.target,
            fact.path,
            *(value for attribute in fact.attributes for value in attribute),
            *fact.related_paths,
        )
        return any(
            len(value) > self.MAX_GRAPH_FACT_STRING_LENGTH
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
            descriptor = self.catalog.registry.descriptor(plugin_id)
            owned_paths = tuple(
                path for path in paths
                if self._plugin_root_for_path(
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
            try:
                outcome = contributor(owned_paths)
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
            descriptor = self.catalog.registry.descriptor(plugin_id)
            if self._plugin_root_for_path(
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

    @staticmethod
    def _plugin_root_for_path(
        kind: PluginKind,
        plugin_id: str,
        path: str,
        capabilities: ProjectCapabilities,
    ) -> str | None:
        if kind is PluginKind.LANGUAGE:
            return "" if plugin_id in capabilities.file_plugins.get(path, ()) else None
        if kind is not PluginKind.FRAMEWORK:
            return ""
        evidence = capabilities.detection_evidence.get(plugin_id, ())
        roots = tuple(
            item.removeprefix("root:")
            for item in evidence
            if item.startswith("root:")
        )
        if not roots:
            # Legacy hand-built capabilities had no evidence, while older
            # manual projections may only carry their explicit-selection tag.
            # Repository-derived evidence without a root is incomplete and
            # must not widen a framework contribution to the whole repository.
            if not evidence or any(
                item.startswith((
                    "manual-project-type:",
                    "manual-project-type-dependency:",
                ))
                for item in evidence
            ):
                return ""
            return None
        matching = tuple(
            "" if root == "." else root
            for root in roots
            if root == "." or path == root or path.startswith(root + "/")
        )
        return max(matching, key=lambda root: (root.count("/"), len(root))) if matching else None

    @staticmethod
    def _relative_to_root(path: str, root: str) -> str:
        if not root:
            return path
        return path[len(root) + 1:]

    @classmethod
    def _rebase_fact(cls, fact: GraphFact, root: str) -> GraphFact:
        if not root:
            return fact
        return GraphFact(
            fact.kind,
            fact.source,
            fact.relation,
            fact.target,
            f"{root}/{fact.path}",
            fact.line,
            fact.attributes,
            tuple(f"{root}/{path}" for path in fact.related_paths),
        )

    @staticmethod
    def _rebase_diagnostic(
        diagnostic: PluginDiagnostic,
        root: str,
    ) -> PluginDiagnostic:
        if not root or diagnostic.path is None:
            return diagnostic
        return PluginDiagnostic(
            diagnostic.code,
            diagnostic.message,
            diagnostic.plugin_id,
            f"{root}/{diagnostic.path}",
            diagnostic.recoverable,
        )


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

    def ingest(
        self,
        artifacts: tuple[FileArtifact, ...],
        *,
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        if self._finished:
            raise RuntimeError("repository analysis is already finished")
        if tuple(sorted(artifact.path for artifact in artifacts)) != tuple(
            artifact.path for artifact in artifacts
        ):
            raise ValueError("repository artifacts must be path-sorted")
        retained: list[tuple[str, object]] = []
        for plugin_id, session in self._sessions:
            plugin_started = time.monotonic()
            progress_details: dict[str, object] = {
                "pluginId": plugin_id,
                "substage": "ingest",
                "status": "started",
                "files": len(artifacts),
                "message": f"Ingesting repository files with {plugin_id}",
            }
            if artifacts:
                progress_details.update({
                    "firstPath": artifacts[0].path,
                    "lastPath": artifacts[-1].path,
                })
            self._report_progress(progress_callback, progress_details)
            for artifact in artifacts:
                try:
                    session.ingest((artifact,))
                except Exception as exception:
                    self._diagnostics.append(PluginDiagnostic(
                        code="plugin-repository-file-skipped",
                        message=f"{type(exception).__name__}: {exception}",
                        plugin_id=plugin_id,
                        path=artifact.path,
                        recoverable=True,
                    ))
            duration_ms = round((time.monotonic() - plugin_started) * 1000)
            self._report_progress(progress_callback, {
                **progress_details,
                "status": "completed",
                "durationMs": duration_ms,
                "message": (
                    f"Ingested {len(artifacts)} repository files with "
                    f"{plugin_id} in {duration_ms} ms"
                ),
            })
            retained.append((plugin_id, session))
        self._sessions = retained

    @staticmethod
    def _report_progress(
        callback: Callable[[dict[str, object]], None] | None,
        event: dict[str, object],
    ) -> None:
        if callback is None:
            return
        try:
            callback(event)
        except Exception:
            # Repository progress is optional host observability. A broken
            # observer must not change the plugin composition result.
            return

    def finish(
        self,
        *,
        progress_callback: Callable[[dict[str, object]], None] | None = None,
        deadline: float | None = None,
    ) -> tuple[RepositoryAnalysis, tuple[PluginDiagnostic, ...]]:
        if self._finished:
            raise RuntimeError("repository analysis is already finished")
        self._finished = True
        symbols = set()
        packets: dict[tuple[str, str, str], ArchitecturePacket] = {}
        snapshots: dict[tuple[str, str], RepositorySnapshot] = {}
        contexts: dict[tuple[str, str, str], RepositoryContext] = {}
        current = RepositoryAnalysis()
        for plugin_id, session in self._sessions:
            if deadline is not None and time.monotonic() >= deadline:
                self._diagnostics.append(PluginDiagnostic(
                    code="plugin-repository-finalization-timeout",
                    message=(
                        "repository analysis time budget was exhausted before "
                        f"finalizing {plugin_id}"
                    ),
                    plugin_id=plugin_id,
                    recoverable=True,
                ))
                self._report_progress(progress_callback, {
                    "pluginId": plugin_id,
                    "status": "timed_out",
                    "message": (
                        f"Architecture finalization timed out before {plugin_id}"
                    ),
                })
                break

            plugin_started = time.monotonic()
            self._report_progress(progress_callback, {
                "pluginId": plugin_id,
                "status": "started",
                "message": f"Finalizing {plugin_id} repository architecture",
            })
            try:
                configure_progress = getattr(
                    session,
                    "set_progress_callback",
                    None,
                )
                if callable(configure_progress):
                    configure_progress(progress_callback)
                configure_deadline = getattr(
                    session,
                    "set_analysis_deadline",
                    None,
                )
                if callable(configure_deadline):
                    configure_deadline(deadline)
                outcome = session.finish(current)
            except TimeoutError as exception:
                duration_ms = round((time.monotonic() - plugin_started) * 1000)
                self._diagnostics.append(PluginDiagnostic(
                    code="plugin-repository-finalization-timeout",
                    message=str(exception),
                    plugin_id=plugin_id,
                    recoverable=True,
                ))
                self._report_progress(progress_callback, {
                    "pluginId": plugin_id,
                    "status": "timed_out",
                    "durationMs": duration_ms,
                    "message": (
                        f"Architecture finalization timed out in {plugin_id} "
                        f"after {duration_ms} ms"
                    ),
                })
                break
            except Exception as exception:
                duration_ms = round((time.monotonic() - plugin_started) * 1000)
                self._diagnostics.append(PluginDiagnostic(
                    code="plugin-repository-finish-exception",
                    message=f"{type(exception).__name__}: {exception}",
                    plugin_id=plugin_id,
                    recoverable=True,
                ))
                self._report_progress(progress_callback, {
                    "pluginId": plugin_id,
                    "status": "failed",
                    "durationMs": duration_ms,
                    "message": (
                        f"Architecture finalization failed in {plugin_id} "
                        f"after {duration_ms} ms"
                    ),
                })
                continue
            duration_ms = round((time.monotonic() - plugin_started) * 1000)
            if deadline is not None and time.monotonic() >= deadline:
                self._diagnostics.append(PluginDiagnostic(
                    code="plugin-repository-finalization-timeout",
                    message=(
                        f"{plugin_id} repository analysis exceeded the shared "
                        "time budget"
                    ),
                    plugin_id=plugin_id,
                    recoverable=True,
                ))
                self._report_progress(progress_callback, {
                    "pluginId": plugin_id,
                    "status": "timed_out",
                    "durationMs": duration_ms,
                    "message": (
                        f"Architecture finalization timed out in {plugin_id} "
                        f"after {duration_ms} ms"
                    ),
                })
                break
            if outcome.status is OutcomeStatus.FAILED:
                self._diagnostics.append(outcome.diagnostic)
                self._report_progress(progress_callback, {
                    "pluginId": plugin_id,
                    "status": "failed",
                    "durationMs": duration_ms,
                    "message": (
                        f"Architecture finalization failed in {plugin_id} "
                        f"after {duration_ms} ms"
                    ),
                })
                continue
            self._report_progress(progress_callback, {
                "pluginId": plugin_id,
                "status": "completed",
                "durationMs": duration_ms,
                "message": (
                    f"Finalized {plugin_id} repository architecture in "
                    f"{duration_ms} ms"
                ),
            })
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
            self._diagnostics.extend(contribution.diagnostics)
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
