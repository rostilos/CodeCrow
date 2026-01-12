from typing import Optional, List, Dict
from datetime import datetime, timezone
from pathlib import Path
import logging
import gc
import os
import time

from llama_index.core import VectorStoreIndex, StorageContext, Settings
from llama_index.core.schema import Document, TextNode
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, Filter, FieldCondition, MatchAny,
    CreateAlias, DeleteAlias, CreateAliasOperation, DeleteAliasOperation
)

from ..models.config import RAGConfig, IndexStats
from ..utils.utils import make_namespace
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

    def _get_collection_name(self, workspace: str, project: str, branch: str) -> str:
        """Generate Qdrant collection name from workspace/project/branch"""
        namespace = make_namespace(workspace, project, branch)
        return f"{self.config.qdrant_collection_prefix}_{namespace}"

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
        """Create storage context with Qdrant vector store"""
        collection_name = self._get_collection_name(workspace, project, branch)
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
        """Get or create vector index for the given namespace"""
        namespace = make_namespace(workspace, project, branch)
        logger.info(f"Getting/creating index for namespace: {namespace}")

        storage_context = self._get_storage_context(workspace, project, branch)
        collection_name = self._get_collection_name(workspace, project, branch)

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
        """Index entire repository using memory-efficient streaming approach.

        This performs a full reindex by:
        1. Creating a new temporary collection
        2. Processing documents in batches (load -> split -> embed -> upsert -> free)
        3. On success, deleting the old collection and using the new one

        This ensures the old index remains available if indexing fails,
        and keeps memory usage low even for large repositories.
        """
        logger.info(f"Indexing repository: {workspace}/{project}/{branch} from {repo_path}")

        # Convert string to Path object
        repo_path_obj = Path(repo_path)

        # Get collection names (using versioned naming for alias-based swap)
        alias_name = self._get_collection_name(workspace, project, branch)
        temp_collection_name = f"{alias_name}_v{int(time.time())}"

        # Check if alias or collection exists
        old_collection_exists = self._alias_exists(alias_name)
        if not old_collection_exists:
            collections = self.qdrant_client.get_collections().collections
            collection_names = [c.name for c in collections]
            old_collection_exists = alias_name in collection_names
        else:
            collections = self.qdrant_client.get_collections().collections
            collection_names = [c.name for c in collections]

        # Clean up any leftover versioned collections from previous failed attempts
        for coll_name in collection_names:
            if coll_name.startswith(f"{alias_name}_v") and coll_name != temp_collection_name:
                if not self._resolve_alias_to_collection(alias_name) == coll_name:
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

        document_count = 0
        chunk_count = 0
        successful_chunks = 0
        failed_chunks = 0

        # Create vector store and index for the temporary collection
        temp_vector_store = QdrantVectorStore(
            client=self.qdrant_client,
            collection_name=temp_collection_name,
            enable_hybrid=False,
            batch_size=100
        )
        temp_storage_context = StorageContext.from_defaults(vector_store=temp_vector_store)
        temp_index = VectorStoreIndex.from_documents(
            [],
            storage_context=temp_storage_context,
            embed_model=self.embed_model,
            show_progress=False
        )

        try:
            # MEMORY-EFFICIENT STREAMING: Process documents in batches
            logger.info("Starting memory-efficient streaming indexing...")
            
            # Get file list using DocumentLoader (low memory)
            file_list = list(self.loader.iter_repository_files(repo_path_obj, exclude_patterns))
            total_files = len(file_list)
            logger.info(f"Found {total_files} files to index")
            
            if total_files == 0:
                logger.warning("No documents to index")
                self.qdrant_client.delete_collection(temp_collection_name)
                return self._get_index_stats(workspace, project, branch)
            
            # Validate file limit first
            if self.config.max_files_per_index > 0 and total_files > self.config.max_files_per_index:
                self.qdrant_client.delete_collection(temp_collection_name)
                raise ValueError(
                    f"Repository exceeds file limit: {total_files} files (max: {self.config.max_files_per_index}). "
                    f"Use exclude patterns in Project Settings → RAG Indexing to exclude unnecessary directories."
                )
            
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
                
                # Insert chunks using LlamaIndex (maintains proper payload schema for retrieval)
                try:
                    # Batch insert to keep memory low while letting LlamaIndex handle formatting
                    for j in range(0, len(chunks), INSERT_BATCH_SIZE):
                        insert_batch = chunks[j:j + INSERT_BATCH_SIZE]
                        temp_index.insert_nodes(insert_batch)
                    successful_chunks += batch_chunk_count
                except Exception as e:
                    logger.error(f"Failed to insert batch {batch_num}: {e}")
                    failed_chunks += batch_chunk_count
                
                logger.info(
                    f"Batch {batch_num}/{total_batches}: processed {len(documents)} files, "
                    f"{batch_chunk_count} chunks"
                )
                
                # CRITICAL: Free memory after each batch
                del documents
                del chunks
                
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

            # SUCCESS: Atomic alias swap (zero-copy)
            logger.info(f"Indexing successful. Performing atomic alias swap...")

            # Check if there's a collection (not alias) with the target name
            collections = self.qdrant_client.get_collections().collections
            collection_names = [c.name for c in collections]
            is_direct_collection = alias_name in collection_names and not self._alias_exists(alias_name)
            
            # Find old versioned collection BEFORE alias operations
            old_versioned_name = None
            if old_collection_exists and not is_direct_collection:
                old_versioned_name = self._resolve_alias_to_collection(alias_name)

            alias_operations = []

            # Delete old alias if exists (not direct collection)
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
            
            logger.info(f"Alias swap completed successfully")

            # NOW delete old collections (after alias swap is complete)
            # This ensures old index is available until the very last moment
            if is_direct_collection:
                logger.info(f"Migrating from direct collection to alias-based indexing. Deleting old collection: {alias_name}")
                try:
                    self.qdrant_client.delete_collection(alias_name)
                except Exception as del_err:
                    logger.warning(f"Failed to delete old direct collection: {del_err}")
            elif old_versioned_name and old_versioned_name != temp_collection_name:
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
        collection_name = self._get_collection_name(workspace, project, branch)

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
        """Update specific files in the index (Delete Old -> Insert New)"""
        logger.info(f"Updating {len(file_paths)} files in {workspace}/{project}/{branch}")

        # 1. PREPARATION
        repo_base_obj = Path(repo_base)
        file_path_objs = [Path(fp) for fp in file_paths]
        collection_name = self._get_collection_name(workspace, project, branch)

        # 2. DELETE OLD CHUNKS
        # We must remove existing vectors associated with these files to prevent duplicates.
        # This assumes your nodes have 'path' in their metadata.
        logger.info(f"Purging existing vectors for {len(file_paths)} files...")

        try:
            self.qdrant_client.delete(
                collection_name=collection_name,
                points_selector=Filter(
                    must=[
                        FieldCondition(
                            key="path",  # Ensure this matches the key in your metadata
                            match=MatchAny(any=file_paths)
                        )
                    ]
                )
            )
        except Exception as e:
            logger.error(f"Error deleting old chunks: {e}")
            # Decide if you want to raise here or continue.
            # Usually, you want to stop to avoid polluting the index.
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
            return self._get_index_stats(workspace, project, branch)

        chunks = self.splitter.split_documents(documents)
        logger.info(f"Generated {len(chunks)} new chunks")

        # 4. INSERT NEW CHUNKS (Batched)
        index = self._get_or_create_index(workspace, project, branch)

        # Use the same batching logic as index_repository to prevent timeouts on large updates
        for i in range(0, len(chunks), INSERT_BATCH_SIZE):
            batch = chunks[i:i + INSERT_BATCH_SIZE]
            try:
                index.insert_nodes(batch)
            except Exception as e:
                logger.error(f"Failed to insert update batch {i // INSERT_BATCH_SIZE}: {e}")

        logger.info(f"Successfully updated {len(chunks)} chunks")

        # 5. UPDATE METADATA (Optional)
        # You might want to update the 'last_updated' timestamp for the project here

        return self._get_index_stats(workspace, project, branch)

    def delete_files(self, file_paths: List[str], workspace: str, project: str, branch: str) -> IndexStats:
        """Delete specific files from the index using Batch Filter"""
        logger.info(f"Deleting {len(file_paths)} files from {workspace}/{project}/{branch}")
        collection_name = self._get_collection_name(workspace, project, branch)

        self.qdrant_client.delete(
            collection_name=collection_name,
            points_selector=Filter(
                must=[
                    FieldCondition(
                        key="path",
                        match=MatchAny(any=file_paths)
                    )
                ]
            )
        )
        logger.info(f"Deleted files from index")
        return self._get_index_stats(workspace, project, branch)

    def delete_index(self, workspace: str, project: str, branch: str):
        """Delete entire index"""
        collection_name = self._get_collection_name(workspace, project, branch)
        namespace = make_namespace(workspace, project, branch)

        logger.info(f"Deleting index for {namespace}")

        # Delete Qdrant collection
        try:
            self.qdrant_client.delete_collection(collection_name)
            logger.info(f"Deleted Qdrant collection: {collection_name}")
        except Exception as e:
            logger.warning(f"Failed to delete Qdrant collection: {e}")

    def _get_index_stats(self, workspace: str, project: str, branch: str) -> IndexStats:
        """Get statistics about an index"""
        namespace = make_namespace(workspace, project, branch)
        collection_name = self._get_collection_name(workspace, project, branch)

        # Get point count from Qdrant
        try:
            collection_info = self.qdrant_client.get_collection(collection_name)
            chunk_count = collection_info.points_count

            return IndexStats(
                namespace=namespace,
                document_count=0,  # We don't track this separately anymore
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
        """List all indices"""
        indices = []

        # Get all collections from Qdrant
        collections = self.qdrant_client.get_collections().collections

        for collection in collections:
            # Parse collection name to extract workspace/project/branch
            if collection.name.startswith(f"{self.config.qdrant_collection_prefix}_"):
                namespace = collection.name[len(f"{self.config.qdrant_collection_prefix}_"):]
                parts = namespace.split("__")

                if len(parts) == 3:
                    workspace, project, branch = parts
                    stats = self._get_index_stats(workspace, project, branch)
                    indices.append(stats)

        return indices

