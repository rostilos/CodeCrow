"""Optional neutral-plugin file policy, finalization and repository state."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from ..documents import TextNode
from ..structural_store import StructuralGraphWriter
from .build_support import _BATCH_SIZE


logger = logging.getLogger(__name__)


@dataclass
class RepositoryEnrichment:
    plugin_runtime: Any

    def file_policy(
        self, repository_files: Sequence[Path], capabilities: Any,
        check_cancelled: Callable[[], None],
    ) -> tuple[Sequence[Path], dict[str, object]]:
        dispositions: dict[str, object] = {}
        if self.plugin_runtime is None or capabilities is None:
            return repository_files, dispositions
        from codecrow_plugins import FileDisposition

        for file_number, relative_path in enumerate(repository_files):
            if file_number % _BATCH_SIZE == 0:
                check_cancelled()
            path = relative_path.as_posix()
            try:
                disposition = self.plugin_runtime.file_disposition(
                    path,
                    capabilities,
                )
            except Exception as exception:
                logger.warning(
                    "Plugin file policy failed open for %s: %s",
                    path,
                    exception,
                )
                disposition = FileDisposition.FULL
            dispositions[path] = disposition
        eligible_files = [
            path
            for path in repository_files
            if dispositions[path.as_posix()] not in {
                FileDisposition.EXCLUDED,
                FileDisposition.GENERATED,
            }
        ]
        return eligible_files, dispositions

    def finish(
        self, writer: StructuralGraphWriter, analysis_handle: Any, *,
        deadline: float, discard_stale_outputs: bool,
    ) -> None:
        writer.begin_repository_analysis_reconciliation()
        try:
            analysis, diagnostics = analysis_handle.finish(deadline=deadline)
            for diagnostic in diagnostics:
                logger.warning(
                    "Repository architecture diagnostic plugin=%s code=%s "
                    "path=%s: %s",
                    diagnostic.plugin_id,
                    diagnostic.code,
                    diagnostic.path or "<repository>",
                    diagnostic.message,
                )
            for symbol in analysis.symbols:
                writer.add_symbol(
                    symbol,
                    plugin_ids=tuple(getattr(
                        symbol,
                        "contributing_plugin_ids",
                        (),
                    )),
                )
            for packet in analysis.packets:
                for fact in packet.facts:
                    writer.add_graph_fact(
                        fact,
                        plugin_id=packet.plugin_id,
                        packet_kind=packet.kind,
                        packet_key=packet.key,
                    )
            for context in analysis.contexts:
                writer.add_context(context)
            for snapshot in analysis.snapshots:
                writer.add_snapshot(snapshot)
            writer.reconcile_repository_analysis_outputs()
        except Exception as exception:
            writer.abort_repository_analysis_reconciliation()
            if discard_stale_outputs:
                # Cloned packets describe the old revision and cannot be published as
                # proposed-tree evidence after optional enrichment fails.
                writer.remove_repository_analysis_outputs()
            logger.warning(
                "Repository architecture finalization failed open: %s",
                exception,
                exc_info=True,
            )


def write_repository_state(
    writer: StructuralGraphWriter, *, commit: str, repository_facts: Any,
    repository_files: Sequence[Path], project_type: str | None,
    source_root: str | None,
) -> str:
    repository_facts_payload = {
        "revision": commit,
        "paths": (
            list(repository_facts.paths)
            if repository_facts is not None
            else [path.as_posix() for path in repository_files]
        ),
        "markerContents": (
            dict(repository_facts.marker_contents)
            if repository_facts is not None
            else {}
        ),
        "projectType": project_type,
        "sourceRoot": source_root,
    }
    repository_facts_json = json.dumps(
        repository_facts_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    writer.add_unit(
        TextNode(
            text=repository_facts_json,
            metadata={
                "path": "__analysis_state__/repository-facts.state",
                "start_line": 1,
                "end_line": 1,
                "primary_name": "repository-facts",
                "language": "repository-state",
            },
        ),
        record_type="repository_state",
    )
    return repository_facts_json
