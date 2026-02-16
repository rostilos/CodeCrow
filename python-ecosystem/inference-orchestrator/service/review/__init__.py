"""
Review Service Package.

Contains components for code review functionality:
- review_service: Main entry point for review requests
- orchestrator/: Multi-stage review orchestrator (subpackage)
- issue_processor: Issue post-processing and deduplication
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
    IssuePostProcessor,
    IssueDeduplicator,
    post_process_analysis_result,
    restore_missing_diffs_from_previous,
)

__all__ = [
    "ReviewService",
    "MultiStageReviewOrchestrator",
    "IssuePostProcessor",
    "IssueDeduplicator",
    "post_process_analysis_result",
    "restore_missing_diffs_from_previous",
    "RecursiveMCPAgent",
    "extract_llm_response_text",
    "parse_llm_response",
    "clean_json_text",
    "reconcile_previous_issues",
    "deduplicate_issues",
]
