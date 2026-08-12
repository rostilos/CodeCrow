"""
Qdrant collection and alias management utilities.

Handles collection creation, alias operations, and resolution.
"""

import logging
import os
import re
import time
import uuid
from typing import Callable, Mapping, Optional, List

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

    def create_pending_collection(
        self,
        base_name: str,
        *,
        operation_id: Optional[str] = None,
    ) -> str:
        """Create an unpublished collection for atomic index activation."""
        # Pending collections can be created by different workers or processes.
        # Timestamp + operation ownership lets the janitor distinguish a live
        # build from an expired orphan without touching another worker's work.
        for _ in range(3):
            token = re.sub(
                r"[^a-fA-F0-9]",
                "",
                operation_id or uuid.uuid4().hex,
            )[:32] or uuid.uuid4().hex[:32]
            pending_name = (
                f"{base_name}_pending_{int(time.time())}_{token}_"
                f"{uuid.uuid4().hex[:8]}"
            )
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

    def resolve_collection_target(self, collection_name: str) -> Optional[str]:
        """Resolve an alias or direct collection without hiding backend errors.

        Mutation leases use this strict resolver so a transient alias lookup
        failure cannot be mistaken for a direct collection.
        """
        aliases = self.client.get_aliases().aliases
        matching_aliases = [
            alias.collection_name
            for alias in aliases
            if alias.alias_name == collection_name
        ]
        if len(matching_aliases) > 1:
            raise RuntimeError(
                f"collection alias '{collection_name}' has multiple targets"
            )
        if matching_aliases:
            return matching_aliases[0]

        collections = self.client.get_collections().collections
        if collection_name in {collection.name for collection in collections}:
            return collection_name
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

    def read_alias_targets(self, alias_names: List[str]) -> dict[str, Optional[str]]:
        """Read several alias targets from one consistent Qdrant response."""
        requested = list(dict.fromkeys(name for name in alias_names if name))
        aliases = {
            alias.alias_name: alias.collection_name
            for alias in self.client.get_aliases().aliases
        }
        return {name: aliases.get(name) for name in requested}

    def atomic_assign_aliases(
        self,
        assignments: Mapping[str, Optional[str]],
    ) -> None:
        """Atomically point a set of aliases at already-validated collections.

        An immutable generation alias and its human-facing aliases must move in
        the same Qdrant transaction.  Reads that bind analysis to a historical
        generation keep using the immutable alias; readable aliases are solely
        the current branch and legacy-project pointers.
        """
        desired = {
            alias_name: collection_name
            for alias_name, collection_name in assignments.items()
            if alias_name
        }
        if not desired:
            return
        current = self.read_alias_targets(list(desired))
        operations = []
        for alias_name, collection_name in desired.items():
            if current.get(alias_name) == collection_name:
                continue
            if current.get(alias_name) is not None:
                operations.append(
                    DeleteAliasOperation(
                        delete_alias=DeleteAlias(alias_name=alias_name)
                    )
                )
            if collection_name is not None:
                operations.append(
                    CreateAliasOperation(
                        create_alias=CreateAlias(
                            alias_name=alias_name,
                            collection_name=collection_name,
                        )
                    )
                )
        if not operations:
            return
        self.client.update_collection_aliases(
            change_aliases_operations=operations
        )
        logger.info(
            "Atomically assigned Qdrant aliases: %s",
            ", ".join(sorted(desired)),
        )
    
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
        """Deprecated safe wrapper retained for internal compatibility.

        Ownership-less cleanup used to delete every sibling pending collection
        at the start of a job. That could destroy a live build in another
        worker, so lifecycle cleanup now belongs to the expiry-aware janitor.
        """
        logger.debug(
            "Skipping ownership-less pending cleanup for %s (target=%s exclude=%s)",
            base_name,
            current_target,
            exclude_name,
        )
        return 0

    def cleanup_expired_pending_collections(
        self,
        *,
        is_operation_active: Callable[[str], bool],
        min_age_seconds: Optional[int] = None,
    ) -> int:
        """Delete only timestamped, non-aliased pending collections with no lease."""
        if min_age_seconds is None:
            min_age_seconds = max(
                300,
                int(os.getenv("RAG_PENDING_COLLECTION_MAX_AGE_SECONDS", "21600")),
            )
        now = int(time.time())
        try:
            aliased_targets = {
                alias.collection_name for alias in self.client.get_aliases().aliases
            }
        except Exception:
            logger.warning("Pending collection janitor could not read aliases")
            return 0

        pattern = re.compile(
            r"_pending_(\d{10})_([a-fA-F0-9]{8,32})_[a-fA-F0-9]{8}$"
        )
        cleaned = 0
        for collection_name in self.get_collection_names():
            match = pattern.search(collection_name)
            if match is None or collection_name in aliased_targets:
                continue
            created_at = int(match.group(1))
            operation_id = match.group(2)
            if now - created_at < min_age_seconds:
                continue
            if is_operation_active(operation_id):
                continue
            logger.info(
                "Cleaning expired pending collection %s operation_id=%s age_seconds=%s",
                collection_name,
                operation_id,
                now - created_at,
            )
            if self.delete_collection(collection_name):
                cleaned += 1
        return cleaned
