"""Queue reliability contracts independent of review/command business logic."""
import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.command_queue_consumer import CommandQueueConsumer
from server.job_events import OrderedJobEvents
from server.queue_consumer import RedisQueueConsumer


@pytest.mark.asyncio
async def test_slow_publication_uses_one_task_and_keeps_all_events_in_order():
    release = asyncio.Event()
    published = []

    async def publish(event):
        await release.wait()
        published.append(event)

    events = OrderedJobEvents(publish, logger=logging.getLogger(__name__), job_id="job")
    events.emit({"type": "status", "index": 0})
    publisher = events._task
    await asyncio.sleep(0)
    for index in range(1, 2000):
        events.emit({"type": "status", "index": index})
    events.emit({"type": "final"})
    events.emit({"type": "error", "message": "late"})
    assert events._task is publisher
    release.set()
    await events.drain()
    assert [event["index"] for event in published[:-1]] == list(range(2000))
    assert published[-1] == {"type": "final"}
    assert not events._pending


@pytest.mark.asyncio
async def test_publication_restarts_after_drained_progress_and_copies_event():
    publish = AsyncMock()
    events = OrderedJobEvents(publish, logger=logging.getLogger(__name__), job_id="job")
    event = {"type": "status", "state": "before"}
    events.emit(event)
    event["state"] = "after"
    await events.drain()
    events.emit({"type": "final"})
    await events.drain()
    assert [call.args[0] for call in publish.await_args_list] == [
        {"type": "status", "state": "before"}, {"type": "final"},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer_type", [RedisQueueConsumer, CommandQueueConsumer])
async def test_failed_start_releases_client_and_can_retry(consumer_type):
    consumer = consumer_type(MagicMock())
    failed = MagicMock(set=AsyncMock(side_effect=ConnectionError("offline")), aclose=AsyncMock())
    healthy = MagicMock(set=AsyncMock(), aclose=AsyncMock())
    consumer._consume_loop = AsyncMock()
    with patch("server.redis_job_consumer.redis.from_url", side_effect=[failed, healthy]):
        with pytest.raises(ConnectionError, match="offline"):
            await consumer.start()
        failed.aclose.assert_awaited_once_with()
        assert consumer.is_running is False
        assert consumer._redis is None
        assert consumer._task is None
        await consumer.start()
        assert consumer.is_running is True
        await asyncio.sleep(0)
        await consumer.stop()
    healthy.aclose.assert_awaited_once_with()
