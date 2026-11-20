"""Bridge synchronous domain callbacks to ordered asynchronous publications."""
import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any


class OrderedJobEvents:
    """Publish through one drain task, with a single terminal event per job.

    Events emitted while Redis is slow wait in a deque instead of retaining a
    task and the entire predecessor-task chain for every progress notification.
    Callers await ``drain`` before releasing their job and Redis connection.
    """

    def __init__(
        self,
        publish: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        logger: logging.Logger,
        job_id: str,
    ):
        self._publish = publish
        self._logger = logger
        self._job_id = job_id
        self._pending: deque[dict[str, Any]] = deque()
        self._task: asyncio.Task | None = None
        self._terminal_type: str | None = None

    def emit(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if self._terminal_type is not None:
            self._logger.debug(
                "Ignoring event type=%s after terminal type=%s for job=%s",
                event_type, self._terminal_type, self._job_id,
            )
            return
        if event_type in {"final", "error"}:
            self._terminal_type = event_type
        self._pending.append(dict(event))
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._publish_pending())

    async def _publish_pending(self) -> None:
        while self._pending:
            await self._publish(self._pending.popleft())

    async def drain(self) -> None:
        if self._task is not None:
            await self._task
