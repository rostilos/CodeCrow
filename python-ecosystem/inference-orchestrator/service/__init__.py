"""
Service Package.

Contains business logic services organized into subpackages:
- review: Code review functionality (ReviewService, orchestrator, issue processing)
- rag: RAG pipeline client and reranking
- command: Command handling (summarize, ask)
"""

# Re-export from subpackages for backward compatibility
from service.review import (
    ReviewService,
    MultiStageReviewOrchestrator,
    IssuePostProcessor,
    IssueDeduplicator,
    post_process_analysis_result,
    restore_missing_diffs_from_previous,
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
    "IssuePostProcessor",
    "IssueDeduplicator",
    "post_process_analysis_result",
    "restore_missing_diffs_from_previous",
    # RAG
    "RagClient",
    "RAG_MIN_RELEVANCE_SCORE",
    "RAG_DEFAULT_TOP_K",
    "LLMReranker",
    "RerankResult",
    # Command
    "CommandService",
]
