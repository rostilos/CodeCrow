"""
Branch-level operations for RAG indices.

Handles branch-specific point management within project collections.
"""

import logging
from typing import List, Set, Optional, Iterator, Generator

from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue, PointStruct

logger = logging.getLogger(__name__)


class BranchManager:
    """Manages branch-level operations within project collections."""
    
    def __init__(self, client: QdrantClient):
        self.client = client
    
    def delete_branch_points(
        self,
        collection_name: str,
        branch: str
    ) -> bool:
        """Delete all points for a specific branch from the collection."""
        logger.info(f"Deleting all points for branch '{branch}' from {collection_name}")
        
        try:
            self.client.delete(
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
    
    def get_branch_point_count(
        self,
        collection_name: str,
        branch: str
    ) -> int:
        """Get the number of points for a specific branch."""
        try:
            result = self.client.count(
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
    
    def get_indexed_branches(self, collection_name: str) -> List[str]:
        """Get list of branches that have points in the collection."""
        try:
            branches: Set[str] = set()
            offset = None
            limit = 100
            
            while True:
                results = self.client.scroll(
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
    
    def preserve_other_branch_points(
        self,
        collection_name: str,
        exclude_branch: str,
        batch_size: int = 100
    ) -> Generator[List[PointStruct], None, None]:
        """Stream points from branches other than the one being reindexed.
        
        Yields batches of points instead of loading all into memory.
        Used during full reindex to keep data from other branches.
        """
        logger.info(f"Streaming points from branches other than '{exclude_branch}'...")
        
        offset = None
        total_yielded = 0
        
        try:
            while True:
                results = self.client.scroll(
                    collection_name=collection_name,
                    limit=batch_size,
                    offset=offset,
                    scroll_filter=Filter(
                        must_not=[
                            FieldCondition(
                                key="branch",
                                match=MatchValue(value=exclude_branch)
                            )
                        ]
                    ),
                    with_payload=True,
                    with_vectors=True
                )
                points, next_offset = results
                
                if points:
                    total_yielded += len(points)
                    yield points
                
                if next_offset is None or len(points) < batch_size:
                    break
                offset = next_offset
            
            logger.info(f"Streamed {total_yielded} points from other branches")
        except Exception as e:
            logger.warning(f"Could not read existing points: {e}")
            return
    
    def stream_copy_points_to_collection(
        self,
        source_collection: str,
        target_collection: str,
        exclude_branch: str,
        batch_size: int = 50
    ) -> int:
        """Stream copy points from one collection to another, excluding a branch.
        
        Memory-efficient alternative to preserve_other_branch_points + copy_points_to_collection.
        Skips copying if vector dimensions don't match between collections.
        """
        # Check vector dimensions match before copying
        try:
            source_info = self.client.get_collection(source_collection)
            target_info = self.client.get_collection(target_collection)
            
            # Get dimensions from vector config
            source_dim = None
            target_dim = None
            
            if hasattr(source_info.config.params, 'vectors'):
                vectors_config = source_info.config.params.vectors
                if hasattr(vectors_config, 'size'):
                    source_dim = vectors_config.size
                elif isinstance(vectors_config, dict) and '' in vectors_config:
                    source_dim = vectors_config[''].size
            
            if hasattr(target_info.config.params, 'vectors'):
                vectors_config = target_info.config.params.vectors
                if hasattr(vectors_config, 'size'):
                    target_dim = vectors_config.size
                elif isinstance(vectors_config, dict) and '' in vectors_config:
                    target_dim = vectors_config[''].size
            
            if source_dim and target_dim and source_dim != target_dim:
                logger.warning(
                    f"Skipping branch preservation: dimension mismatch "
                    f"(source: {source_dim}, target: {target_dim}). "
                    f"Re-embedding required for all branches."
                )
                return 0
                
        except Exception as e:
            logger.warning(f"Could not verify collection dimensions: {e}")
            # Continue anyway - will fail at upsert if dimensions don't match
        total_copied = 0
        
        for batch in self.preserve_other_branch_points(source_collection, exclude_branch, batch_size):
            points_to_upsert = [
                PointStruct(
                    id=p.id,
                    vector=p.vector,
                    payload=p.payload
                ) for p in batch
            ]
            self.client.upsert(
                collection_name=target_collection,
                points=points_to_upsert
            )
            total_copied += len(points_to_upsert)
        
        if total_copied > 0:
            logger.info(f"Copied {total_copied} points to {target_collection}")
        return total_copied
    
    def copy_points_to_collection(
        self,
        points: List,
        target_collection: str,
        batch_size: int = 50
    ) -> None:
        """Copy preserved points to a new collection."""
        if not points:
            return
        
        logger.info(f"Copying {len(points)} points to {target_collection}...")
        
        for i in range(0, len(points), batch_size):
            batch = points[i:i + batch_size]
            points_to_upsert = [
                PointStruct(
                    id=p.id,
                    vector=p.vector,
                    payload=p.payload
                ) for p in batch
            ]
            self.client.upsert(
                collection_name=target_collection,
                points=points_to_upsert
            )
        
        logger.info("Points copied successfully")
