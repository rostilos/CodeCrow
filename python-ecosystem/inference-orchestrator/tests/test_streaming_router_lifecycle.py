import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.routers.commands import ask_endpoint, summarize_endpoint
from api.routers.review import review_endpoint


def _streaming_request(service_name, service):
    state = SimpleNamespace(**{service_name: service})
    return SimpleNamespace(
        headers={"accept": "application/x-ndjson"},
        app=SimpleNamespace(state=state),
    )


async def _assert_disconnect_cancels_runner(response, cancelled):
    stream = response.body_iterator
    queued = await anext(stream)
    assert '"state": "queued"' in queued

    progress = await anext(stream)
    assert '"state": "working"' in progress

    await stream.aclose()
    await asyncio.wait_for(cancelled.wait(), timeout=1)


async def _collect_stream_events(response):
    return [json.loads(line) async for line in response.body_iterator]


@pytest.mark.asyncio(loop_scope="function")
async def test_review_stream_disconnect_cancels_service_runner():
    cancelled = asyncio.Event()
    service = MagicMock()

    async def process(_request, event_callback):
        event_callback({"type": "status", "state": "working"})
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    service.process_review_request = AsyncMock(side_effect=process)
    response = await review_endpoint(
        MagicMock(),
        _streaming_request("review_service", service),
    )

    await _assert_disconnect_cancels_runner(response, cancelled)


@pytest.mark.asyncio(loop_scope="function")
async def test_review_stream_emits_one_terminal_service_error():
    service = MagicMock()

    async def process(_request, event_callback):
        event_callback({"type": "error", "message": "provider failed"})
        event_callback({"type": "status", "state": "late_diagnostic"})
        return {"result": {"status": "error", "message": "provider failed"}}

    service.process_review_request = AsyncMock(side_effect=process)
    response = await review_endpoint(
        MagicMock(),
        _streaming_request("review_service", service),
    )

    events = await _collect_stream_events(response)
    assert events[-1] == {"type": "error", "message": "provider failed"}
    assert sum(event["type"] in {"error", "final"} for event in events) == 1


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(
    ("endpoint", "method_name"),
    (
        (summarize_endpoint, "process_summarize"),
        (ask_endpoint, "process_ask"),
    ),
)
async def test_command_stream_disconnect_cancels_service_runner(
    endpoint,
    method_name,
):
    cancelled = asyncio.Event()
    service = MagicMock()

    async def process(_request, event_callback):
        event_callback({"type": "status", "state": "working"})
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    setattr(service, method_name, AsyncMock(side_effect=process))
    response = await endpoint(
        MagicMock(),
        _streaming_request("command_service", service),
    )

    await _assert_disconnect_cancels_runner(response, cancelled)


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize(
    ("endpoint", "method_name"),
    (
        (summarize_endpoint, "process_summarize"),
        (ask_endpoint, "process_ask"),
    ),
)
async def test_command_stream_emits_one_terminal_service_error(
    endpoint,
    method_name,
):
    service = MagicMock()

    async def process(_request, event_callback):
        event_callback({"type": "error", "message": "provider failed"})
        event_callback({"type": "status", "state": "late_diagnostic"})
        return {"error": "provider failed"}

    setattr(service, method_name, AsyncMock(side_effect=process))
    response = await endpoint(
        MagicMock(),
        _streaming_request("command_service", service),
    )

    events = await _collect_stream_events(response)
    assert events[-1] == {"type": "error", "message": "provider failed"}
    assert sum(event["type"] in {"error", "final"} for event in events) == 1
