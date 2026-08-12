import asyncio
import json
import logging
import threading
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from rag_pipeline.server.rag_queue_consumer import RAGQueueConsumer


class _Stats:
    def model_dump(self):
        return {"document_count": 1, "chunk_count": 1}


def _redis_with_transactional_pipeline():
    redis_client = AsyncMock()
    pipeline = MagicMock()
    pipeline.lpush.return_value = pipeline
    pipeline.expire.return_value = pipeline
    pipeline.execute = AsyncMock(return_value=[1, True])
    pipeline.__aenter__ = AsyncMock(return_value=pipeline)
    pipeline.__aexit__ = AsyncMock(return_value=None)
    redis_client.pipeline = Mock(return_value=pipeline)
    return redis_client, pipeline


@pytest.mark.asyncio
async def test_active_indexing_emits_heartbeats_and_refreshes_event_ttl(tmp_path):
    owned_repo = tmp_path / "codecrow-rag-owned"
    owned_repo.mkdir()

    manager = Mock()
    indexing_started = threading.Event()
    finish_indexing = threading.Event()

    def index_until_released(**_kwargs):
        indexing_started.set()
        finish_indexing.wait(timeout=2)
        return _Stats()

    manager.index_repository.side_effect = index_until_released
    consumer = RAGQueueConsumer(manager)
    consumer.heartbeat_seconds = 0.01
    consumer.event_ttl_seconds = 123
    consumer._redis, event_pipeline = _redis_with_transactional_pipeline()

    payload = json.dumps({
        "job_id": "job-1",
        "request": {
            "repo_path": str(owned_repo),
            "workspace": "ws",
            "project": "project",
            "branch": "main",
            "commit": "abc123",
            "cleanup_repo_path": False,
        },
    })

    task = asyncio.create_task(consumer._handle_job(payload))
    while not indexing_started.is_set():
        await asyncio.sleep(0)
    await asyncio.sleep(0.04)
    finish_indexing.set()
    await task

    events = [
        json.loads(call.args[1])
        for call in event_pipeline.lpush.call_args_list
    ]
    assert any(event.get("state") == "processing" for event in events)
    assert events[-1]["type"] == "final"
    assert event_pipeline.expire.call_count == len(events)
    event_pipeline.expire.assert_called_with(
        "codecrow:analysis:events:job-1", 123
    )


@pytest.mark.asyncio
async def test_indexer_progress_is_published_to_the_durable_event_stream(tmp_path):
    owned_repo = tmp_path / "codecrow-rag-owned"
    owned_repo.mkdir()

    manager = Mock()

    def index_with_progress(**kwargs):
        kwargs["progress_callback"]({
            "stage": "indexing",
            "message": "Indexed 20 of 100 files",
            "progress": 40,
            "total": 100,
        })
        return _Stats()

    manager.index_repository.side_effect = index_with_progress
    consumer = RAGQueueConsumer(manager)
    consumer._redis, event_pipeline = _redis_with_transactional_pipeline()
    payload = json.dumps({
        "job_id": "job-progress",
        "request": {
            "repo_path": str(owned_repo),
            "workspace": "ws",
            "project": "project",
            "branch": "main",
            "commit": "abc123",
            "cleanup_repo_path": False,
        },
    })

    await consumer._handle_job(payload)

    assert not any(
        thread.is_alive() and thread.name.startswith("rag-index")
        for thread in threading.enumerate()
    )

    events = [
        json.loads(call.args[1])
        for call in event_pipeline.lpush.call_args_list
    ]
    assert any(
        event.get("stage") == "indexing" and event.get("progress") == 40
        for event in events
    )
    assert events[-1]["type"] == "final"


@pytest.mark.asyncio
async def test_consumer_removes_only_explicitly_owned_workspace(tmp_path):
    owned_repo = tmp_path / "codecrow-rag-owned"
    owned_repo.mkdir()
    (owned_repo / "source.py").write_text("value = 1", encoding="utf-8")

    manager = Mock()
    manager.index_repository.return_value = _Stats()
    consumer = RAGQueueConsumer(manager)
    consumer._redis, _ = _redis_with_transactional_pipeline()

    payload = json.dumps({
        "job_id": "job-2",
        "request": {
            "repo_path": str(owned_repo),
            "workspace": "ws",
            "project": "project",
            "branch": "main",
            "commit": "abc123",
            "cleanup_repo_path": True,
        },
    })

    with patch.dict("os.environ", {"ALLOWED_REPO_ROOT": str(tmp_path)}):
        await consumer._handle_job(payload)

    assert not owned_repo.exists()


def test_cleanup_refuses_paths_outside_owned_temp_namespace(tmp_path):
    unrelated = tmp_path / "user-repository"
    unrelated.mkdir()

    with patch.dict("os.environ", {"ALLOWED_REPO_ROOT": str(tmp_path)}):
        RAGQueueConsumer._cleanup_owned_repository_path(str(unrelated))

    assert unrelated.exists()


@pytest.mark.asyncio
async def test_cancelled_job_retires_executor_after_worker_returns(tmp_path):
    owned_repo = tmp_path / "codecrow-rag-owned"
    owned_repo.mkdir()
    (owned_repo / "source.py").write_text("value = 1", encoding="utf-8")
    started = threading.Event()
    release = threading.Event()

    manager = Mock()

    def index_until_released(**_kwargs):
        started.set()
        release.wait(timeout=2)
        return _Stats()

    manager.index_repository.side_effect = index_until_released
    consumer = RAGQueueConsumer(manager)
    consumer._redis, _ = _redis_with_transactional_pipeline()
    payload = json.dumps({
        "job_id": "job-cancelled",
        "request": {
            "repo_path": str(owned_repo),
            "workspace": "ws",
            "project": "project",
            "branch": "main",
            "commit": "abc123",
            "cleanup_repo_path": True,
        },
    })

    with patch.dict("os.environ", {"ALLOWED_REPO_ROOT": str(tmp_path)}):
        task = asyncio.create_task(consumer._handle_job(payload))
        while not started.is_set():
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert owned_repo.exists()

        stop_task = asyncio.create_task(consumer.stop())
        await asyncio.sleep(0)
        assert not stop_task.done()
        consumer._redis.aclose.assert_not_awaited()

        release.set()
        await asyncio.wait_for(stop_task, timeout=1)

    assert not any(
        thread.is_alive() and thread.name.startswith("rag-index")
        for thread in threading.enumerate()
    )
    assert not owned_repo.exists()


@pytest.mark.asyncio
async def test_stop_waits_for_admitted_job_before_closing_redis(tmp_path):
    owned_repo = tmp_path / "codecrow-rag-owned"
    owned_repo.mkdir()
    started = threading.Event()
    release = threading.Event()

    manager = Mock()

    def index_until_released(**_kwargs):
        started.set()
        release.wait(timeout=2)
        return _Stats()

    manager.index_repository.side_effect = index_until_released
    consumer = RAGQueueConsumer(manager)
    consumer._redis, _ = _redis_with_transactional_pipeline()
    payload = json.dumps({
        "job_id": "job-shutdown",
        "request": {
            "repo_path": str(owned_repo),
            "workspace": "ws",
            "project": "project",
            "branch": "main",
            "commit": "abc123",
            "cleanup_repo_path": False,
        },
    })

    job_task = asyncio.create_task(consumer._handle_job(payload))
    consumer._job_tasks.add(job_task)
    job_task.add_done_callback(consumer._job_tasks.discard)
    while not started.is_set():
        await asyncio.sleep(0)

    stop_task = asyncio.create_task(consumer.stop())
    await asyncio.sleep(0)
    assert not stop_task.done()
    consumer._redis.aclose.assert_not_awaited()

    release.set()
    await asyncio.wait_for(stop_task, timeout=1)

    assert job_task.done()
    consumer._redis.aclose.assert_awaited_once_with()
    assert not any(
        thread.is_alive() and thread.name.startswith("rag-index")
        for thread in threading.enumerate()
    )


@pytest.mark.asyncio
async def test_progress_buffer_coalesces_to_latest_event():
    queue = asyncio.Queue(maxsize=1)

    RAGQueueConsumer._coalesce_progress_event(queue, {"progress": 10})
    RAGQueueConsumer._coalesce_progress_event(queue, {"progress": 20})

    assert queue.qsize() == 1
    assert await queue.get() == {"progress": 20}
    queue.task_done()
    await queue.join()


@pytest.mark.asyncio
async def test_late_progress_does_not_replace_publisher_sentinel():
    queue = asyncio.Queue(maxsize=1)
    queue.put_nowait(None)

    RAGQueueConsumer._coalesce_progress_event(queue, {"progress": 100})

    assert await queue.get() is None
    queue.task_done()
    await queue.join()


@pytest.mark.asyncio
async def test_event_delivery_outage_logs_one_warning_until_recovery(caplog):
    consumer = RAGQueueConsumer(Mock())
    consumer._redis, event_pipeline = _redis_with_transactional_pipeline()
    event_pipeline.execute.side_effect = [
        RuntimeError("redis unavailable"),
        RuntimeError("redis unavailable"),
        [1, True],
    ]

    with caplog.at_level(logging.DEBUG):
        assert not await consumer._publish_event("events", {"type": "status"})
        assert not await consumer._publish_event("events", {"type": "status"})
        assert await consumer._publish_event("events", {"type": "final"})

    outage_records = [
        record
        for record in caplog.records
        if "Redis RAG event delivery" in record.getMessage()
    ]
    assert sum(record.levelno == logging.WARNING for record in outage_records) == 1
    assert not any(record.levelno >= logging.ERROR for record in outage_records)
    assert any("recovered" in record.getMessage() for record in outage_records)


@pytest.mark.asyncio
async def test_event_and_ttl_are_enqueued_in_one_redis_transaction():
    consumer = RAGQueueConsumer(Mock())
    consumer.event_ttl_seconds = 321
    consumer._redis, event_pipeline = _redis_with_transactional_pipeline()

    assert await consumer._publish_event(
        "events", {"type": "final", "result": {"ok": True}}
    )

    consumer._redis.pipeline.assert_called_once_with(transaction=True)
    event_pipeline.lpush.assert_called_once()
    assert json.loads(event_pipeline.lpush.call_args.args[1]) == {
        "type": "final",
        "result": {"ok": True},
    }
    event_pipeline.expire.assert_called_once_with("events", 321)
    event_pipeline.execute.assert_awaited_once_with()


def test_queue_read_outage_logs_one_warning_until_recovery(caplog):
    consumer = RAGQueueConsumer(Mock())

    with caplog.at_level(logging.DEBUG):
        consumer._record_queue_read_failure(RuntimeError("redis unavailable"))
        consumer._record_queue_read_failure(RuntimeError("redis unavailable"))
        consumer._record_queue_read_recovery()

    outage_records = [
        record
        for record in caplog.records
        if "Redis RAG queue read" in record.getMessage()
    ]
    assert sum(record.levelno == logging.WARNING for record in outage_records) == 1
    assert not any(record.levelno >= logging.ERROR for record in outage_records)
    assert any("recovered" in record.getMessage() for record in outage_records)


@pytest.mark.asyncio
async def test_worker_capacity_is_reserved_before_rag_job_is_dequeued():
    consumer = RAGQueueConsumer(Mock())
    consumer._job_semaphore = asyncio.Semaphore(1)
    consumer._redis = AsyncMock()

    async def stop_after_dequeue(*_args, **_kwargs):
        consumer.is_running = False
        return None

    consumer._redis.brpop.side_effect = stop_after_dequeue
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
