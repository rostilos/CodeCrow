import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from server.queue_consumer import RedisQueueConsumer
from server.command_queue_consumer import CommandQueueConsumer


@pytest.fixture(params=[RedisQueueConsumer, CommandQueueConsumer])
def consumer(request, monkeypatch):
    result = request.param(MagicMock())
    result._redis = MagicMock()
    result._redis.exists = AsyncMock(return_value=True)
    result._redis.expire = AsyncMock(return_value=True)
    claims = set()

    async def claim(key, value, **kwargs):
        assert kwargs == {"nx": True, "ex": 86400}
        if key in claims:
            return False
        claims.add(key)
        return True

    result._redis.set = AsyncMock(side_effect=claim)
    result._publish_event = AsyncMock()
    result._handle_job = AsyncMock()
    monkeypatch.setattr("server.redis_job_consumer.OWNER_CHECK_SECONDS", 0.001)
    return result


PAYLOAD = json.dumps({"job_id": "same-execution", "request": {"projectId": 7}})


@pytest.mark.asyncio
async def test_orphaned_or_restored_queue_entry_makes_zero_calls(consumer):
    consumer._redis.exists.return_value = False
    await consumer._run_owned_job(PAYLOAD)
    consumer._handle_job.assert_not_awaited()
    consumer._publish_event.assert_awaited_once()
    assert "Caller lease is absent" in consumer._publish_event.call_args.args[1]["message"]


@pytest.mark.asyncio
async def test_concurrent_and_completed_duplicate_delivery_makes_one_call(consumer):
    started, finish = asyncio.Event(), asyncio.Event()

    async def paid_work(payload):
        started.set()
        await finish.wait()

    consumer._handle_job.side_effect = paid_work
    first = asyncio.create_task(consumer._run_owned_job(PAYLOAD))
    try:
        await asyncio.wait_for(started.wait(), 1)
        await consumer._run_owned_job(PAYLOAD)
        consumer._handle_job.assert_awaited_once()
        finish.set()
        await asyncio.wait_for(first, 1)
        await consumer._run_owned_job(PAYLOAD)
        consumer._handle_job.assert_awaited_once()
        consumer._publish_event.assert_not_awaited()
    finally:
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("redis_failure", [False, True])
async def test_lost_caller_stops_work_before_publishing_error(consumer, redis_failure):
    started, stopped = asyncio.Event(), asyncio.Event()

    async def paid_work(payload):
        started.set()
        try:
            await asyncio.Future()
        finally:
            stopped.set()

    async def publish(*args):
        assert stopped.is_set()

    consumer._handle_job.side_effect = paid_work
    consumer._publish_event.side_effect = publish
    task = asyncio.create_task(consumer._run_owned_job(PAYLOAD))
    try:
        await asyncio.wait_for(started.wait(), 1)
        if redis_failure:
            consumer._redis.exists.side_effect = ConnectionError("Redis unavailable")
        else:
            consumer._redis.exists.return_value = False
        await asyncio.wait_for(task, 1)
        consumer._handle_job.assert_awaited_once()
        consumer._publish_event.assert_awaited_once()
        assert stopped.is_set()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
