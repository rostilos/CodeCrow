"""Helpers for transparent wrappers around provider-backed chat models."""

from __future__ import annotations

from typing import Any


def unwrap_llm_delegate(llm: Any) -> Any:
    """Return the provider model hidden behind CodeCrow's transparent wrappers.

    Wrappers expose ``__codecrow_delegate__``.  The bounded loop avoids a bad
    third-party wrapper creating an unbounded delegate cycle.
    """
    current = llm
    seen: set[int] = set()
    for _ in range(8):
        identity = id(current)
        if identity in seen:
            break
        seen.add(identity)
        delegate = getattr(current, "__codecrow_delegate__", None)
        if delegate is None or delegate is current:
            break
        current = delegate
    return current


def llm_class_names(llm: Any) -> set[str]:
    """Return provider class names even when a CodeCrow wrapper is active."""
    delegate = unwrap_llm_delegate(llm)
    return {
        getattr(candidate, "__name__", "")
        for candidate in delegate.__class__.mro()
    }
