"""Bounded CPU processes shared by indexing and proposed-tree preparation."""
from __future__ import annotations

import asyncio
import logging
import multiprocessing
import time
from concurrent.futures import Future, ProcessPoolExecutor, TimeoutError as FutureTimeout
from concurrent.futures.process import BrokenProcessPool
from queue import Empty, LifoQueue
from threading import Lock, Thread
from uuid import uuid4
from weakref import WeakKeyDictionary

import anyio
from fastapi import HTTPException

from ..core.index_manager import RAGIndexManager
from ..core.index_manager.build_support import RepositoryIndexCancelled
from .build_operations import execute_build_operation, initialize_build_worker

logger = logging.getLogger(__name__)


class BuildWorkerPool:
    """One admitted operation per process, with query work left in the API host."""

    def __init__(self, config, *, manager_factory=RAGIndexManager, runner=execute_build_operation):
        self.capacity = config.full_index_concurrency
        self._config = config.model_dump(mode="json")
        self._manager_factory, self._runner = manager_factory, runner
        self._context = multiprocessing.get_context("spawn")
        self._cancel_flags = self._context.RawArray("b", self.capacity)
        # Progress is coarse (stage/file batches), unlike cancellation checks.
        # A manager-owned queue cannot be poisoned when a child exits while a
        # multiprocessing.Queue feeder holds its shared writer lock.
        self._progress_manager = self._context.Manager()
        self._progress_queue = self._progress_manager.Queue()
        self._slots = LifoQueue()
        for slot in range(self.capacity):
            self._slots.put(slot)
        self._lock = Lock()
        self._jobs = {}
        self._closed = False
        self._executors = {}
        self._retirement_lock = Lock()
        self._retirements = WeakKeyDictionary()
        self._flights = {}
        self._reader = Thread(target=self._read_progress, name="rag-build-progress", daemon=True)
        self._reader.start()

    def _read_progress(self):
        while True:
            try:
                event = self._progress_queue.get()
            except (EOFError, OSError):
                logger.warning("Build progress transport stopped; build results remain independent")
                return
            if event is None:
                return
            job_id, payload = event
            with self._lock:
                callback = self._jobs.get(job_id)
            if callback is not None:
                try:
                    callback(payload)
                except Exception:
                    logger.debug("Build progress observer failed", exc_info=True)

    def _submit(self, operation, payload, slot, progress_callback):
        job_id = uuid4().hex
        with self._lock:
            if self._closed:
                raise RuntimeError("Repository build workers are shutting down")
            self._cancel_flags[slot] = 0
            self._jobs[job_id] = progress_callback
            try:
                executor = self._executors.get(slot)
                if executor is None:
                    executor = self._executors[slot] = self._new_executor()
                try:
                    future = executor.submit(self._runner, operation, payload, slot, job_id)
                except BrokenProcessPool:
                    # Only this slot failed. Other independent builds continue.
                    # submit failed before admission, so replacement cannot
                    # replay an operation that already started.
                    self._retire_executor(executor)
                    executor = self._executors[slot] = self._new_executor()
                    future = executor.submit(self._runner, operation, payload, slot, job_id)
            except BaseException:
                self._jobs.pop(job_id, None)
                raise
        return job_id, future, executor

    def _new_executor(self):
        # One reusable child per slot avoids dynamic process registration
        # races in older supported Python executors. It also confines a child
        # crash to one operation. LIFO slots reuse warm children for serial work.
        return ProcessPoolExecutor(
            max_workers=1,
            mp_context=self._context,
            initializer=initialize_build_worker,
            initargs=(self._config, self._cancel_flags, self._progress_queue, self._manager_factory),
        )

    def _retire_executor(self, executor, *, cancel_futures=False):
        """One shutdown/join per executor even when many children fail together."""
        with self._retirement_lock:
            retired = self._retirements.get(executor)
            if retired is not None:
                return retired
            retired = Future()
            self._retirements[executor] = retired

        def shutdown():
            try:
                executor.shutdown(wait=True, cancel_futures=cancel_futures)
            except BaseException as error:
                retired.set_exception(error)
            else:
                retired.set_result(None)

        Thread(target=shutdown, name="rag-build-shutdown", daemon=False).start()
        return retired

    @staticmethod
    async def _join_retirement(retired):
        future = asyncio.wrap_future(retired)
        cancelled = False
        with anyio.CancelScope(shield=True):
            while not future.done():
                try:
                    await asyncio.shield(future)
                except asyncio.CancelledError:
                    cancelled = True
            future.result()
        if cancelled:
            raise asyncio.CancelledError

    def _release(self, slot, job_id):
        with self._lock:
            self._jobs.pop(job_id, None)
        self._slots.put(slot)

    @staticmethod
    def _result(envelope):
        if "error" in envelope:
            raise HTTPException(**envelope["error"])
        return envelope["result"]

    @staticmethod
    def _log_admission(operation, payload, queued_at):
        admitted_at = time.monotonic()
        request = payload.get("request", {})
        logger.info(
            "Repository build admitted: operation=%s workspace=%s project=%s queue_wait_ms=%d",
            operation, request.get("workspace"), request.get("project"),
            round((admitted_at - queued_at) * 1000),
        )
        return admitted_at

    @staticmethod
    def _log_completion(operation, payload, admitted_at, outcome):
        request = payload.get("request", {})
        logger.info(
            "Repository build finished: operation=%s workspace=%s project=%s outcome=%s execution_ms=%d",
            operation, request.get("workspace"), request.get("project"), outcome,
            round((time.monotonic() - admitted_at) * 1000),
        )

    async def run(self, operation, payload, *, progress_callback=None):
        """Cancellation joins admitted work before its source snapshot can close."""
        queued_at = time.monotonic()
        while True:
            if self._closed:
                raise RuntimeError("Repository build workers are shutting down")
            try:
                slot = self._slots.get_nowait()
                break
            except Empty:
                await asyncio.sleep(0.01)
        job_id = None
        admitted_at = self._log_admission(operation, payload, queued_at)
        outcome = "failed"
        try:
            job_id, submitted, executor = self._submit(operation, payload, slot, progress_callback)
            future = asyncio.wrap_future(submitted)
            try:
                result = self._result(await asyncio.shield(future))
                outcome = "completed"
                return result
            except BrokenProcessPool:
                # BrokenProcessPool is delivered before its surviving children
                # have necessarily stopped. Join before source ownership closes.
                await self._join_retirement(self._retire_executor(executor))
                raise
            except asyncio.CancelledError:
                outcome = "cancelled"
                self._cancel_flags[slot] = 1
                # ASGI cancellation scopes can repeatedly cancel awaits. Both
                # scope shielding and asyncio shielding are required to join
                # the child before returning ownership to caller cleanup.
                with anyio.CancelScope(shield=True):
                    while not future.done():
                        try:
                            await asyncio.shield(future)
                        except asyncio.CancelledError:
                            continue
                        except Exception:
                            break
                    if future.done() and not future.cancelled():
                        error = future.exception()
                        if isinstance(error, BrokenProcessPool):
                            await self._join_retirement(self._retire_executor(executor))
                raise
        finally:
            self._log_completion(operation, payload, admitted_at, outcome)
            self._release(slot, job_id)

    def run_blocking(self, operation, payload, *, progress_callback=None, cancellation_event=None):
        """A stream coordinator waits here; CPU work runs in the shared process pool."""
        queued_at = time.monotonic()
        while True:
            if self._closed:
                raise RuntimeError("Repository build workers are shutting down")
            if cancellation_event is not None and cancellation_event.is_set():
                raise RepositoryIndexCancelled("structural repository indexing was cancelled")
            try:
                slot = self._slots.get(timeout=0.05)
                break
            except Empty:
                continue
        job_id = None
        admitted_at = self._log_admission(operation, payload, queued_at)
        outcome = "failed"
        try:
            job_id, future, executor = self._submit(operation, payload, slot, progress_callback)
            while True:
                if cancellation_event is not None and cancellation_event.is_set():
                    self._cancel_flags[slot] = 1
                    outcome = "cancelled"
                try:
                    result = self._result(future.result(timeout=0.05))
                    outcome = "completed"
                    return result
                except FutureTimeout:
                    if future.done():
                        raise
                    continue
                except BrokenProcessPool:
                    self._retire_executor(executor).result()
                    raise
        finally:
            self._log_completion(operation, payload, admitted_at, outcome)
            self._release(slot, job_id)

    async def run_coalesced(self, key, operation, payload):
        """Share one parent-side preparation across otherwise isolated children."""
        flight = self._flights.get(key)
        follower = flight is not None
        if flight is None:
            flight = [asyncio.create_task(self.run(operation, payload)), 0]
            self._flights[key] = flight
        task = flight[0]
        flight[1] += 1
        cancelled = False
        try:
            result = await asyncio.shield(task)
            return {**result, "cache_hit": True} if follower else result
        except asyncio.CancelledError:
            cancelled = True
            flight[1] -= 1
            if flight[1] == 0:
                if self._flights.get(key) is flight:
                    self._flights.pop(key, None)
                task.cancel()
            with anyio.CancelScope(shield=True):
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
            if task.done() and not task.cancelled():
                task.exception()
            raise
        finally:
            if not cancelled:
                flight[1] -= 1
            if flight[1] == 0 and self._flights.get(key) is flight:
                self._flights.pop(key, None)

    async def close(self):
        """Signal workers, finish their cleanup, then close IPC resources."""
        with self._lock:
            self._closed = True
            for slot in range(self.capacity):
                self._cancel_flags[slot] = 1
            executors = tuple(self._executors.values())
        for executor in executors:
            self._retire_executor(executor, cancel_futures=True)
        with self._retirement_lock:
            retirements = tuple(self._retirements.values())
        cancelled = False
        for retired in retirements:
            try:
                await self._join_retirement(retired)
            except asyncio.CancelledError:
                cancelled = True
        try:
            self._progress_queue.put(None)
        except (EOFError, OSError):
            logger.debug("Build progress transport was already closed", exc_info=True)
        with anyio.CancelScope(shield=True):
            joined = asyncio.create_task(asyncio.to_thread(self._reader.join))
            while not joined.done():
                try:
                    await asyncio.shield(joined)
                except asyncio.CancelledError:
                    cancelled = True
        self._progress_manager.shutdown()
        if cancelled:
            raise asyncio.CancelledError
