"""
Qdrant collection and alias management utilities.

Handles collection creation, alias operations, and resolution.
"""

import logging
import time
from typing import Optional, List

from qdrant_client import QdrantClient
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
            logger.info(f"Creating Qdrant collection: {collection_name}")
            self.client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(
                    size=self.embedding_dim,
                    distance=Distance.COSINE
                )
            )
            logger.info(f"Created collection {collection_name}")
            self._ensure_payload_indexes(collection_name)
        else:
            logger.info(f"Collection {collection_name} already exists")
    
    def create_versioned_collection(self, base_name: str) -> str:
        """Create a new versioned collection for atomic swap indexing."""
        # Use milliseconds to avoid collisions in rapid calls
        versioned_name = f"{base_name}_v{int(time.time() * 1000)}"
        logger.info(f"Creating versioned collection: {versioned_name}")
        
        self.client.create_collection(
            collection_name=versioned_name,
            vectors_config=VectorParams(
                size=self.embedding_dim,
                distance=Distance.COSINE
            )
        )
        self._ensure_payload_indexes(versioned_name)
        return versioned_name
    
    def _ensure_payload_indexes(self, collection_name: str) -> None:
        """Create payload indexes for efficient filtering on common fields."""
        try:
            # Keyword index on 'path' for exact match and prefix filtering
            self.client.create_payload_index(
                collection_name=collection_name,
                field_name="path",
                field_schema=PayloadSchemaType.KEYWORD,
            )
            # Keyword index on 'branch' for branch filtering
            self.client.create_payload_index(
                collection_name=collection_name,
                field_name="branch",
                field_schema=PayloadSchemaType.KEYWORD,
            )
            logger.info(f"Payload indexes created for {collection_name}")
        except Exception as e:
            logger.warning(f"Failed to create payload indexes for {collection_name}: {e}")
    
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
        """Check if a collection exists (not alias)."""
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
    
    def cleanup_orphaned_versioned_collections(
        self,
        base_name: str,
        current_target: Optional[str] = None,
        exclude_name: Optional[str] = None
    ) -> int:
        """Clean up orphaned versioned collections from failed indexing attempts."""
        cleaned = 0
        collection_names = self.get_collection_names()
        
        for coll_name in collection_names:
            if coll_name.startswith(f"{base_name}_v") and coll_name != exclude_name:
                if current_target != coll_name:
                    logger.info(f"Cleaning up orphaned versioned collection: {coll_name}")
                    if self.delete_collection(coll_name):
                        cleaned += 1
        
        return cleaned
