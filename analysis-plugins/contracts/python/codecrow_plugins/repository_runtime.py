from __future__ import annotations

import time
from dataclasses import replace
from typing import Callable, TYPE_CHECKING

from .api import (
    ArchitecturePacket,
    FileArtifact,
    OutcomeStatus,
    PluginDiagnostic,
    RepositoryAnalysis,
    RepositoryContext,
    RepositorySnapshot,
    SymbolDefinition,
)


if TYPE_CHECKING:
    from .runtime import PluginRuntime


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
        if any(
            artifacts[index - 1].path > artifacts[index].path
            for index in range(1, len(artifacts))
        ):
            raise ValueError("repository artifacts must be path-sorted")
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

    def close(self) -> None:
        """Release every session, including contributors skipped after a timeout."""
        sessions, self._sessions = self._sessions, []
        self._finished = True
        for plugin_id, session in sessions:
            closer = getattr(session, "close", None)
            if not callable(closer):
                continue
            try:
                closer()
            except Exception as exception:
                self._diagnostics.append(PluginDiagnostic(
                    code="plugin-repository-close-exception",
                    message=f"{type(exception).__name__}: {exception}",
                    plugin_id=plugin_id,
                    recoverable=True,
                ))

    def finish(
        self,
        *,
        progress_callback: Callable[[dict[str, object]], None] | None = None,
        deadline: float | None = None,
    ) -> tuple[RepositoryAnalysis, tuple[PluginDiagnostic, ...]]:
        try:
            analysis, _diagnostics = self._finish(
                progress_callback=progress_callback,
                deadline=deadline,
            )
        finally:
            self.close()
        return analysis, tuple(self._diagnostics)

    def _finish(
        self,
        *,
        progress_callback: Callable[[dict[str, object]], None] | None = None,
        deadline: float | None = None,
    ) -> tuple[RepositoryAnalysis, tuple[PluginDiagnostic, ...]]:
        if self._finished:
            raise RuntimeError("repository analysis is already finished")
        self._finished = True
        symbols: dict[SymbolDefinition, SymbolDefinition] = {}
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
            candidate_symbols = dict(symbols) if contribution.symbols else symbols
            for symbol in contribution.symbols:
                attributed = self._merge_symbol_contributors(
                    symbol,
                    (plugin_id,),
                )
                current_symbol = candidate_symbols.get(attributed)
                candidate_symbols[attributed] = (
                    attributed
                    if current_symbol is None
                    else self._merge_symbol_contributors(
                        current_symbol,
                        attributed.contributing_plugin_ids,
                    )
                )
            if len(candidate_symbols) > self._runtime.MAX_REPOSITORY_SYMBOLS:
                self._diagnostics.append(PluginDiagnostic(
                    code="plugin-repository-symbol-limit",
                    message=(
                        f"repository analysis produced more than "
                        f"{self._runtime.MAX_REPOSITORY_SYMBOLS} symbols"
                    ),
                    plugin_id=plugin_id,
                ))
                break
            candidate_packets = dict(packets) if contribution.packets else packets
            for packet in contribution.packets:
                key = (packet.plugin_id, packet.kind, packet.key)
                if key in candidate_packets and candidate_packets[key] != packet:
                    self._diagnostics.append(PluginDiagnostic(
                        code="plugin-repository-packet-conflict",
                        message=f"conflicting architecture packet {key}",
                        plugin_id=plugin_id,
                    ))
                    continue
                candidate_packets[key] = packet
            if len(candidate_packets) > self._runtime.MAX_ARCHITECTURE_PACKETS:
                self._diagnostics.append(PluginDiagnostic(
                    code="plugin-repository-packet-limit",
                    message=(
                        f"repository analysis produced more than "
                        f"{self._runtime.MAX_ARCHITECTURE_PACKETS} architecture packets"
                    ),
                    plugin_id=plugin_id,
                ))
                break
            candidate_snapshots = dict(snapshots) if contribution.snapshots else snapshots
            for snapshot in contribution.snapshots:
                key = (snapshot.plugin_id, snapshot.kind)
                if key in candidate_snapshots and candidate_snapshots[key] != snapshot:
                    self._diagnostics.append(PluginDiagnostic(
                        code="plugin-repository-snapshot-conflict",
                        message=f"conflicting repository snapshot {key}",
                        plugin_id=plugin_id,
                    ))
                    continue
                candidate_snapshots[key] = snapshot
            candidate_contexts = dict(contexts) if contribution.contexts else contexts
            for context in contribution.contexts:
                key = (context.plugin_id, context.kind, context.path)
                if key in candidate_contexts and candidate_contexts[key] != context:
                    self._diagnostics.append(PluginDiagnostic(
                        code="plugin-repository-context-conflict",
                        message=f"conflicting repository context {key}",
                        plugin_id=plugin_id,
                    ))
                    continue
                candidate_contexts[key] = context
            symbols = candidate_symbols
            packets = candidate_packets
            snapshots = candidate_snapshots
            contexts = candidate_contexts
            current = RepositoryAnalysis(
                symbols=tuple(sorted(symbols.values())),
                packets=tuple(sorted(packets.values())),
                snapshots=tuple(sorted(snapshots.values())),
                contexts=tuple(sorted(contexts.values())),
            )
        return current, tuple(self._diagnostics)

    @staticmethod
    def _merge_symbol_contributors(
        symbol: SymbolDefinition,
        contributing_plugin_ids: tuple[str, ...],
    ) -> SymbolDefinition:
        merged = tuple(sorted({
            *symbol.contributing_plugin_ids,
            *contributing_plugin_ids,
        }))
        if merged == symbol.contributing_plugin_ids:
            return symbol
        return replace(symbol, contributing_plugin_ids=merged)
