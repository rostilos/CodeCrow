"""Shared NDJSON event stream with request-owned processing lifetime."""
import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any


async def service_event_stream(
    process: Callable[[Callable[[dict[str, Any]], None]], Awaitable[Any]],
    *,
    queued_message: str,
    result_value: Callable[[Any], Any] = lambda value: value,
) -> AsyncIterator[str]:
    """Deliver ordered progress and exactly one terminal event.

    Completion uses an explicit sentinel instead of polling or adding a fixed
    delay to every completed request. Closing the stream cancels and joins its
    processor before shared application clients can be closed.
    """
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    terminal = False

    def emit(event: dict[str, Any]) -> None:
        nonlocal terminal
        if terminal:
            return
        terminal = event.get("type") in {"final", "error"}
        queue.put_nowait(dict(event))

    async def run() -> None:
        try:
            result = await process(emit)
            emit({"type": "final", "result": result_value(result)})
        except Exception as error:
            emit({"type": "error", "message": str(error)})
        finally:
            queue.put_nowait(None)

    yield json.dumps({"type": "status", "state": "queued", "message": queued_message}) + "\n"
    task = asyncio.create_task(run())
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            yield json.dumps(event) + "\n"
    finally:
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def wants_streaming(request: Any) -> bool:
    return "application/x-ndjson" in request.headers.get("accept", "").lower()
