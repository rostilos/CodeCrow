"""Full and incremental generation orchestration through focused services."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, List, Mapping, Optional, Sequence

from ...models.config import IndexStats, RAGConfig
from ..exact_index import (
    ExactIndexPreconditionError,
    RepositoryDeltaRebuildRequired,
)
from ..loader import DocumentLoader, RepositoryFileSkip
from ..source_tree import require_repository_source_tree_unchanged
from ..splitter import ASTCodeSplitter
from ..structural_store import (
    StructuralGenerationStore,
    StructuralGraphWriter,
    build_receipt,
)


from dataclasses import dataclass, field
from .build_support import (
    _ZERO_FINGERPRINT,
    _config_float,
    _unsafe_delta_plugin_selection_changes,
)
from .file_indexer import FileIndexer
from .repository_enrichment import RepositoryEnrichment, write_repository_state
from .publication import GenerationPublisher, pending_generation


logger = logging.getLogger(__name__)


@dataclass
class GenerationBuilder:
    """Coordinate full/delta builds with explicit runtime dependencies."""
    config: RAGConfig
    store: StructuralGenerationStore
    loader: DocumentLoader
    splitter: ASTCodeSplitter
    plugin_catalog: Any
    plugin_runtime: Any
    plugin_selector: Any
    index_representation_fingerprint: str
    _progress: Callable[..., None]
    _raise_if_cancelled: Callable[..., None]
    get_revision_preflight: Callable[..., dict[str, Any] | None]
    _stats_from_receipt: Callable[..., IndexStats]
    files: FileIndexer = field(init=False)
    enrichment: RepositoryEnrichment = field(init=False)
    publisher: GenerationPublisher = field(init=False)

    def __post_init__(self) -> None:
        self.files = FileIndexer(self.loader, self.splitter, self.plugin_runtime)
        self.enrichment = RepositoryEnrichment(self.plugin_runtime)
        self.publisher = GenerationPublisher(self.store, self.get_revision_preflight)

    def _build_repository_delta(
        self,
        *,
        repo_path: Path,
        workspace: str,
        project: str,
        branch: str,
        base_revision: str,
        commit: str,
        changed_paths: Sequence[str],
        deleted_paths: Sequence[str],
        source_tree,
        collection_target: str,
        base_collection_target: str,
        base_generation_manifest_sha256: str,
        progress_callback: Optional[Callable[[dict], None]],
        project_type: Optional[str],
        source_root: Optional[str],
        activation_guard: Callable[[], None],
        snapshot_metadata: Optional[Mapping[str, Any]] = None,
        cancellation_event: Optional[threading.Event] = None,
        source_tree_exclusively_owned: bool = False,
    ) -> IndexStats:
        self._progress(
            progress_callback,
            "cloning_base",
            "Cloning the sealed base structural generation",
            5,
        )
        with pending_generation(
            self.store, collection_target,
            base_binding={
                "source_target": base_collection_target,
                "workspace": workspace, "project": project, "branch": branch,
                "revision": base_revision,
                "manifest_sha256": base_generation_manifest_sha256,
            },
        ) as build:
            pending, connection, base_receipt = (
                build.paths, build.connection, build.base_receipt,
            )
            writer = StructuralGraphWriter(connection, track_mutations=True)
            writer.refresh_counts()
            skipped_paths: set[str] = set()
            analysis_handle = None

            def record_skip(skip: RepositoryFileSkip) -> None:
                skipped_paths.add(skip.path)

            self._raise_if_cancelled(cancellation_event)
            if base_receipt.get("index_representation_fingerprint") != (
                self.index_representation_fingerprint
            ):
                raise ExactIndexPreconditionError(
                    "sealed base generation uses a different structural "
                    "representation"
                )
            include_patterns = list(
                base_receipt.get("index_include_patterns") or ()
            ) or None
            exclude_patterns = list(
                base_receipt.get("index_exclude_patterns") or ()
            ) or None
            self._progress(
                progress_callback,
                "scanning_delta",
                "Scanning repository-delta files under the base index policy",
                10,
            )
            repository_files = list(self.loader.iter_repository_files(
                repo_path,
                include_patterns,
                exclude_patterns,
                expected_file_sha256=source_tree.file_sha256_by_path,
                on_skip=record_skip,
            ))
            self._raise_if_cancelled(cancellation_event)
            if (
                self.config.max_files_per_index > 0
                and len(repository_files) > self.config.max_files_per_index
            ):
                raise ValueError(
                    "Repository exceeds structural file limit: "
                    f"{len(repository_files)} files "
                    f"(max: {self.config.max_files_per_index})"
                )

            capabilities = None
            implementation_fingerprint = _ZERO_FINGERPRINT
            repository_facts = None
            if self.plugin_catalog is not None and self.plugin_selector is not None:
                from codecrow_plugins import build_repository_facts

                repository_facts = build_repository_facts(
                    repo_path,
                    commit,
                    repository_files,
                    self.plugin_catalog.registry,
                    project_type=project_type,
                    source_root=source_root,
                )
                capabilities = self.plugin_selector.select(repository_facts)
                implementation_fingerprint = (
                    self.plugin_catalog.implementation_fingerprint(
                        capabilities.repository_plugins
                    )
                )

            plugin_ids = (
                tuple(capabilities.repository_plugins)
                if capabilities is not None
                else ()
            )
            plugin_fingerprint = (
                capabilities.fingerprint
                if capabilities is not None
                else _ZERO_FINGERPRINT
            )
            descriptor_fingerprint = (
                capabilities.descriptor_fingerprint
                if capabilities is not None
                else _ZERO_FINGERPRINT
            )
            base_plugin_ids = tuple(base_receipt.get("plugin_ids") or ())
            base_descriptor_fingerprint = (
                self.plugin_catalog.registry.fingerprint_for(base_plugin_ids)
                if self.plugin_catalog is not None
                else _ZERO_FINGERPRINT
            )
            base_implementation_fingerprint = (
                self.plugin_catalog.implementation_fingerprint(base_plugin_ids)
                if self.plugin_catalog is not None
                else _ZERO_FINGERPRINT
            )
            if (
                base_receipt.get("plugin_descriptor_fingerprint")
                != base_descriptor_fingerprint
                or base_receipt.get("plugin_implementation_fingerprint")
                != base_implementation_fingerprint
            ):
                raise ExactIndexPreconditionError(
                    "sealed base generation uses different structural plugin "
                    "implementations or descriptors"
                )
            changed_plugin_ids = set(base_plugin_ids).symmetric_difference(
                plugin_ids
            )
            unsafe_selection_changes: Sequence[str] = ()
            if self.plugin_catalog is not None:
                unsafe_selection_changes = (
                    _unsafe_delta_plugin_selection_changes(
                        self.plugin_catalog.registry,
                        base_plugin_ids,
                        plugin_ids,
                    )
                )
            elif changed_plugin_ids:
                unsafe_selection_changes = tuple(sorted(changed_plugin_ids))
            if unsafe_selection_changes:
                raise RepositoryDeltaRebuildRequired(
                    "proposed changes alter repository-aware structural plugin "
                    "selection; the sealed base cannot be updated incrementally: "
                    + ", ".join(unsafe_selection_changes)
                )
            if changed_plugin_ids:
                logger.info(
                    "Continuing exact delta across syntax-only plugin selection "
                    "change: %s",
                    ", ".join(sorted(changed_plugin_ids)),
                )

            eligible_files, dispositions = self.enrichment.file_policy(
                repository_files, capabilities,
                lambda: self._raise_if_cancelled(cancellation_event),
            )
            if self.plugin_runtime is not None and capabilities is not None:
                from codecrow_plugins import RepositorySnapshot

                snapshots = tuple(
                    RepositorySnapshot(
                        plugin_id=str(row["plugin_id"]),
                        kind=str(row["kind"]),
                        content=str(row["content"]),
                    )
                    for row in connection.execute(
                        "SELECT plugin_id, kind, content FROM "
                        "repository_snapshots ORDER BY plugin_id, kind"
                    )
                )
                repository_analysis_plugins = set(
                    self.plugin_runtime.repository_analysis_plugins(capabilities)
                )
                snapshot_plugins = {snapshot.plugin_id for snapshot in snapshots}
                unrestorable = {
                    plugin_id
                    for plugin_id in repository_analysis_plugins
                    if plugin_id not in snapshot_plugins
                    or not callable(getattr(
                        self.plugin_catalog.implementation(plugin_id),
                        "restore_repository_analysis",
                        None,
                    ))
                }
                if unrestorable:
                    raise RepositoryDeltaRebuildRequired(
                        "sealed base generation lacks resumable architecture state "
                        "for: " + ", ".join(sorted(unrestorable))
                    )
                build.analysis_handle = analysis_handle = self.plugin_runtime.start_repository_analysis(
                    capabilities,
                    commit,
                    snapshots=snapshots,
                    source_root=(
                        repository_facts.source_root if repository_facts else None
                    ),
                )

            requested_paths = tuple(sorted({
                *changed_paths,
                *deleted_paths,
            }))
            cancellation_check = (
                (lambda: self._raise_if_cancelled(cancellation_event))
                if cancellation_event is not None
                else None
            )
            affected_paths, old_document_paths = writer.remove_paths(
                requested_paths,
                cancellation_check=cancellation_check,
            )
            self._raise_if_cancelled(cancellation_event)
            # Repository state is replaced below. Repository-wide plugin
            # output is reconciled after finalization so identical
            # content-addressed rows survive the delta instead of being
            # deleted and recreated on every historical revision.
            writer.remove_repository_state_output()
            self._raise_if_cancelled(cancellation_event)

            repository_file_by_path = {
                path.as_posix(): path for path in repository_files
            }
            eligible_path_set = {path.as_posix() for path in eligible_files}
            affected_eligible_files = [
                repository_file_by_path[path]
                for path in affected_paths
                if path in eligible_path_set
            ]
            if analysis_handle is not None and analysis_handle.active:
                from codecrow_plugins import FileArtifact

                deleted_path_set = set(deleted_paths)
                removed_from_analysis = tuple(sorted(
                    path
                    for path in affected_paths
                    if path not in eligible_path_set
                    and (
                        path in old_document_paths
                        or path in deleted_path_set
                    )
                ))
                if removed_from_analysis:
                    analysis_handle.ingest(tuple(
                        FileArtifact(path=path, content="", deleted=True)
                        for path in removed_from_analysis
                    ))

            document_count = max(
                0,
                int(base_receipt.get("document_count", 0) or 0)
                - len(old_document_paths),
            )
            def report_batch(batch_number: int, total_batches: int) -> None:
                self._progress(
                    progress_callback,
                    "indexing_delta",
                    f"Indexed repository delta {batch_number}/{total_batches}",
                    20 + round(55 * batch_number / total_batches),
                    completedBatches=batch_number,
                    totalBatches=total_batches,
                    changedFiles=len(affected_paths),
                    indexedUnits=writer.unit_count,
                    indexedRelations=writer.relation_count,
                )

            document_count += self.files.index_files(
                repo_path=repo_path, workspace=workspace, project=project,
                branch=branch, commit=commit, source_tree=source_tree,
                eligible_files=affected_eligible_files, writer=writer,
                dispositions=dispositions, capabilities=capabilities,
                analysis_handle=analysis_handle, skipped_paths=skipped_paths,
                record_skip=record_skip,
                check_cancelled=lambda: self._raise_if_cancelled(cancellation_event),
                on_batch=report_batch, replace_missing_files=True,
            )

            if analysis_handle is not None:
                self._raise_if_cancelled(cancellation_event)
                self._progress(
                    progress_callback,
                    "finalizing_repository",
                    "Finalizing repository-wide structural plugin output",
                    82,
                    indexedUnits=writer.unit_count,
                    indexedRelations=writer.relation_count,
                )
                deadline = time.monotonic() + _config_float(
                    self.config,
                    "architecture_finalization_timeout_seconds",
                    600.0,
                )
                self.enrichment.finish(
                    writer, analysis_handle, deadline=deadline,
                    discard_stale_outputs=True,
                )
                self._raise_if_cancelled(cancellation_event)

            repository_facts_json = write_repository_state(
                writer, commit=commit, repository_facts=repository_facts,
                repository_files=repository_files, project_type=project_type,
                source_root=source_root,
            )
            self._raise_if_cancelled(cancellation_event)
            self._progress(
                progress_callback,
                "resolving_relations",
                "Resolving structural relation endpoints",
                90,
                indexedUnits=writer.unit_count,
                indexedRelations=writer.relation_count,
            )
            writer.resolve_touched_relations()
            self._raise_if_cancelled(cancellation_event)
            writer.refresh_counts()
            if (
                self.config.max_chunks_per_index > 0
                and writer.unit_count > self.config.max_chunks_per_index
            ):
                raise ValueError(
                    "Repository exceeds structural unit limit: "
                    f"{writer.unit_count} units "
                    f"(max: {self.config.max_chunks_per_index})"
                )
            if not source_tree_exclusively_owned:
                require_repository_source_tree_unchanged(repo_path, source_tree)
            self._raise_if_cancelled(cancellation_event)
            self._progress(
                progress_callback,
                "sealing",
                "Computing and sealing the repository-delta generation receipt",
                96,
                indexedUnits=writer.unit_count,
                indexedRelations=writer.relation_count,
            )
            skipped_file_count = int(
                base_receipt.get("skipped_file_count", 0) or 0
            ) + len(skipped_paths)
            receipt = build_receipt(
                connection,
                workspace=workspace,
                project=project,
                branch=branch,
                revision=commit,
                source_tree_sha256=source_tree.tree_sha256,
                collection_target=collection_target,
                repository_facts_json=repository_facts_json,
                plugin_ids=plugin_ids,
                plugin_fingerprint=plugin_fingerprint,
                plugin_descriptor_fingerprint=descriptor_fingerprint,
                plugin_implementation_fingerprint=implementation_fingerprint,
                index_representation_fingerprint=(
                    self.index_representation_fingerprint
                ),
                include_patterns=include_patterns,
                exclude_patterns=exclude_patterns,
                document_count=document_count,
                skipped_file_count=skipped_file_count,
                snapshot_metadata=snapshot_metadata,
            )
            receipt = self.publisher.publish(
                writer, pending, receipt, workspace=workspace, project=project,
                branch=branch, commit=commit, collection_target=collection_target,
                activation_guard=activation_guard,
                check_cancelled=lambda: self._raise_if_cancelled(cancellation_event),
            )
            self._progress(
                progress_callback,
                "complete",
                "Repository structural delta is sealed",
                100,
                changedFiles=len(affected_paths),
                indexedUnits=receipt.get("unit_count", writer.unit_count),
                indexedRelations=receipt.get(
                    "relation_count", writer.relation_count
                ),
            )
            return self._stats_from_receipt(
                receipt,
                document_count=document_count,
                skipped_file_count=skipped_file_count,
            )

    def _build_generation(
        self,
        *,
        repo_path: Path,
        workspace: str,
        project: str,
        branch: str,
        commit: str,
        include_patterns: Optional[List[str]],
        exclude_patterns: Optional[List[str]],
        source_tree,
        collection_target: str,
        progress_callback: Optional[Callable[[dict], None]],
        project_type: Optional[str],
        source_root: Optional[str],
        activation_guard: Callable[[], None],
        snapshot_metadata: Optional[Mapping[str, Any]] = None,
        cancellation_event: Optional[threading.Event] = None,
        source_tree_exclusively_owned: bool = False,
        repository_fact_paths: Optional[Sequence[str]] = None,
    ) -> IndexStats:
        with pending_generation(self.store, collection_target) as build:
            pending, connection = build.paths, build.connection
            writer = StructuralGraphWriter(
                connection,
                defer_file_relation_ownership=True,
            )
            skipped_paths: set[str] = set()
            document_count = 0
            capabilities = None
            implementation_fingerprint = _ZERO_FINGERPRINT
            repository_facts = None
            analysis_handle = None

            def record_skip(skip: RepositoryFileSkip) -> None:
                skipped_paths.add(skip.path)

            self._raise_if_cancelled(cancellation_event)
            self._progress(
                progress_callback,
                "scanning",
                "Scanning repository files for structural units",
                5,
            )
            repository_files = list(self.loader.iter_repository_files(
                repo_path,
                include_patterns,
                exclude_patterns,
                expected_file_sha256=source_tree.file_sha256_by_path,
                on_skip=record_skip,
            ))
            self._raise_if_cancelled(cancellation_event)
            if (
                self.config.max_files_per_index > 0
                and len(repository_files) > self.config.max_files_per_index
            ):
                raise ValueError(
                    "Repository exceeds structural file limit: "
                    f"{len(repository_files)} files "
                    f"(max: {self.config.max_files_per_index})"
                )

            if self.plugin_catalog is not None and self.plugin_selector is not None:
                from codecrow_plugins import build_repository_facts

                self._raise_if_cancelled(cancellation_event)
                repository_facts = build_repository_facts(
                    repo_path,
                    commit,
                    (
                        repository_fact_paths
                        if repository_fact_paths is not None
                        else repository_files
                    ),
                    self.plugin_catalog.registry,
                    project_type=project_type,
                    source_root=source_root,
                )
                capabilities = self.plugin_selector.select(repository_facts)
                implementation_fingerprint = (
                    self.plugin_catalog.implementation_fingerprint(
                        capabilities.repository_plugins
                    )
                )
                self._progress(
                    progress_callback,
                    "framework",
                    "Selected structural plugins: "
                    + (", ".join(capabilities.repository_plugins) or "generic"),
                    10,
                )
                self._raise_if_cancelled(cancellation_event)

            eligible_files, dispositions = self.enrichment.file_policy(
                repository_files, capabilities,
                lambda: self._raise_if_cancelled(cancellation_event),
            )
            if self.plugin_runtime is not None and capabilities is not None:
                build.analysis_handle = analysis_handle = self.plugin_runtime.start_repository_analysis(
                    capabilities,
                    commit,
                    source_root=(
                        repository_facts.source_root if repository_facts else None
                    ),
                )
                self._raise_if_cancelled(cancellation_event)

            def report_batch(batch_number: int, total_batches: int) -> None:
                self._progress(
                    progress_callback,
                    "indexing",
                    f"Indexed structural batch {batch_number}/{total_batches}",
                    15 + round(65 * batch_number / total_batches),
                    completedBatches=batch_number,
                    totalBatches=total_batches,
                    indexedUnits=writer.unit_count,
                    indexedRelations=writer.relation_count,
                    skippedFiles=len(skipped_paths),
                )

            document_count += self.files.index_files(
                repo_path=repo_path, workspace=workspace, project=project,
                branch=branch, commit=commit, source_tree=source_tree,
                eligible_files=eligible_files, writer=writer,
                dispositions=dispositions, capabilities=capabilities,
                analysis_handle=analysis_handle, skipped_paths=skipped_paths,
                record_skip=record_skip,
                check_cancelled=lambda: self._raise_if_cancelled(cancellation_event),
                on_batch=report_batch, replace_missing_files=False,
            )

            writer.flush_deferred_file_relation_ownership()
            if analysis_handle is not None:
                self._raise_if_cancelled(cancellation_event)
                self._progress(
                    progress_callback,
                    "finalizing_repository",
                    "Finalizing repository-wide structural plugin output",
                    82,
                    indexedUnits=writer.unit_count,
                    indexedRelations=writer.relation_count,
                )
                deadline = time.monotonic() + _config_float(
                    self.config,
                    "architecture_finalization_timeout_seconds",
                    600.0,
                )
                self.enrichment.finish(
                    writer, analysis_handle, deadline=deadline,
                    discard_stale_outputs=False,
                )
                self._raise_if_cancelled(cancellation_event)

            self._raise_if_cancelled(cancellation_event)
            repository_facts_json = write_repository_state(
                writer, commit=commit, repository_facts=repository_facts,
                repository_files=repository_files, project_type=project_type,
                source_root=source_root,
            )
            self._raise_if_cancelled(cancellation_event)
            self._progress(
                progress_callback,
                "resolving_relations",
                "Resolving structural relation endpoints",
                90,
                indexedUnits=writer.unit_count,
                indexedRelations=writer.relation_count,
            )
            writer.resolve_relations()
            self._raise_if_cancelled(cancellation_event)

            if (
                self.config.max_chunks_per_index > 0
                and writer.unit_count > self.config.max_chunks_per_index
            ):
                raise ValueError(
                    "Repository exceeds structural unit limit: "
                    f"{writer.unit_count} units "
                    f"(max: {self.config.max_chunks_per_index})"
                )

            plugin_ids = (
                tuple(capabilities.repository_plugins)
                if capabilities is not None
                else ()
            )
            # A file may change after the attested scan but before its batch is
            # loaded. Optional parsers may skip unreadable files, but an exact
            # generation must never seal that partial result under the old
            # source-tree identity.
            self._raise_if_cancelled(cancellation_event)
            if not source_tree_exclusively_owned:
                require_repository_source_tree_unchanged(repo_path, source_tree)
            self._raise_if_cancelled(cancellation_event)
            self._progress(
                progress_callback,
                "sealing",
                "Computing and sealing the structural generation receipt",
                96,
                indexedUnits=writer.unit_count,
                indexedRelations=writer.relation_count,
            )
            receipt = build_receipt(
                connection,
                workspace=workspace,
                project=project,
                branch=branch,
                revision=commit,
                source_tree_sha256=source_tree.tree_sha256,
                collection_target=collection_target,
                repository_facts_json=repository_facts_json,
                plugin_ids=plugin_ids,
                plugin_fingerprint=(
                    capabilities.fingerprint
                    if capabilities is not None
                    else _ZERO_FINGERPRINT
                ),
                plugin_descriptor_fingerprint=(
                    capabilities.descriptor_fingerprint
                    if capabilities is not None
                    else _ZERO_FINGERPRINT
                ),
                plugin_implementation_fingerprint=implementation_fingerprint,
                index_representation_fingerprint=(
                    self.index_representation_fingerprint
                ),
                include_patterns=include_patterns,
                exclude_patterns=exclude_patterns,
                document_count=document_count,
                skipped_file_count=len(skipped_paths),
                snapshot_metadata=snapshot_metadata,
            )
            receipt = self.publisher.publish(
                writer, pending, receipt, workspace=workspace, project=project,
                branch=branch, commit=commit, collection_target=collection_target,
                activation_guard=activation_guard,
                check_cancelled=lambda: self._raise_if_cancelled(cancellation_event),
            )
            self._progress(
                progress_callback,
                "complete",
                "Structural repository generation is sealed",
                100,
                indexedUnits=receipt.get("unit_count", writer.unit_count),
                indexedRelations=receipt.get(
                    "relation_count", writer.relation_count
                ),
            )
            return self._stats_from_receipt(
                receipt,
                document_count=document_count,
                skipped_file_count=len(skipped_paths),
            )
