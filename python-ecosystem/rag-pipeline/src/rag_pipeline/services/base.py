"""
Shared base class for RAG query service modules.

Provides Qdrant client initialization, collection helpers, VectorStoreIndex
caching, and fallback branch resolution.
"""
from typing import Dict, List, Optional
import logging
import threading

from llama_index.core import VectorStoreIndex
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.http.models import Filter, FieldCondition, MatchValue

from ..models.config import RAGConfig
from ..utils.utils import make_project_namespace
from ..core.embedding_factory import create_embedding_model, get_embedding_model_info

logger = logging.getLogger(__name__)


class RAGQueryBase:
    """Shared infrastructure for all RAG query modules.

    Manages:
    - Qdrant client connection
    - Embedding model initialization
    - VectorStoreIndex caching (thread-safe)
    - Collection/alias existence checks
    - Fallback branch resolution
    """

    def __init__(self, config: RAGConfig):
        self.config = config
        self.qdrant_client = QdrantClient(url=config.qdrant_url)

        embed_info = get_embedding_model_info(config)
        logger.info(f"QueryService using embedding provider: {embed_info['provider']} ({embed_info['type']})")
        self.embed_model = create_embedding_model(config)

        self._supports_instructions = config.embedding_supports_instructions

        # Cache for VectorStoreIndex instances — avoids creating new ones per query
        self._index_cache: Dict[str, VectorStoreIndex] = {}
        self._index_cache_lock = threading.Lock()

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

    def _get_fallback_branch(self, workspace: str, project: str, requested_branch: str) -> Optional[str]:
        """Find a fallback branch when requested branch has no data."""
        fallback_branches = self.config.fallback_branches
        collection_name = self._get_project_collection_name(workspace, project)

        if not self._collection_or_alias_exists(collection_name):
            return None

        for fallback in fallback_branches:
            if fallback == requested_branch:
                continue

            try:
                count_result = self.qdrant_client.count(
                    collection_name=collection_name,
                    count_filter=Filter(
                        must=[FieldCondition(key="branch", match=MatchValue(value=fallback))]
                    )
                )
                if count_result.count > 0:
                    logger.info(f"Found fallback branch '{fallback}' with {count_result.count} points")
                    return fallback
            except Exception as e:
                logger.debug(f"Error checking fallback branch '{fallback}': {e}")

        return None
