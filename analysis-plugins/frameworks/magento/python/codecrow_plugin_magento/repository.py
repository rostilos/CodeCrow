from __future__ import annotations

import base64
import gzip
import json
import logging
import time
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from typing import Callable

from codecrow_plugins import (
    ArchitecturePacket,
    FileArtifact,
    GraphFact,
    PluginDiagnostic,
    PluginOutcome,
    RepositoryAnalysis,
    RepositoryContext,
    RepositorySnapshot,
    SymbolDefinition,
)

from .architecture import (
    MAGENTO_VIEW_SOURCE_SUFFIXES,
    is_magento_config_xml,
    is_magento_view_xml,
)
from .resolver import MagentoRepositoryResolver


logger = logging.getLogger(__name__)


@dataclass
class MagentoRepositorySession:
    plugin_id: str
    revision: str
    artifacts: dict[str, str] = field(default_factory=dict)
    source_root: str | None = None
    progress_callback: Callable[[dict[str, object]], None] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    analysis_deadline: float | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    @classmethod
    def restore(cls, plugin_id: str, revision: str, snapshots) -> "MagentoRepositorySession":
        snapshot = next(
            (item for item in snapshots if item.kind == "magento-architecture-sources"),
            None,
        )
        if snapshot is None:
            raise ValueError(
                "Magento repository snapshot is missing magento-architecture-sources"
            )
        raw = gzip.decompress(base64.b64decode(snapshot.content.encode("ascii")))
        artifacts = json.loads(raw.decode("utf-8"))
        if not isinstance(artifacts, dict) or any(
            not isinstance(path, str) or not isinstance(content, str)
            for path, content in artifacts.items()
        ):
            raise ValueError("Magento repository snapshot has invalid architecture sources")
        return cls(plugin_id, revision, dict(sorted(artifacts.items())))

    def _snapshot(self) -> RepositorySnapshot:
        raw = json.dumps(
            dict(sorted(self.artifacts.items())),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        content = base64.b64encode(gzip.compress(raw, compresslevel=6, mtime=0)).decode("ascii")
        return RepositorySnapshot(
            self.plugin_id,
            "magento-architecture-sources",
            content,
        )

    def set_source_root(self, source_root: str | None) -> None:
        self.source_root = source_root

    def set_progress_callback(
        self,
        progress_callback: Callable[[dict[str, object]], None] | None,
    ) -> None:
        self.progress_callback = progress_callback

    def set_analysis_deadline(self, deadline: float | None) -> None:
        self.analysis_deadline = deadline

    def _report_scoped_progress(
        self,
        root: str,
        event: dict[str, object],
    ) -> None:
        if self.progress_callback is None:
            return
        self.progress_callback({
            **event,
            "sourceRoot": root or ".",
        })

    def ingest(self, artifacts: tuple[FileArtifact, ...]) -> None:
        for artifact in artifacts:
            path = artifact.path
            if artifact.deleted:
                self.artifacts.pop(path, None)
                continue
            filename = PurePosixPath(path).name
            is_config = is_magento_config_xml(path)
            is_schema_whitelist = (
                filename == "db_schema_whitelist.json"
                and "/etc/" in f"/{path}"
            )
            is_component = filename in {
                "composer.json", "module.xml", "registration.php", "theme.xml",
            }
            is_view_configuration = is_magento_view_xml(path)
            is_template = (
                "/templates/" in f"/{path}"
            )
            is_email_template = (
                "/email/" in f"/{path}"
                and path.casefold().endswith(".html")
            )
            is_view_asset = (
                "/web/" in f"/{path}"
                and path.casefold().endswith(MAGENTO_VIEW_SOURCE_SUFFIXES)
            )
            is_graphql = path.casefold().endswith(".graphqls")
            is_requirejs = filename == "requirejs-config.js"
            is_app_config = path == "app/etc/config.php" or path.endswith(
                "/app/etc/config.php"
            )
            if (
                is_config
                or is_schema_whitelist
                or is_component
                or is_view_configuration
                or is_template
                or is_email_template
                or is_view_asset
                or is_graphql
                or is_requirejs
                or is_app_config
            ):
                self.artifacts[path] = artifact.content

    def finish(self, dependencies: RepositoryAnalysis):
        started = time.monotonic()
        roots = self._analysis_roots()
        analyses: list[RepositoryAnalysis] = []
        diagnostics: list[PluginDiagnostic] = []
        invalid_paths: set[str] = set()
        for root in roots:
            scoped = self._scoped_artifacts(root)
            scoped_symbols = self._scoped_symbols(root, dependencies.symbols)
            scoped_paths = {*scoped, *(symbol.path for symbol in scoped_symbols)}
            resolver = MagentoRepositoryResolver(
                self.plugin_id,
                scoped,
                scoped_symbols,
                progress_callback=(
                    lambda event, root=root: self._report_scoped_progress(
                        root,
                        event,
                    )
                ),
                deadline=self.analysis_deadline,
            )
            analysis, scoped_diagnostics = resolver.resolve()
            analyses.append(self._prefix_analysis(analysis, root, scoped_paths))
            diagnostics.extend(
                PluginDiagnostic(
                    code=item.code,
                    message=item.message,
                    plugin_id=item.plugin_id,
                    path=(
                        self._prefix_path(root, item.path)
                        if item.path in scoped_paths
                        else item.path
                    ),
                    recoverable=item.recoverable,
                )
                for item in scoped_diagnostics
            )
            invalid_paths.update(
                self._prefix_path(root, path) for path in resolver.invalid_paths
            )
        analysis = RepositoryAnalysis(
            symbols=tuple(sorted({item for part in analyses for item in part.symbols})),
            packets=tuple(sorted({item for part in analyses for item in part.packets})),
            contexts=tuple(sorted({item for part in analyses for item in part.contexts})),
            diagnostics=tuple(
                item for part in analyses for item in part.diagnostics
            ),
        )
        if diagnostics:
            for diagnostic in diagnostics:
                logger.warning(
                    "Skipping invalid Magento repository input "
                    "(code=%s path=%s): %s",
                    diagnostic.code,
                    diagnostic.path or "<repository>",
                    diagnostic.message,
                )
        for path in invalid_paths:
            self.artifacts.pop(path, None)
        related_paths = {
            path for packet in analysis.packets for path in packet.paths
        }
        contexts = tuple(sorted(
            RepositoryContext(
                self.plugin_id,
                (
                    "magento-template-source"
                    if path.casefold().endswith(".phtml")
                    else "magento-view-source"
                ),
                path,
                content,
            )
            for path, content in self.artifacts.items()
            if any(
                path.startswith(f"{root}/vendor/" if root else "vendor/")
                for root in roots
            )
            and path in related_paths
            and content.strip()
            and path.casefold().endswith(MAGENTO_VIEW_SOURCE_SUFFIXES)
        ))
        snapshot_started = time.monotonic()
        snapshot = self._snapshot()
        logger.info(
            "Magento repository snapshot: sources=%s contexts=%s encoded_bytes=%s elapsed=%.3fs total_finish=%.3fs",
            len(self.artifacts),
            len(contexts),
            len(snapshot.content),
            time.monotonic() - snapshot_started,
            time.monotonic() - started,
        )
        return PluginOutcome.handled(RepositoryAnalysis(
            symbols=analysis.symbols,
            packets=analysis.packets,
            snapshots=(snapshot,),
            contexts=contexts,
            diagnostics=tuple(
                PluginDiagnostic(
                    code=diagnostic.code,
                    message=diagnostic.message,
                    plugin_id=diagnostic.plugin_id,
                    path=diagnostic.path,
                    recoverable=True,
                )
                for diagnostic in diagnostics
            ),
        ))

    def _analysis_roots(self) -> tuple[str, ...]:
        if self.source_root is not None:
            return (self.source_root,)
        application_markers = (
            "app/etc/config.php",
            "app/etc/di.xml",
            "app/etc/env.php",
            "bin/magento",
        )
        roots = {
            "" if path == marker else path[: -(len(marker) + 1)]
            for path in self.artifacts
            for marker in application_markers
            if path == marker or path.endswith("/" + marker)
        }
        if roots:
            return tuple(sorted(roots, key=lambda value: (value.count("/"), value)))
        return ("",)

    def _scoped_artifacts(self, root: str) -> dict[str, str]:
        if not root:
            return dict(self.artifacts)
        prefix = root + "/"
        return {
            path[len(prefix):]: content
            for path, content in self.artifacts.items()
            if path.startswith(prefix)
        }

    @staticmethod
    def _scoped_symbols(
        root: str,
        symbols: tuple[SymbolDefinition, ...],
    ) -> tuple[SymbolDefinition, ...]:
        if not root:
            return symbols
        prefix = root + "/"
        return tuple(sorted(
            replace(symbol, path=symbol.path[len(prefix):])
            for symbol in symbols
            if symbol.path.startswith(prefix)
        ))

    @staticmethod
    def _prefix_path(root: str, path: str) -> str:
        return f"{root}/{path}" if root else path

    def _prefix_analysis(
        self,
        analysis: RepositoryAnalysis,
        root: str,
        scoped_paths: set[str],
    ) -> RepositoryAnalysis:
        if not root:
            return analysis

        def path(value: str) -> str:
            return self._prefix_path(root, value) if value in scoped_paths else value

        packets = tuple(sorted(
            ArchitecturePacket(
                plugin_id=packet.plugin_id,
                kind=packet.kind,
                key=packet.key,
                paths=tuple(sorted({path(value) for value in packet.paths})),
                facts=tuple(sorted(
                    GraphFact(
                        kind=fact.kind,
                        source=fact.source,
                        relation=fact.relation,
                        target=fact.target,
                        path=path(fact.path),
                        line=fact.line,
                        attributes=fact.attributes,
                        related_paths=tuple(sorted({
                            path(value) for value in fact.related_paths
                        })),
                    )
                    for fact in packet.facts
                )),
                attributes=packet.attributes,
            )
            for packet in analysis.packets
        ))
        contexts = tuple(sorted(
            RepositoryContext(
                context.plugin_id,
                context.kind,
                path(context.path),
                context.content,
                context.attributes,
            )
            for context in analysis.contexts
        ))
        return RepositoryAnalysis(
            symbols=tuple(sorted(
                replace(symbol, path=path(symbol.path))
                for symbol in analysis.symbols
            )),
            packets=packets,
            contexts=contexts,
            diagnostics=analysis.diagnostics,
        )
