"""Immutable SQLite structural-index manager."""

from __future__ import annotations

import json
import hashlib
import logging
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, List, Mapping, Optional, Sequence

from ...models.config import IndexStats, RAGConfig
from ..coordination import ProjectMutationCoordinator
from ..documents import TextNode
from ..exact_index import (
    ExactIndexPreconditionError,
    RepositoryDeltaRebuildRequired,
)
from ..index_representation import (
    branch_splitter_kwargs,
    index_representation_fingerprint,
)
from ..loader import DocumentLoader, RepositoryFileSkip
from ..source_tree import (
    attest_repository_source_tree,
    require_repository_source_tree_unchanged,
    verify_repository_source_tree,
)
from ..splitter import ASTCodeSplitter
from ..structural_store import (
    StructuralGenerationStore,
    StructuralGraphReader,
    StructuralGraphWriter,
    build_receipt,
    write_receipt,
)


logger = logging.getLogger(__name__)
_ZERO_FINGERPRINT = "sha256:" + "0" * 64
_BATCH_SIZE = 50


class RepositoryIndexCancelled(InterruptedError):
    """Raised when an admitted repository build is cooperatively cancelled."""


def _config_int(config, name: str, default: int) -> int:
    try:
        return max(1, int(getattr(config, name, default)))
    except (TypeError, ValueError):
        return default


def _config_float(config, name: str, default: float) -> float:
    try:
        return max(0.0, float(getattr(config, name, default)))
    except (TypeError, ValueError):
        return default


def _unsafe_delta_plugin_selection_changes(
    registry,
    base_plugin_ids: Sequence[str],
    plugin_ids: Sequence[str],
) -> tuple[str, ...]:
    """Return selection changes that need a complete repository rebuild.

    A syntax-only language plugin is selected from the presence of its own file
    extension. Adding or deleting the last such file is fully represented by
    the exact changed/deleted path set, and the plugin owns no repository-wide
    state. Framework, domain, graph, and policy changes are not locally bounded.
    """

    unsafe = []
    for plugin_id in sorted(set(base_plugin_ids).symmetric_difference(plugin_ids)):
        descriptor = registry.descriptor(plugin_id)
        kind = str(getattr(descriptor.kind, "value", descriptor.kind))
        capabilities = {
            str(getattr(capability, "value", capability))
            for capability in descriptor.capabilities
        }
        if kind != "language" or capabilities != {"syntax"}:
            unsafe.append(plugin_id)
    return tuple(unsafe)


class RAGIndexManager:
    """Build and query exact structural generations without a vector store."""

    def __init__(self, config: RAGConfig):
        self.config = config
        self.store = StructuralGenerationStore(config.structural_index_root)
        self.index_representation_fingerprint = (
            index_representation_fingerprint(config)
        )
        self._full_index_capacity = threading.BoundedSemaphore(
            _config_int(config, "full_index_concurrency", 1)
        )
        self._mutation_coordinator = ProjectMutationCoordinator(
            os.getenv("REDIS_URL", "redis://redis:6379/1"),
            lease_seconds=_config_int(
                config,
                "rag_mutation_lease_seconds",
                300,
            ),
            acquire_timeout_seconds=_config_float(
                config,
                "rag_mutation_acquire_timeout_seconds",
                5.0,
            ),
        )

        plugin_catalog = None
        plugin_runtime = None
        plugin_selector = None
        try:
            from codecrow_plugins import PluginRuntime, ProjectSelector
            from codecrow_plugins.bootstrap import discover_builtin_plugins

            plugin_catalog = discover_builtin_plugins()
            plugin_runtime = PluginRuntime(plugin_catalog)
            plugin_selector = ProjectSelector(plugin_catalog.registry)
            logger.info(
                "Loaded structural plugins: %s",
                ", ".join(plugin_catalog.registry.ordered_ids),
            )
        except ModuleNotFoundError as exception:
            if exception.name != "codecrow_plugins":
                raise
            logger.warning(
                "Plugin package is unavailable; using generic Tree-sitter structure"
            )

        self.plugin_catalog = plugin_catalog
        self.plugin_runtime = plugin_runtime
        self.plugin_selector = plugin_selector
        self.splitter = ASTCodeSplitter(
            **branch_splitter_kwargs(config),
            plugin_runtime=plugin_runtime,
        )
        self.loader = DocumentLoader(config)

    def current_representation_identity(self) -> dict[str, object]:
        """Return build-content identity independent of any repository."""
        plugin_ids: tuple[str, ...] = ()
        descriptor_fingerprint = _ZERO_FINGERPRINT
        implementation_fingerprint = _ZERO_FINGERPRINT
        if self.plugin_catalog is not None:
            plugin_ids = self.plugin_catalog.registry.ordered_ids
            descriptor_fingerprint = self.plugin_catalog.registry.fingerprint
            implementation_fingerprint = (
                self.plugin_catalog.implementation_fingerprint(plugin_ids)
            )
        projection = {
            "index_representation_fingerprint": (
                self.index_representation_fingerprint
            ),
            "plugin_descriptor_fingerprint": descriptor_fingerprint,
            "plugin_implementation_fingerprint": implementation_fingerprint,
            "plugin_ids": list(plugin_ids),
        }
        encoded = json.dumps(
            projection,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return {
            **projection,
            "representation_identity": (
                "sha256:" + hashlib.sha256(encoded).hexdigest()
            ),
        }

    @contextmanager
    def _admit_full_index(
        self,
        workspace: str,
        project: str,
        branch: str,
        progress_callback: Optional[Callable[[dict], None]],
        cancellation_event: Optional[threading.Event] = None,
    ):
        self._raise_if_cancelled(cancellation_event)
        acquired = self._full_index_capacity.acquire(blocking=False)
        if not acquired:
            self._progress(
                progress_callback,
                "waiting_capacity",
                "Waiting for structural-index capacity",
                0,
            )
            if cancellation_event is None:
                self._full_index_capacity.acquire()
                acquired = True
            else:
                while not acquired:
                    self._raise_if_cancelled(cancellation_event)
                    acquired = self._full_index_capacity.acquire(timeout=0.05)
        try:
            self._raise_if_cancelled(cancellation_event)
            yield
        finally:
            if acquired:
                self._full_index_capacity.release()

    @staticmethod
    def _raise_if_cancelled(
        cancellation_event: Optional[threading.Event],
    ) -> None:
        if cancellation_event is not None and cancellation_event.is_set():
            raise RepositoryIndexCancelled(
                "structural repository indexing was cancelled"
            )

    @staticmethod
    def _progress(
        callback: Optional[Callable[[dict], None]],
        stage: str,
        message: str,
        progress: int | None = None,
        **details,
    ) -> None:
        if callback is None:
            return
        event = {"stage": stage, "message": message, **details}
        if progress is not None:
            event["progress"] = max(0, min(100, int(progress)))
        try:
            callback(event)
        except Exception:
            logger.debug("Structural progress observer failed", exc_info=True)

    def index_repository(
        self,
        repo_path: str,
        workspace: str,
        project: str,
        branch: str,
        commit: str,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
        source_tree_sha256: Optional[str] = None,
        collection_target: str = "",
        progress_callback: Optional[Callable[[dict], None]] = None,
        project_type: Optional[str] = None,
        source_root: Optional[str] = None,
        snapshot_metadata: Optional[Mapping[str, Any]] = None,
        cancellation_event: Optional[threading.Event] = None,
        source_tree_exclusively_owned: bool = False,
        repository_fact_paths: Optional[Sequence[str]] = None,
    ) -> IndexStats:
        if not collection_target:
            raise ValueError(
                "structural repository indexing requires an immutable target"
            )
        with self._admit_full_index(
            workspace,
            project,
            branch,
            progress_callback,
            cancellation_event,
        ):
            self._raise_if_cancelled(cancellation_event)
            source_tree = (
                verify_repository_source_tree(
                    repo_path,
                    commit,
                    source_tree_sha256,
                )
                if source_tree_sha256
                else attest_repository_source_tree(repo_path, commit)
            )
            self._raise_if_cancelled(cancellation_event)
            with self._mutation_coordinator.acquire(
                workspace,
                project,
                "full-structural-index",
                collection_target=collection_target,
            ) as lease:
                self._raise_if_cancelled(cancellation_event)
                existing = self.get_revision_preflight(
                    workspace,
                    project,
                    branch,
                    commit,
                    collection_target=collection_target,
                )
                if existing is not None:
                    if existing.get("source_tree_sha256") != source_tree.tree_sha256:
                        raise ExactIndexPreconditionError(
                            "structural generation target is already sealed for "
                            "different repository content"
                        )
                    if snapshot_metadata is not None and existing.get(
                        "snapshot_metadata"
                    ) != dict(snapshot_metadata):
                        raise ExactIndexPreconditionError(
                            "structural generation target is already sealed for "
                            "different snapshot provenance"
                        )
                    self._raise_if_cancelled(cancellation_event)
                    return self._stats_from_receipt(existing)
                return self._build_generation(
                    repo_path=Path(repo_path),
                    workspace=workspace,
                    project=project,
                    branch=branch,
                    commit=commit,
                    include_patterns=include_patterns,
                    exclude_patterns=exclude_patterns,
                    source_tree=source_tree,
                    collection_target=collection_target,
                    progress_callback=progress_callback,
                    project_type=project_type,
                    source_root=source_root,
                    snapshot_metadata=snapshot_metadata,
                    cancellation_event=cancellation_event,
                    activation_guard=lease.assert_owned,
                    source_tree_exclusively_owned=(
                        source_tree_exclusively_owned
                    ),
                    repository_fact_paths=repository_fact_paths,
                )

    def index_repository_delta(
        self,
        *,
        repo_path: str,
        workspace: str,
        project: str,
        branch: str,
        base_revision: str,
        commit: str,
        changed_paths: Sequence[str],
        deleted_paths: Sequence[str],
        source_tree_sha256: Optional[str],
        collection_target: str,
        base_collection_target: str,
        base_generation_manifest_sha256: str,
        progress_callback: Optional[Callable[[dict], None]] = None,
        project_type: Optional[str] = None,
        source_root: Optional[str] = None,
        snapshot_metadata: Optional[Mapping[str, Any]] = None,
        cancellation_event: Optional[threading.Event] = None,
        source_tree_exclusively_owned: bool = False,
    ) -> IndexStats:
        """Build one exact repository generation as a delta over a sealed base."""

        if not collection_target or not base_collection_target:
            raise ValueError(
                "repository delta indexing requires immutable base and output targets"
            )
        if not base_generation_manifest_sha256:
            raise ValueError(
                "repository delta indexing requires an exact base manifest"
            )
        with self._admit_full_index(
            workspace,
            project,
            branch,
            progress_callback,
            cancellation_event,
        ):
            self._raise_if_cancelled(cancellation_event)
            source_tree = (
                verify_repository_source_tree(
                    repo_path,
                    commit,
                    source_tree_sha256,
                )
                if source_tree_sha256
                else attest_repository_source_tree(repo_path, commit)
            )
            with self._mutation_coordinator.acquire(
                workspace,
                project,
                "repository-structural-delta",
                collection_target=collection_target,
            ) as lease:
                self._raise_if_cancelled(cancellation_event)
                existing = self.get_revision_preflight(
                    workspace,
                    project,
                    branch,
                    commit,
                    collection_target=collection_target,
                )
                if existing is not None:
                    if existing.get("source_tree_sha256") != source_tree.tree_sha256:
                        raise ExactIndexPreconditionError(
                            "repository delta target is already sealed for different "
                            "repository content"
                        )
                    if snapshot_metadata is not None and existing.get(
                        "snapshot_metadata"
                    ) != dict(snapshot_metadata):
                        raise ExactIndexPreconditionError(
                            "repository delta target is already sealed for different "
                            "base provenance"
                        )
                    return self._stats_from_receipt(existing)

                return self._build_repository_delta(
                    repo_path=Path(repo_path),
                    workspace=workspace,
                    project=project,
                    branch=branch,
                    base_revision=base_revision,
                    commit=commit,
                    changed_paths=changed_paths,
                    deleted_paths=deleted_paths,
                    source_tree=source_tree,
                    collection_target=collection_target,
                    base_collection_target=base_collection_target,
                    base_generation_manifest_sha256=(
                        base_generation_manifest_sha256
                    ),
                    progress_callback=progress_callback,
                    project_type=project_type,
                    source_root=source_root,
                    snapshot_metadata=snapshot_metadata,
                    cancellation_event=cancellation_event,
                    activation_guard=lease.assert_owned,
                    source_tree_exclusively_owned=(
                        source_tree_exclusively_owned
                    ),
                )

    def index_proposed_tree_delta(
        self,
        *,
        repo_path: str,
        workspace: str,
        project: str,
        branch: str,
        base_revision: str,
        commit: str,
        changed_paths: Sequence[str],
        deleted_paths: Sequence[str],
        source_tree_sha256: Optional[str],
        collection_target: str,
        base_collection_target: str,
        base_generation_manifest_sha256: str,
        progress_callback: Optional[Callable[[dict], None]] = None,
        project_type: Optional[str] = None,
        source_root: Optional[str] = None,
        snapshot_metadata: Optional[Mapping[str, Any]] = None,
        cancellation_event: Optional[threading.Event] = None,
        source_tree_exclusively_owned: bool = False,
    ) -> IndexStats:
        """Build the request-scoped proposed tree through the neutral delta path."""

        return self.index_repository_delta(
            repo_path=repo_path,
            workspace=workspace,
            project=project,
            branch=branch,
            base_revision=base_revision,
            commit=commit,
            changed_paths=changed_paths,
            deleted_paths=deleted_paths,
            source_tree_sha256=source_tree_sha256,
            collection_target=collection_target,
            base_collection_target=base_collection_target,
            base_generation_manifest_sha256=base_generation_manifest_sha256,
            progress_callback=progress_callback,
            project_type=project_type,
            source_root=source_root,
            snapshot_metadata=snapshot_metadata,
            cancellation_event=cancellation_event,
            source_tree_exclusively_owned=source_tree_exclusively_owned,
        )

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
        pending, connection, base_receipt, pending_ownership = (
            self.store.clone_bound_to_pending(
                source_target=base_collection_target,
                target=collection_target,
                workspace=workspace,
                project=project,
                branch=branch,
                revision=base_revision,
                manifest_sha256=base_generation_manifest_sha256,
            )
        )
        writer = StructuralGraphWriter(connection, track_mutations=True)
        writer.refresh_counts()
        skipped_paths: set[str] = set()
        analysis_handle = None

        def record_skip(skip: RepositoryFileSkip) -> None:
            skipped_paths.add(skip.path)

        try:
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

            dispositions: dict[str, object] = {}
            eligible_files = repository_files
            if self.plugin_runtime is not None and capabilities is not None:
                from codecrow_plugins import FileDisposition, RepositorySnapshot

                for file_number, relative_path in enumerate(repository_files):
                    if file_number % _BATCH_SIZE == 0:
                        self._raise_if_cancelled(cancellation_event)
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
                analysis_handle = self.plugin_runtime.start_repository_analysis(
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

                removed_from_analysis = tuple(sorted(
                    path
                    for path in affected_paths
                    if path not in eligible_path_set
                    and (
                        path in old_document_paths
                        or path in set(deleted_paths)
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
            total_batches = max(
                1,
                (len(affected_eligible_files) + _BATCH_SIZE - 1)
                // _BATCH_SIZE,
            )
            for offset in range(0, len(affected_eligible_files), _BATCH_SIZE):
                self._raise_if_cancelled(cancellation_event)
                batch = affected_eligible_files[offset:offset + _BATCH_SIZE]
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
                self._raise_if_cancelled(cancellation_event)
                loaded_paths = {
                    str(document.metadata["path"]) for document in documents
                }
                if analysis_handle is not None and analysis_handle.active:
                    from codecrow_plugins import FileArtifact

                    artifacts = [
                        FileArtifact(
                            path=str(document.metadata["path"]),
                            content=document.text,
                        )
                        for document in documents
                    ]
                    artifacts.extend(
                        FileArtifact(
                            path=path.as_posix(),
                            content="",
                            deleted=True,
                        )
                        for path in batch
                        if path.as_posix() not in loaded_paths
                    )
                    if artifacts:
                        analysis_handle.ingest(tuple(sorted(
                            artifacts,
                            key=lambda artifact: artifact.path,
                        )))
                        self._raise_if_cancelled(cancellation_event)

                for document in documents:
                    self._raise_if_cancelled(cancellation_event)
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
                        document_count += 1
                    for chunk in chunks:
                        unit_id = writer.add_unit(
                            chunk,
                            record_type=(
                                "plugin_context"
                                if architecture_only
                                else "source_unit"
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
                                    "Structural plugin diagnostic plugin=%s "
                                    "code=%s path=%s: %s",
                                    diagnostic.plugin_id,
                                    diagnostic.code,
                                    diagnostic.path or path,
                                    diagnostic.message,
                                )
                            for fact in facts:
                                writer.add_graph_fact(fact, plugin_id=None)
                        except Exception as exception:
                            logger.warning(
                                "Plugin graph extraction failed open for %s: %s",
                                path,
                                exception,
                            )

                batch_number = offset // _BATCH_SIZE + 1
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
                self._raise_if_cancelled(cancellation_event)

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
                    # The cloned generation still contains the base revision's
                    # repository-wide packets and snapshots. They are not valid
                    # proposed-tree evidence after any changed/deleted artifact,
                    # so fail open by dropping the optional enrichment instead
                    # of sealing stale base facts under the proposed revision.
                    writer.remove_repository_analysis_outputs()
                    logger.warning(
                        "Repository architecture delta finalization failed open: %s",
                        exception,
                        exc_info=True,
                    )
                self._raise_if_cancelled(cancellation_event)

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
            self._raise_if_cancelled(cancellation_event)
            writer.seal(receipt)
            self._raise_if_cancelled(cancellation_event)
            connection.close()
            connection = None
            write_receipt(pending.receipt, receipt)
            self._raise_if_cancelled(cancellation_event)
            activation_guard()
            self._raise_if_cancelled(cancellation_event)
            try:
                self.store.publish(pending)
            except FileExistsError:
                existing = self.get_revision_preflight(
                    workspace,
                    project,
                    branch,
                    commit,
                    collection_target=collection_target,
                )
                if existing is None or existing.get(
                    "generation_manifest_sha256"
                ) != receipt["generation_manifest_sha256"]:
                    raise ExactIndexPreconditionError(
                        "proposed-tree target was published concurrently with "
                        "different content"
                    )
                self.store.remove_pending(pending)
                receipt = existing
            self.store.release_pending_ownership(pending_ownership)
            pending_ownership = None
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
        except BaseException:
            if connection is not None:
                connection.close()
            self.store.remove_pending(pending)
            self.store.release_pending_ownership(pending_ownership)
            raise

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
        pending = self.store.pending_paths(collection_target)
        connection = self.store.initialize(pending)
        try:
            pending_ownership = self.store.acquire_pending_ownership(pending)
        except BaseException:
            connection.close()
            self.store.remove_pending(pending)
            raise
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

        try:
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

            eligible_files = repository_files
            dispositions: dict[str, object] = {}
            if self.plugin_runtime is not None and capabilities is not None:
                from codecrow_plugins import FileDisposition

                for file_number, relative_path in enumerate(repository_files):
                    if file_number % _BATCH_SIZE == 0:
                        self._raise_if_cancelled(cancellation_event)
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
                analysis_handle = self.plugin_runtime.start_repository_analysis(
                    capabilities,
                    commit,
                    source_root=(
                        repository_facts.source_root if repository_facts else None
                    ),
                )
                self._raise_if_cancelled(cancellation_event)

            total_batches = max(1, (len(eligible_files) + _BATCH_SIZE - 1) // _BATCH_SIZE)
            for offset in range(0, len(eligible_files), _BATCH_SIZE):
                self._raise_if_cancelled(cancellation_event)
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
                self._raise_if_cancelled(cancellation_event)
                if analysis_handle is not None and analysis_handle.active and documents:
                    from codecrow_plugins import FileArtifact

                    analysis_handle.ingest(tuple(sorted(
                        (
                            FileArtifact(
                                path=str(document.metadata["path"]),
                                content=document.text,
                            )
                            for document in documents
                        ),
                        key=lambda artifact: artifact.path,
                    )))
                    self._raise_if_cancelled(cancellation_event)

                for document in documents:
                    self._raise_if_cancelled(cancellation_event)
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
                        document_count += 1
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

                batch_number = offset // _BATCH_SIZE + 1
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
                self._raise_if_cancelled(cancellation_event)

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
                    logger.warning(
                        "Repository architecture finalization failed open: %s",
                        exception,
                        exc_info=True,
                    )
                self._raise_if_cancelled(cancellation_event)

            self._raise_if_cancelled(cancellation_event)
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
            self._raise_if_cancelled(cancellation_event)
            writer.seal(receipt)
            self._raise_if_cancelled(cancellation_event)
            connection.close()
            connection = None
            write_receipt(pending.receipt, receipt)
            self._raise_if_cancelled(cancellation_event)
            activation_guard()
            self._raise_if_cancelled(cancellation_event)
            try:
                self.store.publish(pending)
            except FileExistsError:
                existing = self.get_revision_preflight(
                    workspace,
                    project,
                    branch,
                    commit,
                    collection_target=collection_target,
                )
                if existing is None or existing.get(
                    "generation_manifest_sha256"
                ) != receipt["generation_manifest_sha256"]:
                    raise ExactIndexPreconditionError(
                        "structural generation target was published concurrently "
                        "with different content"
                    )
                self.store.remove_pending(pending)
                receipt = existing
            self.store.release_pending_ownership(pending_ownership)
            pending_ownership = None
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
        except BaseException:
            if connection is not None:
                connection.close()
            self.store.remove_pending(pending)
            self.store.release_pending_ownership(pending_ownership)
            raise

    def get_revision_preflight(
        self,
        workspace: str,
        project: str,
        branch: str,
        commit: str,
        *,
        collection_target: str,
    ) -> dict | None:
        receipt = self.store.read_receipt(collection_target)
        if receipt is None:
            return None
        if any(receipt.get(key) != value for key, value in (
            ("workspace", workspace),
            ("project", project),
            ("branch", branch),
            ("repository_revision", commit),
        )):
            return None
        # A receipt file is only a pointer. Preflight is the paid/review reuse
        # boundary, so also open the bound SQLite generation and verify its
        # schema plus embedded immutable seal before advertising it as usable.
        with self.store.open_bound(
            target=collection_target,
            workspace=workspace,
            project=project,
            branch=branch,
            revision=commit,
            manifest_sha256=str(receipt["generation_manifest_sha256"]),
        ):
            pass
        return {
            **receipt,
            "current_index_representation_fingerprint": (
                self.index_representation_fingerprint
            ),
        }

    def discover_revision_preflights(
        self,
        workspace: str,
        project: str,
        branch: str,
        *,
        commit: str | None = None,
    ) -> list[dict]:
        """Return verified reusable generations for one tenant-bound branch."""

        discovered: list[dict] = []
        receipts = self.store.repository_generation_receipts(
            workspace=workspace,
            project=project,
            branch=branch,
            revision=commit,
        )
        for receipt in receipts:
            target = str(receipt.get("collection_target") or "")
            revision = str(receipt.get("repository_revision") or "")
            if not target or not revision:
                continue
            try:
                preflight = self.get_revision_preflight(
                    workspace,
                    project,
                    branch,
                    revision,
                    collection_target=target,
                )
            except ExactIndexPreconditionError as exception:
                logger.warning(
                    "Skipping invalid discovered structural generation %s: %s",
                    target,
                    exception,
                )
                continue
            if preflight is not None:
                discovered.append(preflight)
        return discovered

    @contextmanager
    def open_reader(
        self,
        *,
        workspace: str,
        project: str,
        branch: str,
        revision: str,
        generation_manifest_sha256: str,
        collection_target: str,
    ):
        with self.store.open_bound(
            target=collection_target,
            workspace=workspace,
            project=project,
            branch=branch,
            revision=revision,
            manifest_sha256=generation_manifest_sha256,
        ) as (connection, receipt):
            yield StructuralGraphReader(connection, receipt)

    def delete_branch(
        self,
        workspace: str,
        project: str,
        branch: str,
        collection_target: str,
        generation_revision: str,
        generation_manifest_sha256: str,
    ) -> bool:
        with self._mutation_coordinator.acquire(
            workspace,
            project,
            "delete-structural-generation",
            collection_target=collection_target,
        ) as lease:
            lease.assert_owned()
            return self.store.delete(
                collection_target,
                workspace=workspace,
                project=project,
                branch=branch,
                revision=generation_revision,
                manifest_sha256=generation_manifest_sha256,
            )

    def cleanup_expired_pending_collections(self) -> int:
        return self.store.cleanup_pending()

    def cleanup_expired_review_generations(self) -> int:
        """Remove inactive request-scoped proposed-tree generations."""
        max_age_seconds = self.config.review_generation_ttl_seconds
        removed = 0
        for candidate in self.store.expired_review_generation_receipts(
            max_age_seconds=max_age_seconds,
        ):
            target = str(candidate["collection_target"])
            workspace = str(candidate["workspace"])
            project = str(candidate["project"])
            with self._mutation_coordinator.acquire(
                workspace,
                project,
                "cleanup-proposed-tree-generation",
                collection_target=target,
            ) as lease:
                lease.assert_owned()
                current = self.store.expired_review_generation_receipt(
                    target,
                    max_age_seconds=max_age_seconds,
                )
                if current is None:
                    continue
                if self.store.delete_expired_review_generation(
                    target,
                    max_age_seconds=max_age_seconds,
                    workspace=workspace,
                    project=project,
                    branch=str(current["branch"]),
                    revision=str(current["repository_revision"]),
                    manifest_sha256=str(
                        current["generation_manifest_sha256"]
                    ),
                ):
                    removed += 1
        return removed

    def cleanup_expired_collections(self) -> dict[str, int]:
        """Run the pending-build and proposed-tree lifecycle cleanup pass."""
        return {
            "pending": self.cleanup_expired_pending_collections(),
            "proposedTree": self.cleanup_expired_review_generations(),
        }

    @staticmethod
    def _stats_from_receipt(
        receipt: dict,
        *,
        document_count: int | None = None,
        skipped_file_count: int | None = None,
    ) -> IndexStats:
        unit_count = int(receipt.get("unit_count", 0) or 0)
        receipt_document_count = int(
            receipt.get("document_count", unit_count) or 0
        )
        receipt_skipped_file_count = int(
            receipt.get("skipped_file_count", 0) or 0
        )
        return IndexStats(
            namespace=str(receipt["collection_target"]),
            document_count=(
                receipt_document_count
                if document_count is None
                else document_count
            ),
            chunk_count=unit_count,
            skipped_file_count=(
                receipt_skipped_file_count
                if skipped_file_count is None
                else skipped_file_count
            ),
            skipped_chunk_count=0,
            last_updated=datetime.now(timezone.utc).isoformat(),
            workspace=str(receipt["workspace"]),
            project=str(receipt["project"]),
            branch=str(receipt["branch"]),
            generation_manifest_sha256=str(
                receipt["generation_manifest_sha256"]
            ),
            source_tree_sha256=str(receipt["source_tree_sha256"]),
            collection_target=str(receipt["collection_target"]),
            generation_member_count=int(
                receipt.get("generation_member_count", unit_count) or unit_count
            ),
        )

    def close(self) -> None:
        self._mutation_coordinator.close()
