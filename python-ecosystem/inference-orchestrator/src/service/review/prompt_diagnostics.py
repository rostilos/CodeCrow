"""Review-scoped prompt-assembly diagnostics with async task isolation."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator, Mapping, Optional


_PROMPT_DIAGNOSTIC_SINK: ContextVar[
    Optional[Callable[[dict[str, Any]], None]]
] = ContextVar("codecrow_prompt_diagnostic_sink", default=None)


@contextmanager
def capture_prompt_diagnostics(
    sink: Callable[[dict[str, Any]], None],
) -> Iterator[None]:
    token = _PROMPT_DIAGNOSTIC_SINK.set(sink)
    try:
        yield
    finally:
        _PROMPT_DIAGNOSTIC_SINK.reset(token)


def record_prompt_diagnostic(diagnostic: Mapping[str, Any]) -> None:
    sink = _PROMPT_DIAGNOSTIC_SINK.get()
    if sink is not None:
        sink(dict(diagnostic))
