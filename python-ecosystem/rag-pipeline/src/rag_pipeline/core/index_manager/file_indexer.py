"""Shared source ingestion and per-file graph extraction for full/delta builds."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..documents import TextNode
from ..loader import DocumentLoader, RepositoryFileSkip
from ..splitter import ASTCodeSplitter
from ..structural_store import StructuralGraphWriter
from .build_support import _BATCH_SIZE


logger = logging.getLogger(__name__)


@dataclass
class FileIndexer:
    loader: DocumentLoader
    splitter: ASTCodeSplitter
    plugin_runtime: Any

    def index_files(
        self, *, repo_path: Path, workspace: str, project: str, branch: str,
        commit: str, source_tree: Any, eligible_files: Sequence[Path],
        writer: StructuralGraphWriter, dispositions: Mapping[str, object],
        capabilities: Any, analysis_handle: Any, skipped_paths: set[str],
        record_skip: Callable[[RepositoryFileSkip], None],
        check_cancelled: Callable[[], None],
        on_batch: Callable[[int, int], None], replace_missing_files: bool = False,
    ) -> int:
        document_count = 0
        total_batches = max(1, (len(eligible_files) + _BATCH_SIZE - 1) // _BATCH_SIZE)
        for offset in range(0, len(eligible_files), _BATCH_SIZE):
            check_cancelled()
            batch = eligible_files[offset:offset + _BATCH_SIZE]
            documents = self.loader.load_file_batch(
                batch,
                repo_path,
                workspace,
                project,
                branch,
                commit,
                expected_file_sha256=source_tree.file_sha256_by_path,
                on_skip=record_skip,
            )
            check_cancelled()
            if analysis_handle is not None and analysis_handle.active:
                from codecrow_plugins import FileArtifact

                loaded_paths = {str(document.metadata["path"]) for document in documents}
                artifacts = [
                    FileArtifact(path=str(document.metadata["path"]), content=document.text)
                    for document in documents
                ]
                if replace_missing_files:
                    artifacts.extend(
                        FileArtifact(path=path.as_posix(), content="", deleted=True)
                        for path in batch if path.as_posix() not in loaded_paths
                    )
                if artifacts:
                    analysis_handle.ingest(tuple(sorted(artifacts, key=lambda artifact: artifact.path)))
                    check_cancelled()

            for document in documents:
                document_count += self.index_document(
                    document, writer=writer, dispositions=dispositions,
                    capabilities=capabilities, skipped_paths=skipped_paths,
                    check_cancelled=check_cancelled,
                )

            batch_number = offset // _BATCH_SIZE + 1
            on_batch(batch_number, total_batches)
            check_cancelled()
        return document_count

    def index_document(
        self, document: TextNode, *, writer: StructuralGraphWriter,
        dispositions: Mapping[str, object], capabilities: Any,
        skipped_paths: set[str], check_cancelled: Callable[[], None],
    ) -> int:
        document_indexed = 0
        check_cancelled()
        path = str(document.metadata["path"])
        file_unit_id = writer.add_file(document)
        architecture_only = str(
            getattr(dispositions.get(path), "value", "")
        ) == "architecture-only"
        if architecture_only:
            chunks = [TextNode(
                text=document.text,
                metadata={
                    **document.metadata,
                    "start_line": 1,
                    "end_line": document.text.count("\n") + 1,
                    "primary_name": Path(path).name,
                    "content_type": "architecture-source",
                },
            )]
        else:
            chunks, failed = self.splitter.split_documents_resilient(
                [document],
                capabilities=capabilities,
            )
            skipped_paths.update(failed)
        if chunks:
            document_indexed = 1
        for chunk in chunks:
            unit_id = writer.add_unit(
                chunk,
                record_type=(
                    "plugin_context" if architecture_only else "source_unit"
                ),
            )
            writer.add_file_containment(
                file_unit_id,
                unit_id,
                chunk,
            )
            writer.add_ast_relations(unit_id, chunk)

        if self.plugin_runtime is not None and capabilities is not None:
            from codecrow_plugins import FileArtifact

            try:
                facts, diagnostics = self.plugin_runtime.graph_facts(
                    FileArtifact(path=path, content=document.text),
                    capabilities,
                )
                for diagnostic in diagnostics:
                    logger.warning(
                        "Structural plugin diagnostic plugin=%s code=%s "
                        "path=%s: %s",
                        diagnostic.plugin_id,
                        diagnostic.code,
                        diagnostic.path or path,
                        diagnostic.message,
                    )
                for fact in facts:
                    writer.add_graph_fact(
                        fact,
                        # The neutral file-fact API composes and
                        # de-duplicates contributions before it
                        # returns them, so no single plugin owner is
                        # authoritative here. Repository packets
                        # retain their exact plugin IDs below.
                        plugin_id=None,
                    )
            except Exception as exception:
                logger.warning(
                    "Plugin graph extraction failed open for %s: %s",
                    path,
                    exception,
                )

        return document_indexed
