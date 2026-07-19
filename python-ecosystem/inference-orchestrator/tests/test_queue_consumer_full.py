import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

import server.queue_consumer as queue_module
from server.queue_consumer import RedisQueueConsumer


LEGACY_COMPATIBILITY = {
    "kind": "legacy",
    "deadline": "2026-09-30T00:00:00Z",
}


def _request(**overrides):
    return {
        "projectId": 1,
        "projectVcsWorkspace": "vcs-workspace",
        "projectVcsRepoSlug": "repo",
        "projectWorkspace": "workspace",
        "projectNamespace": "namespace",
        "aiProvider": "scripted",
        "aiModel": "fixture-v1",
        "aiApiKey": "fake-key",
        "previousCommitHash": "a" * 40,
        "currentCommitHash": "b" * 40,
        "legacyCompatibility": dict(LEGACY_COMPATIBILITY),
        **overrides,
    }


def test_review_consumer_uses_the_single_java_producer_queue():
    consumer = RedisQueueConsumer(MagicMock())

    assert getattr(queue_module, "JOB_QUEUE_KEY") == "codecrow:analysis:jobs"
    assert consumer.job_queue_keys == ("codecrow:analysis:jobs",)
    assert consumer.job_queue_key == "codecrow:analysis:jobs"


@pytest.mark.asyncio(loop_scope="function")
async def test_consume_loop_blocks_on_the_single_job_queue():
    redis = MagicMock()
    redis.brpop = AsyncMock(side_effect=[None, asyncio.CancelledError()])
    consumer = RedisQueueConsumer(MagicMock())
    consumer._redis = redis
    consumer.is_running = True

    await consumer._consume_loop()

    assert redis.brpop.await_args_list[0] == call(
        ("codecrow:analysis:jobs",), timeout=1
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_start_is_idempotent_and_stop_closes_redis(monkeypatch):
    redis = MagicMock()
    redis.aclose = AsyncMock()
    monkeypatch.setattr(queue_module.redis, "from_url", MagicMock(return_value=redis))
    service = MagicMock()
    consumer = RedisQueueConsumer(service)
    consumer._consume_loop = AsyncMock()

    await consumer.start()
    first_task = consumer._task
    await asyncio.sleep(0)
    await consumer.start()
    assert consumer._task is first_task

    await consumer.stop()
    redis.aclose.assert_awaited_once()
    assert consumer.is_running is False

    empty = RedisQueueConsumer(service)
    await empty.stop()


@pytest.mark.asyncio(loop_scope="function")
async def test_stop_awaits_cancelled_background_task():
    consumer = RedisQueueConsumer(MagicMock())

    async def wait_forever():
        await asyncio.sleep(60)

    consumer._task = asyncio.create_task(wait_forever())
    await asyncio.sleep(0)
    await consumer.stop()
    assert consumer._task.cancelled()


@pytest.mark.asyncio(loop_scope="function")
async def test_consume_loop_handles_empty_job_payload_and_cancellation(monkeypatch):
    redis = MagicMock()
    redis.brpop = AsyncMock(
        side_effect=[None, ("codecrow:analysis:jobs", "payload"), asyncio.CancelledError()]
    )
    consumer = RedisQueueConsumer(MagicMock())
    consumer._redis = redis
    consumer._bounded_handle_job = AsyncMock()
    consumer.is_running = True

    await consumer._consume_loop()
    await asyncio.sleep(0)

    consumer._bounded_handle_job.assert_awaited_once_with("payload")


@pytest.mark.asyncio(loop_scope="function")
async def test_consume_loop_backs_off_after_redis_failure(monkeypatch):
    redis = MagicMock()
    redis.brpop = AsyncMock(side_effect=RuntimeError("redis down"))
    consumer = RedisQueueConsumer(MagicMock())
    consumer._redis = redis
    consumer.is_running = True

    async def stop_after_backoff(delay):
        assert delay == 2
        consumer.is_running = False

    monkeypatch.setattr(queue_module.asyncio, "sleep", stop_after_backoff)
    await consumer._consume_loop()


@pytest.mark.asyncio(loop_scope="function")
async def test_bounded_handler_delegates_under_semaphore():
    consumer = RedisQueueConsumer(MagicMock())
    consumer._handle_job = AsyncMock()

    await consumer._bounded_handle_job("payload")

    consumer._handle_job.assert_awaited_once_with("payload")


@pytest.mark.asyncio(loop_scope="function")
async def test_handle_job_rejects_malformed_and_incomplete_payloads():
    consumer = RedisQueueConsumer(MagicMock())
    consumer._publish_event = AsyncMock()

    await consumer._handle_job("not-json")
    await consumer._handle_job(json.dumps({"job_id": "job-only"}))
    await consumer._handle_job(json.dumps({"request": _request()}))

    consumer._publish_event.assert_not_awaited()


@pytest.mark.asyncio(loop_scope="function")
async def test_handle_job_never_publishes_worker_errors_as_final_results():
    service = MagicMock()
    service.process_review_request = AsyncMock(
        side_effect=[
            {"result": {"status": "error"}},
            {"error": "MCP server is unavailable"},
            {"comment": "ok", "issues": []},
        ]
    )
    consumer = RedisQueueConsumer(service)
    consumer._publish_event = AsyncMock()
    payload = json.dumps({"job_id": "job-1", "request": _request()})

    outcomes = [
        await consumer._handle_job(payload),
        await consumer._handle_job(payload),
        await consumer._handle_job(payload),
    ]
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    events = [call.args[1] for call in consumer._publish_event.await_args_list]
    assert events.count({
        "type": "error",
        "message": "Review processing failed",
        "reasonCode": "review_processing_failed",
    }) == 2
    assert {"type": "final", "result": {"comment": "ok", "issues": []}} in events
    assert [event for event in events if event["type"] == "final"] == [
        {"type": "final", "result": {"comment": "ok", "issues": []}}
    ]
    assert outcomes == ["failed", "failed", "complete"]


@pytest.mark.asyncio(loop_scope="function")
async def test_handle_job_publishes_validation_and_unhandled_errors():
    service = MagicMock()
    service.process_review_request = AsyncMock(side_effect=RuntimeError("review crashed"))
    consumer = RedisQueueConsumer(service)
    consumer._publish_event = AsyncMock()

    await consumer._handle_job(
        json.dumps({"job_id": "validation", "request": {"projectId": "bad"}})
    )
    await consumer._handle_job(
        json.dumps({"job_id": "runtime", "request": _request()})
    )

    errors = [
        published_call.args[1]
        for published_call in consumer._publish_event.await_args_list
        if published_call.args[1].get("type") == "error"
    ]
    assert {
        (event["message"], event["reasonCode"])
        for event in errors
    } == {
        ("Input validation error", "input_validation_error"),
        ("Internal orchestrator error", "internal_orchestrator_error"),
    }


@pytest.mark.asyncio(loop_scope="function")
async def test_queue_logs_and_error_events_never_disclose_request_or_exception_secrets(
    caplog,
):
    credential = "QUEUE-CREDENTIAL-SENTINEL-7f5cb5"
    source = "QUEUE-SOURCE-SENTINEL-e2f419"
    service = MagicMock()
    consumer = RedisQueueConsumer(service)
    consumer._publish_event = AsyncMock()
    caplog.set_level(logging.DEBUG, logger=queue_module.__name__)

    await consumer._handle_job(json.dumps({
        "job_id": "validation-job",
        "request": {
            "projectId": "not-an-integer",
            "aiApiKey": credential,
            "rawDiff": source,
        },
    }))

    service.process_review_request = AsyncMock(
        side_effect=RuntimeError(f"backend failed while handling {source}")
    )
    await consumer._handle_job(json.dumps({
        "job_id": "runtime-job",
        "request": _request(aiApiKey=credential),
    }))

    service.process_review_request = AsyncMock(return_value={
        "result": {
            "status": "error",
            "message": f"model exposed {source}",
        }
    })
    await consumer._handle_job(json.dumps({
        "job_id": "model-error-job",
        "request": _request(aiApiKey=credential),
    }))

    published = json.dumps([
        published_call.args[1]
        for published_call in consumer._publish_event.await_args_list
    ])
    observable = caplog.text + published
    assert credential not in observable
    assert source not in observable
    assert {
        event.get("reasonCode")
        for event in json.loads(published)
        if event.get("type") == "error"
    } == {
        "input_validation_error",
        "internal_orchestrator_error",
        "review_processing_failed",
    }


@pytest.mark.asyncio(loop_scope="function")
async def test_consumer_start_does_not_log_redis_credentials(
    monkeypatch,
    caplog,
):
    credential = "REDIS-CREDENTIAL-SENTINEL-9c0d31"
    redis_client = MagicMock()
    redis_client.aclose = AsyncMock()
    monkeypatch.setenv(
        "REDIS_URL",
        f"redis://worker:{credential}@redis.internal:6379/1",
    )
    monkeypatch.setattr(
        queue_module.redis,
        "from_url",
        MagicMock(return_value=redis_client),
    )
    consumer = RedisQueueConsumer(MagicMock())
    consumer._consume_loop = AsyncMock()
    caplog.set_level(logging.INFO, logger=queue_module.__name__)

    await consumer.start()
    await asyncio.sleep(0)
    await consumer.stop()

    assert credential not in caplog.text


class _Pipeline:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    def lpush(self, key, value):
        self.calls.append(("lpush", key, json.loads(value)))
        return self

    def expire(self, key, seconds):
        self.calls.append(("expire", key, seconds))
        return self

    async def execute(self):
        if self.fail:
            raise RuntimeError("pipeline failed")


@pytest.mark.asyncio(loop_scope="function")
async def test_publish_event_handles_absent_redis_success_and_pipeline_failure():
    consumer = RedisQueueConsumer(MagicMock())
    await consumer._publish_event("events", {"value": object()})

    pipeline = _Pipeline()
    consumer._redis = SimpleNamespace(pipeline=lambda: pipeline)
    await consumer._publish_event("events", {"value": object()})
    assert pipeline.calls[0][0] == "lpush"
    assert pipeline.calls[1] == ("expire", "events", 3600)

    consumer._redis = SimpleNamespace(pipeline=lambda: _Pipeline(fail=True))
    await consumer._publish_event("events", {"value": "safe"})
