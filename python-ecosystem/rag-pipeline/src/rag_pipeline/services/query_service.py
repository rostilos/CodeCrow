"""
RAG Query Service — facade class.

Composes the decomposed modules (base, semantic search, deterministic context,
PR context) into a single class that preserves the original public API.

All callers continue to import ``RAGQueryService`` unchanged.
"""
from ..models.config import RAGConfig
from .base import RAGQueryBase
from .semantic_search import SemanticSearchMixin
from .deterministic_context import DeterministicContextMixin
from .pr_context import PRContextMixin


class RAGQueryService(
    SemanticSearchMixin,
    DeterministicContextMixin,
    PRContextMixin,
    RAGQueryBase
):
    """Service for querying RAG indices using Qdrant.

    Uses single-collection-per-project architecture with branch metadata filtering.
    Supports multi-branch queries for PR reviews (base + target branches).

    This is a facade that composes:
    - RAGQueryBase: Qdrant client, index caching, collection helpers
    - SemanticSearchMixin: single/multi-branch semantic search
    - DeterministicContextMixin: metadata-based deterministic retrieval
    - PRContextMixin: PR review context with query decomposition and ranking
    """

    def __init__(self, config: RAGConfig):
        super().__init__(config)
