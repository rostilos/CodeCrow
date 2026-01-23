from typing import Optional, List, Dict
from datetime import datetime, timezone
from pathlib import Path
import logging
import gc
import os
import time
import uuid
import hashlib

from llama_index.core import VectorStoreIndex, StorageContext, Settings
from llama_index.core.schema import Document, TextNode
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, Filter, FieldCondition, MatchAny, MatchValue,
    CreateAlias, DeleteAlias, CreateAliasOperation, DeleteAliasOperation,
    PointStruct
)

from ..models.config import RAGConfig, IndexStats
from ..utils.utils import make_namespace, make_project_namespace
from .semantic_splitter import SemanticCodeSplitter
from .ast_splitter import ASTCodeSplitter
from .loader import DocumentLoader
from .openrouter_embedding import OpenRouterEmbedding

logger = logging.getLogger(__name__)

# Memory-efficient batch sizes
DOCUMENT_BATCH_SIZE = 50  # Process documents in batches to limit memory
INSERT_BATCH_SIZE = 50  # Batch size for LlamaIndex insert_nodes


class RAGIndexManager:
    """Manage RAG indices for code repositories using Qdrant"""

    def __init__(self, config: RAGConfig):
        self.config = config

        # Qdrant client for vector storage
        self.qdrant_client = QdrantClient(url=config.qdrant_url)
        logger.info(f"Connected to Qdrant at {config.qdrant_url}")

        # Configure OpenRouter embeddings
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

        # Choose splitter based on environment variable or config
        # AST splitter provides better semantic chunking for supported languages
        use_ast_splitter = os.environ.get('RAG_USE_AST_SPLITTER', 'true').lower() == 'true'

        if use_ast_splitter:
            logger.info("Using ASTCodeSplitter for code chunking (tree-sitter based)")
            self.splitter = ASTCodeSplitter(
                max_chunk_size=config.chunk_size,
                min_chunk_size=min(200, config.chunk_size // 4),
                chunk_overlap=config.chunk_overlap,
                parser_threshold=10  # Minimum lines for AST parsing
            )
        else:
            logger.info("Using SemanticCodeSplitter for code chunking (regex-based)")
            self.splitter = SemanticCodeSplitter(
                max_chunk_size=config.chunk_size,
                min_chunk_size=min(200, config.chunk_size // 4),
                overlap=config.chunk_overlap
            )

        self.loader = DocumentLoader(config)

    def _get_project_collection_name(self, workspace: str, project: str) -> str:
        """Generate Qdrant collection name from workspace/project (single collection per project)"""
        namespace = make_project_namespace(workspace, project)
        return f"{self.config.qdrant_collection_prefix}_{namespace}"

    def _get_collection_name(self, workspace: str, project: str, branch: str) -> str:
        """Generate Qdrant collection name from workspace/project/branch (DEPRECATED - use _get_project_collection_name)"""
        namespace = make_namespace(workspace, project, branch)
        return f"{self.config.qdrant_collection_prefix}_{namespace}"

    def _generate_point_id(self, workspace: str, project: str, branch: str, path: str, chunk_index: int) -> str:
        """Generate deterministic point ID for upsert (same content = same ID = replace)"""
        key = f"{workspace}:{project}:{branch}:{path}:{chunk_index}"
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, key))

    def _ensure_collection_exists(self, collection_name: str):
        """Ensure Qdrant collection exists with proper configuration.
        
        If the collection_name is actually an alias, use the aliased collection instead.
        """
        # First check if this is an alias
        if self._alias_exists(collection_name):
            logger.info(f"Collection name {collection_name} is an alias, using existing aliased collection")
            return
        
        # Also check if there's a collection with this exact name
        collections = self.qdrant_client.get_collections().collections
        collection_names = [c.name for c in collections]
        logger.debug(f"Existing collections: {collection_names}")

        if collection_name not in collection_names:
            logger.info(f"Creating Qdrant collection: {collection_name}")
            self.qdrant_client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(
                    size=self.config.embedding_dim,
                    distance=Distance.COSINE
                )
            )
            logger.info(f"Created collection {collection_name}")
        else:
            logger.info(f"Collection {collection_name} already exists")

    def _get_storage_context(self, workspace: str, project: str, branch: str) -> StorageContext:
        """Create storage context with Qdrant vector store (DEPRECATED - use direct Qdrant operations)"""
        collection_name = self._get_project_collection_name(workspace, project)
        self._ensure_collection_exists(collection_name)

        vector_store = QdrantVectorStore(
            client=self.qdrant_client,
            collection_name=collection_name,
            enable_hybrid=False,  # Disable hybrid mode to avoid string ID issues
            batch_size=100
        )

        return StorageContext.from_defaults(vector_store=vector_store)

    def _get_or_create_index(
            self,
            workspace: str,
            project: str,
            branch: str
    ) -> VectorStoreIndex:
        """Get or create vector index for the given namespace (DEPRECATED - use direct Qdrant operations)"""
        namespace = make_namespace(workspace, project, branch)
        logger.info(f"Getting/creating index for namespace: {namespace}")

        storage_context = self._get_storage_context(workspace, project, branch)
        collection_name = self._get_project_collection_name(workspace, project)

        # Check if collection has data
        collection_info = self.qdrant_client.get_collection(collection_name)

        if collection_info.points_count > 0:
            logger.info(f"Loaded existing index for {namespace} ({collection_info.points_count} points)")
            index = VectorStoreIndex.from_vector_store(
                vector_store=storage_context.vector_store,
                embed_model=self.embed_model
            )
        else:
            logger.info(f"Creating new index for {namespace}")
            index = VectorStoreIndex.from_documents(
                [],
                storage_context=storage_context,
                embed_model=self.embed_model,
                show_progress=True
            )

        return index

    def _resolve_alias_to_collection(self, alias_name: str) -> Optional[str]:
        """Resolve an alias to its underlying collection name"""
        try:
            # Get all aliases and find the one matching our alias_name
            aliases = self.qdrant_client.get_aliases()
            for alias in aliases.aliases:
                if alias.alias_name == alias_name:
                    return alias.collection_name
        except Exception as e:
            logger.debug(f"Error resolving alias {alias_name}: {e}")
        return None

    def _alias_exists(self, alias_name: str) -> bool:
        """Check if an alias exists"""
        try:
            aliases = self.qdrant_client.get_aliases()
            exists = any(a.alias_name == alias_name for a in aliases.aliases)
            logger.debug(f"Checking if alias '{alias_name}' exists: {exists}. All aliases: {[a.alias_name for a in aliases.aliases]}")
            return exists
        except Exception as e:
            logger.warning(f"Error checking alias {alias_name}: {e}")
            return False

    def estimate_repository_size(
            self,
            repo_path: str,
            exclude_patterns: Optional[List[str]] = None
    ) -> tuple[int, int]:
        """Estimate repository size (file count and chunk count) without actually indexing.

        Uses streaming approach to avoid loading all files into memory.

        Args:
            repo_path: Path to the repository
            exclude_patterns: Additional patterns to exclude

        Returns:
            Tuple of (file_count, estimated_chunk_count)
        """
        logger.info(f"Estimating repository size for: {repo_path}")

        repo_path_obj = Path(repo_path)

        # Count files without loading content (memory-efficient)
        file_list = list(self.loader.iter_repository_files(repo_path_obj, exclude_patterns))
        file_count = len(file_list)
        logger.info(f"Found {file_count} files for estimation")

        if file_count == 0:
            return 0, 0

        # Sample-based chunk estimation for large repos
        # For repos with many files, sample a subset and extrapolate
        SAMPLE_SIZE = 100
        chunk_count = 0
        
        if file_count <= SAMPLE_SIZE:
            # Small repo: count all chunks
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
            # Large repo: sample and extrapolate
            import random
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
            
            # Extrapolate
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
            exclude_patterns: Optional[List[str]] = None
    ) -> IndexStats:
        """Index entire repository for a branch using single-collection-per-project architecture.

        Uses versioned collections with alias-based atomic swap for zero-downtime reindexing:
        1. Create a new versioned collection (e.g., project_v1234567)
        2. Index all files into the temp collection
        3. On success, atomically swap the alias to point to new collection
        4. Delete the old versioned collection
        
        This ensures the old index remains available if indexing fails.
        Branch metadata is stored in point payloads for multi-branch queries.
        """
        logger.info(f"Indexing repository: {workspace}/{project}/{branch} from {repo_path}")

        # Convert string to Path object
        repo_path_obj = Path(repo_path)

        # Project-level alias name (no branch in name)
        alias_name = self._get_project_collection_name(workspace, project)
        temp_collection_name = f"{alias_name}_v{int(time.time())}"

        # Check if alias or collection exists and get existing branch data to preserve
        old_collection_exists = self._alias_exists(alias_name)
        existing_other_branch_points = []
        
        if not old_collection_exists:
            collections = self.qdrant_client.get_collections().collections
            collection_names = [c.name for c in collections]
            old_collection_exists = alias_name in collection_names
        
        # If collection exists, preserve points from OTHER branches (not the one being reindexed)
        if old_collection_exists:
            try:
                actual_collection = self._resolve_alias_to_collection(alias_name) or alias_name
                logger.info(f"Preserving points from other branches in {actual_collection}...")
                
                # Scroll through all points NOT in the branch being indexed
                offset = None
                while True:
                    results = self.qdrant_client.scroll(
                        collection_name=actual_collection,
                        limit=100,
                        offset=offset,
                        scroll_filter=Filter(
                            must_not=[
                                FieldCondition(
                                    key="branch",
                                    match=MatchValue(value=branch)
                                )
                            ]
                        ),
                        with_payload=True,
                        with_vectors=True
                    )
                    points, next_offset = results
                    existing_other_branch_points.extend(points)
                    
                    if next_offset is None or len(points) < 100:
                        break
                    offset = next_offset
                
                logger.info(f"Found {len(existing_other_branch_points)} points from other branches to preserve")
            except Exception as e:
                logger.warning(f"Could not read existing points: {e}")
        
        # Clean up any leftover versioned collections from previous failed attempts
        collections = self.qdrant_client.get_collections().collections
        collection_names = [c.name for c in collections]
        for coll_name in collection_names:
            if coll_name.startswith(f"{alias_name}_v") and coll_name != temp_collection_name:
                current_alias_target = self._resolve_alias_to_collection(alias_name)
                if current_alias_target != coll_name:
                    logger.info(f"Cleaning up orphaned versioned collection: {coll_name}")
                    try:
                        self.qdrant_client.delete_collection(coll_name)
                    except Exception as e:
                        logger.warning(f"Failed to clean up orphaned collection: {e}")

        # Create new temporary collection
        logger.info(f"Creating temporary collection: {temp_collection_name}")
        self.qdrant_client.create_collection(
            collection_name=temp_collection_name,
            vectors_config=VectorParams(
                size=self.config.embedding_dim,
                distance=Distance.COSINE
            )
        )

        # Get file list using DocumentLoader (low memory)
        file_list = list(self.loader.iter_repository_files(repo_path_obj, exclude_patterns))
        total_files = len(file_list)
        logger.info(f"Found {total_files} files to index for branch '{branch}'")
        
        if total_files == 0:
            logger.warning("No documents to index")
            self.qdrant_client.delete_collection(temp_collection_name)
            return self._get_branch_index_stats(workspace, project, branch)
        
        # Validate file limit first (BEFORE any expensive operations)
        if self.config.max_files_per_index > 0 and total_files > self.config.max_files_per_index:
            self.qdrant_client.delete_collection(temp_collection_name)
            raise ValueError(
                f"Repository exceeds file limit: {total_files} files (max: {self.config.max_files_per_index}). "
                f"Use exclude patterns in Project Settings → RAG Indexing to exclude unnecessary directories."
            )
        
        # Estimate chunk count and validate BEFORE starting embeddings
        # This prevents wasting API calls on repos that will fail the chunk limit
        if self.config.max_chunks_per_index > 0:
            logger.info("Estimating chunk count before indexing...")
            _, estimated_chunks = self.estimate_repository_size(repo_path, exclude_patterns)
            logger.info(f"Estimated chunks: {estimated_chunks}, limit: {self.config.max_chunks_per_index}")
            
            # Add 20% buffer for estimation variance
            if estimated_chunks > self.config.max_chunks_per_index * 1.2:
                self.qdrant_client.delete_collection(temp_collection_name)
                raise ValueError(
                    f"Repository estimated to exceed chunk limit: ~{estimated_chunks} chunks (max: {self.config.max_chunks_per_index}). "
                    f"Use exclude patterns in Project Settings → RAG Indexing to exclude large directories."
                )

        document_count = 0
        chunk_count = 0
        successful_chunks = 0
        failed_chunks = 0

        try:
            # First, copy preserved points from other branches to temp collection
            if existing_other_branch_points:
                logger.info(f"Copying {len(existing_other_branch_points)} points from other branches...")
                for i in range(0, len(existing_other_branch_points), INSERT_BATCH_SIZE):
                    batch = existing_other_branch_points[i:i + INSERT_BATCH_SIZE]
                    points_to_upsert = [
                        PointStruct(
                            id=p.id,
                            vector=p.vector,
                            payload=p.payload
                        ) for p in batch
                    ]
                    self.qdrant_client.upsert(
                        collection_name=temp_collection_name,
                        points=points_to_upsert
                    )
                logger.info("Other branch points copied successfully")
            
            # MEMORY-EFFICIENT STREAMING: Process documents in batches
            logger.info("Starting memory-efficient streaming indexing...")
            
            # Process files in batches
            batch_num = 0
            total_batches = (total_files + DOCUMENT_BATCH_SIZE - 1) // DOCUMENT_BATCH_SIZE
            
            for i in range(0, total_files, DOCUMENT_BATCH_SIZE):
                batch_num += 1
                file_batch = file_list[i:i + DOCUMENT_BATCH_SIZE]
                
                # Load batch of documents using DocumentLoader
                documents = self.loader.load_file_batch(
                    file_batch, repo_path_obj, workspace, project, branch, commit
                )
                document_count += len(documents)
                
                if not documents:
                    continue
                
                # Split documents into chunks
                chunks = self.splitter.split_documents(documents)
                batch_chunk_count = len(chunks)
                chunk_count += batch_chunk_count
                
                # Check chunk limit
                if self.config.max_chunks_per_index > 0 and chunk_count > self.config.max_chunks_per_index:
                    self.qdrant_client.delete_collection(temp_collection_name)
                    raise ValueError(
                        f"Repository exceeds chunk limit: {chunk_count}+ chunks (max: {self.config.max_chunks_per_index}). "
                        f"Use exclude patterns to exclude large directories."
                    )
                
                # Prepare points with deterministic IDs for upsert
                # First, collect all chunks with their metadata
                chunk_data = []  # List of (point_id, chunk, metadata)
                chunks_by_file: Dict[str, List[TextNode]] = {}
                for chunk in chunks:
                    path = chunk.metadata.get("path", "unknown")
                    if path not in chunks_by_file:
                        chunks_by_file[path] = []
                    chunks_by_file[path].append(chunk)
                
                for path, file_chunks in chunks_by_file.items():
                    for chunk_index, chunk in enumerate(file_chunks):
                        point_id = self._generate_point_id(workspace, project, branch, path, chunk_index)
                        chunk.metadata["indexed_at"] = datetime.now(timezone.utc).isoformat()
                        chunk_data.append((point_id, chunk))
                
                # Batch embed all chunks at once (much more efficient)
                texts_to_embed = [chunk.text for _, chunk in chunk_data]
                embeddings = self.embed_model.get_text_embedding_batch(texts_to_embed)
                
                # Build points with embeddings
                points = []
                for (point_id, chunk), embedding in zip(chunk_data, embeddings):
                    points.append(PointStruct(
                        id=point_id,
                        vector=embedding,
                        payload={
                            **chunk.metadata,
                            "text": chunk.text,
                            "_node_content": chunk.text,
                        }
                    ))
                
                # Upsert in batches (idempotent - same ID = replace)
                try:
                    for j in range(0, len(points), INSERT_BATCH_SIZE):
                        insert_batch = points[j:j + INSERT_BATCH_SIZE]
                        self.qdrant_client.upsert(
                            collection_name=temp_collection_name,
                            points=insert_batch
                        )
                    successful_chunks += batch_chunk_count
                except Exception as e:
                    logger.error(f"Failed to upsert batch {batch_num}: {e}")
                    failed_chunks += batch_chunk_count
                
                logger.info(
                    f"Batch {batch_num}/{total_batches}: processed {len(documents)} files, "
                    f"{batch_chunk_count} chunks"
                )
                
                # CRITICAL: Free memory after each batch
                del documents
                del chunks
                del points
                
                # Aggressive garbage collection every few batches
                if batch_num % 5 == 0:
                    gc.collect()
                    logger.debug(f"Memory cleanup after batch {batch_num}")

            logger.info(
                f"Streaming indexing complete: {document_count} files, "
                f"{successful_chunks}/{chunk_count} chunks indexed ({failed_chunks} failed)"
            )

            # Verify temp collection has data
            temp_collection_info = self.qdrant_client.get_collection(temp_collection_name)
            if temp_collection_info.points_count == 0:
                raise Exception("Temporary collection is empty after indexing")

            # SUCCESS: Atomic alias swap (zero-downtime)
            logger.info(f"Indexing successful. Performing atomic alias swap...")

            # Check if there's a collection (not alias) with the target name
            collections = self.qdrant_client.get_collections().collections
            collection_names = [c.name for c in collections]
            is_direct_collection = alias_name in collection_names and not self._alias_exists(alias_name)
            
            # Find old versioned collection BEFORE alias operations
            old_versioned_name = None
            if old_collection_exists and not is_direct_collection:
                old_versioned_name = self._resolve_alias_to_collection(alias_name)

            # If there's a direct collection with the target name, we need to delete it FIRST
            # before we can create an alias with that name
            if is_direct_collection:
                logger.info(f"Migrating from direct collection to alias-based indexing. Deleting old collection: {alias_name}")
                try:
                    self.qdrant_client.delete_collection(alias_name)
                except Exception as del_err:
                    logger.error(f"Failed to delete old direct collection before alias swap: {del_err}")
                    raise Exception(f"Cannot create alias - collection '{alias_name}' exists and cannot be deleted: {del_err}")

            alias_operations = []

            # Delete old alias if exists (not direct collection - already handled above)
            if old_collection_exists and not is_direct_collection:
                alias_operations.append(
                    DeleteAliasOperation(delete_alias=DeleteAlias(alias_name=alias_name))
                )

            # Create new alias pointing to temp collection
            alias_operations.append(
                CreateAliasOperation(create_alias=CreateAlias(
                    alias_name=alias_name,
                    collection_name=temp_collection_name
                ))
            )

            # Perform atomic alias swap
            self.qdrant_client.update_collection_aliases(
                change_aliases_operations=alias_operations
            )
            
            logger.info(f"Alias swap completed successfully: {alias_name} -> {temp_collection_name}")

            # Delete old versioned collection (after alias swap is complete)
            if old_versioned_name and old_versioned_name != temp_collection_name:
                logger.info(f"Deleting old versioned collection: {old_versioned_name}")
                try:
                    self.qdrant_client.delete_collection(old_versioned_name)
                except Exception as del_err:
                    logger.warning(f"Failed to delete old collection: {del_err}")

        except Exception as e:
            logger.error(f"Indexing failed: {e}")
            logger.info(f"Cleaning up temporary collection, old index preserved")
            try:
                self.qdrant_client.delete_collection(temp_collection_name)
            except Exception as cleanup_error:
                logger.warning(f"Failed to clean up temp collection: {cleanup_error}")
            raise e
        finally:
            # Free the preserved points from memory
            del existing_other_branch_points
            # Force garbage collection
            gc.collect()
            logger.info("Memory cleanup completed after indexing")

        # Store metadata
        self._store_metadata(workspace, project, branch, commit, [], [], document_count, chunk_count)

        # Return stats with actual counts from this indexing run
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

    def _store_metadata(
            self,
            workspace: str,
            project: str,
            branch: str,
            commit: str,
            documents: List[Document],
            chunks: List[TextNode],
            document_count: Optional[int] = None,
            chunk_count: Optional[int] = None
    ):
        """Store metadata in Qdrant collection payload"""
        namespace = make_namespace(workspace, project, branch)
        collection_name = self._get_project_collection_name(workspace, project)

        # Use provided counts or calculate from lists
        doc_count = document_count if document_count is not None else len(documents)
        chk_count = chunk_count if chunk_count is not None else len(chunks)

        # Update collection metadata
        metadata = {
            "namespace": namespace,
            "workspace": workspace,
            "project": project,
            "branch": branch,
            "commit": commit,
            "document_count": doc_count,
            "chunk_count": chk_count,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

        # Store in collection's payload_schema or as a special point
        # For now, we'll just log it - Qdrant will track points automatically
        logger.info(f"Indexed {namespace}: {doc_count} docs, {chk_count} chunks")

    def update_files(
            self,
            file_paths: List[str],
            repo_base: str,
            workspace: str,
            project: str,
            branch: str,
            commit: str
    ) -> IndexStats:
        """Update specific files in the index for a specific branch (Delete Old -> Insert New).
        
        Uses single project collection with branch in metadata.
        Deterministic point IDs ensure same file+branch+chunk = replace, not duplicate.
        """
        logger.info(f"Updating {len(file_paths)} files in {workspace}/{project} for branch '{branch}'")

        # 1. PREPARATION
        repo_base_obj = Path(repo_base)
        file_path_objs = [Path(fp) for fp in file_paths]
        collection_name = self._get_project_collection_name(workspace, project)
        
        # Ensure collection exists
        self._ensure_collection_exists(collection_name)

        # 2. DELETE OLD CHUNKS for these files AND this branch
        # Only delete points for the specific branch being updated
        logger.info(f"Purging existing vectors for {len(file_paths)} files in branch '{branch}'...")

        try:
            self.qdrant_client.delete(
                collection_name=collection_name,
                points_selector=Filter(
                    must=[
                        FieldCondition(
                            key="path",
                            match=MatchAny(any=file_paths)
                        ),
                        FieldCondition(
                            key="branch",
                            match=MatchValue(value=branch)
                        )
                    ]
                )
            )
        except Exception as e:
            logger.error(f"Error deleting old chunks: {e}")
            raise e

        # 3. LOAD & SPLIT NEW CONTENT
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
            return self._get_project_index_stats(workspace, project)

        chunks = self.splitter.split_documents(documents)
        logger.info(f"Generated {len(chunks)} new chunks")

        # 4. INSERT NEW CHUNKS with deterministic IDs
        # Group chunks by file path to assign chunk indices
        chunks_by_file: Dict[str, List[TextNode]] = {}
        for chunk in chunks:
            path = chunk.metadata.get("path", "unknown")
            if path not in chunks_by_file:
                chunks_by_file[path] = []
            chunks_by_file[path].append(chunk)
        
        # Collect all chunks with their metadata
        chunk_data = []  # List of (point_id, chunk)
        for path, file_chunks in chunks_by_file.items():
            for chunk_index, chunk in enumerate(file_chunks):
                point_id = self._generate_point_id(workspace, project, branch, path, chunk_index)
                chunk.metadata["indexed_at"] = datetime.now(timezone.utc).isoformat()
                chunk_data.append((point_id, chunk))
        
        # Batch embed all chunks at once (much more efficient)
        texts_to_embed = [chunk.text for _, chunk in chunk_data]
        embeddings = self.embed_model.get_text_embedding_batch(texts_to_embed)
        
        # Build points with embeddings
        points = []
        for (point_id, chunk), embedding in zip(chunk_data, embeddings):
            points.append(PointStruct(
                id=point_id,
                vector=embedding,
                payload={
                    **chunk.metadata,
                    "text": chunk.text,
                    "_node_content": chunk.text,
                }
            ))
        
        # Upsert in batches
        for i in range(0, len(points), INSERT_BATCH_SIZE):
            batch = points[i:i + INSERT_BATCH_SIZE]
            try:
                self.qdrant_client.upsert(
                    collection_name=collection_name,
                    points=batch
                )
            except Exception as e:
                logger.error(f"Failed to upsert batch {i // INSERT_BATCH_SIZE}: {e}")
                raise e

        logger.info(f"Successfully updated {len(chunks)} chunks for branch '{branch}'")

        return self._get_project_index_stats(workspace, project)

    def delete_files(self, file_paths: List[str], workspace: str, project: str, branch: str) -> IndexStats:
        """Delete specific files from the index for a specific branch"""
        logger.info(f"Deleting {len(file_paths)} files from {workspace}/{project} branch '{branch}'")
        collection_name = self._get_project_collection_name(workspace, project)

        try:
            self.qdrant_client.delete(
                collection_name=collection_name,
                points_selector=Filter(
                    must=[
                        FieldCondition(
                            key="path",
                            match=MatchAny(any=file_paths)
                        ),
                        FieldCondition(
                            key="branch",
                            match=MatchValue(value=branch)
                        )
                    ]
                )
            )
            logger.info(f"Deleted {len(file_paths)} files from branch '{branch}'")
        except Exception as e:
            logger.warning(f"Error deleting files: {e}")
            
        return self._get_project_index_stats(workspace, project)

    def delete_branch(self, workspace: str, project: str, branch: str) -> bool:
        """Delete all points for a specific branch from the project collection.
        
        Used when a branch is deleted or cleaned up.
        Does NOT delete the entire collection - only points for this branch.
        """
        logger.info(f"Deleting all points for branch '{branch}' from {workspace}/{project}")
        collection_name = self._get_project_collection_name(workspace, project)

        try:
            # Check if collection exists
            collections = [c.name for c in self.qdrant_client.get_collections().collections]
            if collection_name not in collections and not self._alias_exists(collection_name):
                logger.warning(f"Collection {collection_name} does not exist")
                return False

            self.qdrant_client.delete(
                collection_name=collection_name,
                points_selector=Filter(
                    must=[
                        FieldCondition(
                            key="branch",
                            match=MatchValue(value=branch)
                        )
                    ]
                )
            )
            logger.info(f"Successfully deleted all points for branch '{branch}'")
            return True
        except Exception as e:
            logger.error(f"Failed to delete branch '{branch}': {e}")
            return False

    def get_branch_point_count(self, workspace: str, project: str, branch: str) -> int:
        """Get the number of points for a specific branch."""
        collection_name = self._get_project_collection_name(workspace, project)
        
        try:
            # Check if collection exists
            collections = [c.name for c in self.qdrant_client.get_collections().collections]
            if collection_name not in collections and not self._alias_exists(collection_name):
                return 0

            result = self.qdrant_client.count(
                collection_name=collection_name,
                count_filter=Filter(
                    must=[
                        FieldCondition(
                            key="branch",
                            match=MatchValue(value=branch)
                        )
                    ]
                )
            )
            return result.count
        except Exception as e:
            logger.error(f"Failed to get point count for branch '{branch}': {e}")
            return 0

    def get_indexed_branches(self, workspace: str, project: str) -> List[str]:
        """Get list of branches that have points in the collection."""
        collection_name = self._get_project_collection_name(workspace, project)
        
        try:
            # Check if collection exists
            collections = [c.name for c in self.qdrant_client.get_collections().collections]
            if collection_name not in collections and not self._alias_exists(collection_name):
                return []

            # Scroll through points and collect unique branches
            # This is a simplified approach - for large collections, consider using facets
            branches = set()
            offset = None
            limit = 100
            
            while True:
                results = self.qdrant_client.scroll(
                    collection_name=collection_name,
                    limit=limit,
                    offset=offset,
                    with_payload=["branch"],
                    with_vectors=False
                )
                
                points, next_offset = results
                
                for point in points:
                    if point.payload and "branch" in point.payload:
                        branches.add(point.payload["branch"])
                
                if next_offset is None or len(points) < limit:
                    break
                offset = next_offset
            
            return list(branches)
        except Exception as e:
            logger.error(f"Failed to get indexed branches: {e}")
            return []

    def _get_project_index_stats(self, workspace: str, project: str) -> IndexStats:
        """Get statistics about a project's index (all branches combined)"""
        collection_name = self._get_project_collection_name(workspace, project)
        namespace = make_project_namespace(workspace, project)

        try:
            collection_info = self.qdrant_client.get_collection(collection_name)
            chunk_count = collection_info.points_count

            return IndexStats(
                namespace=namespace,
                document_count=0,
                chunk_count=chunk_count,
                last_updated=datetime.now(timezone.utc).isoformat(),
                workspace=workspace,
                project=project,
                branch="*"  # Indicates all branches
            )
        except Exception:
            return IndexStats(
                namespace=namespace,
                document_count=0,
                chunk_count=0,
                last_updated="",
                workspace=workspace,
                project=project,
                branch="*"
            )

    def delete_index(self, workspace: str, project: str, branch: str):
        """Delete branch data from project index.
        
        If branch is specified, only deletes that branch's points.
        To delete entire project collection, use delete_project_index().
        """
        if branch and branch != "*":
            # Delete only this branch's points
            self.delete_branch(workspace, project, branch)
        else:
            # Delete all branches - kept for backward compatibility
            self.delete_project_index(workspace, project)

    def delete_project_index(self, workspace: str, project: str):
        """Delete entire project collection (all branches)"""
        collection_name = self._get_project_collection_name(workspace, project)
        namespace = make_project_namespace(workspace, project)

        logger.info(f"Deleting entire project index for {namespace}")

        # Delete Qdrant collection and any aliases
        try:
            # Check if it's an alias
            if self._alias_exists(collection_name):
                actual_collection = self._resolve_alias_to_collection(collection_name)
                # Delete alias first
                self.qdrant_client.delete_alias(collection_name)
                # Then delete actual collection
                if actual_collection:
                    self.qdrant_client.delete_collection(actual_collection)
            else:
                self.qdrant_client.delete_collection(collection_name)
            logger.info(f"Deleted Qdrant collection: {collection_name}")
        except Exception as e:
            logger.warning(f"Failed to delete Qdrant collection: {e}")

    def _get_index_stats(self, workspace: str, project: str, branch: str) -> IndexStats:
        """Get statistics about a branch index (for backward compatibility)"""
        return self._get_branch_index_stats(workspace, project, branch)

    def _get_branch_index_stats(self, workspace: str, project: str, branch: str) -> IndexStats:
        """Get statistics about a specific branch within a project collection"""
        namespace = make_namespace(workspace, project, branch)
        collection_name = self._get_project_collection_name(workspace, project)

        try:
            # Count points for this specific branch
            count_result = self.qdrant_client.count(
                collection_name=collection_name,
                count_filter=Filter(
                    must=[
                        FieldCondition(
                            key="branch",
                            match=MatchValue(value=branch)
                        )
                    ]
                )
            )
            chunk_count = count_result.count

            return IndexStats(
                namespace=namespace,
                document_count=0,
                chunk_count=chunk_count,
                last_updated=datetime.now(timezone.utc).isoformat(),
                workspace=workspace,
                project=project,
                branch=branch
            )
        except Exception:
            return IndexStats(
                namespace=namespace,
                document_count=0,
                chunk_count=0,
                last_updated="",
                workspace=workspace,
                project=project,
                branch=branch
            )

    def list_indices(self) -> List[IndexStats]:
        """List all project indices with branch breakdown"""
        indices = []

        # Get all collections from Qdrant
        collections = self.qdrant_client.get_collections().collections

        for collection in collections:
            # Parse collection name to extract workspace/project (new format: no branch)
            if collection.name.startswith(f"{self.config.qdrant_collection_prefix}_"):
                namespace = collection.name[len(f"{self.config.qdrant_collection_prefix}_"):]
                parts = namespace.split("__")

                if len(parts) == 2:
                    # New format: workspace__project
                    workspace, project = parts
                    stats = self._get_project_index_stats(workspace, project)
                    indices.append(stats)
                elif len(parts) == 3:
                    # Legacy format: workspace__project__branch (for migration)
                    workspace, project, branch = parts
                    stats = self._get_branch_index_stats(workspace, project, branch)
                    indices.append(stats)

        return indices

