"""Offline scheduling checks; model inputs and review decisions are unchanged."""
import asyncio
from contextlib import contextmanager
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from service.review.agent_calls import ReviewAgentSession
from service.review.execution_scheduler import (
    FairReviewScheduler, review_context_slot, review_execution, review_model_slot,
)
from service.review.model_calls import invoke_json
from service.review.review_service import ReviewService


async def turn():
    # Yield deterministically so newly created tasks reach their capacity wait.
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_busy_review_cannot_put_all_queued_calls_ahead_of_new_review():
    scheduler = FairReviewScheduler(1)
    large, small = object(), object()
    order = []
    release = asyncio.Event()
    first_started = asyncio.Event()

    async def call(owner, label):
        async with scheduler.slot(owner):
            order.append(label)
            if label == "large-0":
                first_started.set()
                await release.wait()

    tasks = [asyncio.create_task(call(large, "large-0"))]
    await first_started.wait()
    tasks.extend(asyncio.create_task(call(large, f"large-{index}")) for index in range(1, 100))
    await turn()
    tiny = asyncio.create_task(call(small, "small"))
    await turn()
    assert order == ["large-0"]
    release.set()
    await asyncio.wait_for(asyncio.gather(*tasks, tiny), 2)
    assert order[:3] == ["large-0", "large-1", "small"]
    assert len(order) == 101
    assert scheduler._active == 0
    assert not scheduler._pending


@pytest.mark.asyncio
async def test_cancellation_returns_queued_assigned_and_active_permits():
    scheduler = FairReviewScheduler(1)
    blocker = scheduler.slot(object())
    await blocker.__aenter__()

    async def call():
        async with scheduler.slot(object()):
            await asyncio.Event().wait()

    queued = asyncio.create_task(call())
    await turn()
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    assert scheduler._active == 1
    assert not scheduler._pending

    assigned = asyncio.create_task(call())
    await turn()
    await blocker.__aexit__(None, None, None)
    assigned.cancel()
    with pytest.raises(asyncio.CancelledError):
        await assigned
    assert scheduler._active == 0

    active = asyncio.create_task(call())
    await turn()
    assert scheduler._active == 1
    active.cancel()
    with pytest.raises(asyncio.CancelledError):
        await active
    assert scheduler._active == 0
    async with scheduler.slot(object()):
        assert scheduler._active == 1


@pytest.mark.asyncio
async def test_source_wait_does_not_occupy_provider_capacity():
    scheduler = FairReviewScheduler(1)
    source_capacity = asyncio.Semaphore(1)
    started = asyncio.Event()
    release = asyncio.Event()
    events = []

    async def slow_source():
        with review_execution(scheduler, source_capacity):
            async with review_context_slot("graph_preparation"):
                started.set()
                await release.wait()

    async def waiting_source():
        with review_execution(scheduler, source_capacity, event_callback=events.append):
            async with review_context_slot("discovery_source"):
                pass

    slow = asyncio.create_task(slow_source())
    await started.wait()
    waiting = asyncio.create_task(waiting_source())
    await turn()
    assert events[0]["state"] == "waiting_for_capacity"
    with review_execution(scheduler, source_capacity):
        async with review_model_slot("verification_validate"):
            assert not slow.done()
            assert not waiting.done()
    release.set()
    await asyncio.wait_for(asyncio.gather(slow, waiting), 1)
    assert events[-1]["state"] == "capacity_acquired"


@pytest.mark.asyncio
async def test_tiny_review_runs_while_another_review_waits_between_model_calls(monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_REVIEWS", "1")
    monkeypatch.delenv("MAX_CONCURRENT_REVIEW_CALLS", raising=False)
    service = ReviewService(rag_client=SimpleNamespace(enabled=False))
    waiting_for_source = asyncio.Event()
    release_source = asyncio.Event()
    model = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content='{"ok":true}')))

    async def review(request, callback):
        if request.pullRequestId == "large":
            await invoke_json(model, request, stage="discovery", system="unchanged", payload={"pr": "large"})
            waiting_for_source.set()
            await release_source.wait()
        result = await invoke_json(model, request, stage="verification_validate", system="unchanged", payload={"pr": request.pullRequestId})
        return {"status": "complete", **result}

    monkeypatch.setattr(service, "_review", review)
    slow_request = SimpleNamespace(projectId=7, pullRequestId="large", aiProvider="openai")
    tiny_request = SimpleNamespace(projectId=8, pullRequestId="tiny", aiProvider="openai")
    slow = asyncio.create_task(service.process_review_request(slow_request))
    await waiting_for_source.wait()
    try:
        tiny = await asyncio.wait_for(service.process_review_request(tiny_request), 1)
        assert tiny == {"result": {"status": "complete", "ok": True}}
        assert not slow.done()
    finally:
        release_source.set()
        await slow
    assert model.ainvoke.await_count == 3


@pytest.mark.asyncio
async def test_json_and_native_calls_share_capacity_preserve_inputs_and_capture(monkeypatch, caplog):
    from service.review import agent_calls, model_calls

    scheduler = FairReviewScheduler(1)
    source_capacity = asyncio.Semaphore(1)
    release = asyncio.Event()
    started = asyncio.Event()
    calls = []
    captures = []
    events = []

    @contextmanager
    def capture(request, **metadata):
        captures.append((request.pullRequestId, metadata))
        yield

    monkeypatch.setattr(agent_calls, "model_capture", capture)
    monkeypatch.setattr(model_calls, "model_capture", capture)

    class Model:
        def bind_tools(self, schemas):
            return self

        async def ainvoke(self, messages, **options):
            calls.append((messages, options))
            if len(calls) == 1:
                started.set()
                await release.wait()
            return SimpleNamespace(content='{"decisions":[]}', tool_calls=[], invalid_tool_calls=[])

    model = Model()
    request = SimpleNamespace(aiProvider="openai", pullRequestId="large")
    tiny_request = SimpleNamespace(aiProvider="openai", pullRequestId="tiny")
    messages = [("system", "original instructions"), ("human", "complete source")]
    session = ReviewAgentSession(model, tiny_request, [])
    session.options = {"reasoning_effort": "medium", "custom": {"preserved": True}}

    async def discovery():
        with review_execution(scheduler, source_capacity, project_id=1, pull_request_id="large"):
            return await invoke_json(model, request, stage="discovery", system="original instructions", payload={"source": "complete source"})

    async def native():
        with review_execution(scheduler, source_capacity, project_id=2, pull_request_id="tiny", event_callback=events.append):
            return await session.invoke(messages, stage="verification_validate", batch_ids=["batch-7"])

    with caplog.at_level(logging.INFO):
        first = asyncio.create_task(discovery())
        await started.wait()
        second = asyncio.create_task(native())
        await turn()
        assert len(calls) == 1
        assert len(captures) == 1
        assert [event["state"] for event in events] == ["waiting_for_capacity"]
        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), 1)

    assert calls[0][0] == [("system", "original instructions"), ("human", json.dumps({"source": "complete source"}))]
    assert calls[0][1]["response_format"] == {"type": "json_object"}
    assert calls[1] == (messages, session.options)
    assert captures[1] == ("tiny", {"stage": "verification_validate", "turn": 1, "batch_ids": ["batch-7"]})
    assert [event["state"] for event in events] == ["waiting_for_capacity", "capacity_acquired"]
    assert "queue_wait_ms=" in caplog.text and "duration_ms=" in caplog.text
    assert "original instructions" not in caplog.text


@pytest.mark.asyncio
async def test_uncontended_calls_emit_no_capacity_events_and_exception_releases_slot():
    scheduler = FairReviewScheduler(1)
    events = []
    with review_execution(scheduler, asyncio.Semaphore(1), event_callback=events.append):
        with pytest.raises(RuntimeError, match="provider unavailable"):
            async with review_model_slot("discovery"):
                raise RuntimeError("provider unavailable")
        async with review_model_slot("verification_validate"):
            pass
    assert events == []
    assert scheduler._active == 0
    async with review_model_slot("standalone"):
        assert scheduler._active == 0


def test_model_capacity_uses_existing_setting_unless_explicitly_overridden(monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_REVIEWS", "3")
    monkeypatch.delenv("MAX_CONCURRENT_REVIEW_CALLS", raising=False)
    assert ReviewService(SimpleNamespace())._model_scheduler.capacity == 3
    monkeypatch.setenv("MAX_CONCURRENT_REVIEW_CALLS", "6")
    service = ReviewService(SimpleNamespace())
    assert service._model_scheduler.capacity == 6
    assert service._context_semaphore._value == 3


@pytest.mark.asyncio
async def test_many_requests_share_exact_capacity_and_cancelled_sibling_does_not_leak():
    scheduler = FairReviewScheduler(2)
    owner = object()
    first = scheduler.slot(object())
    second = scheduler.slot(object())
    await first.__aenter__()
    await second.__aenter__()
    active = 0
    maximum = 0

    async def call():
        nonlocal active, maximum
        async with scheduler.slot(owner):
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0)
            active -= 1

    cancelled = asyncio.create_task(call())
    siblings = [asyncio.create_task(call()) for _ in range(20)]
    await turn()
    cancelled.cancel()
    # Dispatch can remove the cancelled future before its owner resumes the
    # cancellation handler, while that same review still has queued siblings.
    await first.__aexit__(None, None, None)
    await second.__aexit__(None, None, None)
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    await asyncio.wait_for(asyncio.gather(*siblings), 1)
    assert maximum == 2
    assert scheduler._active == 0
    assert not scheduler._pending


def test_review_runtime_defaults_are_shared_and_explicit_lower_values_remain_effective(monkeypatch):
    from service.runtime_capacity import review_concurrency, review_call_concurrency
    from server.queue_consumer import RedisQueueConsumer

    monkeypatch.delenv("MAX_CONCURRENT_REVIEWS", raising=False)
    monkeypatch.delenv("MAX_CONCURRENT_REVIEW_CALLS", raising=False)
    assert review_concurrency() == review_call_concurrency() == 16
    assert ReviewService(SimpleNamespace())._model_scheduler.capacity == 16
    assert RedisQueueConsumer(SimpleNamespace()).max_concurrent == 16
    monkeypatch.setenv("MAX_CONCURRENT_REVIEWS", "3")
    assert review_concurrency() == review_call_concurrency() == 3
    assert ReviewService(SimpleNamespace())._context_semaphore._value == 3
    assert RedisQueueConsumer(SimpleNamespace()).max_concurrent == 3
    monkeypatch.setenv("MAX_CONCURRENT_REVIEW_CALLS", "12")
    assert review_call_concurrency() == 12
    assert review_concurrency() == 3


@pytest.mark.asyncio
async def test_repository_http_pools_follow_concurrency_without_sharing_mutation_slots(monkeypatch):
    from service.rag.transport import RagTransport

    monkeypatch.setenv("MAX_CONCURRENT_REVIEWS", "16")
    transport = RagTransport(enabled=True)
    try:
        query_client = await transport._get_client()
        preparation_client = await transport._get_client(mutation=True)
        assert query_client is not preparation_client
        for client in (query_client, preparation_client):
            assert client._transport._pool._max_connections == 16
            assert client._transport._pool._max_keepalive_connections == 16
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_sixteen_graph_preparations_do_not_block_sixteen_queries_or_model_calls():
    scheduler = FairReviewScheduler(16)
    source_capacity = asyncio.Semaphore(16)
    preparation_capacity = asyncio.Semaphore(16)
    preparation_entered = 0
    queries_entered = 0
    models_entered = 0
    all_preparing = asyncio.Event()
    all_querying = asyncio.Event()
    all_inferencing = asyncio.Event()
    release = asyncio.Event()

    async def preparation():
        nonlocal preparation_entered
        with review_execution(scheduler, source_capacity, preparation_semaphore=preparation_capacity):
            async with review_context_slot("graph_preparation", preparation=True):
                preparation_entered += 1
                if preparation_entered == 16:
                    all_preparing.set()
                await release.wait()

    async def query():
        nonlocal queries_entered
        with review_execution(scheduler, source_capacity, preparation_semaphore=preparation_capacity):
            async with review_context_slot("verification_source"):
                queries_entered += 1
                if queries_entered == 16:
                    all_querying.set()
                await release.wait()

    async def inference():
        nonlocal models_entered
        with review_execution(scheduler, source_capacity, preparation_semaphore=preparation_capacity):
            async with review_model_slot("verification_validate"):
                models_entered += 1
                if models_entered == 16:
                    all_inferencing.set()
                await release.wait()

    tasks = [asyncio.create_task(preparation()) for _ in range(16)]
    try:
        await asyncio.wait_for(all_preparing.wait(), 1)
        tasks.extend(asyncio.create_task(query()) for _ in range(16))
        tasks.extend(asyncio.create_task(inference()) for _ in range(16))
        await asyncio.wait_for(asyncio.gather(all_querying.wait(), all_inferencing.wait()), 1)
        assert not any(task.done() for task in tasks)
        assert scheduler._active == 16
        assert preparation_capacity.locked() and source_capacity.locked()
    finally:
        release.set()
        await asyncio.gather(*tasks)
