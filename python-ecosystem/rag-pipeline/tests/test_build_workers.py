"""Offline process lifecycle/IPC checks; no Redis, model or service requests."""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from threading import Event

import pytest
from fastapi import HTTPException

from rag_pipeline.api.build_workers import BuildWorkerPool
from rag_pipeline.models.config import RAGConfig


class FixtureManager:
    def __init__(self, config):
        self.root = Path(config.structural_index_root)

    def close(self):
        (self.root / f"closed-{os.getpid()}").touch()


def fixture_operation(operation, payload, slot, job_id):
    from rag_pipeline.api import build_operations
    cancellation = build_operations.WorkerCancellation(slot)
    root = build_operations._manager.root
    token = payload["token"]
    (root / f"started-{token}").write_text(str(os.getpid()))
    build_operations._progress_queue.put((job_id, {"stage": "started", "token": token}))
    if operation == "failed":
        raise ValueError("invalid build fixture")
    if operation == "http_error":
        return {"error": {"status_code": 409, "detail": "incompatible snapshot"}}
    if operation == "crashed":
        os._exit(7)
    while operation == "blocked" and not (root / f"release-{token}").exists():
        if cancellation.is_set():
            # Delayed cleanup verifies cancellation joins rather than abandoning.
            time.sleep(0.03)
            (root / f"cancelled-{token}").touch()
            return {"error": {"status_code": 500, "detail": "cancelled"}}
        time.sleep(0.005)
    return {"result": {"pid": os.getpid(), "token": token, "cache_hit": False}}


def workers(tmp_path, capacity=2):
    return BuildWorkerPool(
        RAGConfig(structural_index_root=str(tmp_path), full_index_concurrency=capacity),
        manager_factory=FixtureManager, runner=fixture_operation,
    )


async def wait_for_file(path):
    async def wait():
        while not path.exists():
            await asyncio.sleep(0.01)
    await asyncio.wait_for(wait(), 20)


@pytest.mark.asyncio
async def test_processes_overlap_progress_isolated_and_queued_cancel_never_starts(tmp_path):
    pool = workers(tmp_path)
    events = []
    tasks = [asyncio.create_task(pool.run("blocked", {"token": token}, progress_callback=events.append)) for token in ("one", "two")]
    try:
        await asyncio.gather(*(wait_for_file(tmp_path / f"started-{token}") for token in ("one", "two")))
        pids = {(tmp_path / f"started-{token}").read_text() for token in ("one", "two")}
        assert len(pids) == 2
        assert str(os.getpid()) not in pids
        queued = asyncio.create_task(pool.run("ready", {"token": "queued"}))
        await asyncio.sleep(0.02)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        assert not (tmp_path / "started-queued").exists()
        for token in ("one", "two"):
            (tmp_path / f"release-{token}").touch()
        results = await asyncio.gather(*tasks)
        assert {result["token"] for result in results} == {"one", "two"}
        assert {event["token"] for event in events} == {"one", "two"}
    finally:
        await pool.close()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert len(list(tmp_path.glob("closed-*"))) == 2


@pytest.mark.asyncio
async def test_cancelled_admitted_work_finishes_cleanup_before_return_and_reuses_slot(tmp_path):
    pool = workers(tmp_path, 1)
    task = asyncio.create_task(pool.run("blocked", {"token": "cancel"}))
    try:
        await wait_for_file(tmp_path / "started-cancel")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (tmp_path / "cancelled-cancel").exists()
        assert (await pool.run("ready", {"token": "next"}))["token"] == "next"
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_errors_preserve_details_and_later_work_can_run(tmp_path):
    pool = workers(tmp_path, 1)
    try:
        with pytest.raises(HTTPException) as conflict:
            await pool.run("http_error", {"token": "http"})
        assert (conflict.value.status_code, conflict.value.detail) == (409, "incompatible snapshot")
        with pytest.raises(ValueError, match="invalid build fixture"):
            await pool.run("failed", {"token": "error"})
        assert (await pool.run("ready", {"token": "next"}))["token"] == "next"
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_preparation_singleflight_keeps_follower_when_first_request_cancels(tmp_path):
    pool = workers(tmp_path, 2)
    first = asyncio.create_task(pool.run_coalesced(("tenant", "overlay"), "blocked", {"token": "same"}))
    second = None
    try:
        await wait_for_file(tmp_path / "started-same")
        second = asyncio.create_task(pool.run_coalesced(("tenant", "overlay"), "blocked", {"token": "same"}))
        await asyncio.sleep(0)
        first.cancel()
        await asyncio.sleep(0.05)
        assert not first.done()
        assert not (tmp_path / "cancelled-same").exists()
        (tmp_path / "release-same").touch()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert (await second)["cache_hit"] is True
        assert not pool._flights
        assert len(list(tmp_path.glob("started-*"))) == 1
    finally:
        await pool.close()
        await asyncio.gather(first, *([second] if second else []), return_exceptions=True)


@pytest.mark.asyncio
async def test_stream_waiter_and_async_call_share_process_capacity_and_shutdown_joins(tmp_path):
    pool = workers(tmp_path, 1)
    cancellation = Event()
    task = asyncio.create_task(asyncio.to_thread(pool.run_blocking, "blocked", {"token": "stream"}, cancellation_event=cancellation))
    try:
        await wait_for_file(tmp_path / "started-stream")
        queued = asyncio.create_task(pool.run("ready", {"token": "queued"}))
        await asyncio.sleep(0.02)
        assert not (tmp_path / "started-queued").exists()
        await pool.close()
        with pytest.raises(HTTPException):
            await task
        with pytest.raises(RuntimeError, match="shutting down"):
            await queued
        assert (tmp_path / "cancelled-stream").exists()
        assert len(list(tmp_path.glob("closed-*"))) == 1
    finally:
        if not pool._closed:
            await pool.close()


@pytest.mark.asyncio
async def test_last_cancelled_preparation_is_removed_before_new_request_joins(tmp_path):
    pool = workers(tmp_path, 2)
    first = asyncio.create_task(pool.run_coalesced(("same",), "blocked", {"token": "old"}))
    try:
        await wait_for_file(tmp_path / "started-old")
        first.cancel()
        await asyncio.sleep(0)
        result = await pool.run_coalesced(("same",), "ready", {"token": "replacement"})
        assert result["token"] == "replacement"
        assert result["cache_hit"] is False
        with pytest.raises(asyncio.CancelledError):
            await first
        assert (tmp_path / "cancelled-old").exists()
        assert not pool._flights
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_failed_executor_creation_releases_job_and_slot(tmp_path, monkeypatch):
    pool = workers(tmp_path, 1)
    original = pool._new_executor
    try:
        def fail():
            raise OSError("cannot create process")
        monkeypatch.setattr(pool, "_new_executor", fail)
        with pytest.raises(OSError, match="cannot create process"):
            await pool.run("ready", {"token": "failed"})
        assert not pool._jobs
        assert pool._slots.qsize() == 1
        monkeypatch.setattr(pool, "_new_executor", original)
        assert (await pool.run("ready", {"token": "next"}))["token"] == "next"
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_process_crash_is_joined_without_stopping_an_independent_worker(tmp_path):
    from concurrent.futures.process import BrokenProcessPool
    pool = workers(tmp_path, 2)
    survivor = asyncio.create_task(pool.run("blocked", {"token": "survivor"}))
    try:
        await wait_for_file(tmp_path / "started-survivor")
        survivor_pid = int((tmp_path / "started-survivor").read_text())
        with pytest.raises(BrokenProcessPool):
            await pool.run("crashed", {"token": "crash"})
        crash_pid = int((tmp_path / "started-crash").read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(crash_pid, 0)
        assert not survivor.done()
        os.kill(survivor_pid, 0)
        assert (await pool.run("ready", {"token": "next"}))["token"] == "next"
        (tmp_path / "release-survivor").touch()
        assert (await survivor)["pid"] == survivor_pid
        assert not pool._jobs
    finally:
        await pool.close()


class OwnedSourceManager(FixtureManager):
    @staticmethod
    def _raise_if_cancelled(event):
        from rag_pipeline.core.index_manager.build_support import RepositoryIndexCancelled
        if event.is_set():
            raise RepositoryIndexCancelled("cancelled")

    def index_repository(self, **kwargs):
        source = Path(kwargs["repo_path"])
        assert kwargs["source_tree_exclusively_owned"] is True
        assert (source / "example.py").read_text() == "def example(): return True\n"
        (self.root / "worker-source").write_text(str(source))
        kwargs["progress_callback"]({"stage": "indexing", "message": "child source open"})
        while not kwargs["cancellation_event"].is_set():
            time.sleep(0.005)
        time.sleep(0.04)
        assert source.exists(), "source removed before child finished cleanup"
        (self.root / "source-survived-cancellation").touch()
        self._raise_if_cancelled(kwargs["cancellation_event"])


@pytest.mark.asyncio
async def test_stream_process_disconnect_retains_transferred_source_until_child_cleanup(tmp_path, monkeypatch):
    from rag_pipeline.api.build_operations import execute_build_operation
    from rag_pipeline.api.models import IndexRequest
    from rag_pipeline.api.routers import index
    monkeypatch.setenv("ALLOWED_REPO_ROOT", str(tmp_path))
    source = tmp_path / "codecrow-rag-branch-generation-process-test"
    source.mkdir()
    (source / "example.py").write_text("def example(): return True\n")
    pool = BuildWorkerPool(
        RAGConfig(structural_index_root=str(tmp_path), full_index_concurrency=1),
        manager_factory=OwnedSourceManager, runner=execute_build_operation,
    )
    monkeypatch.setattr(index, "get_build_workers", lambda: pool)
    monkeypatch.setattr(index, "_get_singletons", lambda: (None, object()))
    request = IndexRequest(
        repo_path=str(source), workspace="ws", project="project", branch="main",
        commit="source", transfer_repo_ownership=True,
    )
    body = None
    try:
        body = index.index_repository_stream(request).body_iterator
        assert '"type": "admitted"' in await anext(body)
        await wait_for_file(tmp_path / "worker-source")
        owned = Path((tmp_path / "worker-source").read_text())
        assert owned.exists() and not source.exists()
        await body.aclose()
        await asyncio.wait_for(index.drain_index_repository_stream_workers(), 20)
        assert (tmp_path / "source-survived-cancellation").exists()
        assert not owned.exists()
    finally:
        if body is not None:
            await body.aclose()
        await index.drain_index_repository_stream_workers()
        await pool.close()


@pytest.mark.asyncio
async def test_cancel_during_retirement_joins_once_before_releasing_caller(tmp_path):
    pool = workers(tmp_path, 1)
    started, release = Event(), Event()
    shutdown_calls = []

    class Executor:
        def shutdown(self, **kwargs):
            shutdown_calls.append(kwargs)
            started.set()
            release.wait()

    executor = Executor()
    retired = pool._retire_executor(executor)
    waiter = asyncio.create_task(pool._join_retirement(retired))
    try:
        await asyncio.wait_for(asyncio.to_thread(started.wait), 5)
        assert pool._retire_executor(executor) is retired
        waiter.cancel()
        await asyncio.sleep(0.02)
        assert not waiter.done()
        closing = asyncio.create_task(pool.close())
        await asyncio.sleep(0.02)
        assert not closing.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        await closing
        assert len(shutdown_calls) == 1
    finally:
        release.set()
        if not pool._closed:
            await pool.close()


@pytest.mark.asyncio
async def test_failed_broken_executor_replacement_does_not_leak_job_or_slot(tmp_path, monkeypatch):
    from concurrent.futures.process import BrokenProcessPool
    pool = workers(tmp_path, 1)

    class BrokenExecutor:
        def submit(self, *args):
            raise BrokenProcessPool("worker unavailable")
        def shutdown(self, **kwargs):
            pass

    monkeypatch.setattr(pool, "_new_executor", BrokenExecutor)
    try:
        with pytest.raises(BrokenProcessPool):
            await pool.run("ready", {"token": "broken"})
        assert not pool._jobs
        assert pool._slots.qsize() == 1
    finally:
        await pool.close()
