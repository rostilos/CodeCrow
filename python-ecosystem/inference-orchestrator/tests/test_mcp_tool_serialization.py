import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from utils.mcp_tool_serialization import (
    DEFAULT_MCP_TOOL_CALL_TIMEOUT_SECONDS,
    McpToolCallTimeoutError,
    PerConnectionToolCallMiddleware,
    install_per_connection_tool_serialization,
)


def _context(*, request_id: str, connection_id: str, method: str = "tools/call"):
    return SimpleNamespace(
        id=request_id,
        connection_id=connection_id,
        method=method,
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_tool_calls_on_one_connection_are_serialized():
    middleware = PerConnectionToolCallMiddleware()
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()

    async def call_next(context):
        if context.id == "first":
            first_entered.set()
            await release_first.wait()
        else:
            second_entered.set()
        return context.id

    first = asyncio.create_task(
        middleware(
            _context(request_id="first", connection_id="vcs"),
            call_next,
        )
    )
    await first_entered.wait()
    second = asyncio.create_task(
        middleware(
            _context(request_id="second", connection_id="vcs"),
            call_next,
        )
    )
    await asyncio.sleep(0)

    assert not second_entered.is_set()

    release_first.set()
    assert await asyncio.gather(first, second) == ["first", "second"]
    assert second_entered.is_set()


@pytest.mark.asyncio(loop_scope="function")
async def test_separate_mcp_connections_remain_parallel():
    middleware = PerConnectionToolCallMiddleware()
    both_entered = asyncio.Event()
    release = asyncio.Event()
    entered: set[str] = set()

    async def call_next(context):
        entered.add(context.connection_id)
        if len(entered) == 2:
            both_entered.set()
        await release.wait()
        return context.connection_id

    vcs = asyncio.create_task(
        middleware(
            _context(request_id="vcs", connection_id="vcs"),
            call_next,
        )
    )
    rag = asyncio.create_task(
        middleware(
            _context(request_id="rag", connection_id="rag"),
            call_next,
        )
    )

    await asyncio.wait_for(both_entered.wait(), timeout=1)
    release.set()

    assert await asyncio.gather(vcs, rag) == ["vcs", "rag"]


@pytest.mark.asyncio(loop_scope="function")
async def test_non_tool_requests_are_not_serialized():
    middleware = PerConnectionToolCallMiddleware()
    context = _context(
        request_id="list",
        connection_id="vcs",
        method="tools/list",
    )

    async def call_next(received):
        return received.id

    assert await middleware(context, call_next) == "list"
    assert middleware._connection_locks == {}


@pytest.mark.asyncio(loop_scope="function")
async def test_queued_tool_call_has_a_total_operation_deadline():
    middleware = PerConnectionToolCallMiddleware(
        operation_timeout_seconds=0.01,
    )
    lock = asyncio.Lock()
    await lock.acquire()
    middleware._connection_locks["vcs"] = lock
    call_entered = asyncio.Event()

    async def call_next(context):
        call_entered.set()
        return context.id

    try:
        with pytest.raises(
            McpToolCallTimeoutError,
            match=(
                "total serialized-operation timeout of 0.01 seconds "
                "for connection 'vcs'"
            ),
        ):
            await asyncio.wait_for(
                middleware(
                    _context(request_id="queued", connection_id="vcs"),
                    call_next,
                ),
                timeout=1,
            )
    finally:
        lock.release()

    assert not call_entered.is_set()
    assert await middleware(
        _context(request_id="after-timeout", connection_id="vcs"),
        call_next,
    ) == "after-timeout"


@pytest.mark.asyncio(loop_scope="function")
async def test_running_tool_call_uses_the_same_total_operation_deadline():
    middleware = PerConnectionToolCallMiddleware(
        operation_timeout_seconds=0.01,
    )
    call_entered = asyncio.Event()
    never_finishes = asyncio.Event()

    async def call_next(context):
        call_entered.set()
        await never_finishes.wait()
        return context.id

    with pytest.raises(McpToolCallTimeoutError):
        await asyncio.wait_for(
            middleware(
                _context(request_id="running", connection_id="vcs"),
                call_next,
            ),
            timeout=1,
        )

    assert call_entered.is_set()

    async def complete_next(context):
        return context.id

    assert await middleware(
        _context(request_id="after-cancel", connection_id="vcs"),
        complete_next,
    ) == "after-cancel"


@pytest.mark.parametrize(
    "configured",
    ["invalid", "0", "-1", "nan", "inf"],
)
def test_invalid_nonfinite_or_non_positive_timeout_uses_positive_default(
    monkeypatch,
    configured,
):
    monkeypatch.setenv("MCP_TOOL_CALL_TIMEOUT_SECONDS", configured)

    middleware = PerConnectionToolCallMiddleware()

    assert (
        middleware.operation_timeout_seconds
        == DEFAULT_MCP_TOOL_CALL_TIMEOUT_SECONDS
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_inner_transport_timeout_is_not_relabelled_as_queue_timeout():
    middleware = PerConnectionToolCallMiddleware(
        operation_timeout_seconds=1,
    )

    async def call_next(context):
        raise TimeoutError("transport response timed out")

    with pytest.raises(TimeoutError, match="transport response timed out") as error:
        await middleware(
            _context(request_id="transport", connection_id="vcs"),
            call_next,
        )

    assert not isinstance(error.value, McpToolCallTimeoutError)


def test_installer_adds_one_request_scoped_middleware():
    client = MagicMock()

    assert install_per_connection_tool_serialization(
        client,
        operation_timeout_seconds=7.5,
    ) is client

    client.add_middleware.assert_called_once()
    middleware = client.add_middleware.call_args.args[0]
    assert isinstance(middleware, PerConnectionToolCallMiddleware)
    assert middleware.operation_timeout_seconds == 7.5
