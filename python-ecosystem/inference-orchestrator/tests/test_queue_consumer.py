import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.exceptions import TimeoutError as RedisTimeoutError

from server.queue_consumer import RedisQueueConsumer
from service.review.review_service import ReviewService
from .prompt_dry_run_neutral_fixture import (
    SECRET_API_KEY,
    DeterministicRagSpy,
    mixed_language_request,
)


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


def _payload():
    return json.dumps({
        "job_id": "review-job",
        "request": {"projectId": 42},
    })


@pytest.mark.asyncio(loop_scope="function")
async def test_review_events_are_ordered_and_terminal_event_is_last():
    review_service = MagicMock()

    async def process(_request, callback):
        callback({"type": "status", "state": "stage_0"})
        await asyncio.sleep(0)
        callback({"type": "status", "state": "stage_1"})
        return {"result": {"comment": "done", "issues": []}}

    review_service.process_review_request = AsyncMock(side_effect=process)
    consumer = RedisQueueConsumer(review_service)
    consumer._redis = FakeRedis()

    with patch("server.queue_consumer.ReviewRequestDto", return_value=MagicMock()):
        await consumer._handle_job(_payload())

    events = [event for _, event in consumer._redis.events]
    assert [event.get("state") for event in events[:-1]] == [
        "acknowledged",
        "stage_0",
        "stage_1",
    ]
    assert events[-1] == {
        "type": "final",
        "result": {"comment": "done", "issues": []},
    }
    assert len(consumer._redis.expiries) == len(events)


@pytest.mark.asyncio(loop_scope="function")
async def test_long_running_review_emits_liveness_before_terminal_event():
    review_service = MagicMock()
    release = asyncio.Event()

    async def process(_request, _callback):
        await release.wait()
        return {"result": {"comment": "done", "issues": []}}

    review_service.process_review_request = AsyncMock(side_effect=process)
    consumer = RedisQueueConsumer(review_service)
    consumer._redis = FakeRedis()
    consumer.heartbeat_seconds = 0.01

    with patch("server.queue_consumer.ReviewRequestDto", return_value=MagicMock()):
        task = asyncio.create_task(consumer._handle_job(_payload()))
        await asyncio.sleep(0.025)
        release.set()
        await task

    events = [event for _, event in consumer._redis.events]
    processing_positions = [
        index
        for index, event in enumerate(events)
        if event.get("state") == "processing"
    ]
    assert processing_positions
    assert processing_positions[-1] < len(events) - 1
    assert events[-1]["type"] == "final"


@pytest.mark.asyncio(loop_scope="function")
async def test_neutral_mixed_language_dry_run_traverses_queue_handler(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("ANALYSIS_PROMPT_DRY_RUN_ENABLED", "true")
    monkeypatch.setenv(
        "ANALYSIS_PROMPT_DRY_RUN_OUTPUT_DIR",
        str(tmp_path),
    )
    monkeypatch.setenv(
        "ANALYSIS_PROMPT_DRY_RUN_SYNTHETIC_FINDINGS_PER_FILE",
        "1",
    )
    monkeypatch.setattr(
        "service.review.plugin_context._plugin_host",
        lambda: None,
    )
    request = mixed_language_request().model_copy(update={
        "promptDryRun": True,
        "promptDryRunId": "neutral-queued-replay",
    })
    review_service = ReviewService.__new__(ReviewService)
    review_service.rag_client = DeterministicRagSpy()
    review_service._review_semaphore = asyncio.Semaphore(1)

    consumer = RedisQueueConsumer(review_service)
    consumer._redis = FakeRedis()
    payload = json.dumps({
        "job_id": "neutral-queued-replay",
        "request": request.model_dump(mode="json", by_alias=True),
    })

    await consumer._handle_job(payload)

    events = [event for _, event in consumer._redis.events]
    states = {
        event.get("state")
        for event in events
        if event.get("state")
    }
    assert {
        "acknowledged",
        "prompt_dry_run_started",
        "pr_context_enrichment_started",
        "pr_context_enrichment_completed",
        "stage_0_started",
        "stage_1_started",
        "verification_started",
        "stage_2_started",
        "stage_3_started",
        "review_evidence_completed",
        "prompt_dry_run_completed",
    } <= states
    assert events[-1]["type"] == "final"
    result = events[-1]["result"]
    assert result["dryRun"] is True
    artifact = result["promptArtifact"]
    assert artifact["providerCalls"] == 0
    assert artifact["pipeline"]["completed"] is True
    assert SECRET_API_KEY not in json.dumps(events)
    artifact_path = tmp_path / artifact["filename"]
    assert artifact_path.is_file()
    stored = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert stored["reviewIdentity"]["changedFiles"] == sorted(
        request.changedFiles
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_start_uses_blocking_read_safe_redis_timeouts():
    review_service = MagicMock()
    redis_client = MagicMock()
    redis_client.aclose = AsyncMock()
    redis_client.set = AsyncMock()
    consumer = RedisQueueConsumer(review_service)
    consumer._consume_loop = AsyncMock()

    with patch(
        "server.queue_consumer.redis.from_url",
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
    redis_client.set.assert_awaited()


@pytest.mark.asyncio(loop_scope="function")
async def test_review_consumer_heartbeat_has_a_short_expiry():
    consumer = RedisQueueConsumer(MagicMock())
    consumer._redis = MagicMock()
    consumer._redis.set = AsyncMock()
    consumer.consumer_heartbeat_ttl_seconds = 15

    await consumer._publish_consumer_heartbeat()

    consumer._redis.set.assert_awaited_once_with(
        "codecrow:analysis:consumer:heartbeat",
        "alive",
        ex=15,
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_review_consumer_health_requires_live_tasks_and_heartbeat():
    consumer = RedisQueueConsumer(MagicMock())
    consumer.is_running = True
    consumer._redis = MagicMock()
    consumer._redis.exists = AsyncMock(return_value=1)
    consumer._task = MagicMock()
    consumer._task.done.return_value = False
    consumer._consumer_heartbeat_task = MagicMock()
    consumer._consumer_heartbeat_task.done.return_value = False

    assert await consumer.is_healthy() is True

    consumer._task.done.return_value = True
    assert await consumer.is_healthy() is False


@pytest.mark.asyncio(loop_scope="function")
async def test_blocking_read_timeout_is_retried_without_consumer_failure():
    consumer = RedisQueueConsumer(MagicMock())
    consumer._redis = MagicMock()
    consumer._redis.brpop = AsyncMock(
        side_effect=RedisTimeoutError("temporary read deadline")
    )
    consumer.is_running = True

    async def stop_after_backoff(_seconds):
        consumer.is_running = False

    with (
        patch(
            "server.queue_consumer.asyncio.sleep",
            side_effect=stop_after_backoff,
        ),
        patch("server.queue_consumer.logger.warning") as warning,
    ):
        await consumer._consume_loop()

    warning.assert_called_once()


@pytest.mark.asyncio(loop_scope="function")
async def test_worker_capacity_is_reserved_before_job_is_dequeued():
    consumer = RedisQueueConsumer(MagicMock())
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
