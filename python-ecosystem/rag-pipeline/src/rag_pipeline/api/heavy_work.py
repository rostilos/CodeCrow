"""Isolate blocking index/preparation work from FastAPI query threads.

Queued heavy operations wait asynchronously for their own admission permit.
They cannot exhaust the default AnyIO limiter used by sealed graph queries.
"""
from __future__ import annotations

from functools import partial
from typing import Any, Callable

import anyio
from anyio.lowlevel import RunVar

from ..models.config import DEFAULT_FULL_INDEX_CONCURRENCY

_HEAVY_LIMITER: RunVar[anyio.CapacityLimiter] = RunVar("rag_heavy_operation_limiter")
_BUILD_WORKERS = None


def configure_build_workers(config) -> None:
    """Create lifecycle-owned process resources; children start on demand."""
    global _BUILD_WORKERS
    from .build_workers import BuildWorkerPool
    _BUILD_WORKERS = BuildWorkerPool(config)


def get_build_workers():
    return _BUILD_WORKERS


async def close_build_workers() -> None:
    global _BUILD_WORKERS
    workers = _BUILD_WORKERS
    try:
        if workers is not None:
            await workers.close()
    finally:
        _BUILD_WORKERS = None


def configure_heavy_operations(capacity: int) -> None:
    """Own one limiter per application event loop, including test lifespans."""
    _HEAVY_LIMITER.set(anyio.CapacityLimiter(max(1, capacity)))


async def run_heavy_operation(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Offload one heavy operation without borrowing the query thread budget.

    Cancellation while queued never starts the operation. Once admitted, the
    default non-abandoning thread behavior waits for completion before a caller
    can release its repository snapshot or the application closes the manager.
    """
    try:
        limiter = _HEAVY_LIMITER.get()
    except LookupError:
        # Direct ASGI embeddings without the normal lifespan still retain a
        # separate, bounded pool. Normal service startup supplies its config.
        configure_heavy_operations(DEFAULT_FULL_INDEX_CONCURRENCY)
        limiter = _HEAVY_LIMITER.get()
    return await anyio.to_thread.run_sync(partial(function, *args, **kwargs), limiter=limiter)
