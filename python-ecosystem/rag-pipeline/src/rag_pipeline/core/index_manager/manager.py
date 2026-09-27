"""Immutable SQLite structural-index manager."""

from __future__ import annotations

import json
import hashlib
import logging
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, List, Mapping, Optional, Sequence

from ...models.config import IndexStats, RAGConfig
from ..coordination import ProjectMutationCoordinator
from ..exact_index import ExactIndexPreconditionError
from ..index_representation import (
    branch_splitter_kwargs,
    index_representation_fingerprint,
)
from ..loader import DocumentLoader
from ..source_tree import attest_repository_source_tree, verify_repository_source_tree
from ..splitter import ASTCodeSplitter
from ..structural_store import StructuralGenerationStore, StructuralGraphReader


_ZERO_FINGERPRINT = "sha256:" + "0" * 64


from .build_support import (
    RepositoryIndexCancelled,
    _config_int,
    _config_float,
    _unsafe_delta_plugin_selection_changes,
)
from .generation_builder import GenerationBuilder


logger = logging.getLogger(__name__)


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

    def _generation_builder(self) -> GenerationBuilder:
        # Assemble per invocation so callers may replace a loader/runtime in
        # tests or configuration without retaining a stale collaborator.
        return GenerationBuilder(
            config=self.config, store=self.store, loader=self.loader,
            splitter=self.splitter, plugin_catalog=self.plugin_catalog,
            plugin_runtime=self.plugin_runtime, plugin_selector=self.plugin_selector,
            index_representation_fingerprint=self.index_representation_fingerprint,
            _progress=self._progress, _raise_if_cancelled=self._raise_if_cancelled,
            get_revision_preflight=self.get_revision_preflight,
            _stats_from_receipt=self._stats_from_receipt,
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
        return self._generation_builder()._build_repository_delta(
            repo_path=repo_path,
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
            base_generation_manifest_sha256=base_generation_manifest_sha256,
            progress_callback=progress_callback,
            project_type=project_type,
            source_root=source_root,
            activation_guard=activation_guard,
            snapshot_metadata=snapshot_metadata,
            cancellation_event=cancellation_event,
            source_tree_exclusively_owned=source_tree_exclusively_owned,
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
        return self._generation_builder()._build_generation(
            repo_path=repo_path,
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
            activation_guard=activation_guard,
            snapshot_metadata=snapshot_metadata,
            cancellation_event=cancellation_event,
            source_tree_exclusively_owned=source_tree_exclusively_owned,
            repository_fact_paths=repository_fact_paths,
        )

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
