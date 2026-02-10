"""
Repository indexing operations.

Handles full repository indexing with atomic swap and streaming processing.
"""

import gc
import logging
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List

from qdrant_client.models import Filter, FieldCondition, MatchAny, MatchValue

from ...models.config import RAGConfig, IndexStats
from ...utils.utils import make_namespace
from .collection_manager import CollectionManager
from .branch_manager import BranchManager
from .point_operations import PointOperations
from .stats_manager import StatsManager

logger = logging.getLogger(__name__)

# Memory-efficient batch sizes
DOCUMENT_BATCH_SIZE = 50
INSERT_BATCH_SIZE = 50


class RepositoryIndexer:
    """Handles repository indexing operations."""
    
    def __init__(
        self,
        config: RAGConfig,
        collection_manager: CollectionManager,
        branch_manager: BranchManager,
        point_ops: PointOperations,
        stats_manager: StatsManager,
        splitter,
        loader
    ):
        self.config = config
        self.collection_manager = collection_manager
        self.branch_manager = branch_manager
        self.point_ops = point_ops
        self.stats_manager = stats_manager
        self.splitter = splitter
        self.loader = loader
    
    def estimate_repository_size(
        self,
        repo_path: str,
        exclude_patterns: Optional[List[str]] = None
    ) -> tuple[int, int]:
        """Estimate repository size (file count and chunk count) without actually indexing."""
        logger.info(f"Estimating repository size for: {repo_path}")

        repo_path_obj = Path(repo_path)
        file_list = list(self.loader.iter_repository_files(repo_path_obj, exclude_patterns))
        file_count = len(file_list)
        logger.info(f"Found {file_count} files for estimation")

        if file_count == 0:
            return 0, 0

        SAMPLE_SIZE = 100
        chunk_count = 0
        
        if file_count <= SAMPLE_SIZE:
            for i in range(0, file_count, DOCUMENT_BATCH_SIZE):
                batch = file_list[i:i + DOCUMENT_BATCH_SIZE]
                documents = self.loader.load_file_batch(
                    batch, repo_path_obj, "estimate", "estimate", "estimate", "estimate"
                )
                if documents:
                    chunks = self.splitter.split_documents(documents)
                    chunk_count += len(chunks)
                    del chunks
                del documents
                gc.collect()
        else:
            sample_files = random.sample(file_list, SAMPLE_SIZE)
            sample_chunk_count = 0
            
            for i in range(0, len(sample_files), DOCUMENT_BATCH_SIZE):
                batch = sample_files[i:i + DOCUMENT_BATCH_SIZE]
                documents = self.loader.load_file_batch(
                    batch, repo_path_obj, "estimate", "estimate", "estimate", "estimate"
                )
                if documents:
                    chunks = self.splitter.split_documents(documents)
                    sample_chunk_count += len(chunks)
                    del chunks
                del documents
                gc.collect()
            
            avg_chunks_per_file = sample_chunk_count / SAMPLE_SIZE
            chunk_count = int(avg_chunks_per_file * file_count)
            logger.info(f"Estimated ~{avg_chunks_per_file:.1f} chunks/file from {SAMPLE_SIZE} samples")
            gc.collect()

        logger.info(f"Estimated {chunk_count} chunks from {file_count} files")
        return file_count, chunk_count
    
    def index_repository(
        self,
        repo_path: str,
        workspace: str,
        project: str,
        branch: str,
        commit: str,
        alias_name: str,
        exclude_patterns: Optional[List[str]] = None
    ) -> IndexStats:
        """Index entire repository for a branch using atomic swap strategy."""
        logger.info(f"Indexing repository: {workspace}/{project}/{branch} from {repo_path}")

        repo_path_obj = Path(repo_path)
        temp_collection_name = self.collection_manager.create_versioned_collection(alias_name)

        # Check existing collection and preserve other branch data using streaming
        old_collection_exists = self.collection_manager.alias_exists(alias_name)
        if not old_collection_exists:
            old_collection_exists = self.collection_manager.collection_exists(alias_name)
        
        actual_old_collection = None
        if old_collection_exists:
            actual_old_collection = self.collection_manager.resolve_alias(alias_name) or alias_name
        
        # Clean up orphaned versioned collections
        current_target = self.collection_manager.resolve_alias(alias_name)
        self.collection_manager.cleanup_orphaned_versioned_collections(
            alias_name, current_target, temp_collection_name
        )

        # Get file list
        file_list = list(self.loader.iter_repository_files(repo_path_obj, exclude_patterns))
        total_files = len(file_list)
        logger.info(f"Found {total_files} files to index for branch '{branch}'")
        
        if total_files == 0:
            logger.warning("No documents to index")
            self.collection_manager.delete_collection(temp_collection_name)
            return self.stats_manager.get_branch_stats(
                workspace, project, branch,
                self.collection_manager.resolve_alias(alias_name) or alias_name
            )
        
        # Validate limits
        if self.config.max_files_per_index > 0 and total_files > self.config.max_files_per_index:
            self.collection_manager.delete_collection(temp_collection_name)
            raise ValueError(
                f"Repository exceeds file limit: {total_files} files (max: {self.config.max_files_per_index})."
            )
        
        if self.config.max_chunks_per_index > 0:
            logger.info("Estimating chunk count before indexing...")
            _, estimated_chunks = self.estimate_repository_size(repo_path, exclude_patterns)
            if estimated_chunks > self.config.max_chunks_per_index * 1.2:
                self.collection_manager.delete_collection(temp_collection_name)
                raise ValueError(
                    f"Repository estimated to exceed chunk limit: ~{estimated_chunks} chunks (max: {self.config.max_chunks_per_index})."
                )

        document_count = 0
        chunk_count = 0
        successful_chunks = 0
        failed_chunks = 0

        try:
            # Stream copy preserved points from other branches (memory-efficient)
            if actual_old_collection:
                self.branch_manager.stream_copy_points_to_collection(
                    actual_old_collection,
                    temp_collection_name,
                    branch,
                    INSERT_BATCH_SIZE
                )
            
            # Stream process files in batches
            logger.info("Starting memory-efficient streaming indexing...")
            batch_num = 0
            total_batches = (total_files + DOCUMENT_BATCH_SIZE - 1) // DOCUMENT_BATCH_SIZE
            
            for i in range(0, total_files, DOCUMENT_BATCH_SIZE):
                batch_num += 1
                file_batch = file_list[i:i + DOCUMENT_BATCH_SIZE]
                
                documents = self.loader.load_file_batch(
                    file_batch, repo_path_obj, workspace, project, branch, commit
                )
                document_count += len(documents)
                
                if not documents:
                    continue
                
                chunks = self.splitter.split_documents(documents)
                batch_chunk_count = len(chunks)
                chunk_count += batch_chunk_count
                
                # Check chunk limit
                if self.config.max_chunks_per_index > 0 and chunk_count > self.config.max_chunks_per_index:
                    self.collection_manager.delete_collection(temp_collection_name)
                    raise ValueError(f"Repository exceeds chunk limit: {chunk_count}+ chunks.")
                
                # Process and upsert
                success, failed = self.point_ops.process_and_upsert_chunks(
                    chunks, temp_collection_name, workspace, project, branch
                )
                successful_chunks += success
                failed_chunks += failed
                
                logger.info(
                    f"Batch {batch_num}/{total_batches}: processed {len(documents)} files, "
                    f"{batch_chunk_count} chunks"
                )
                
                del documents
                del chunks
                
                if batch_num % 5 == 0:
                    gc.collect()

            logger.info(
                f"Streaming indexing complete: {document_count} files, "
                f"{successful_chunks}/{chunk_count} chunks indexed ({failed_chunks} failed)"
            )

            # Verify and perform atomic swap
            temp_info = self.point_ops.client.get_collection(temp_collection_name)
            if temp_info.points_count == 0:
                raise Exception("Temporary collection is empty after indexing")

            self._perform_atomic_swap(
                alias_name, temp_collection_name, old_collection_exists
            )

        except Exception as e:
            logger.error(f"Indexing failed: {e}")
            self.collection_manager.delete_collection(temp_collection_name)
            raise e
        finally:
            gc.collect()

        self.stats_manager.store_metadata(
            workspace, project, branch, commit, document_count, chunk_count
        )

        namespace = make_namespace(workspace, project, branch)
        return IndexStats(
            namespace=namespace,
            document_count=document_count,
            chunk_count=successful_chunks,
            last_updated=datetime.now(timezone.utc).isoformat(),
            workspace=workspace,
            project=project,
            branch=branch
        )
    
    def _perform_atomic_swap(
        self,
        alias_name: str,
        temp_collection_name: str,
        old_collection_exists: bool
    ) -> None:
        """Perform atomic alias swap with migration handling."""
        logger.info("Performing atomic alias swap...")
        
        is_direct_collection = (
            self.collection_manager.collection_exists(alias_name) and
            not self.collection_manager.alias_exists(alias_name)
        )
        
        old_versioned_name = None
        if old_collection_exists and not is_direct_collection:
            old_versioned_name = self.collection_manager.resolve_alias(alias_name)

        try:
            self.collection_manager.atomic_alias_swap(
                alias_name, temp_collection_name,
                old_collection_exists and not is_direct_collection
            )
        except Exception as alias_err:
            if is_direct_collection and "already exists" in str(alias_err).lower():
                logger.info("Migrating from direct collection to alias-based indexing...")
                self.collection_manager.delete_collection(alias_name)
                self.collection_manager.atomic_alias_swap(alias_name, temp_collection_name, False)
            else:
                raise alias_err

        if old_versioned_name and old_versioned_name != temp_collection_name:
            self.collection_manager.delete_collection(old_versioned_name)


class FileOperations:
    """Handles individual file update and delete operations."""
    
    def __init__(
        self,
        client,
        point_ops: PointOperations,
        collection_manager: CollectionManager,
        stats_manager: StatsManager,
        splitter,
        loader
    ):
        self.client = client
        self.point_ops = point_ops
        self.collection_manager = collection_manager
        self.stats_manager = stats_manager
        self.splitter = splitter
        self.loader = loader
    
    def update_files(
        self,
        file_paths: List[str],
        repo_base: str,
        workspace: str,
        project: str,
        branch: str,
        commit: str,
        collection_name: str
    ) -> IndexStats:
        """Update specific files in the index (Delete Old -> Insert New)."""
        logger.info(f"Updating {len(file_paths)} files in {workspace}/{project} for branch '{branch}'")

        repo_base_obj = Path(repo_base)
        file_path_objs = [Path(fp) for fp in file_paths]
        
        self.collection_manager.ensure_collection_exists(collection_name)

        # Delete old chunks for these files and branch
        logger.info(f"Purging existing vectors for {len(file_paths)} files in branch '{branch}'...")
        self.client.delete(
            collection_name=collection_name,
            points_selector=Filter(
                must=[
                    FieldCondition(key="path", match=MatchAny(any=file_paths)),
                    FieldCondition(key="branch", match=MatchValue(value=branch))
                ]
            )
        )

        # Load and split new content
        documents = self.loader.load_specific_files(
            file_paths=file_path_objs,
            repo_base=repo_base_obj,
            workspace=workspace,
            project=project,
            branch=branch,
            commit=commit
        )

        if not documents:
            logger.warning("No documents loaded from provided paths.")
            return self.stats_manager.get_project_stats(workspace, project, collection_name)

        chunks = self.splitter.split_documents(documents)
        logger.info(f"Generated {len(chunks)} new chunks")

        # Process and upsert
        self.point_ops.process_and_upsert_chunks(
            chunks, collection_name, workspace, project, branch
        )

        logger.info(f"Successfully updated {len(chunks)} chunks for branch '{branch}'")
        return self.stats_manager.get_project_stats(workspace, project, collection_name)
    
    def delete_files(
        self,
        file_paths: List[str],
        workspace: str,
        project: str,
        branch: str,
        collection_name: str
    ) -> IndexStats:
        """Delete specific files from the index for a specific branch."""
        logger.info(f"Deleting {len(file_paths)} files from {workspace}/{project} branch '{branch}'")

        self.client.delete(
            collection_name=collection_name,
            points_selector=Filter(
                must=[
                    FieldCondition(key="path", match=MatchAny(any=file_paths)),
                    FieldCondition(key="branch", match=MatchValue(value=branch))
                ]
            )
        )
        logger.info(f"Deleted {len(file_paths)} files from branch '{branch}'")
            
        return self.stats_manager.get_project_stats(workspace, project, collection_name)
