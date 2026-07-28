"""Multi-stage review orchestration with lazy compatibility exports."""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS = {
    "MultiStageReviewOrchestrator": (
        "service.review.orchestrator.orchestrator",
        "MultiStageReviewOrchestrator",
    ),
    "RecursiveMCPAgent": (
        "service.review.orchestrator.agents",
        "RecursiveMCPAgent",
    ),
    "extract_llm_response_text": (
        "utils.llm_response",
        "extract_llm_response_text",
    ),
    "parse_llm_response": (
        "service.review.orchestrator.json_utils",
        "parse_llm_response",
    ),
    "clean_json_text": (
        "service.review.orchestrator.json_utils",
        "clean_json_text",
    ),
    "reconcile_previous_issues": (
        "service.review.orchestrator.reconciliation",
        "reconcile_previous_issues",
    ),
    "deduplicate_issues": (
        "service.review.orchestrator.reconciliation",
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
