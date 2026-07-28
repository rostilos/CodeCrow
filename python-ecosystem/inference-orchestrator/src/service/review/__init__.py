"""Review service package with compatibility exports loaded on demand."""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS = {
    "ReviewService": ("service.review.review_service", "ReviewService"),
    "MultiStageReviewOrchestrator": (
        "service.review.orchestrator",
        "MultiStageReviewOrchestrator",
    ),
    "post_process_analysis_result": (
        "service.review.issue_processor",
        "post_process_analysis_result",
    ),
    "RecursiveMCPAgent": (
        "service.review.orchestrator",
        "RecursiveMCPAgent",
    ),
    "extract_llm_response_text": (
        "service.review.orchestrator",
        "extract_llm_response_text",
    ),
    "parse_llm_response": (
        "service.review.orchestrator",
        "parse_llm_response",
    ),
    "clean_json_text": (
        "service.review.orchestrator",
        "clean_json_text",
    ),
    "reconcile_previous_issues": (
        "service.review.orchestrator",
        "reconcile_previous_issues",
    ),
    "deduplicate_issues": (
        "service.review.orchestrator",
        "deduplicate_issues",
    ),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
