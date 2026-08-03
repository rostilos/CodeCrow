import asyncio
import json
import logging
import os
import shutil
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
                if not result:
                    continue
                    
                queue_name, payload_str = result
                logger.debug(f"Received raw RAG job payload from {queue_name}")
                asyncio.create_task(self._handle_admitted_job(payload_str))
                permit_acquired = False
                
            except asyncio.CancelledError:
                break
            except RedisTimeoutError as error:
                logger.warning("Redis RAG queue read timed out; retrying: %s", error)
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Error in RAG Queue consume loop: {e}", exc_info=True)
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
        indexing_future = None
        
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

            def publish_progress(event: Dict[str, Any]) -> None:
                nonlocal progress_delivery_available
                if not progress_delivery_available:
                    return
                payload = {"type": "status", **event}
                future = asyncio.run_coroutine_threadsafe(
                    self._publish_event(event_queue_key, payload),
                    loop,
                )
                # Surface Redis publication failures promptly without coupling
                # indexing correctness to progress delivery.
                try:
                    future.result(timeout=5)
                except Exception as exception:
                    progress_delivery_available = False
                    logger.warning(
                        "Could not publish RAG progress for job %s: %s",
                        job_id,
                        exception,
                    )

            indexing_future = loop.run_in_executor(
                None,
                lambda: self.index_manager.index_repository(
                    repo_path=request_dto.repo_path,
                    workspace=request_dto.workspace,
                    project=request_dto.project,
                    branch=request_dto.branch,
                    commit=request_dto.commit,
                    source_tree_sha256=request_dto.source_tree_sha256,
                    preserve_other_branches=request_dto.preserve_other_branches,
                    include_patterns=request_dto.include_patterns,
                    exclude_patterns=request_dto.exclude_patterns,
                    progress_callback=publish_progress,
                )
            )
            while True:
                done, _ = await asyncio.wait(
                    {indexing_future},
                    timeout=self.heartbeat_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if done:
                    break
                await self._publish_event(event_queue_key, {
                    "type": "status",
                    "state": "processing",
                    "message": "RAG indexing is still processing",
                })

            result_obj = await indexing_future
            
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
            if "request_dto" in locals() and request_dto.cleanup_repo_path:
                if indexing_future is None or indexing_future.done():
                    await asyncio.to_thread(
                        self._cleanup_owned_repository_path,
                        request_dto.repo_path,
                    )
                else:
                    logger.warning(
                        "Preserving RAG job workspace because indexing is still active: %s",
                        request_dto.repo_path,
                    )

    async def _publish_event(self, key: str, event: Dict[str, Any]):
        """Publish an event back to the job's specific event list. LPUSH (Java uses rightPop)."""
        try:
            if not self._redis:
                return
            event_json = json.dumps(event)
            await self._redis.lpush(key, event_json)
            await self._redis.expire(key, self.event_ttl_seconds)
        except Exception as e:
            logger.error(f"Failed to publish event to {key}: {e}")

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
