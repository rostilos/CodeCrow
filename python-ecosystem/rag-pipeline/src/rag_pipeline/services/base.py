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
    index_representation_fingerprint,
    observe_branch_representation,
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
        self._observed_branch_cache: set[tuple[str, str]] = set()
        self.qdrant_client = QdrantClient(
            url=config.qdrant_url,
            api_key=config.qdrant_api_key or None,
        )

        embed_info = get_embedding_model_info(config)
        logger.info(f"QueryService using embedding provider: {embed_info['provider']} ({embed_info['type']})")
        self.embed_model = create_embedding_model(config, workload="query")

        self._supports_instructions = config.embedding_supports_instructions

        # Cache for VectorStoreIndex instances — avoids creating new ones per query
        self._index_cache: Dict[str, VectorStoreIndex] = {}
        self._index_cache_lock = threading.Lock()

    def _observe_branches(
        self,
        collection_name: str,
        branches: List[str],
    ) -> None:
        """Confirm branch presence; build fingerprints are provenance only."""
        for branch in dict.fromkeys(branches):
            cache_key = (collection_name, branch)
            if cache_key in self._observed_branch_cache:
                continue
            exists = observe_branch_representation(
                self.qdrant_client,
                collection_name,
                branch,
                expected_fingerprint=self.index_representation_fingerprint,
            )
            if exists:
                self._observed_branch_cache.add(cache_key)

    def _accept_stored_points(self, points: List[Any]) -> List[Any]:
        """Return stored points without build-identity eligibility filtering."""
        return list(points)

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
