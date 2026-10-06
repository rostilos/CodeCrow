"""Real ASGI disconnects cancel optional work without cancelling other callers."""
import asyncio
import json

import pytest
from fastapi import FastAPI

from rag_pipeline.api.build_workers import BuildWorkerPool
from rag_pipeline.api.routers import query
from rag_pipeline.core.review_generation import ReviewGenerationService


class CoalescedPreparation:
    run_coalesced = BuildWorkerPool.run_coalesced

    def __init__(self):
        self._flights = {}
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cleaned = asyncio.Event()
        self.cancelled = False

    async def run(self, operation, payload):
        self.started.set()
        try:
            await self.release.wait()
            return {"status": "ready", "cache_hit": False}
        except asyncio.CancelledError:
            self.cancelled = True
            await asyncio.sleep(0.01)
            raise
        finally:
            self.cleaned.set()


def asgi_call(app, root):
    incoming = asyncio.Queue()
    incoming.put_nowait({"type": "http.request", "more_body": False, "body": json.dumps({
        "workspace": "tenant", "project": "project", "target_branch": "main",
        "base_revision": "base", "source_revision": "head",
        "target_repo_path": str(root / "source"), "review_overlay_path": str(root / "overlay"),
    }).encode()})
    sent = []

    async def send(message):
        sent.append(message)

    task = asyncio.create_task(app({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": "/query/review-generation",
        "raw_path": b"/query/review-generation", "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 1234), "server": ("127.0.0.1", 8001),
    }, incoming.get, send))
    return task, incoming, sent


@pytest.mark.asyncio
@pytest.mark.parametrize("second_waiter", [False, True])
async def test_http_disconnect_respects_coalesced_ownership(monkeypatch, tmp_path, second_waiter):
    monkeypatch.setenv("ALLOWED_REPO_ROOT", str(tmp_path))
    pool = CoalescedPreparation()
    monkeypatch.setattr(query, "get_build_workers", lambda: pool)
    monkeypatch.setattr(query, "_manager", lambda: object())
    monkeypatch.setattr(ReviewGenerationService, "preparation_key", lambda self, **kw: (kw["workspace"], kw["source_revision"]))
    app = FastAPI()
    app.include_router(query.router)
    first, incoming, sent = asgi_call(app, tmp_path)
    other = None
    try:
        await asyncio.wait_for(pool.started.wait(), 2)
        if second_waiter:
            other, _, other_sent = asgi_call(app, tmp_path)
            async def joined():
                while next(iter(pool._flights.values()))[1] != 2:
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(joined(), 2)
        incoming.put_nowait({"type": "http.disconnect"})
        if second_waiter:
            await asyncio.sleep(0.2)
            assert not pool.cancelled and not other.done()
            pool.release.set()
            await asyncio.wait_for(other, 2)
            assert next(x["status"] for x in other_sent if x["type"] == "http.response.start") == 200
        await asyncio.wait_for(first, 2)
        assert next(x["status"] for x in sent if x["type"] == "http.response.start") == 499
        assert pool.cleaned.is_set() and not pool._flights
        assert pool.cancelled is not second_waiter
    finally:
        pool.release.set()
        await asyncio.gather(first, *([other] if other else []), return_exceptions=True)


@pytest.mark.asyncio
async def test_completed_preparation_wins_over_disconnect(monkeypatch, tmp_path):
    monkeypatch.setenv("ALLOWED_REPO_ROOT", str(tmp_path))
    pool = CoalescedPreparation()
    pool.release.set()
    monkeypatch.setattr(query, "get_build_workers", lambda: pool)
    monkeypatch.setattr(query, "_manager", lambda: object())
    monkeypatch.setattr(ReviewGenerationService, "preparation_key", lambda self, **kw: ("ready",))
    app = FastAPI()
    app.include_router(query.router)
    task, incoming, sent = asgi_call(app, tmp_path)
    incoming.put_nowait({"type": "http.disconnect"})
    await asyncio.wait_for(task, 2)
    assert next(x["status"] for x in sent if x["type"] == "http.response.start") == 200
    assert not pool.cancelled
