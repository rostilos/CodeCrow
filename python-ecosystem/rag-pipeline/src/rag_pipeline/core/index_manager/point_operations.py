"""
Point operations for embedding and upserting vectors.

Handles embedding generation, point creation, and batch upsert operations.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Tuple

from llama_index.core.schema import TextNode
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct

logger = logging.getLogger(__name__)


class PointOperations:
    """Handles point embedding and upsert operations."""
    
    def __init__(
        self,
        client: QdrantClient,
        embed_model,
        batch_size: int = 50,
        embedding_dim: int | None = None,
    ):
        self.client = client
        self.embed_model = embed_model
        self.batch_size = batch_size
        self.embedding_dim = embedding_dim
    
    @staticmethod
    def generate_point_id(
        workspace: str,
        project: str,
        branch: str,
        path: str,
        chunk_index: int
    ) -> str:
        """Generate deterministic point ID for upsert (same content = same ID = replace)."""
        key = f"{workspace}:{project}:{branch}:{path}:{chunk_index}"
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, key))
    
    def prepare_chunks_for_embedding(
        self,
        chunks: List[TextNode],
        workspace: str,
        project: str,
        branch: str
    ) -> List[Tuple[str, TextNode]]:
        """Prepare chunks with deterministic IDs for embedding.
        
        Returns list of (point_id, chunk) tuples.
        """
        # Group chunks by file path
        chunks_by_file: Dict[str, List[TextNode]] = {}
        for chunk in chunks:
            path = chunk.metadata.get("path")
            if not path:
                # Log warning - missing path breaks idempotency but shouldn't happen in normal flow
                logger.warning("Chunk missing 'path' metadata - using non-deterministic ID (breaks idempotency)")
                path = f"__unknown__{uuid.uuid4()}"
            if path not in chunks_by_file:
                chunks_by_file[path] = []
            chunks_by_file[path].append(chunk)
        
        # Assign deterministic IDs
        chunk_data = []
        for path, file_chunks in chunks_by_file.items():
            for chunk_index, chunk in enumerate(file_chunks):
                point_id = self.generate_point_id(workspace, project, branch, path, chunk_index)
                chunk.metadata["indexed_at"] = datetime.now(timezone.utc).isoformat()
                chunk_data.append((point_id, chunk))
        
        return chunk_data
    
    def embed_and_create_points(
        self,
        chunk_data: List[Tuple[str, TextNode]]
    ) -> List[PointStruct]:
        """Embed chunks and create Qdrant points.
        
        Args:
            chunk_data: List of (point_id, chunk) tuples
            
        Returns:
            List of PointStruct ready for upsert
        """
        if not chunk_data:
            return []
        
        # Architecture packets are retrieved only through exact metadata edges.
        # Giving them a zero vector avoids a paid embedding request and keeps
        # them out of similarity ranking without creating a second storage API.
        semantic_chunks = [
            chunk
            for _, chunk in chunk_data
            if not (
                chunk.metadata.get("architecture_context")
                or chunk.metadata.get("architecture_source")
                or chunk.metadata.get("repository_snapshot")
                or chunk.metadata.get("repository_facts_state")
            )
        ]
        semantic_embeddings = (
            self.embed_model.get_text_embedding_batch(
                [chunk.text for chunk in semantic_chunks]
            )
            if semantic_chunks
            else []
        )
        embeddings = iter(semantic_embeddings)
        
        # Build points with embeddings
        points = []
        for point_id, chunk in chunk_data:
            if (
                chunk.metadata.get("architecture_context")
                or chunk.metadata.get("architecture_source")
                or chunk.metadata.get("repository_snapshot")
                or chunk.metadata.get("repository_facts_state")
            ):
                if not self.embedding_dim:
                    raise RuntimeError(
                        "embedding dimension is required for architecture context storage"
                    )
                embedding = [0.0] * self.embedding_dim
            else:
                embedding = next(embeddings)
            payload = {
                **chunk.metadata,
                "text": chunk.text,
            }
            if not (
                chunk.metadata.get("repository_snapshot")
                or chunk.metadata.get("repository_facts_state")
            ):
                payload["_node_content"] = chunk.text
            points.append(PointStruct(
                id=point_id,
                vector=embedding,
                payload=payload,
            ))
        
        return points
    
    def upsert_points(
        self,
        collection_name: str,
        points: List[PointStruct]
    ) -> Tuple[int, int]:
        """Upsert points to collection in batches.
        
        Returns:
            Tuple of (successful_count, failed_count)
        """
        successful = 0
        failed = 0
        
        for i in range(0, len(points), self.batch_size):
            batch = points[i:i + self.batch_size]
            try:
                self.client.upsert(
                    collection_name=collection_name,
                    points=batch
                )
                successful += len(batch)
            except Exception as e:
                logger.error(f"Failed to upsert batch starting at {i}: {e}")
                failed += len(batch)
        
        return successful, failed
    
    def process_and_upsert_chunks(
        self,
        chunks: List[TextNode],
        collection_name: str,
        workspace: str,
        project: str,
        branch: str
    ) -> Tuple[int, int]:
        """Full pipeline: prepare, embed, and upsert chunks.
        
        Returns:
            Tuple of (successful_count, failed_count)
        """
        # Prepare chunks with IDs
        chunk_data = self.prepare_chunks_for_embedding(
            chunks, workspace, project, branch
        )
        
        successful = 0
        failed = 0

        # Embed and write bounded point slices. The collection is still pending
        # and cannot become active until the index manager verifies the complete
        # work count, but operators can now observe steady progress instead of
        # waiting for every chunk from a 50-file document batch to finish.
        for i in range(0, len(chunk_data), self.batch_size):
            point_batch = self.embed_and_create_points(
                chunk_data[i:i + self.batch_size]
            )
            batch_successful, batch_failed = self.upsert_points(
                collection_name,
                point_batch,
            )
            successful += batch_successful
            failed += batch_failed
            if batch_failed:
                break

        return successful, failed
