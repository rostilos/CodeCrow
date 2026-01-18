"""
Delta Index Manager for Hierarchical RAG System.

Manages delta (branch-specific) indexes that layer on top of a base index,
enabling efficient hybrid RAG queries for release branches and similar use cases.
"""
from typing import Optional, List, Dict, Tuple
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass
from enum import Enum
import logging
import gc
import subprocess

from llama_index.core import VectorStoreIndex, StorageContext, Settings
from llama_index.core.schema import TextNode
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, Filter, FieldCondition, MatchAny

from ..models.config import RAGConfig
from ..utils.utils import make_namespace
from .loader import DocumentLoader
from .semantic_splitter import SemanticCodeSplitter
from .ast_splitter import ASTCodeSplitter
from .openrouter_embedding import OpenRouterEmbedding

logger = logging.getLogger(__name__)

# Batch sizes for memory efficiency
DOCUMENT_BATCH_SIZE = 50
INSERT_BATCH_SIZE = 50


class DeltaIndexStatus(str, Enum):
    """Status of a delta index."""
    CREATING = "CREATING"
    READY = "READY"
    STALE = "STALE"
    ARCHIVED = "ARCHIVED"
    FAILED = "FAILED"


@dataclass
class DeltaIndexStats:
    """Statistics about a delta index."""
    workspace: str
    project: str
    branch_name: str
    base_branch: str
    collection_name: str
    status: DeltaIndexStatus
    chunk_count: int
    file_count: int
    base_commit_hash: Optional[str] = None
    delta_commit_hash: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class DeltaIndexManager:
    """
    Manages delta (branch-specific) indexes for hierarchical RAG.
    
    Delta indexes contain only the differences between a branch (e.g., release/1.0) 
    and the base branch (e.g., master), enabling efficient hybrid queries that
    combine general context from base with branch-specific changes.
    """

    def __init__(self, config: RAGConfig):
        self.config = config
        
        # Qdrant client
        self.qdrant_client = QdrantClient(url=config.qdrant_url)
        logger.info(f"DeltaIndexManager connected to Qdrant at {config.qdrant_url}")
        
        # Embedding model
        self.embed_model = OpenRouterEmbedding(
            api_key=config.openrouter_api_key,
            model=config.openrouter_model,
            api_base=config.openrouter_base_url,
            timeout=60.0,
            max_retries=3,
            expected_dim=config.embedding_dim
        )
        
        # Set global settings
        Settings.embed_model = self.embed_model
        Settings.chunk_size = config.chunk_size
        Settings.chunk_overlap = config.chunk_overlap
        
        # Use AST splitter by default for better semantic chunking
        import os
        use_ast_splitter = os.environ.get('RAG_USE_AST_SPLITTER', 'true').lower() == 'true'
        
        if use_ast_splitter:
            self.splitter = ASTCodeSplitter(
                max_chunk_size=config.chunk_size,
                min_chunk_size=min(200, config.chunk_size // 4),
                chunk_overlap=config.chunk_overlap,
                parser_threshold=10
            )
        else:
            self.splitter = SemanticCodeSplitter(
                max_chunk_size=config.chunk_size,
                min_chunk_size=min(200, config.chunk_size // 4),
                overlap=config.chunk_overlap
            )
        
        self.loader = DocumentLoader(config)

    def _get_delta_collection_name(self, workspace: str, project: str, branch: str) -> str:
        """Generate Qdrant collection name for a delta index."""
        # Prefix with 'delta_' to distinguish from base indexes
        namespace = make_namespace(workspace, project, f"delta_{branch}")
        return f"{self.config.qdrant_collection_prefix}_{namespace}"

    def _get_base_collection_name(self, workspace: str, project: str, branch: str) -> str:
        """Generate Qdrant collection name for base index."""
        namespace = make_namespace(workspace, project, branch)
        return f"{self.config.qdrant_collection_prefix}_{namespace}"

    def _collection_exists(self, collection_name: str) -> bool:
        """Check if a collection exists (either as direct collection or alias)."""
        try:
            collections = [c.name for c in self.qdrant_client.get_collections().collections]
            if collection_name in collections:
                return True
            
            # Check aliases
            aliases = self.qdrant_client.get_aliases()
            if any(a.alias_name == collection_name for a in aliases.aliases):
                return True
            
            return False
        except Exception as e:
            logger.warning(f"Error checking collection existence: {e}")
            return False

    def get_branch_diff_files(
        self,
        repo_path: str,
        base_commit: str,
        delta_commit: str
    ) -> Tuple[List[str], List[str], List[str]]:
        """
        Get lists of files that differ between two commits.
        
        Returns:
            Tuple of (added_files, modified_files, deleted_files)
        """
        logger.info(f"Getting diff between {base_commit[:8]}..{delta_commit[:8]}")
        
        try:
            # Get diff with status (A=added, M=modified, D=deleted)
            result = subprocess.run(
                ["git", "-C", repo_path, "diff", "--name-status", f"{base_commit}..{delta_commit}"],
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode != 0:
                logger.warning(f"Git diff failed: {result.stderr}")
                # Fallback to simpler diff
                result = subprocess.run(
                    ["git", "-C", repo_path, "diff", "--name-only", f"{base_commit}..{delta_commit}"],
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                files = [f.strip() for f in result.stdout.strip().split('\n') if f.strip()]
                return [], files, []  # Treat all as modified
            
            added = []
            modified = []
            deleted = []
            
            for line in result.stdout.strip().split('\n'):
                if not line.strip():
                    continue
                parts = line.split('\t', 1)
                if len(parts) != 2:
                    continue
                status, filepath = parts
                status = status[0]  # Handle cases like 'R100' (rename)
                
                if status == 'A':
                    added.append(filepath)
                elif status == 'D':
                    deleted.append(filepath)
                else:  # M, R, C, etc.
                    modified.append(filepath)
            
            logger.info(f"Diff result: {len(added)} added, {len(modified)} modified, {len(deleted)} deleted")
            return added, modified, deleted
            
        except subprocess.TimeoutExpired:
            logger.error("Git diff timed out")
            return [], [], []
        except Exception as e:
            logger.error(f"Error getting branch diff: {e}")
            return [], [], []

    def parse_diff_for_changed_files(self, raw_diff: str) -> List[str]:
        """
        Parse raw git diff output to extract changed file paths.
        Used when raw diff is provided instead of commit hashes.
        """
        import re
        
        changed_files = set()
        
        # Pattern to match diff header: diff --git a/path b/path
        diff_pattern = re.compile(r'^diff --git a/(.+?) b/(.+?)$', re.MULTILINE)
        
        for match in diff_pattern.finditer(raw_diff):
            # Use the 'b' path (destination) as the canonical path
            changed_files.add(match.group(2))
        
        return list(changed_files)

    def create_delta_index(
        self,
        workspace: str,
        project: str,
        base_branch: str,
        delta_branch: str,
        repo_path: str,
        base_commit: Optional[str] = None,
        delta_commit: Optional[str] = None,
        raw_diff: Optional[str] = None,
        exclude_patterns: Optional[List[str]] = None
    ) -> DeltaIndexStats:
        """
        Create a delta index containing only the differences between delta_branch and base_branch.
        
        Args:
            workspace: Workspace identifier
            project: Project identifier
            base_branch: The base branch (e.g., "master")
            delta_branch: The branch to create delta for (e.g., "release/1.0")
            repo_path: Path to the repository
            base_commit: Commit hash of base branch (for tracking)
            delta_commit: Commit hash of delta branch
            raw_diff: Raw diff string (alternative to using commits)
            exclude_patterns: Patterns to exclude from indexing
            
        Returns:
            DeltaIndexStats with information about the created index
        """
        logger.info(f"Creating delta index: {workspace}/{project} {delta_branch} (base: {base_branch})")
        
        collection_name = self._get_delta_collection_name(workspace, project, delta_branch)
        repo_path_obj = Path(repo_path)
        
        # Determine changed files
        if raw_diff:
            changed_files = self.parse_diff_for_changed_files(raw_diff)
            added_files = []
            deleted_files = []
        elif base_commit and delta_commit:
            added_files, modified_files, deleted_files = self.get_branch_diff_files(
                repo_path, base_commit, delta_commit
            )
            changed_files = added_files + modified_files
        else:
            logger.error("Either raw_diff or both base_commit and delta_commit must be provided")
            return DeltaIndexStats(
                workspace=workspace,
                project=project,
                branch_name=delta_branch,
                base_branch=base_branch,
                collection_name=collection_name,
                status=DeltaIndexStatus.FAILED,
                chunk_count=0,
                file_count=0,
                error_message="Missing diff information"
            )
        
        if not changed_files:
            logger.info("No changed files found, skipping delta index creation")
            return DeltaIndexStats(
                workspace=workspace,
                project=project,
                branch_name=delta_branch,
                base_branch=base_branch,
                collection_name=collection_name,
                status=DeltaIndexStatus.READY,
                chunk_count=0,
                file_count=0,
                base_commit_hash=base_commit,
                delta_commit_hash=delta_commit
            )
        
        # Filter out excluded patterns
        if exclude_patterns:
            from ..utils.utils import should_exclude_file
            changed_files = [f for f in changed_files if not should_exclude_file(f, exclude_patterns)]
        
        logger.info(f"Delta will index {len(changed_files)} changed files")
        
        try:
            # Delete existing delta collection if exists
            if self._collection_exists(collection_name):
                logger.info(f"Deleting existing delta collection: {collection_name}")
                self.qdrant_client.delete_collection(collection_name)
            
            # Create new collection
            logger.info(f"Creating delta collection: {collection_name}")
            self.qdrant_client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(
                    size=self.config.embedding_dim,
                    distance=Distance.COSINE
                )
            )
            
            # Create vector store and index
            vector_store = QdrantVectorStore(
                client=self.qdrant_client,
                collection_name=collection_name,
                enable_hybrid=False,
                batch_size=100
            )
            storage_context = StorageContext.from_defaults(vector_store=vector_store)
            index = VectorStoreIndex.from_documents(
                [],
                storage_context=storage_context,
                embed_model=self.embed_model,
                show_progress=False
            )
            
            # Load and index changed files
            file_count = 0
            chunk_count = 0
            
            # Convert to Path objects and filter existing files
            file_paths = []
            for f in changed_files:
                full_path = repo_path_obj / f
                if full_path.exists() and full_path.is_file():
                    file_paths.append(full_path)
                else:
                    logger.debug(f"Skipping non-existent file: {f}")
            
            # Process in batches
            for i in range(0, len(file_paths), DOCUMENT_BATCH_SIZE):
                batch = file_paths[i:i + DOCUMENT_BATCH_SIZE]
                
                documents = self.loader.load_specific_files(
                    file_paths=batch,
                    repo_base=repo_path_obj,
                    workspace=workspace,
                    project=project,
                    branch=delta_branch,
                    commit=delta_commit or "unknown"
                )
                
                if not documents:
                    continue
                
                file_count += len(documents)
                
                # Split and index
                chunks = self.splitter.split_documents(documents)
                chunk_count += len(chunks)
                
                # Add delta-specific metadata to chunks
                for chunk in chunks:
                    chunk.metadata["is_delta"] = True
                    chunk.metadata["base_branch"] = base_branch
                    chunk.metadata["delta_branch"] = delta_branch
                
                # Insert in sub-batches
                for j in range(0, len(chunks), INSERT_BATCH_SIZE):
                    insert_batch = chunks[j:j + INSERT_BATCH_SIZE]
                    index.insert_nodes(insert_batch)
                
                # Free memory
                del documents
                del chunks
                gc.collect()
            
            logger.info(f"Delta index created: {file_count} files, {chunk_count} chunks")
            
            return DeltaIndexStats(
                workspace=workspace,
                project=project,
                branch_name=delta_branch,
                base_branch=base_branch,
                collection_name=collection_name,
                status=DeltaIndexStatus.READY,
                chunk_count=chunk_count,
                file_count=file_count,
                base_commit_hash=base_commit,
                delta_commit_hash=delta_commit,
                created_at=datetime.now(timezone.utc).isoformat(),
                updated_at=datetime.now(timezone.utc).isoformat()
            )
            
        except Exception as e:
            logger.error(f"Failed to create delta index: {e}")
            # Cleanup on failure
            try:
                if self._collection_exists(collection_name):
                    self.qdrant_client.delete_collection(collection_name)
            except Exception:
                pass
            
            return DeltaIndexStats(
                workspace=workspace,
                project=project,
                branch_name=delta_branch,
                base_branch=base_branch,
                collection_name=collection_name,
                status=DeltaIndexStatus.FAILED,
                chunk_count=0,
                file_count=0,
                error_message=str(e)
            )
        finally:
            gc.collect()

    def update_delta_index(
        self,
        workspace: str,
        project: str,
        delta_branch: str,
        repo_path: str,
        delta_commit: str,
        raw_diff: str,
        exclude_patterns: Optional[List[str]] = None
    ) -> DeltaIndexStats:
        """
        Incrementally update an existing delta index with new changes.
        
        This method updates only the files that changed in the latest commit,
        rather than rebuilding the entire delta index.
        """
        logger.info(f"Updating delta index: {workspace}/{project}/{delta_branch}")
        
        collection_name = self._get_delta_collection_name(workspace, project, delta_branch)
        
        if not self._collection_exists(collection_name):
            logger.warning(f"Delta collection {collection_name} does not exist, creating new")
            # Need base branch info - for now, return failed status
            return DeltaIndexStats(
                workspace=workspace,
                project=project,
                branch_name=delta_branch,
                base_branch="unknown",
                collection_name=collection_name,
                status=DeltaIndexStatus.FAILED,
                chunk_count=0,
                file_count=0,
                error_message="Delta index does not exist, cannot update"
            )
        
        # Parse changed files from diff
        changed_files = self.parse_diff_for_changed_files(raw_diff)
        
        if not changed_files:
            logger.info("No changed files in diff, skipping update")
            # Return current stats
            collection_info = self.qdrant_client.get_collection(collection_name)
            return DeltaIndexStats(
                workspace=workspace,
                project=project,
                branch_name=delta_branch,
                base_branch="unknown",
                collection_name=collection_name,
                status=DeltaIndexStatus.READY,
                chunk_count=collection_info.points_count,
                file_count=0,
                delta_commit_hash=delta_commit,
                updated_at=datetime.now(timezone.utc).isoformat()
            )
        
        # Filter excluded
        if exclude_patterns:
            from ..utils.utils import should_exclude_file
            changed_files = [f for f in changed_files if not should_exclude_file(f, exclude_patterns)]
        
        logger.info(f"Updating delta index with {len(changed_files)} changed files")
        
        try:
            # Delete old chunks for changed files
            self.qdrant_client.delete(
                collection_name=collection_name,
                points_selector=Filter(
                    must=[
                        FieldCondition(
                            key="path",
                            match=MatchAny(any=changed_files)
                        )
                    ]
                )
            )
            
            # Load and index new content
            repo_path_obj = Path(repo_path)
            file_paths = []
            for f in changed_files:
                full_path = repo_path_obj / f
                if full_path.exists() and full_path.is_file():
                    file_paths.append(full_path)
            
            if not file_paths:
                collection_info = self.qdrant_client.get_collection(collection_name)
                return DeltaIndexStats(
                    workspace=workspace,
                    project=project,
                    branch_name=delta_branch,
                    base_branch="unknown",
                    collection_name=collection_name,
                    status=DeltaIndexStatus.READY,
                    chunk_count=collection_info.points_count,
                    file_count=0,
                    delta_commit_hash=delta_commit,
                    updated_at=datetime.now(timezone.utc).isoformat()
                )
            
            # Create index for inserting
            vector_store = QdrantVectorStore(
                client=self.qdrant_client,
                collection_name=collection_name,
                enable_hybrid=False,
                batch_size=100
            )
            index = VectorStoreIndex.from_vector_store(
                vector_store=vector_store,
                embed_model=self.embed_model
            )
            
            new_chunks = 0
            for i in range(0, len(file_paths), DOCUMENT_BATCH_SIZE):
                batch = file_paths[i:i + DOCUMENT_BATCH_SIZE]
                
                documents = self.loader.load_specific_files(
                    file_paths=batch,
                    repo_base=repo_path_obj,
                    workspace=workspace,
                    project=project,
                    branch=delta_branch,
                    commit=delta_commit
                )
                
                if documents:
                    chunks = self.splitter.split_documents(documents)
                    
                    for chunk in chunks:
                        chunk.metadata["is_delta"] = True
                        chunk.metadata["delta_branch"] = delta_branch
                    
                    for j in range(0, len(chunks), INSERT_BATCH_SIZE):
                        insert_batch = chunks[j:j + INSERT_BATCH_SIZE]
                        index.insert_nodes(insert_batch)
                    
                    new_chunks += len(chunks)
                    del documents
                    del chunks
                    gc.collect()
            
            collection_info = self.qdrant_client.get_collection(collection_name)
            
            return DeltaIndexStats(
                workspace=workspace,
                project=project,
                branch_name=delta_branch,
                base_branch="unknown",
                collection_name=collection_name,
                status=DeltaIndexStatus.READY,
                chunk_count=collection_info.points_count,
                file_count=len(file_paths),
                delta_commit_hash=delta_commit,
                updated_at=datetime.now(timezone.utc).isoformat()
            )
            
        except Exception as e:
            logger.error(f"Failed to update delta index: {e}")
            return DeltaIndexStats(
                workspace=workspace,
                project=project,
                branch_name=delta_branch,
                base_branch="unknown",
                collection_name=collection_name,
                status=DeltaIndexStatus.FAILED,
                chunk_count=0,
                file_count=0,
                error_message=str(e)
            )
        finally:
            gc.collect()

    def delete_delta_index(self, workspace: str, project: str, branch: str) -> bool:
        """Delete a delta index."""
        collection_name = self._get_delta_collection_name(workspace, project, branch)
        
        try:
            if self._collection_exists(collection_name):
                self.qdrant_client.delete_collection(collection_name)
                logger.info(f"Deleted delta index: {collection_name}")
                return True
            else:
                logger.warning(f"Delta index not found: {collection_name}")
                return False
        except Exception as e:
            logger.error(f"Failed to delete delta index: {e}")
            return False

    def get_delta_index_stats(self, workspace: str, project: str, branch: str) -> Optional[DeltaIndexStats]:
        """Get statistics about a delta index."""
        collection_name = self._get_delta_collection_name(workspace, project, branch)
        
        try:
            if not self._collection_exists(collection_name):
                return None
            
            collection_info = self.qdrant_client.get_collection(collection_name)
            
            return DeltaIndexStats(
                workspace=workspace,
                project=project,
                branch_name=branch,
                base_branch="unknown",  # Would need to store this separately
                collection_name=collection_name,
                status=DeltaIndexStatus.READY if collection_info.points_count > 0 else DeltaIndexStatus.CREATING,
                chunk_count=collection_info.points_count,
                file_count=0  # Not tracked at collection level
            )
        except Exception as e:
            logger.error(f"Failed to get delta index stats: {e}")
            return None

    def list_delta_indexes(self, workspace: str, project: str) -> List[DeltaIndexStats]:
        """List all delta indexes for a project."""
        prefix = self._get_delta_collection_name(workspace, project, "").rstrip("_")
        
        indexes = []
        try:
            collections = self.qdrant_client.get_collections().collections
            
            for collection in collections:
                if collection.name.startswith(prefix) and "delta_" in collection.name:
                    # Extract branch name from collection name
                    # Format: codecrow_workspace__project__delta_branch_name
                    try:
                        # This is a simplified extraction - might need adjustment
                        parts = collection.name.split("__")
                        if len(parts) >= 3:
                            branch_part = parts[-1]
                            if branch_part.startswith("delta_"):
                                branch_name = branch_part[6:]  # Remove "delta_" prefix
                                
                                collection_info = self.qdrant_client.get_collection(collection.name)
                                
                                indexes.append(DeltaIndexStats(
                                    workspace=workspace,
                                    project=project,
                                    branch_name=branch_name,
                                    base_branch="unknown",
                                    collection_name=collection.name,
                                    status=DeltaIndexStatus.READY,
                                    chunk_count=collection_info.points_count,
                                    file_count=0
                                ))
                    except Exception as e:
                        logger.warning(f"Failed to parse delta collection {collection.name}: {e}")
            
            return indexes
        except Exception as e:
            logger.error(f"Failed to list delta indexes: {e}")
            return []

    def delta_index_exists(self, workspace: str, project: str, branch: str) -> bool:
        """Check if a delta index exists and is ready."""
        collection_name = self._get_delta_collection_name(workspace, project, branch)
        return self._collection_exists(collection_name)
