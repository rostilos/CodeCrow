"""
Main RAG Index Manager.

Composes all index management components and provides the public API.
"""

import logging
from typing import Optional, List

from llama_index.core import Settings
from qdrant_client import QdrantClient

from ...models.config import RAGConfig, IndexStats
from ...utils.utils import make_namespace, make_project_namespace
from ..splitter import ASTCodeSplitter
from ..loader import DocumentLoader
from ..openrouter_embedding import OpenRouterEmbedding

from .collection_manager import CollectionManager
from .branch_manager import BranchManager
from .point_operations import PointOperations
from .stats_manager import StatsManager
from .indexer import RepositoryIndexer, FileOperations

logger = logging.getLogger(__name__)


class RAGIndexManager:
    """Manage RAG indices for code repositories using Qdrant.
    
    This is the main entry point for all indexing operations.
    """

    def __init__(self, config: RAGConfig):
        self.config = config

        # Qdrant client
        self.qdrant_client = QdrantClient(url=config.qdrant_url)
        logger.info(f"Connected to Qdrant at {config.qdrant_url}")

        # Embedding model
        self.embed_model = OpenRouterEmbedding(
            api_key=config.openrouter_api_key,
            model=config.openrouter_model,
            api_base=config.openrouter_base_url,
            timeout=60.0,
            max_retries=3,
            expected_dim=config.embedding_dim
        )

        # Global settings
        Settings.embed_model = self.embed_model
        Settings.chunk_size = config.chunk_size
        Settings.chunk_overlap = config.chunk_overlap

        # Splitter and loader
        logger.info("Using ASTCodeSplitter for code chunking (tree-sitter query-based)")
        self.splitter = ASTCodeSplitter(
            max_chunk_size=config.chunk_size,
            min_chunk_size=min(200, config.chunk_size // 4),
            chunk_overlap=config.chunk_overlap,
            parser_threshold=10
        )
        self.loader = DocumentLoader(config)

        # Component managers
        self._collection_manager = CollectionManager(
            self.qdrant_client, config.embedding_dim
        )
        self._branch_manager = BranchManager(self.qdrant_client)
        self._point_ops = PointOperations(
            self.qdrant_client, self.embed_model, batch_size=50
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
            loader=self.loader
        )
        self._file_ops = FileOperations(
            client=self.qdrant_client,
            point_ops=self._point_ops,
            collection_manager=self._collection_manager,
            stats_manager=self._stats_manager,
            splitter=self.splitter,
            loader=self.loader
        )

    # Collection naming

    def _get_project_collection_name(self, workspace: str, project: str) -> str:
        """Generate Qdrant collection name from workspace/project."""
        namespace = make_project_namespace(workspace, project)
        return f"{self.config.qdrant_collection_prefix}_{namespace}"

    def _get_collection_name(self, workspace: str, project: str, branch: str) -> str:
        """Generate collection name (DEPRECATED - use _get_project_collection_name)."""
        namespace = make_namespace(workspace, project, branch)
        return f"{self.config.qdrant_collection_prefix}_{namespace}"

    # Repository indexing

    def estimate_repository_size(
        self,
        repo_path: str,
        exclude_patterns: Optional[List[str]] = None
    ) -> tuple[int, int]:
        """Estimate repository size (file count and chunk count)."""
        return self._indexer.estimate_repository_size(repo_path, exclude_patterns)

    def index_repository(
        self,
        repo_path: str,
        workspace: str,
        project: str,
        branch: str,
        commit: str,
        exclude_patterns: Optional[List[str]] = None
    ) -> IndexStats:
        """Index entire repository for a branch using atomic swap strategy."""
        alias_name = self._get_project_collection_name(workspace, project)
        return self._indexer.index_repository(
            repo_path=repo_path,
            workspace=workspace,
            project=project,
            branch=branch,
            commit=commit,
            alias_name=alias_name,
            exclude_patterns=exclude_patterns
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
        return self._file_ops.update_files(
            file_paths=file_paths,
            repo_base=repo_base,
            workspace=workspace,
            project=project,
            branch=branch,
            commit=commit,
            collection_name=collection_name
        )

    def delete_files(
        self,
        file_paths: List[str],
        workspace: str,
        project: str,
        branch: str
    ) -> IndexStats:
        """Delete specific files from the index for a specific branch."""
        collection_name = self._get_project_collection_name(workspace, project)
        return self._file_ops.delete_files(
            file_paths=file_paths,
            workspace=workspace,
            project=project,
            branch=branch,
            collection_name=collection_name
        )

    # Branch operations

    def delete_branch(self, workspace: str, project: str, branch: str) -> bool:
        """Delete all points for a specific branch from the project collection."""
        collection_name = self._get_project_collection_name(workspace, project)
        
        if not self._collection_manager.collection_exists(collection_name):
            if not self._collection_manager.alias_exists(collection_name):
                logger.warning(f"Collection {collection_name} does not exist")
                return False

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
        collection_name = self._get_project_collection_name(workspace, project)
        namespace = make_project_namespace(workspace, project)

        logger.info(f"Deleting entire project index for {namespace}")

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
