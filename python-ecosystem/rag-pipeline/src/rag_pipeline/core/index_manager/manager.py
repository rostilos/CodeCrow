"""
Main RAG Index Manager.

Composes all index management components and provides the public API.
"""

import logging
import os
from typing import Callable, Optional, List

from llama_index.core import Settings
from qdrant_client import QdrantClient

from ...models.config import RAGConfig, IndexStats
from ...utils.utils import make_namespace, make_project_namespace
from ..splitter import ASTCodeSplitter
from ..loader import DocumentLoader
from ..embedding_factory import create_embedding_model, get_embedding_model_info
from ..coordination import ProjectMutationCoordinator
from ..index_representation import (
    branch_splitter_kwargs,
    index_representation_fingerprint,
)
from ..pr_overlay_representation import (
    pr_overlay_representation_fingerprint,
)

from .collection_manager import CollectionManager
from .branch_manager import BranchManager
from .point_operations import PointOperations
from .stats_manager import StatsManager
from .indexer import RepositoryIndexer, FileOperations

logger = logging.getLogger(__name__)


def _config_int(config, name: str, default: int) -> int:
    value = getattr(config, name, default)
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return default
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _config_float(config, name: str, default: float) -> float:
    value = getattr(config, name, default)
    if not isinstance(value, (int, float, str)):
        return default
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return default


class RAGIndexManager:
    """Manage RAG indices for code repositories using Qdrant.
    
    This is the main entry point for all indexing operations.
    """

    def __init__(self, config: RAGConfig):
        self.config = config
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
        self.index_representation_fingerprint = (
            index_representation_fingerprint(config)
        )
        self.pr_overlay_representation_fingerprint = (
            pr_overlay_representation_fingerprint(config)
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
            logger.info("Loaded plugins: %s", ", ".join(plugin_catalog.registry.ordered_ids))
        except ModuleNotFoundError as exception:
            if exception.name != "codecrow_plugins":
                raise
            logger.warning("Plugin package is not installed; using the generic RAG fallback")

        self.plugin_catalog = plugin_catalog
        self.plugin_runtime = plugin_runtime
        self.plugin_selector = plugin_selector

        # Qdrant client
        self.qdrant_client = QdrantClient(
            url=config.qdrant_url,
            api_key=config.qdrant_api_key or None,
        )
        logger.info(f"Connected to Qdrant at {config.qdrant_url}")

        # Embedding model (supports Ollama and OpenRouter via factory)
        embed_info = get_embedding_model_info(config)
        logger.info(f"Using embedding provider: {embed_info['provider']} ({embed_info['type']})")
        logger.info(f"Embedding model: {embed_info['model']}, dimension: {embed_info['embedding_dim']}")
        
        self.embed_model = create_embedding_model(config, workload="index")

        # Global settings
        Settings.embed_model = self.embed_model
        Settings.chunk_size = config.chunk_size
        Settings.chunk_overlap = config.chunk_overlap

        # Splitter and loader
        logger.info("Using ASTCodeSplitter for code chunking (tree-sitter query-based)")
        self.splitter = ASTCodeSplitter(
            **branch_splitter_kwargs(config),
            plugin_runtime=plugin_runtime,
        )
        self.loader = DocumentLoader(config)

        # Component managers
        self._collection_manager = CollectionManager(
            self.qdrant_client, config.embedding_dim
        )
        self._branch_manager = BranchManager(self.qdrant_client)
        self._point_ops = PointOperations(
            self.qdrant_client,
            self.embed_model,
            batch_size=_config_int(config, "qdrant_upsert_batch_size", 128),
            embedding_batch_size=(
                _config_int(config, "openrouter_batch_size", 50)
                if str(config.embedding_provider).lower() == "openrouter"
                else 50
            ),
            max_embedding_workers=(
                _config_int(config, "openrouter_index_concurrency", 8)
                if str(config.embedding_provider).lower() == "openrouter"
                else 1
            ),
            embedding_dim=config.embedding_dim,
        )
        self._stats_manager = StatsManager(
            self.qdrant_client, config.qdrant_collection_prefix
        )
        
        # Higher-level operations
        self._indexer = RepositoryIndexer(
            config=config,
            collection_manager=self._collection_manager,
            branch_manager=self._branch_manager,
            point_ops=self._point_ops,
            stats_manager=self._stats_manager,
            splitter=self.splitter,
            loader=self.loader,
            plugin_catalog=plugin_catalog,
            plugin_runtime=plugin_runtime,
            plugin_selector=plugin_selector,
        )
        self._file_ops = FileOperations(
            client=self.qdrant_client,
            point_ops=self._point_ops,
            collection_manager=self._collection_manager,
            stats_manager=self._stats_manager,
            splitter=self.splitter,
            loader=self.loader,
            plugin_catalog=plugin_catalog,
            plugin_runtime=plugin_runtime,
            plugin_selector=plugin_selector,
            representation_fingerprint=(
                self.index_representation_fingerprint
            ),
        )

    # Collection naming

    def _get_project_collection_name(self, workspace: str, project: str) -> str:
        """Generate Qdrant collection name from workspace/project."""
        namespace = make_project_namespace(workspace, project)
        return f"{self.config.qdrant_collection_prefix}_{namespace}"

    # Repository indexing

    def estimate_repository_size(
        self,
        repo_path: str,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None
    ) -> tuple[int, int]:
        """Estimate repository size (file count and chunk count)."""
        return self._indexer.estimate_repository_size(repo_path, include_patterns, exclude_patterns)

    def index_repository(
        self,
        repo_path: str,
        workspace: str,
        project: str,
        branch: str,
        commit: str,
        preserve_other_branches: bool = False,
        include_patterns: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
        progress_callback: Optional[Callable[[dict], None]] = None,
    ) -> IndexStats:
        """Index entire repository for a branch using atomic swap strategy."""
        alias_name = self._get_project_collection_name(workspace, project)
        with self._mutation_coordinator.acquire(
            workspace,
            project,
            "full-index",
        ) as lease:
            return self._indexer.index_repository(
                repo_path=repo_path,
                workspace=workspace,
                project=project,
                branch=branch,
                commit=commit,
                alias_name=alias_name,
                preserve_other_branches=preserve_other_branches,
                include_patterns=include_patterns,
                exclude_patterns=exclude_patterns,
                operation_id=lease.token,
                activation_guard=lease.assert_owned,
                progress_callback=progress_callback,
            )

    # File operations

    def update_files(
        self,
        file_paths: List[str],
        repo_base: str,
        workspace: str,
        project: str,
        branch: str,
        commit: str
    ) -> IndexStats:
        """Update specific files in the index (Delete Old -> Insert New)."""
        collection_name = self._get_project_collection_name(workspace, project)
        with self._mutation_coordinator.acquire(
            workspace,
            project,
            "update-files",
        ) as lease:
            return self._file_ops.update_files(
                file_paths=file_paths,
                repo_base=repo_base,
                workspace=workspace,
                project=project,
                branch=branch,
                commit=commit,
                collection_name=collection_name,
                mutation_guard=lease.assert_owned,
            )

    def delete_files(
        self,
        file_paths: List[str],
        workspace: str,
        project: str,
        branch: str,
        commit: Optional[str] = None,
    ) -> IndexStats:
        """Delete specific files from the index for a specific branch."""
        collection_name = self._get_project_collection_name(workspace, project)
        with self._mutation_coordinator.acquire(
            workspace,
            project,
            "delete-files",
        ) as lease:
            return self._file_ops.delete_files(
                file_paths=file_paths,
                workspace=workspace,
                project=project,
                branch=branch,
                collection_name=collection_name,
                commit=commit,
                mutation_guard=lease.assert_owned,
            )

    def apply_changes(
        self,
        updated_file_paths: List[str],
        deleted_file_paths: List[str],
        repo_base: Optional[str],
        workspace: str,
        project: str,
        branch: str,
        commit: str,
    ) -> IndexStats:
        """Apply a complete commit change set through one RAG mutation."""
        collection_name = self._get_project_collection_name(workspace, project)
        with self._mutation_coordinator.acquire(
            workspace,
            project,
            "apply-changes",
        ) as lease:
            return self._file_ops.apply_changes(
                updated_file_paths=updated_file_paths,
                deleted_file_paths=deleted_file_paths,
                repo_base=repo_base,
                workspace=workspace,
                project=project,
                branch=branch,
                commit=commit,
                collection_name=collection_name,
                mutation_guard=lease.assert_owned,
            )

    # Branch operations

    def delete_branch(self, workspace: str, project: str, branch: str) -> bool:
        """Delete all points for a specific branch from the project collection."""
        with self._mutation_coordinator.acquire(
            workspace,
            project,
            "delete-branch",
        ) as lease:
            collection_name = self._get_project_collection_name(workspace, project)
            if not self._collection_manager.collection_exists(collection_name):
                if not self._collection_manager.alias_exists(collection_name):
                    logger.warning(f"Collection {collection_name} does not exist")
                    return False

            lease.assert_owned()
            return self._branch_manager.delete_branch_points(collection_name, branch)

    def get_branch_point_count(self, workspace: str, project: str, branch: str) -> int:
        """Get the number of points for a specific branch."""
        collection_name = self._get_project_collection_name(workspace, project)
        
        if not self._collection_manager.collection_exists(collection_name):
            if not self._collection_manager.alias_exists(collection_name):
                return 0

        return self._branch_manager.get_branch_point_count(collection_name, branch)

    def get_indexed_branches(self, workspace: str, project: str) -> List[str]:
        """Get list of branches that have points in the collection."""
        collection_name = self._get_project_collection_name(workspace, project)
        
        if not self._collection_manager.collection_exists(collection_name):
            if not self._collection_manager.alias_exists(collection_name):
                return []

        return self._branch_manager.get_indexed_branches(collection_name)

    # Index management

    def delete_index(self, workspace: str, project: str, branch: str):
        """Delete branch data from project index."""
        if branch and branch != "*":
            self.delete_branch(workspace, project, branch)
        else:
            self.delete_project_index(workspace, project)

    def delete_project_index(self, workspace: str, project: str):
        """Delete entire project collection (all branches)."""
        with self._mutation_coordinator.acquire(
            workspace,
            project,
            "delete-project-index",
        ) as lease:
            collection_name = self._get_project_collection_name(workspace, project)
            namespace = make_project_namespace(workspace, project)

            logger.info(f"Deleting entire project index for {namespace}")

            # Coordination failures must escape to the HTTP boundary. The
            # legacy best-effort Qdrant cleanup below must not make a lost
            # lease look like a successful project deletion.
            lease.assert_owned()
            try:
                if self._collection_manager.alias_exists(collection_name):
                    actual_collection = self._collection_manager.resolve_alias(collection_name)
                    self._collection_manager.delete_alias(collection_name)
                    if actual_collection:
                        self._collection_manager.delete_collection(actual_collection)
                else:
                    self._collection_manager.delete_collection(collection_name)
                logger.info(f"Deleted Qdrant collection: {collection_name}")
            except Exception as e:
                logger.warning(f"Failed to delete Qdrant collection: {e}")

    def cleanup_expired_pending_collections(self) -> int:
        """Remove only old pending collections without a live operation lease."""
        return self._collection_manager.cleanup_expired_pending_collections(
            is_operation_active=self._mutation_coordinator.is_operation_active,
        )

    def project_mutation(self, workspace: str, project: str, operation: str):
        """Return the shared mutation boundary for auxiliary index endpoints."""
        return self._mutation_coordinator.acquire(workspace, project, operation)

    def close(self) -> None:
        self._mutation_coordinator.close()

    # Statistics

    def _get_index_stats(self, workspace: str, project: str, branch: str) -> IndexStats:
        """Get statistics about a branch index (backward compatibility)."""
        return self._get_branch_index_stats(workspace, project, branch)

    def _get_branch_index_stats(self, workspace: str, project: str, branch: str) -> IndexStats:
        """Get statistics about a specific branch within a project collection."""
        collection_name = self._get_project_collection_name(workspace, project)
        return self._stats_manager.get_branch_stats(
            workspace, project, branch, collection_name
        )

    def _get_project_index_stats(self, workspace: str, project: str) -> IndexStats:
        """Get statistics about a project's index (all branches combined)."""
        collection_name = self._get_project_collection_name(workspace, project)
        return self._stats_manager.get_project_stats(workspace, project, collection_name)

    def list_indices(self) -> List[IndexStats]:
        """List all project indices with branch breakdown."""
        return self._stats_manager.list_all_indices(
            self._collection_manager.alias_exists
        )

    # Legacy/compatibility methods
    
    def _ensure_collection_exists(self, collection_name: str):
        """Ensure Qdrant collection exists (legacy compatibility)."""
        self._collection_manager.ensure_collection_exists(collection_name)

    def _alias_exists(self, alias_name: str) -> bool:
        """Check if an alias exists (legacy compatibility)."""
        return self._collection_manager.alias_exists(alias_name)

    def _resolve_alias_to_collection(self, alias_name: str) -> Optional[str]:
        """Resolve an alias to its collection (legacy compatibility)."""
        return self._collection_manager.resolve_alias(alias_name)

    def _generate_point_id(
        self,
        workspace: str,
        project: str,
        branch: str,
        path: str,
        chunk_index: int
    ) -> str:
        """Generate deterministic point ID (legacy compatibility)."""
        return PointOperations.generate_point_id(
            workspace, project, branch, path, chunk_index
        )
