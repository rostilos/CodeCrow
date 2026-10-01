"""Request-fair admission for review model calls and shared source work.

A review owns its transcript, not a provider permit. Capacity is released after
one invocation so long investigations cannot exclude other admitted reviews.
"""
from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import logging
import time
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)


class FairReviewScheduler:
    """Round-robin pending requests while bounding actual model invocations."""

    def __init__(self, capacity: int):
        if capacity < 1:
            raise ValueError("Review model-call capacity must be positive")
        self.capacity = capacity
        self._active = 0
        self._pending: dict[object, deque[asyncio.Future[None]]] = {}
        self._ready: deque[object] = deque()

    def _dispatch(self) -> None:
        while self._active < self.capacity and self._ready:
            owner = self._ready.popleft()
            pending = self._pending[owner]
            future = pending.popleft()
            if pending:
                self._ready.append(owner)
            else:
                del self._pending[owner]
            if future.cancelled():
                continue
            self._active += 1
            future.set_result(None)

    @asynccontextmanager
    async def slot(self, owner: object, on_wait: Callable[[], None] | None = None):
        future = asyncio.get_running_loop().create_future()
        if owner not in self._pending:
            self._pending[owner] = deque()
            self._ready.append(owner)
        self._pending[owner].append(future)
        self._dispatch()
        try:
            if not future.done() and on_wait:
                on_wait()
            await future
        except BaseException:
            # Cancellation may arrive after a permit was assigned but before
            # this task resumes. Return it instead of leaking provider capacity.
            if future.done() and not future.cancelled():
                self._active -= 1
            else:
                future.cancel()
                pending = self._pending.get(owner)
                if pending is not None and future in pending:
                    pending.remove(future)
                    if not pending:
                        del self._pending[owner]
                        self._ready.remove(owner)
            self._dispatch()
            raise
        try:
            yield
        finally:
            self._active -= 1
            self._dispatch()


@dataclass(frozen=True)
class _ReviewExecution:
    scheduler: FairReviewScheduler
    source_semaphore: asyncio.Semaphore
    preparation_semaphore: asyncio.Semaphore
    event_callback: Callable[[dict[str, Any]], None] | None
    project_id: Any
    pull_request_id: Any
    owner: object = field(default_factory=object)


_EXECUTION: ContextVar[_ReviewExecution | None] = ContextVar("review_execution", default=None)


@contextmanager
def review_execution(
    scheduler: FairReviewScheduler, source_semaphore: asyncio.Semaphore, *,
    event_callback: Callable[[dict[str, Any]], None] | None = None,
    preparation_semaphore: asyncio.Semaphore | None = None,
    project_id: Any = None, pull_request_id: Any = None,
) -> Iterator[None]:
    """Children inherit one review identity without changing model objects."""
    token = _EXECUTION.set(_ReviewExecution(
        scheduler, source_semaphore, preparation_semaphore or source_semaphore,
        event_callback, project_id, pull_request_id,
    ))
    try:
        yield
    finally:
        _EXECUTION.reset(token)


def _capacity_event(execution: _ReviewExecution, *, stage: str, resource: str,
                    acquired: bool, wait_ms: float = 0.0) -> None:
    if execution.event_callback:
        label = {"model-call": "inference", "source": "repository context",
                 "preparation": "repository preparation"}[resource]
        execution.event_callback({
            "type": "status",
            "state": "capacity_acquired" if acquired else "waiting_for_capacity",
            "stage": stage,
            "resource": resource,
            "message": (f"{label.capitalize()} processing resumed after {wait_ms / 1000:.1f}s waiting"
                        if acquired else f"Waiting for {label} capacity"),
        })


@asynccontextmanager
async def review_model_slot(stage: str):
    """Schedule exactly one existing provider invocation, preserving its input."""
    execution = _EXECUTION.get()
    if execution is None:
        yield
        return
    queued_at = time.perf_counter()
    waited = False

    def on_wait() -> None:
        nonlocal waited
        waited = True
        _capacity_event(execution, stage=stage, resource="model-call", acquired=False)

    async with execution.scheduler.slot(execution.owner, on_wait):
        started = time.perf_counter()
        wait_ms = (started - queued_at) * 1000
        logger.info("Review model call acquired: project=%s PR=%s stage=%s queue_wait_ms=%.1f",
                    execution.project_id, execution.pull_request_id, stage, wait_ms)
        if waited:
            _capacity_event(execution, stage=stage, resource="model-call", acquired=True, wait_ms=wait_ms)
        outcome = "completed"
        try:
            yield
        except BaseException as error:
            outcome = type(error).__name__
            raise
        finally:
            logger.info("Review model call finished: project=%s PR=%s stage=%s duration_ms=%.1f outcome=%s",
                        execution.project_id, execution.pull_request_id, stage,
                        (time.perf_counter() - started) * 1000, outcome)


@asynccontextmanager
async def review_context_slot(stage: str, *, preparation: bool = False):
    """Keep graph preparation, source queries and provider work independent."""
    execution = _EXECUTION.get()
    if execution is None:
        yield
        return
    queued_at = time.perf_counter()
    semaphore = execution.preparation_semaphore if preparation else execution.source_semaphore
    resource = "preparation" if preparation else "source"
    waited = semaphore.locked()
    if waited:
        _capacity_event(execution, stage=stage, resource=resource, acquired=False)
    async with semaphore:
        started = time.perf_counter()
        wait_ms = (started - queued_at) * 1000
        logger.info("Review context acquired: project=%s PR=%s stage=%s queue_wait_ms=%.1f",
                    execution.project_id, execution.pull_request_id, stage, wait_ms)
        if waited:
            _capacity_event(execution, stage=stage, resource=resource, acquired=True, wait_ms=wait_ms)
        try:
            yield
        finally:
            logger.info("Review context finished: project=%s PR=%s stage=%s duration_ms=%.1f",
                        execution.project_id, execution.pull_request_id, stage,
                        (time.perf_counter() - started) * 1000)
