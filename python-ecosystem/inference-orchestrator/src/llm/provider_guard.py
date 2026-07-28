"""Task-local guard for executions that must never construct a review provider."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional


_PROVIDER_CONSTRUCTION_BLOCK_REASON: ContextVar[Optional[str]] = ContextVar(
    "codecrow_provider_construction_block_reason",
    default=None,
)


@contextmanager
def forbid_llm_provider_construction(reason: str) -> Iterator[None]:
    """Fail closed if this async execution path tries to create an LLM client."""
    normalized = str(reason).strip()
    if not normalized:
        raise ValueError("provider construction block reason must be non-blank")
    token = _PROVIDER_CONSTRUCTION_BLOCK_REASON.set(normalized)
    try:
        yield
    finally:
        _PROVIDER_CONSTRUCTION_BLOCK_REASON.reset(token)


def provider_construction_block_reason() -> Optional[str]:
    """Return the active task-local block reason, if provider creation is forbidden."""
    return _PROVIDER_CONSTRUCTION_BLOCK_REASON.get()
