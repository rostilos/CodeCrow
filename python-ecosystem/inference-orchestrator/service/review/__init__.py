"""
Review Service Package.

Contains components for code review functionality:
- review_service: Main entry point for review requests
- orchestrator/: Multi-stage review orchestrator (subpackage)
- issue_processor: No-op pass-through (post-processing moved to Java)
"""
from service.review.review_service import ReviewService
from service.review.orchestrator import (
    MultiStageReviewOrchestrator,
    RecursiveMCPAgent,
    extract_llm_response_text,
    parse_llm_response,
    clean_json_text,
    reconcile_previous_issues,
    deduplicate_issues,
)
from service.review.issue_processor import (
    post_process_analysis_result,
)

__all__ = [
    "ReviewService",
    "MultiStageReviewOrchestrator",
    "post_process_analysis_result",
    "RecursiveMCPAgent",
    "extract_llm_response_text",
    "parse_llm_response",
    "clean_json_text",
    "reconcile_previous_issues",
    "deduplicate_issues",
]
