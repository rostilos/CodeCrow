"""
RAG Service Package.

Contains components for RAG (Retrieval Augmented Generation) functionality:
- rag_client: Client for interacting with the RAG Pipeline API
- llm_reranker: LLM-based reranking for RAG results
"""
from service.rag.rag_client import RagClient, RAG_MIN_RELEVANCE_SCORE, RAG_DEFAULT_TOP_K
from service.rag.llm_reranker import LLMReranker, LLM_RERANK_ENABLED, LLM_RERANK_THRESHOLD, RerankResult

__all__ = [
    "RagClient",
    "RAG_MIN_RELEVANCE_SCORE",
    "RAG_DEFAULT_TOP_K",
    "LLMReranker",
    "LLM_RERANK_ENABLED",
    "LLM_RERANK_THRESHOLD",
    "RerankResult",
]
