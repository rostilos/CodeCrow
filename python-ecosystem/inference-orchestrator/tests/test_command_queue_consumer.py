import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.exceptions import TimeoutError as RedisTimeoutError

from server.command_queue_consumer import CommandQueueConsumer


class FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.pending = []

    def lpush(self, key, value):
        self.pending.append(("lpush", key, value))
        return self

    def expire(self, key, ttl):
        self.pending.append(("expire", key, ttl))
        return self

    async def execute(self):
        for operation, key, value in self.pending:
            if operation == "lpush":
                self.redis.events.append((key, json.loads(value)))
            else:
                self.redis.expiries.append((key, value))


class FakeRedis:
    def __init__(self):
        self.events = []
        self.expiries = []

    def pipeline(self):
        return FakePipeline(self)


def _ask_request():
    return {
        "projectId": 1,
        "projectVcsWorkspace": "ws",
        "projectVcsRepoSlug": "repo",
        "projectWorkspace": "workspace",
        "projectNamespace": "namespace",
        "aiProvider": "OPENAI_COMPATIBLE",
        "aiModel": "model",
        "aiApiKey": "key",
        "question": "describe this PR",
        "pullRequestId": 7,
    }


def _summarize_request():
    return {
        "projectId": 1,
        "projectVcsWorkspace": "ws",
        "projectVcsRepoSlug": "repo",
        "projectWorkspace": "workspace",
        "projectNamespace": "namespace",
        "aiProvider": "OPENAI_COMPATIBLE",
        "aiModel": "model",
        "aiApiKey": "key",
        "pullRequestId": 7,
    }


def _payload(command_type, request):
    return json.dumps({
        "job_id": f"job-{command_type}",
        "command_type": command_type,
        "request": request,
    })


def _consumer(command_service):
    consumer = CommandQueueConsumer(command_service)
    consumer._redis = FakeRedis()
    return consumer


async def _handle_and_collect_events(consumer, payload):
    await consumer._handle_job(payload)
    return [event for _, event in consumer._redis.events]


@pytest.mark.asyncio(loop_scope="function")
async def test_error_result_is_published_as_error_without_final():
    command_service = MagicMock()

    async def process(_request, callback):
        callback({"type": "error", "message": "provider failed"})
        callback({"type": "status", "state": "late_diagnostic"})
        return {"error": "provider failed"}

    command_service.process_ask = AsyncMock(side_effect=process)
    consumer = _consumer(command_service)

    events = await _handle_and_collect_events(consumer, _payload("ask", _ask_request()))

    assert events == [
        {
            "type": "status",
            "state": "acknowledged",
            "message": "Orchestrator picked up ask command from queue",
        },
        {"type": "error", "message": "provider failed"},
    ]
    assert sum(event["type"] in {"error", "final"} for event in events) == 1
    assert not any(event["type"] == "final" for event in events)


@pytest.mark.asyncio(loop_scope="function")
async def test_empty_ask_answer_is_published_as_error_without_final():
    command_service = MagicMock()
    command_service.process_ask = AsyncMock(return_value={"answer": None})
    consumer = _consumer(command_service)

    events = await _handle_and_collect_events(consumer, _payload("ask", _ask_request()))

    assert any(
        event["type"] == "error" and event["message"] == "AI service returned an empty answer"
        for event in events
    )
    assert not any(event["type"] == "final" for event in events)


@pytest.mark.asyncio(loop_scope="function")
async def test_successful_ask_answer_is_published_as_final():
    command_service = MagicMock()
    command_service.process_ask = AsyncMock(return_value={"answer": "42"})
    consumer = _consumer(command_service)

    events = await _handle_and_collect_events(consumer, _payload("ask", _ask_request()))

    assert {"type": "final", "result": {"answer": "42"}} in events
    assert not any(event["type"] == "error" for event in events)


@pytest.mark.asyncio(loop_scope="function")
async def test_progress_and_final_events_are_ordered_before_job_returns():
    command_service = MagicMock()

    async def process(_request, callback):
        callback({"type": "status", "state": "answering"})
        return {"answer": "42"}

    command_service.process_ask = AsyncMock(side_effect=process)
    consumer = _consumer(command_service)

    events = await _handle_and_collect_events(
        consumer,
        _payload("ask", _ask_request()),
    )

    assert [event.get("state") for event in events[:-1]] == [
        "acknowledged",
        "answering",
    ]
    assert events[-1] == {"type": "final", "result": {"answer": "42"}}
    assert consumer._redis.expiries == [
        ("codecrow:analysis:events:job-ask", 3600),
        ("codecrow:analysis:events:job-ask", 3600),
        ("codecrow:analysis:events:job-ask", 3600),
    ]


@pytest.mark.asyncio(loop_scope="function")
async def test_command_event_publish_uses_configured_expiry(monkeypatch):
    monkeypatch.setenv("COMMAND_EVENT_TTL_SECONDS", "123")
    consumer = _consumer(MagicMock())

    await consumer._publish_event(
        "codecrow:analysis:events:job-ttl",
        {"type": "status"},
    )

    assert consumer._redis.events == [
        ("codecrow:analysis:events:job-ttl", {"type": "status"}),
    ]
    assert consumer._redis.expiries == [
        ("codecrow:analysis:events:job-ttl", 123),
    ]


@pytest.mark.asyncio(loop_scope="function")
async def test_empty_summarize_result_is_published_as_error_without_final():
    command_service = MagicMock()
    command_service.process_summarize = AsyncMock(return_value={"summary": "No output generated"})
    consumer = _consumer(command_service)

    events = await _handle_and_collect_events(consumer, _payload("summarize", _summarize_request()))

    assert any(
        event["type"] == "error" and event["message"] == "AI service returned an empty summary"
        for event in events
    )
    assert not any(event["type"] == "final" for event in events)


@pytest.mark.asyncio(loop_scope="function")
async def test_start_uses_blocking_read_safe_redis_timeouts():
    command_service = MagicMock()
    redis_client = MagicMock()
    redis_client.aclose = AsyncMock()
    redis_client.set = AsyncMock()
    consumer = CommandQueueConsumer(command_service)
    consumer._consume_loop = AsyncMock()

    with patch(
        "server.command_queue_consumer.redis.from_url",
        return_value=redis_client,
    ) as from_url:
        await consumer.start()
        await asyncio.sleep(0)
        await consumer.stop()

    assert from_url.call_args.kwargs == {
        "decode_responses": True,
        "socket_connect_timeout": 5,
        "socket_timeout": 30,
        "health_check_interval": 30,
    }
    redis_client.set.assert_awaited_with(
        "codecrow:commands:consumer:heartbeat",
        "alive",
        ex=consumer.consumer_heartbeat_ttl_seconds,
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_command_consumer_heartbeat_uses_the_java_supervision_contract():
    consumer = CommandQueueConsumer(MagicMock())
    consumer._redis = MagicMock()
    consumer._redis.set = AsyncMock()

    await consumer._publish_consumer_heartbeat()

    consumer._redis.set.assert_awaited_once_with(
        "codecrow:commands:consumer:heartbeat",
        "alive",
        ex=consumer.consumer_heartbeat_ttl_seconds,
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_blocking_read_timeout_is_retried_without_consumer_failure():
    consumer = CommandQueueConsumer(MagicMock())
    consumer._redis = MagicMock()
    consumer._redis.brpop = AsyncMock(
        side_effect=RedisTimeoutError("temporary read deadline")
    )
    consumer.is_running = True

    async def stop_after_backoff(_seconds):
        consumer.is_running = False

    with (
        patch(
            "server.command_queue_consumer.asyncio.sleep",
            side_effect=stop_after_backoff,
        ),
        patch("server.command_queue_consumer.logger.warning") as warning,
    ):
        await consumer._consume_loop()

    warning.assert_called_once()


@pytest.mark.asyncio(loop_scope="function")
async def test_worker_capacity_is_reserved_before_command_is_dequeued():
    consumer = CommandQueueConsumer(MagicMock())
    consumer._job_semaphore = asyncio.Semaphore(1)
    consumer._redis = MagicMock()

    async def stop_after_dequeue(*_args, **_kwargs):
        consumer.is_running = False
        return None

    consumer._redis.brpop = AsyncMock(side_effect=stop_after_dequeue)
    consumer.is_running = True
    await consumer._job_semaphore.acquire()

    consume_task = asyncio.create_task(consumer._consume_loop())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    consumer._redis.brpop.assert_not_awaited()

    consumer._job_semaphore.release()
    await consume_task

    consumer._redis.brpop.assert_awaited_once_with(
        [consumer.job_queue_key],
        timeout=1,
    )
    assert not consumer._job_semaphore.locked()


@pytest.mark.asyncio(loop_scope="function")
async def test_stop_waits_for_admitted_command_before_closing_redis():
    consumer = CommandQueueConsumer(MagicMock())
    consumer._redis = MagicMock()
    consumer._redis.aclose = AsyncMock()
    started = asyncio.Event()
    release = asyncio.Event()

    async def handle_until_released(_payload):
        started.set()
        await release.wait()

    consumer._handle_job = AsyncMock(side_effect=handle_until_released)
    await consumer._job_semaphore.acquire()
    job_task = asyncio.create_task(consumer._handle_admitted_job("payload"))
    consumer._job_tasks.add(job_task)
    job_task.add_done_callback(consumer._job_tasks.discard)
    await started.wait()

    stop_task = asyncio.create_task(consumer.stop())
    await asyncio.sleep(0)

    assert not stop_task.done()
    consumer._redis.aclose.assert_not_awaited()

    release.set()
    await asyncio.wait_for(stop_task, timeout=1)

    assert job_task.done()
    consumer._redis.aclose.assert_awaited_once_with()


def test_redis_outage_diagnostic_is_bounded_until_recovery():
    consumer = CommandQueueConsumer(MagicMock())

    with (
        patch("server.command_queue_consumer.logger.warning") as warning,
        patch("server.command_queue_consumer.logger.debug") as debug,
        patch("server.command_queue_consumer.logger.info") as info,
    ):
        consumer._record_redis_failure("command event publication", RuntimeError("down"))
        consumer._record_redis_failure("command event publication", RuntimeError("still down"))
        consumer._record_redis_success("command queue read")
        consumer._record_redis_success("command event publication")

    warning.assert_called_once()
    debug.assert_called_once()
    info.assert_called_once_with(
        "Redis connectivity restored during %s",
        "command event publication",
    )
