"""Inference service package with compatibility exports loaded on demand.

Leaf modules are used by provider-free quality tooling. Importing one must not
initialize unrelated MCP, RAG, or command dependencies.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS = {
    "ReviewService": ("service.review", "ReviewService"),
    "MultiStageReviewOrchestrator": (
        "service.review",
        "MultiStageReviewOrchestrator",
    ),
    "post_process_analysis_result": (
        "service.review",
        "post_process_analysis_result",
    ),
    "RagClient": ("service.rag", "RagClient"),
    "CommandService": ("service.command", "CommandService"),
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
