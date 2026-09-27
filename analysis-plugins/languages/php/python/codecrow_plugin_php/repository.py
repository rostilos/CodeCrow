from __future__ import annotations

import base64
import gzip
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from codecrow_plugins import (
    FileArtifact,
    PluginOutcome,
    RepositoryAnalysis,
    RepositorySnapshot,
    SymbolDefinition,
)

from .ast_parser import _parse_artifact, _parse_template_artifact
from .relations import PhpRelationResolver
from .syntax import _DECLARATION_HINT


logger = logging.getLogger(__name__)


@dataclass
class PhpRepositorySession:
    plugin_id: str
    revision: str
    _symbols_by_path: dict[str, tuple[SymbolDefinition, ...]] = field(default_factory=dict)
    _executor: ThreadPoolExecutor | None = field(default=None, init=False, repr=False)

    def _parse_workers(self) -> int:
        configured = os.getenv("CODECROW_PHP_PARSE_WORKERS")
        if configured is not None:
            try:
                return max(1, min(8, int(configured)))
            except ValueError as exception:
                raise ValueError("CODECROW_PHP_PARSE_WORKERS must be an integer") from exception
        return max(1, min(4, os.cpu_count() or 1))

    @classmethod
    def restore(cls, plugin_id: str, revision: str, snapshots) -> "PhpRepositorySession":
        snapshot = next((item for item in snapshots if item.kind == "php-symbols"), None)
        if snapshot is None:
            raise ValueError("PHP repository snapshot is missing php-symbols")
        raw = gzip.decompress(base64.b64decode(snapshot.content.encode("ascii")))
        records = json.loads(raw.decode("utf-8"))
        symbols = {
            SymbolDefinition(
                qualified_name=record["name"],
                kind=record["kind"],
                path=record["path"],
                line=record["line"],
                parents=tuple(record.get("parents", ())),
                methods=tuple(record.get("methods", ())),
                constructor_types=tuple(record.get("constructorTypes", ())),
                attributes=tuple(tuple(item) for item in record.get("attributes", ())),
            )
            for record in records
        }
        by_path: dict[str, list[SymbolDefinition]] = {}
        for symbol in symbols:
            by_path.setdefault(symbol.path, []).append(symbol)
        return cls(plugin_id, revision, _symbols_by_path={
            path: tuple(sorted(values)) for path, values in by_path.items()
        })

    def _snapshot(self, symbols: tuple[SymbolDefinition, ...]) -> RepositorySnapshot:
        records = [
            {
                "name": symbol.qualified_name,
                "kind": symbol.kind,
                "path": symbol.path,
                "line": symbol.line,
                "parents": list(symbol.parents),
                "methods": list(symbol.methods),
                "constructorTypes": list(symbol.constructor_types),
                "attributes": [list(item) for item in symbol.attributes],
            }
            for symbol in symbols
        ]
        raw = json.dumps(
            records, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        content = base64.b64encode(gzip.compress(raw, compresslevel=6, mtime=0)).decode("ascii")
        return RepositorySnapshot(self.plugin_id, "php-symbols", content)

    def ingest(self, artifacts: tuple[FileArtifact, ...]) -> None:
        changed = {
            artifact.path: artifact
            for artifact in artifacts
            if artifact.path.casefold().endswith((".php", ".phtml", ".inc"))
        }
        if not changed:
            return
        parse_jobs = []
        for artifact in changed.values():
            if artifact.deleted:
                continue
            if artifact.path.casefold().endswith(".phtml"):
                parse_jobs.append((_parse_template_artifact, artifact))
            elif _DECLARATION_HINT.search(artifact.content):
                parse_jobs.append((_parse_artifact, artifact))

        # Parse replacements together, but never retain old facts for a changed
        # source if parsing fails. The runtime reports the skipped input and may
        # continue finalizing the unaffected repository evidence.
        parsed = ()
        try:
            if parse_jobs:
                workers = self._parse_workers()
                if workers == 1 or len(parse_jobs) == 1:
                    parsed = tuple(parser(artifact) for parser, artifact in parse_jobs)
                else:
                    if self._executor is None:
                        self._executor = ThreadPoolExecutor(
                            max_workers=workers,
                            thread_name_prefix="codecrow-php-ast",
                        )
                    parsed = tuple(self._executor.map(
                        lambda job: job[0](job[1]),
                        parse_jobs,
                    ))
        except BaseException:
            for path in changed:
                self._symbols_by_path.pop(path, None)
            self.close()
            raise

        for path in changed:
            self._symbols_by_path.pop(path, None)
        for (_, artifact), symbols in zip(parse_jobs, parsed):
            if symbols:
                self._symbols_by_path[artifact.path] = tuple(sorted(set(symbols)))

    def close(self) -> None:
        """Release parsing workers after completion or an abandoned parse batch."""
        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None

    def finish(self, dependencies: RepositoryAnalysis):
        self.close()
        started = time.monotonic()
        symbols = tuple(sorted(
            symbol for values in self._symbols_by_path.values() for symbol in values
        ))
        snapshot = self._snapshot(symbols)
        logger.info(
            "PHP repository snapshot: symbols=%s encoded_bytes=%s elapsed=%.3fs",
            len(symbols),
            len(snapshot.content),
            time.monotonic() - started,
        )
        return PluginOutcome.handled(RepositoryAnalysis(
            symbols=symbols,
            packets=PhpRelationResolver(self.plugin_id, symbols).relation_packets(),
            snapshots=(snapshot,),
        ))
