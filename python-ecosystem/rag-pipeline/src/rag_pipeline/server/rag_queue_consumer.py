import asyncio
from concurrent.futures import Future as ConcurrentFuture, ThreadPoolExecutor
import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Dict, Any, Optional
import redis.asyncio as redis
from pydantic import ValidationError
from redis.exceptions import TimeoutError as RedisTimeoutError

from ..api.models import IndexRequest
from ..core.index_manager import RAGIndexManager

logger = logging.getLogger(__name__)

class RAGQueueConsumer:
    """
    Consumes RAG indexing jobs from a Redis List queue and processes them
    using the RAGIndexManager. Events and final results are pushed back 
    to a job-specific Redis event queue over the 4-hour indexing process.
    """
    
    def __init__(self, index_manager: RAGIndexManager):
        self.index_manager = index_manager
        self.redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/1")
        self.job_queue_key = "codecrow:queue:rag"
        self.is_running = False
        self._redis: Optional[redis.Redis] = None
        self._task: Optional[asyncio.Task] = None
        self._job_tasks: set[asyncio.Task] = set()
        self._index_workers: set[ConcurrentFuture] = set()
        self._index_workers_lock = threading.Lock()
        self._queue_read_unavailable = False
        self._event_delivery_unavailable = False
        max_concurrent = int(os.environ.get("MAX_CONCURRENT_RAG_JOBS", "2"))
        self._job_semaphore = asyncio.Semaphore(max_concurrent)
        self.heartbeat_seconds = max(
            1.0, float(os.environ.get("RAG_INDEX_HEARTBEAT_SECONDS", "30"))
        )
        self.event_ttl_seconds = max(
            60, int(os.environ.get("RAG_EVENT_TTL_SECONDS", "1800"))
        )

    async def start(self):
        """Start the consumer background loop."""
        if self.is_running:
            return
            
        logger.info(f"Starting RAG Queue Consumer connected to {self.redis_url}")
        self._redis = redis.from_url(
            self.redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=30,
            health_check_interval=30,
        )
        self.is_running = True
        self._task = asyncio.create_task(self._consume_loop())

    async def stop(self):
        """Stop processing new jobs and close connections."""
        self.is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        # A dequeued job is already admitted durable work. Let every admitted
        # index call finish before API shutdown closes its embedding and
        # Qdrant clients. Deployment supervisors may still enforce their
        # outer termination deadline, but this service must not manufacture a
        # client-use-after-close race itself.
        active_jobs = tuple(self._job_tasks)
        if active_jobs:
            logger.info(
                "Waiting for %s admitted RAG indexing jobs before shutdown",
                len(active_jobs),
            )
            await asyncio.gather(*active_jobs, return_exceptions=True)

        # A job task can be canceled independently while its synchronous
        # indexing call is still running. Wait for that admitted durable work
        # before the application closes shared embedding and Qdrant clients.
        await self._wait_for_index_workers()
        
        if self._redis:
            await self._redis.aclose()
            logger.info("RAG Queue Consumer stopped")

    async def _consume_loop(self):
        """Infinite loop blocking on the Redis queue for new jobs."""
        logger.info(f"Listening for RAG jobs on '{self.job_queue_key}'...")
        while self.is_running:
            permit_acquired = False
            try:
                # Reserve capacity before dequeue. Producer supervision is
                # already active, so an admitted job must be able to
                # acknowledge immediately.
                await self._job_semaphore.acquire()
                permit_acquired = True
                if not self.is_running:
                    break

                result = await self._redis.brpop([self.job_queue_key], timeout=1)
                self._record_queue_read_recovery()
                if not result:
                    continue
                    
                queue_name, payload_str = result
                logger.debug(f"Received raw RAG job payload from {queue_name}")
                job_task = asyncio.create_task(
                    self._handle_admitted_job(payload_str)
                )
                self._job_tasks.add(job_task)
                job_task.add_done_callback(self._job_tasks.discard)
                permit_acquired = False
                
            except asyncio.CancelledError:
                break
            except RedisTimeoutError as error:
                self._record_queue_read_failure(error)
                await asyncio.sleep(1)
            except Exception as e:
                self._record_queue_read_failure(e)
                await asyncio.sleep(2)
            finally:
                if permit_acquired:
                    self._job_semaphore.release()

    async def _handle_admitted_job(self, payload_str: str):
        """Process a job using the capacity reserved before dequeue."""
        try:
            await self._handle_job(payload_str)
        finally:
            self._job_semaphore.release()

    async def _bounded_handle_job(self, payload_str: str):
        """Compatibility helper for direct callers that do not pre-admit work."""
        async with self._job_semaphore:
            await self._handle_job(payload_str)

    async def _handle_job(self, payload_str: str):
        """Process a single RAG job popped from the queue."""
        job_id = "UNKNOWN"
        event_queue_key = None
        index_worker_future = None
        index_executor = None
        progress_queue = None
        progress_publisher_task = None
        progress_delivery_available = False
        
        try:
            payload = json.loads(payload_str)
            job_id = payload.get("job_id")
            request_data = payload.get("request")
            
            if not job_id or not request_data:
                logger.error(f"Invalid RAG job payload structure. Missing job_id or request: {payload_str[:100]}...")
                return

            event_queue_key = f"codecrow:analysis:events:{job_id}"
            queued_at_epoch_ms = payload.get("queued_at_epoch_ms")
            queue_wait_ms = None
            if isinstance(queued_at_epoch_ms, (int, float)):
                queue_wait_ms = max(
                    0,
                    round(time.time() * 1000 - queued_at_epoch_ms),
                )
            logger.info(
                "Processing RAG index job job_id=%s queue_wait_ms=%s",
                job_id,
                queue_wait_ms,
            )
            
            # The Java pipeline passes IndexRequest payload wrapped inside job_id/request
            request_dto = IndexRequest(**request_data)

            # Acknowledge start of indexing
            await self._publish_event(event_queue_key, {
                "type": "status", 
                "state": "acknowledged", 
                "message": "RAG pipeline picked up indexing job from queue"
            })
            
            # Start indexing - it takes a long time
            # index_manager.index_repository is synchronous, so we run it in an executor
            loop = asyncio.get_running_loop()
            progress_delivery_available = True
            # Progress is observability, so cap producer pressure and retain
            # only the latest pending status when Redis publication is slower
            # than indexing.
            progress_queue = asyncio.Queue(maxsize=1)

            async def publish_progress_events() -> None:
                while True:
                    event = await progress_queue.get()
                    try:
                        if event is None:
                            return
                        await self._publish_event(event_queue_key, event)
                    finally:
                        progress_queue.task_done()

            progress_publisher_task = asyncio.create_task(
                publish_progress_events()
            )

            def publish_progress(event: Dict[str, Any]) -> None:
                if not progress_delivery_available:
                    return
                payload = {"type": "status", **event}
                try:
                    # Transfer plain data to the event-loop thread. Creating
                    # cross-loop coroutine futures here used to retain the
                    # default executor thread after a job completed and could
                    # hang process/test-loop shutdown.
                    loop.call_soon_threadsafe(
                        self._coalesce_progress_event,
                        progress_queue,
                        payload,
                    )
                except RuntimeError:
                    # The service is shutting down. Progress is auxiliary and
                    # must never keep the indexing worker alive.
                    return

            # Keep long-lived indexing work out of asyncio's process-wide
            # default executor. A per-job executor has an explicit lifecycle,
            # so completed progress jobs cannot leave idle threads attached to
            # an event loop and block graceful shutdown.
            index_executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="rag-index",
            )

            def run_index_job():
                try:
                    return self.index_manager.index_repository(
                        repo_path=request_dto.repo_path,
                        workspace=request_dto.workspace,
                        project=request_dto.project,
                        branch=request_dto.branch,
                        commit=request_dto.commit,
                        source_tree_sha256=request_dto.source_tree_sha256,
                        preserve_other_branches=request_dto.preserve_other_branches,
                        include_patterns=request_dto.include_patterns,
                        exclude_patterns=request_dto.exclude_patterns,
                        collection_target=request_dto.collection_target,
                        progress_callback=publish_progress,
                    )
                finally:
                    # Cleanup belongs to the synchronous worker lifecycle, so
                    # cancellation can never delete a checkout while that same
                    # worker is still indexing it.
                    if request_dto.cleanup_repo_path:
                        self._cleanup_owned_repository_path(
                            request_dto.repo_path
                        )

            index_worker_future = index_executor.submit(run_index_job)
            self._track_index_worker(index_worker_future)
            next_heartbeat = loop.time() + self.heartbeat_seconds
            while not index_worker_future.done():
                # Poll the thread-safe concurrent future rather than relying
                # on a worker-thread callback to wake the asyncio selector.
                # The latter can be retained until a long heartbeat timeout
                # on some Python/event-loop combinations during shutdown.
                await asyncio.sleep(
                    min(0.1, max(0.0, next_heartbeat - loop.time()))
                )
                if (
                    not index_worker_future.done()
                    and loop.time() >= next_heartbeat
                ):
                    await self._publish_event(event_queue_key, {
                        "type": "status",
                        "state": "processing",
                        "message": "RAG indexing is still processing",
                    })
                    next_heartbeat = loop.time() + self.heartbeat_seconds

            try:
                result_obj = index_worker_future.result()
            finally:
                # The worker has returned, so all progress callbacks have
                # already been enqueued ahead of this sentinel. Drain them
                # before the final/error event to preserve event ordering.
                progress_delivery_available = False
                # A callback queued from the worker can be behind this task's
                # timer wakeup in the loop-ready queue. Yield once so every
                # already-scheduled transfer reaches the bounded queue before
                # it is joined and closed.
                await asyncio.sleep(0)
                await progress_queue.join()
                progress_queue.put_nowait(None)
                await progress_publisher_task
                progress_publisher_task = None
            
            # Serialize the IndexStats result to a dictionary
            result = result_obj.dict() if hasattr(result_obj, "dict") else result_obj.model_dump()
            
            await self._publish_event(event_queue_key, {"type": "final", "result": result})
            logger.info(f"RAG Index Job ID {job_id} processing completed successfully.")

        except ValidationError as ve:
            logger.error(f"RAG Job ID {job_id} Validation Error: {ve}")
            if event_queue_key:
                await self._publish_event(event_queue_key, {
                    "type": "error",
                    "message": f"Input validation error: {str(ve)}"
                })
        except Exception as e:
            logger.error(f"RAG Job ID {job_id} Unhandled Error: {e}", exc_info=True)
            if event_queue_key:
                await self._publish_event(event_queue_key, {
                    "type": "error",
                    "message": f"Internal RAG pipeline error: {str(e)}"
                })
        finally:
            progress_delivery_available = False
            if progress_publisher_task is not None:
                if index_worker_future is not None and not index_worker_future.done():
                    progress_publisher_task.cancel()
                    try:
                        await progress_publisher_task
                    except asyncio.CancelledError:
                        pass
                else:
                    await asyncio.sleep(0)
                    await progress_queue.join()
                    progress_queue.put_nowait(None)
                    await progress_publisher_task
            worker_finished = (
                index_worker_future is None or index_worker_future.done()
            )
            if (
                not worker_finished
                and "request_dto" in locals()
                and request_dto.cleanup_repo_path
            ):
                logger.info(
                    "Deferring owned RAG workspace cleanup until active "
                    "indexing returns: %s",
                    request_dto.repo_path,
                )
            if index_executor is not None:
                # wait=False still marks the executor closed; a live
                # synchronous call exits its worker as soon as it returns.
                # Completed calls are joined before the job task returns.
                index_executor.shutdown(
                    wait=worker_finished,
                    cancel_futures=False,
                )

    def _track_index_worker(self, worker_future: ConcurrentFuture) -> None:
        """Track synchronous indexing independently of its asyncio job task."""
        with self._index_workers_lock:
            self._index_workers.add(worker_future)
        worker_future.add_done_callback(self._retire_index_worker)

    def _retire_index_worker(self, worker_future: ConcurrentFuture) -> None:
        with self._index_workers_lock:
            self._index_workers.discard(worker_future)

    async def _wait_for_index_workers(self) -> None:
        announced = False
        while True:
            with self._index_workers_lock:
                active_count = len(self._index_workers)
            if active_count == 0:
                return
            if not announced:
                logger.info(
                    "Waiting for %s active RAG indexing workers before shutdown",
                    active_count,
                )
                announced = True
            await asyncio.sleep(0.01)

    @staticmethod
    def _coalesce_progress_event(
        progress_queue: asyncio.Queue,
        event: Optional[Dict[str, Any]],
    ) -> None:
        """Keep at most the latest pending progress event or sentinel."""
        if progress_queue.full():
            try:
                pending = progress_queue.get_nowait()
                progress_queue.task_done()
                if pending is None:
                    # Once the publisher is closing, late auxiliary progress
                    # must not replace its sentinel and strand the task.
                    progress_queue.put_nowait(None)
                    return
            except asyncio.QueueEmpty:
                pass
        progress_queue.put_nowait(event)

    async def _publish_event(self, key: str, event: Dict[str, Any]) -> bool:
        """Publish an event back to the job's specific event list. LPUSH (Java uses rightPop)."""
        if not self._redis:
            return False
        try:
            event_json = json.dumps(event)
            # The event and its retention policy are one Redis transaction.
            # A process/network failure cannot commit LPUSH while losing the
            # EXPIRE and leave an orphaned event list indefinitely.
            async with self._redis.pipeline(transaction=True) as pipeline:
                pipeline.lpush(key, event_json)
                pipeline.expire(key, self.event_ttl_seconds)
                await pipeline.execute()
            if self._event_delivery_unavailable:
                logger.info("Redis RAG event delivery recovered")
                self._event_delivery_unavailable = False
            return True
        except Exception as e:
            event_type = event.get("type", "unknown")
            if not self._event_delivery_unavailable:
                logger.warning(
                    "Redis RAG event delivery unavailable; events remain "
                    "fail-open (key=%s type=%s): %s",
                    key,
                    event_type,
                    e,
                )
                self._event_delivery_unavailable = True
            else:
                logger.debug(
                    "Redis RAG event delivery still unavailable "
                    "(key=%s type=%s): %s",
                    key,
                    event_type,
                    e,
                )
            return False

    def _record_queue_read_failure(self, error: BaseException) -> None:
        if not self._queue_read_unavailable:
            logger.warning(
                "Redis RAG queue read unavailable; retrying: %s",
                error,
            )
            self._queue_read_unavailable = True
        else:
            logger.debug(
                "Redis RAG queue read still unavailable; retrying: %s",
                error,
            )

    def _record_queue_read_recovery(self) -> None:
        if self._queue_read_unavailable:
            logger.info("Redis RAG queue read recovered")
            self._queue_read_unavailable = False

    @staticmethod
    def _cleanup_owned_repository_path(repo_path: str) -> None:
        """Remove only an explicitly transferred, direct child of the shared temp root."""
        allowed_root = Path(os.environ.get("ALLOWED_REPO_ROOT", "/tmp")).resolve()
        candidate = Path(repo_path).resolve()
        if (
            candidate.parent != allowed_root
            or not candidate.name.startswith("codecrow-rag-")
        ):
            logger.error(
                "Refusing cleanup of repository path outside the owned temp namespace: %s",
                repo_path,
            )
            return

        try:
            shutil.rmtree(candidate)
            logger.info("Removed completed RAG job workspace: %s", candidate)
        except FileNotFoundError:
            return
        except Exception:
            logger.exception("Failed to remove completed RAG job workspace: %s", candidate)
