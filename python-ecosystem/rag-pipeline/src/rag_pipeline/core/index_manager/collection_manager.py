"""
Qdrant collection and alias management utilities.

Handles collection creation, alias operations, and resolution.
"""

import logging
import os
import uuid
from typing import Optional, List

from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import (
    Distance, VectorParams,
    CreateAlias, DeleteAlias, CreateAliasOperation, DeleteAliasOperation,
    PayloadSchemaType, TextIndexParams, TokenizerType
)

logger = logging.getLogger(__name__)


class CollectionManager:
    """Manages Qdrant collections and aliases."""

    def __init__(self, client: QdrantClient, embedding_dim: int):
        self.client = client
        self.embedding_dim = embedding_dim
        self.vectors_on_disk = os.environ.get("QDRANT_VECTORS_ON_DISK", "true").lower() == "true"
    
    def ensure_collection_exists(self, collection_name: str) -> None:
        """Ensure Qdrant collection exists with proper configuration.
        
        If the collection_name is actually an alias, use the aliased collection instead.
        """
        if self.alias_exists(collection_name):
            logger.info(f"Collection name {collection_name} is an alias, using existing aliased collection")
            return
        
        collections = self.client.get_collections().collections
        collection_names = [c.name for c in collections]
        logger.debug(f"Existing collections: {collection_names}")

        if collection_name not in collection_names:
            logger.info(f"Creating Qdrant collection: {collection_name} (vectors_on_disk={self.vectors_on_disk})")
            created = self._create_collection(collection_name)
            if created:
                logger.info(f"Created collection {collection_name}")
            else:
                logger.info(
                    "Collection %s was created concurrently; using it",
                    collection_name,
                )
            self._ensure_payload_indexes(collection_name)
        else:
            logger.info(f"Collection {collection_name} already exists")

    def create_pending_collection(self, base_name: str) -> str:
        """Create an unpublished collection for atomic index activation."""
        # Pending collections can be created by different workers or processes.
        # A random suffix avoids timestamp collisions without coordination.
        for _ in range(3):
            pending_name = f"{base_name}_pending_{uuid.uuid4().hex[:16]}"
            logger.info(f"Creating pending collection: {pending_name}")
            if self._create_collection(pending_name):
                self._ensure_payload_indexes(pending_name)
                return pending_name
            logger.warning(
                "Pending collection name %s already exists; generating another",
                pending_name,
            )
        raise RuntimeError(
            f"Unable to allocate a unique pending collection for {base_name}"
        )

    def _create_collection(self, collection_name: str) -> bool:
        """Create one physical collection, accepting only a proven create race."""
        try:
            self.client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(
                    size=self.embedding_dim,
                    distance=Distance.COSINE,
                    on_disk=self.vectors_on_disk,
                ),
                on_disk_payload=self.vectors_on_disk,
            )
            return True
        except UnexpectedResponse as exception:
            if (
                exception.status_code == 409
                and self._physical_collection_exists(collection_name)
            ):
                return False
            raise

    def _physical_collection_exists(self, collection_name: str) -> bool:
        """Check a physical collection name without treating aliases as matches."""
        collections = self.client.get_collections().collections
        return any(collection.name == collection_name for collection in collections)

    def _ensure_payload_indexes(self, collection_name: str) -> None:
        """Create payload indexes for efficient filtering on common fields."""
        fields = (
            "path",
            "branch",
            "architecture_paths",
            "architecture_group",
            "snapshot_plugin",
            "snapshot_kind",
        )
        for field_name in fields:
            try:
                self.client.create_payload_index(
                    collection_name=collection_name,
                    field_name=field_name,
                    field_schema=PayloadSchemaType.KEYWORD,
                )
            except Exception as exception:
                logger.warning(
                    "Failed to create payload index %s on %s: %s",
                    field_name,
                    collection_name,
                    exception,
                )
        logger.info(f"Payload indexes ensured for {collection_name}")
    
    def delete_collection(self, collection_name: str) -> bool:
        """Delete a collection."""
        try:
            self.client.delete_collection(collection_name)
            logger.info(f"Deleted collection: {collection_name}")
            return True
        except Exception as e:
            logger.warning(f"Failed to delete collection {collection_name}: {e}")
            return False
    
    def collection_exists(self, collection_name: str) -> bool:
        """Check if a collection or alias exists."""
        if self.alias_exists(collection_name):
            return True

        collections = self.client.get_collections().collections
        return collection_name in [c.name for c in collections]
    
    def get_collection_names(self) -> List[str]:
        """Get all collection names."""
        collections = self.client.get_collections().collections
        return [c.name for c in collections]
    
    # Alias operations
    
    def alias_exists(self, alias_name: str) -> bool:
        """Check if an alias exists."""
        try:
            aliases = self.client.get_aliases()
            exists = any(a.alias_name == alias_name for a in aliases.aliases)
            logger.debug(f"Checking if alias '{alias_name}' exists: {exists}")
            return exists
        except Exception as e:
            logger.warning(f"Error checking alias {alias_name}: {e}")
            return False
    
    def resolve_alias(self, alias_name: str) -> Optional[str]:
        """Resolve an alias to its underlying collection name."""
        try:
            aliases = self.client.get_aliases()
            for alias in aliases.aliases:
                if alias.alias_name == alias_name:
                    return alias.collection_name
        except Exception as e:
            logger.debug(f"Error resolving alias {alias_name}: {e}")
        return None
    
    def atomic_alias_swap(
        self,
        alias_name: str,
        new_collection: str,
        old_alias_exists: bool
    ) -> None:
        """Perform atomic alias swap for zero-downtime reindexing."""
        alias_operations = []

        if old_alias_exists:
            alias_operations.append(
                DeleteAliasOperation(delete_alias=DeleteAlias(alias_name=alias_name))
            )

        alias_operations.append(
            CreateAliasOperation(create_alias=CreateAlias(
                alias_name=alias_name,
                collection_name=new_collection
            ))
        )

        self.client.update_collection_aliases(
            change_aliases_operations=alias_operations
        )
        logger.info(f"Alias swap completed: {alias_name} -> {new_collection}")
    
    def delete_alias(self, alias_name: str) -> bool:
        """Delete an alias."""
        try:
            self.client.delete_alias(alias_name)
            logger.info(f"Deleted alias: {alias_name}")
            return True
        except Exception as e:
            logger.warning(f"Failed to delete alias {alias_name}: {e}")
            return False
    
    def cleanup_orphaned_pending_collections(
        self,
        base_name: str,
        current_target: Optional[str] = None,
        exclude_name: Optional[str] = None
    ) -> int:
        """Clean up unpublished collections left by interrupted indexing attempts."""
        cleaned = 0
        collection_names = self.get_collection_names()
        
        for coll_name in collection_names:
            if coll_name.startswith(f"{base_name}_pending_") and coll_name != exclude_name:
                if current_target != coll_name:
                    logger.info(f"Cleaning up orphaned pending collection: {coll_name}")
                    if self.delete_collection(coll_name):
                        cleaned += 1
        
        return cleaned
