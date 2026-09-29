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
@pytest.mark.parametrize("streaming", [False, True])
async def test_review_http_preserves_partial_coverage_in_a_successful_response(streaming):
    result = {
        "status": "partial", "comment": "Reviewed with unavailable dependency source",
        "issues": [], "reviewedHunkIds": ["changed-part"],
        "unresolvedScopes": {"vendor-class": "Vendor declaration unavailable"},
        "diagnostics": ["Unavailable dependency source cannot establish a defect or safety"],
    }
    service = MagicMock()
    service.process_review_request = AsyncMock(return_value={"result": result})
    request = _streaming_request("review_service", service)
    if not streaming:
        request.headers = {"accept": "application/json"}

    response = await review_endpoint(MagicMock(), request)

    if streaming:
        events = await _collect_stream_events(response)
        assert events[-1] == {"type": "final", "result": result}
        assert sum(event["type"] in {"error", "final"} for event in events) == 1
    else:
        assert response.result == result
        assert response.error is None


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


@pytest.mark.asyncio(loop_scope="function")
async def test_idle_stream_heartbeats_preserve_progress_and_terminal_order():
    from api.event_stream import service_event_stream

    release = asyncio.Event()

    async def process(emit):
        await release.wait()
        emit({"type": "status", "state": "reviewing"})
        return {"issues": []}

    stream = service_event_stream(process, queued_message="queued", heartbeat_seconds=0.01)
    assert json.loads(await anext(stream))["state"] == "queued"
    assert json.loads(await asyncio.wait_for(anext(stream), 1))["state"] == "heartbeat"
    release.set()
    events = [json.loads(line) async for line in stream]
    assert events == [
        {"type": "status", "state": "reviewing"},
        {"type": "final", "result": {"issues": []}},
    ]


@pytest.mark.asyncio(loop_scope="function")
async def test_disconnect_after_idle_heartbeat_cancels_and_joins_processor():
    from api.event_stream import service_event_stream

    cancelled = asyncio.Event()

    async def process(_emit):
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    stream = service_event_stream(process, queued_message="queued", heartbeat_seconds=0.01)
    await anext(stream)
    assert json.loads(await anext(stream))["state"] == "heartbeat"
    await stream.aclose()
    assert cancelled.is_set()


@pytest.mark.asyncio(loop_scope="function")
async def test_completion_does_not_wait_for_heartbeat_interval():
    from api.event_stream import service_event_stream

    async def process(_emit):
        return "done"

    async def collect():
        return [json.loads(line) async for line in service_event_stream(
            process, queued_message="queued", heartbeat_seconds=3600,
        )]

    events = await asyncio.wait_for(collect(), 1)
    assert events[-1] == {"type": "final", "result": "done"}
