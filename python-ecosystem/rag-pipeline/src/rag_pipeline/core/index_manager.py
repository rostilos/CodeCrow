from typing import Optional, List, Dict
from datetime import datetime, timezone
from pathlib import Path
import logging

from llama_index.core import VectorStoreIndex, StorageContext, Settings
from llama_index.core.schema import Document, TextNode
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, Filter, FieldCondition, MatchAny

from ..models.config import RAGConfig, IndexStats
from ..utils.utils import make_namespace
from .chunking import CodeAwareSplitter
from .loader import DocumentLoader
from .openrouter_embedding import OpenRouterEmbedding

logger = logging.getLogger(__name__)


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
            max_retries=3
        )

        # Set global settings
        Settings.embed_model = self.embed_model
        Settings.chunk_size = config.chunk_size
        Settings.chunk_overlap = config.chunk_overlap

        self.splitter = CodeAwareSplitter(
            code_chunk_size=config.chunk_size,
            code_overlap=config.chunk_overlap,
            text_chunk_size=config.text_chunk_size,
            text_overlap=config.text_chunk_overlap
        )

        self.loader = DocumentLoader(config)

    def _get_collection_name(self, workspace: str, project: str, branch: str) -> str:
        """Generate Qdrant collection name from workspace/project/branch"""
        namespace = make_namespace(workspace, project, branch)
        return f"{self.config.qdrant_collection_prefix}_{namespace}"

    def _ensure_collection_exists(self, collection_name: str):
        """Ensure Qdrant collection exists with proper configuration"""
        collections = self.qdrant_client.get_collections().collections
        collection_names = [c.name for c in collections]

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

    def index_repository(
        self,
        repo_path: str,
        workspace: str,
        project: str,
        branch: str,
        commit: str
    ) -> IndexStats:
        """Index entire repository"""
        logger.info(f"Indexing repository: {workspace}/{project}/{branch} from {repo_path}")

        # Convert string to Path object
        repo_path_obj = Path(repo_path)

        # Load documents
        documents = self.loader.load_from_directory(
            repo_path=repo_path_obj,
            workspace=workspace,
            project=project,
            branch=branch,
            commit=commit
        )

        logger.info(f"Loaded {len(documents)} documents")

        # Split into chunks
        chunks = self.splitter.split_documents(documents)
        logger.info(f"Created {len(chunks)} chunks")

        # Get or create index
        index = self._get_or_create_index(workspace, project, branch)

        # Insert documents in batches to handle errors better
        logger.info(f"Inserting {len(chunks)} chunks into vector store...")
        batch_size = 50
        successful_chunks = 0
        failed_chunks = 0

        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            try:
                index.insert_nodes(batch)
                successful_chunks += len(batch)
                logger.info(f"Inserted batch {i//batch_size + 1}/{(len(chunks) + batch_size - 1)//batch_size}: {len(batch)} chunks")
            except Exception as e:
                failed_chunks += len(batch)
                logger.error(f"Failed to insert batch {i//batch_size + 1}: {e}")
                # Try individual chunks in failed batch
                for chunk in batch:
                    try:
                        index.insert_nodes([chunk])
                        successful_chunks += 1
                        failed_chunks -= 1
                    except Exception as chunk_error:
                        logger.warning(f"Failed to insert chunk from {chunk.metadata.get('path', 'unknown')}: {chunk_error}")

        logger.info(f"Successfully indexed {successful_chunks}/{len(chunks)} chunks ({failed_chunks} failed)")

        # Store metadata in MongoDB
        self._store_metadata(workspace, project, branch, commit, documents, chunks)

        return self._get_index_stats(workspace, project, branch)

    def _store_metadata(
        self,
        workspace: str,
        project: str,
        branch: str,
        commit: str,
        documents: List[Document],
        chunks: List[TextNode]
    ):
        """Store metadata in Qdrant collection payload"""
        namespace = make_namespace(workspace, project, branch)
        collection_name = self._get_collection_name(workspace, project, branch)

        # Update collection metadata
        metadata = {
            "namespace": namespace,
            "workspace": workspace,
            "project": project,
            "branch": branch,
            "commit": commit,
            "document_count": len(documents),
            "chunk_count": len(chunks),
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

        # Store in collection's payload_schema or as a special point
        # For now, we'll just log it - Qdrant will track points automatically
        logger.info(f"Indexed {namespace}: {len(documents)} docs, {len(chunks)} chunks")

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
        batch_size = 50
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i:i + batch_size]
            try:
                index.insert_nodes(batch)
            except Exception as e:
                logger.error(f"Failed to insert update batch {i}: {e}")

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

