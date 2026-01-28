"""
Index statistics and metadata operations.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional

from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue

from ...models.config import IndexStats
from ...utils.utils import make_namespace, make_project_namespace

logger = logging.getLogger(__name__)


class StatsManager:
    """Manages index statistics and metadata."""
    
    def __init__(self, client: QdrantClient, collection_prefix: str):
        self.client = client
        self.collection_prefix = collection_prefix
    
    def get_branch_stats(
        self,
        workspace: str,
        project: str,
        branch: str,
        collection_name: str
    ) -> IndexStats:
        """Get statistics about a specific branch within a project collection."""
        namespace = make_namespace(workspace, project, branch)

        try:
            count_result = self.client.count(
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
    
    def get_project_stats(
        self,
        workspace: str,
        project: str,
        collection_name: str
    ) -> IndexStats:
        """Get statistics about a project's index (all branches combined)."""
        namespace = make_project_namespace(workspace, project)

        try:
            collection_info = self.client.get_collection(collection_name)
            chunk_count = collection_info.points_count

            return IndexStats(
                namespace=namespace,
                document_count=0,
                chunk_count=chunk_count,
                last_updated=datetime.now(timezone.utc).isoformat(),
                workspace=workspace,
                project=project,
                branch="*"
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
    
    def list_all_indices(self, alias_checker) -> List[IndexStats]:
        """List all project indices with branch breakdown.
        
        Args:
            alias_checker: Function to check if name is an alias
        """
        indices = []
        collections = self.client.get_collections().collections

        for collection in collections:
            if collection.name.startswith(f"{self.collection_prefix}_"):
                namespace = collection.name[len(f"{self.collection_prefix}_"):]
                parts = namespace.split("__")

                if len(parts) == 2:
                    # New format: workspace__project
                    workspace, project = parts
                    stats = self.get_project_stats(
                        workspace, project, collection.name
                    )
                    indices.append(stats)
                elif len(parts) == 3:
                    # Legacy format: workspace__project__branch
                    workspace, project, branch = parts
                    stats = self.get_branch_stats(
                        workspace, project, branch, collection.name
                    )
                    indices.append(stats)

        return indices
    
    def store_metadata(
        self,
        workspace: str,
        project: str,
        branch: str,
        commit: str,
        document_count: int,
        chunk_count: int
    ) -> None:
        """Store/log metadata for an indexing operation."""
        namespace = make_namespace(workspace, project, branch)
        
        metadata = {
            "namespace": namespace,
            "workspace": workspace,
            "project": project,
            "branch": branch,
            "commit": commit,
            "document_count": document_count,
            "chunk_count": chunk_count,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
        
        logger.info(f"Indexed {namespace}: {document_count} docs, {chunk_count} chunks")
