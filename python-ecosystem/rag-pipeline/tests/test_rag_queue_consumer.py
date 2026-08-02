import asyncio
import json
from unittest.mock import AsyncMock, Mock, patch

import pytest

from rag_pipeline.server.rag_queue_consumer import RAGQueueConsumer


class _Stats:
    def model_dump(self):
        return {"document_count": 1, "chunk_count": 1}


@pytest.mark.asyncio
async def test_active_indexing_emits_heartbeats_and_refreshes_event_ttl(tmp_path):
    owned_repo = tmp_path / "codecrow-rag-owned"
    owned_repo.mkdir()

    manager = Mock()
    consumer = RAGQueueConsumer(manager)
    consumer.heartbeat_seconds = 0.01
    consumer.event_ttl_seconds = 123
    consumer._redis = AsyncMock()

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

    loop = asyncio.get_running_loop()
    indexing_future = loop.create_future()
    with patch.object(loop, "run_in_executor", return_value=indexing_future):
        task = asyncio.create_task(consumer._handle_job(payload))
        await asyncio.sleep(0.04)
        indexing_future.set_result(_Stats())
        await task

    events = [
        json.loads(call.args[1])
        for call in consumer._redis.lpush.await_args_list
    ]
    assert any(event.get("state") == "processing" for event in events)
    assert events[-1]["type"] == "final"
    assert consumer._redis.expire.await_count == len(events)
    consumer._redis.expire.assert_awaited_with(
        "codecrow:analysis:events:job-1", 123
    )


@pytest.mark.asyncio
async def test_consumer_removes_only_explicitly_owned_workspace(tmp_path):
    owned_repo = tmp_path / "codecrow-rag-owned"
    owned_repo.mkdir()
    (owned_repo / "source.py").write_text("value = 1", encoding="utf-8")

    manager = Mock()
    manager.index_repository.return_value = _Stats()
    consumer = RAGQueueConsumer(manager)
    consumer._redis = AsyncMock()

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

    loop = asyncio.get_running_loop()

    def run_inline(_executor, function):
        result = loop.create_future()
        try:
            result.set_result(function())
        except Exception as error:
            result.set_exception(error)
        return result

    with (
        patch.object(loop, "run_in_executor", side_effect=run_inline),
        patch.dict("os.environ", {"ALLOWED_REPO_ROOT": str(tmp_path)}),
    ):
        await consumer._handle_job(payload)

    assert not owned_repo.exists()


def test_cleanup_refuses_paths_outside_owned_temp_namespace(tmp_path):
    unrelated = tmp_path / "user-repository"
    unrelated.mkdir()

    with patch.dict("os.environ", {"ALLOWED_REPO_ROOT": str(tmp_path)}):
        RAGQueueConsumer._cleanup_owned_repository_path(str(unrelated))

    assert unrelated.exists()


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
