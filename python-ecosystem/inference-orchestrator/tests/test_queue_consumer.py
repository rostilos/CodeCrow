import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.exceptions import TimeoutError as RedisTimeoutError

from server.queue_consumer import RedisQueueConsumer
from service.review.review_service import ReviewService


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
@pytest.mark.parametrize("issues", [[], [{"file": "totals.php", "line": 1, "title": "Division by zero"}]])
async def test_partial_review_is_a_final_result_with_findings_and_coverage_preserved(issues):
    result = {
        "status": "partial", "comment": "Reviewed with unresolved vendor source",
        "issues": issues, "reviewedHunkIds": ["changed-part"],
        "unresolvedScopes": {"vendor-class": "Vendor declaration unavailable"},
        "diagnostics": ["Search did not cover all requested source; absence is not evidence."],
    }
    service = MagicMock()
    service.process_review_request = AsyncMock(return_value={"result": result})
    consumer = RedisQueueConsumer(service)
    consumer._redis = FakeRedis()

    with patch("server.queue_consumer.ReviewRequestDto", return_value=MagicMock()):
        await consumer._handle_job(_payload())

    events = [event for _, event in consumer._redis.events]
    assert events[-1] == {"type": "final", "result": result}
    assert sum(event["type"] in {"error", "final"} for event in events) == 1
    assert all(event["type"] != "error" for event in events)


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
async def test_service_error_is_the_only_terminal_review_event():
    review_service = MagicMock()

    async def process(_request, callback):
        callback({"type": "error", "message": "provider failed"})
        callback({"type": "status", "state": "late_diagnostic"})
        return {
            "result": {
                "status": "error",
                "message": "provider failed",
            },
        }

    review_service.process_review_request = AsyncMock(side_effect=process)
    consumer = RedisQueueConsumer(review_service)
    consumer._redis = FakeRedis()

    with patch("server.queue_consumer.ReviewRequestDto", return_value=MagicMock()):
        await consumer._handle_job(_payload())

    events = [event for _, event in consumer._redis.events]
    assert events == [
        {
            "type": "status",
            "state": "acknowledged",
            "message": "Orchestrator picked up job from queue",
        },
        {"type": "error", "message": "provider failed"},
    ]
    assert sum(event["type"] in {"error", "final"} for event in events) == 1


@pytest.mark.asyncio(loop_scope="function")
async def test_start_uses_blocking_read_safe_redis_timeouts():
    review_service = MagicMock()
    redis_client = MagicMock()
    redis_client.aclose = AsyncMock()
    redis_client.set = AsyncMock()
    consumer = RedisQueueConsumer(review_service)
    consumer._consume_loop = AsyncMock()

    with patch(
        "server.redis_job_consumer.redis.from_url",
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


@pytest.mark.asyncio(loop_scope="function")
async def test_stop_waits_for_admitted_review_before_closing_redis():
    consumer = RedisQueueConsumer(MagicMock())
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

    redis_client = consumer._redis
    stop_task = asyncio.create_task(consumer.stop())
    await asyncio.sleep(0)

    assert not stop_task.done()
    consumer._redis.aclose.assert_not_awaited()

    release.set()
    await asyncio.wait_for(stop_task, timeout=1)

    assert job_task.done()
    redis_client.aclose.assert_awaited_once_with()


def test_redis_outage_diagnostic_is_bounded_until_recovery():
    consumer = RedisQueueConsumer(MagicMock())

    with (
        patch("server.queue_consumer.logger.warning") as warning,
        patch("server.queue_consumer.logger.debug") as debug,
        patch("server.queue_consumer.logger.info") as info,
    ):
        consumer._record_redis_failure("review event publication", RuntimeError("down"))
        consumer._record_redis_failure("review consumer heartbeat", RuntimeError("still down"))
        consumer._record_redis_success("review queue read")
        consumer._record_redis_success("review consumer heartbeat")

    warning.assert_called_once()
    debug.assert_called_once()
    info.assert_called_once_with(
        "Redis connectivity restored during %s",
        "review consumer heartbeat",
    )


@pytest.mark.asyncio
async def test_default_capacity_admits_sixteen_reviews_and_they_all_reach_source_and_model(monkeypatch):
    from collections import deque
    from types import SimpleNamespace
    from service.review.execution_scheduler import review_context_slot
    from service.review.model_calls import invoke_json

    monkeypatch.delenv("MAX_CONCURRENT_REVIEWS", raising=False)
    monkeypatch.delenv("MAX_CONCURRENT_REVIEW_CALLS", raising=False)
    source_started = set()
    model_started = set()
    all_source_started = asyncio.Event()
    all_model_started = asyncio.Event()
    release_source = asyncio.Event()
    release_model = asyncio.Event()

    class Model:
        async def ainvoke(self, messages, **kwargs):
            project = json.loads(messages[1][1])["project"]
            model_started.add(project)
            if len(model_started) == 16:
                all_model_started.set()
            await release_model.wait()
            return SimpleNamespace(content='{"issues":[]}')

    model = Model()
    service = ReviewService(SimpleNamespace(enabled=False))

    async def review(request, callback):
        async with review_context_slot("graph_preparation"):
            source_started.add(request.projectId)
            if len(source_started) == 16:
                all_source_started.set()
            await release_source.wait()
        return {"status": "complete", **await invoke_json(
            model, request, stage="discovery", system="unchanged instructions",
            payload={"project": request.projectId},
        )}

    monkeypatch.setattr(service, "_review", review)
    consumer = RedisQueueConsumer(service)

    class QueueRedis(FakeRedis):
        def __init__(self):
            super().__init__()
            self.pending = deque(json.dumps({
                "job_id": f"job-{number}",
                "request": {"projectId": number, "pullRequestId": str(number)},
            }) for number in range(17))
            self.popped = 0

        async def brpop(self, *args, **kwargs):
            if not self.pending:
                await asyncio.Event().wait()
            self.popped += 1
            return consumer.job_queue_key, self.pending.popleft()

    redis_client = QueueRedis()
    consumer._redis = redis_client
    consumer.is_running = True
    monkeypatch.setattr("server.queue_consumer.ReviewRequestDto", lambda **values: SimpleNamespace(
        **values, aiProvider="openai", sourceBranchName="feature", targetBranchName="main",
    ))
    intake = asyncio.create_task(consumer._consume_loop())
    try:
        await asyncio.wait_for(all_source_started.wait(), 2)
        assert consumer.max_concurrent == 16
        assert len(consumer._job_tasks) == 16
        assert redis_client.popped == 16
        assert len(redis_client.pending) == 1
        await asyncio.sleep(0)
        assert sum(event.get("state") == "acknowledged" for _, event in redis_client.events) == 16

        release_source.set()
        await asyncio.wait_for(all_model_started.wait(), 2)
        assert service._model_scheduler._active == 16
        assert not any(event.get("type") == "final" for _, event in redis_client.events)
        assert redis_client.popped == 16  # No unbounded dequeue behind busy jobs.

        consumer.is_running = False
        intake.cancel()
        await asyncio.gather(intake, return_exceptions=True)
        release_model.set()
        await asyncio.wait_for(asyncio.gather(*tuple(consumer._job_tasks)), 2)
        assert sum(event.get("type") == "final" for _, event in redis_client.events) == 16
        assert len(redis_client.pending) == 1
        assert service._model_scheduler._active == 0
        assert not service._model_scheduler._pending
    finally:
        consumer.is_running = False
        intake.cancel()
        release_source.set()
        release_model.set()
        await asyncio.gather(intake, *tuple(consumer._job_tasks), return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_review_handler_joins_its_processing_task_before_returning_capacity(monkeypatch):
    from types import SimpleNamespace

    started = asyncio.Event()
    finished = asyncio.Event()

    async def process(request, callback):
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            finished.set()

    consumer = RedisQueueConsumer(SimpleNamespace(process_review_request=process))
    consumer._redis = FakeRedis()
    monkeypatch.setattr("server.queue_consumer.ReviewRequestDto", lambda **values: SimpleNamespace(
        **values, pullRequestId="42", sourceBranchName="feature", targetBranchName="main",
    ))
    task = asyncio.create_task(consumer._bounded_handle_job(_payload()))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()
    assert consumer._job_semaphore._value == consumer.max_concurrent
