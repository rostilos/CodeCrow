"""Offline admission/isolation checks; no repositories or model calls."""
import asyncio
from threading import Event, Lock
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from rag_pipeline.api.heavy_work import configure_heavy_operations, run_heavy_operation
from rag_pipeline.api.routers import query as query_router


@pytest.mark.asyncio
@pytest.mark.parametrize("capacity", [16, 40])
async def test_queued_review_preparation_leaves_ten_query_threads_available(capacity, monkeypatch):
    configure_heavy_operations(capacity)
    loop = asyncio.get_running_loop()
    heavy_ready = asyncio.Event()
    queries_ready = asyncio.Event()
    release_heavy, release_queries = Event(), Event()
    lock = Lock()
    counts = {"heavy": 0, "queries": 0}

    def blocked_heavy(request):
        with lock:
            counts["heavy"] += 1
            if counts["heavy"] == capacity:
                loop.call_soon_threadsafe(heavy_ready.set)
        release_heavy.wait()
        return {"status": "ready"}

    app = FastAPI()
    app.include_router(query_router.router)
    monkeypatch.setattr(query_router, "prepare_review_generation", blocked_heavy)
    monkeypatch.setattr(query_router, "_manager", lambda: object())

    def blocked_query(**kwargs):
        with lock:
            counts["queries"] += 1
            if counts["queries"] == 10:
                loop.call_soon_threadsafe(queries_ready.set)
        release_queries.wait()
        return {
            "status": "ready", "snapshot": {}, "freshness": {}, "changed": {},
            "evidence": {}, "sourceWindows": [], "coverage": {},
            "provenance": {}, "omittedFollowups": [],
        }

    monkeypatch.setattr(
        query_router, "ProposedTreeReviewContextService",
        lambda manager: SimpleNamespace(review_context=blocked_query),
    )
    preparation = {
        "workspace": "workspace", "project": "project", "target_branch": "main",
        "base_revision": "base", "source_revision": "source",
        "target_repo_path": "/tmp/offline-target", "review_overlay_path": "/tmp/offline-overlay",
    }
    query = {
        **preparation, "review_collection_target": "sealed-review",
        "review_generation_manifest_sha256": "a" * 64,
        "focus_paths": ["app.py"], "question": "Read the changed function",
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://offline") as client:
        heavy_tasks = [
            asyncio.create_task(client.post("/query/review-generation", json=preparation))
            for _ in range(capacity * 2)
        ]
        query_tasks = []
        try:
            await asyncio.wait_for(heavy_ready.wait(), 5)
            # These synchronous query routes must all start while both running
            # and queued indexing requests are still blocked.
            query_tasks = [
                asyncio.create_task(client.post("/query/review-context", json=query))
                for _ in range(10)
            ]
            await asyncio.wait_for(queries_ready.wait(), 5)
            assert counts["heavy"] == capacity
            release_queries.set()
            assert all(response.status_code == 200 for response in await asyncio.gather(*query_tasks))
        finally:
            release_queries.set()
            release_heavy.set()
            await asyncio.gather(*heavy_tasks, *query_tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_queued_operation_never_starts_and_capacity_recovers():
    configure_heavy_operations(1)
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = Event()
    queued_executed = Event()

    def first():
        loop.call_soon_threadsafe(started.set)
        release.wait()

    active = asyncio.create_task(run_heavy_operation(first))
    try:
        await asyncio.wait_for(started.wait(), 5)
        queued = asyncio.create_task(run_heavy_operation(queued_executed.set))
        await asyncio.sleep(0)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        assert not queued_executed.is_set()
    finally:
        release.set()
        await active
    assert await run_heavy_operation(lambda: "next") == "next"
    assert not queued_executed.is_set()


@pytest.mark.asyncio
async def test_failed_operation_returns_its_slot_without_hiding_the_exception():
    configure_heavy_operations(1)

    def failed():
        raise ValueError("invalid repository binding")

    with pytest.raises(ValueError, match="invalid repository binding"):
        await run_heavy_operation(failed)
    assert await run_heavy_operation(lambda *, value: value, value="recovered") == "recovered"
