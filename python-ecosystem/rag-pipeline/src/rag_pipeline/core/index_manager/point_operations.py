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
    
    def __init__(self, client: QdrantClient, embed_model, batch_size: int = 50):
        self.client = client
        self.embed_model = embed_model
        self.batch_size = batch_size
    
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
            path = chunk.metadata.get("path", "unknown")
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
        
        # Batch embed all chunks at once
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
        
        # Embed and create points
        points = self.embed_and_create_points(chunk_data)
        
        # Upsert to collection
        return self.upsert_points(collection_name, points)
