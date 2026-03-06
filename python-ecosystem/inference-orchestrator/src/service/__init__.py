"""
Service Package.

Contains business logic services organized into subpackages:
- review: Code review functionality (ReviewService, orchestrator)
- rag: RAG pipeline client and reranking
- command: Command handling (summarize, ask)
"""

# Re-export from subpackages for backward compatibility
from service.review import (
    ReviewService,
    MultiStageReviewOrchestrator,
    post_process_analysis_result,
)
from service.rag import (
    RagClient,
    RAG_MIN_RELEVANCE_SCORE,
    RAG_DEFAULT_TOP_K,
    LLMReranker,
    RerankResult,
)
from service.command import CommandService

__all__ = [
    # Review
    "ReviewService",
    "MultiStageReviewOrchestrator",
    "post_process_analysis_result",
    # RAG
    "RagClient",
    "RAG_MIN_RELEVANCE_SCORE",
    "RAG_DEFAULT_TOP_K",
    "LLMReranker",
    "RerankResult",
    # Command
    "CommandService",
]
