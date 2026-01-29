"""
Branch-level operations for RAG indices.

Handles branch-specific point management within project collections.
"""

import logging
from typing import List, Set, Optional

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
        exclude_branch: str
    ) -> List[PointStruct]:
        """Preserve points from branches other than the one being reindexed.
        
        Used during full reindex to keep data from other branches.
        """
        logger.info(f"Preserving points from branches other than '{exclude_branch}'...")
        
        preserved_points = []
        offset = None
        
        try:
            while True:
                results = self.client.scroll(
                    collection_name=collection_name,
                    limit=100,
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
                preserved_points.extend(points)
                
                if next_offset is None or len(points) < 100:
                    break
                offset = next_offset
            
            logger.info(f"Found {len(preserved_points)} points from other branches to preserve")
            return preserved_points
        except Exception as e:
            logger.warning(f"Could not read existing points: {e}")
            return []
    
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
