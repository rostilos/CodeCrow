"""Serialize MCP tool calls per connection without reducing job parallelism."""

import asyncio
import math
import os
from typing import Any


DEFAULT_MCP_TOOL_CALL_TIMEOUT_SECONDS = 120.0


class McpToolCallTimeoutError(TimeoutError):
    """Raised when one serialized MCP operation exceeds its total deadline."""


def _tool_call_timeout_seconds(configured: float | None = None) -> float:
    value: Any = (
        os.environ.get("MCP_TOOL_CALL_TIMEOUT_SECONDS")
        if configured is None
        else configured
    )
    try:
        timeout_seconds = float(value)
    except (TypeError, ValueError):
        return DEFAULT_MCP_TOOL_CALL_TIMEOUT_SECONDS
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        return DEFAULT_MCP_TOOL_CALL_TIMEOUT_SECONDS
    return timeout_seconds


class PerConnectionToolCallMiddleware:
    """Keep concurrent tool responses from contending on one MCP transport."""

    def __init__(self, operation_timeout_seconds: float | None = None) -> None:
        self._connection_locks: dict[str, asyncio.Lock] = {}
        self.operation_timeout_seconds = _tool_call_timeout_seconds(
            operation_timeout_seconds
        )

    async def __call__(self, context: Any, call_next: Any) -> Any:
        if getattr(context, "method", None) != "tools/call":
            return await call_next(context)

        connection_id = str(getattr(context, "connection_id", ""))
        lock = self._connection_locks.setdefault(
            connection_id,
            asyncio.Lock(),
        )
        deadline = asyncio.timeout(self.operation_timeout_seconds)
        try:
            # The deadline deliberately starts before lock acquisition. The
            # MCP SDK's response timeout begins only after a queued operation
            # reaches the transport, which otherwise leaves lock wait time
            # unbounded.
            async with deadline:
                async with lock:
                    return await call_next(context)
        except TimeoutError as exception:
            if not deadline.expired():
                raise
            raise McpToolCallTimeoutError(
                "MCP tool call exceeded its total serialized-operation "
                f"timeout of {self.operation_timeout_seconds:g} seconds "
                f"for connection {connection_id!r}"
            ) from exception


def install_per_connection_tool_serialization(
        client: Any,
        *,
        operation_timeout_seconds: float | None = None,
) -> Any:
    """Install one request-scoped serializer before MCP sessions are created."""
    client.add_middleware(PerConnectionToolCallMiddleware(
        operation_timeout_seconds=operation_timeout_seconds,
    ))
    return client
