"""
Shared base class for RAG query service modules.

Provides Qdrant client initialization, collection helpers, VectorStoreIndex
caching, and representation validation.
"""
from typing import Any, Dict, List, Optional
import logging
import threading

from llama_index.core import VectorStoreIndex
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient

from ..models.config import RAGConfig
from ..utils.utils import make_project_namespace
from ..core.embedding_factory import create_embedding_model, get_embedding_model_info
from ..core.index_representation import (
    INDEX_REPRESENTATION_PAYLOAD_KEY,
    index_representation_fingerprint,
    require_compatible_branch_representation,
)

logger = logging.getLogger(__name__)


class RAGQueryBase:
    """Shared infrastructure for all RAG query modules.

    Manages:
    - Qdrant client connection
    - Embedding model initialization
    - VectorStoreIndex caching (thread-safe)
    - Collection/alias existence checks
    - Indexed representation validation
    """

    def __init__(self, config: RAGConfig, plugin_catalog=None):
        self.config = config
        self.plugin_catalog = plugin_catalog
        self.index_representation_fingerprint = (
            index_representation_fingerprint(config)
        )
        self._compatible_branch_cache: set[tuple[str, str]] = set()
        self._plugin_identity_cache: Dict[
            tuple[str, ...], tuple[str, str]
        ] = {}
        self.qdrant_client = QdrantClient(
            url=config.qdrant_url,
            api_key=config.qdrant_api_key or None,
        )

        embed_info = get_embedding_model_info(config)
        logger.info(f"QueryService using embedding provider: {embed_info['provider']} ({embed_info['type']})")
        self.embed_model = create_embedding_model(config)

        self._supports_instructions = config.embedding_supports_instructions

        # Cache for VectorStoreIndex instances — avoids creating new ones per query
        self._index_cache: Dict[str, VectorStoreIndex] = {}
        self._index_cache_lock = threading.Lock()

    def _plugin_identity_compatible(self, metadata: Dict[str, Any]) -> bool:
        """Return whether indexed plugin code matches the running RAG build.

        Branches share one collection and a full reindex intentionally preserves
        points from other branches. Build-content identity is therefore checked
        per result rather than assumed from the active collection alias.
        """
        if (
            self.plugin_catalog is not None
            and metadata.get(INDEX_REPRESENTATION_PAYLOAD_KEY)
            != self.index_representation_fingerprint
        ):
            return False
        if self.plugin_catalog is None:
            return True

        plugin_ids = metadata.get("plugin_ids")
        if not isinstance(plugin_ids, (list, tuple)) or not all(
            isinstance(plugin_id, str) and plugin_id
            for plugin_id in plugin_ids
        ):
            return False
        normalized_ids = tuple(plugin_ids)
        expected = self._plugin_identity_cache.get(normalized_ids)
        if expected is None:
            try:
                expected = (
                    self.plugin_catalog.registry.fingerprint_for(normalized_ids),
                    self.plugin_catalog.implementation_fingerprint(normalized_ids),
                )
            except (KeyError, TypeError, ValueError):
                return False
            self._plugin_identity_cache[normalized_ids] = expected

        return (
            metadata.get("plugin_descriptor_fingerprint") == expected[0]
            and metadata.get("plugin_implementation_fingerprint") == expected[1]
        )

    def _require_compatible_branches(
        self,
        collection_name: str,
        branches: List[str],
    ) -> None:
        """Preflight branch representation once so stale indexes never look empty."""
        for branch in dict.fromkeys(branches):
            cache_key = (collection_name, branch)
            if cache_key in self._compatible_branch_cache:
                continue
            exists = require_compatible_branch_representation(
                self.qdrant_client,
                collection_name,
                branch,
                expected_fingerprint=self.index_representation_fingerprint,
            )
            if exists:
                self._compatible_branch_cache.add(cache_key)

    def _filter_plugin_compatible_points(self, points: List[Any]) -> List[Any]:
        compatible = [
            point
            for point in points
            if self._plugin_identity_compatible(point.payload or {})
        ]
        omitted = len(points) - len(compatible)
        if omitted:
            logger.warning(
                "Discarded %d indexed point(s) with stale or unknown plugin "
                "descriptor/build-content identity",
                omitted,
            )
        return compatible

    def _collection_or_alias_exists(self, name: str) -> bool:
        """Check if a collection or alias with the given name exists."""
        try:
            collections = [c.name for c in self.qdrant_client.get_collections().collections]
            if name in collections:
                return True

            aliases = self.qdrant_client.get_aliases()
            if any(a.alias_name == name for a in aliases.aliases):
                return True

            return False
        except Exception as e:
            logger.warning(f"Error checking collection/alias existence: {e}")
            return False

    def _get_project_collection_name(self, workspace: str, project: str) -> str:
        """Generate collection name for a project (single collection for all branches)."""
        namespace = make_project_namespace(workspace, project)
        return f"{self.config.qdrant_collection_prefix}_{namespace}"

    def _get_or_create_index(self, collection_name: str) -> VectorStoreIndex:
        """Get a cached VectorStoreIndex or create and cache a new one.

        Avoids creating new QdrantVectorStore + VectorStoreIndex objects on every
        query. For PR context requests that fire 10-15 sub-queries, this saves
        significant overhead.
        """
        with self._index_cache_lock:
            if collection_name not in self._index_cache:
                vector_store = QdrantVectorStore(
                    client=self.qdrant_client,
                    collection_name=collection_name
                )
                self._index_cache[collection_name] = VectorStoreIndex.from_vector_store(
                    vector_store=vector_store,
                    embed_model=self.embed_model
                )
            return self._index_cache[collection_name]
