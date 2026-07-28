import asyncio
import json
import logging
import os
import traceback
from typing import Dict, Any, Optional
import redis.asyncio as redis
from pydantic import ValidationError
from redis.exceptions import TimeoutError as RedisTimeoutError

from model.dtos import ReviewRequestDto
from service.review.review_service import ReviewService

logger = logging.getLogger(__name__)

class RedisQueueConsumer:
    """
    Consumes analysis jobs from a Redis List queue and processes them
    using the ReviewService. Events and final results are pushed back 
    to a job-specific Redis event queue.
    
    Uses Redis DB 1 by default to isolate from Spring Session data (DB 0).
    """
    
    def __init__(self, review_service: ReviewService):
        self.review_service = review_service
        # Default to DB 1 (/1 suffix) to isolate from Spring Session (DB 0)
        self.redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/1")
        self.job_queue_key = "codecrow:analysis:jobs"
        self.is_running = False
        self._redis: Optional[redis.Redis] = None
        self._task: Optional[asyncio.Task] = None
        # Bound concurrent job processing to prevent memory pressure
        max_concurrent = int(os.environ.get("MAX_CONCURRENT_REVIEWS", "4"))
        self._job_semaphore = asyncio.Semaphore(max_concurrent)
        self.heartbeat_seconds = max(
            1.0,
            float(os.environ.get("ANALYSIS_QUEUE_HEARTBEAT_SECONDS", "30")),
        )

    async def start(self):
        """Start the consumer background loop."""
        if self.is_running:
            return
            
        logger.info(f"Starting Redis Queue Consumer connected to {self.redis_url}")
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
            logger.info("Redis Queue Consumer stopped")

    async def _consume_loop(self):
        """Infinite loop blocking on the Redis queue for new jobs."""
        logger.info(f"Listening for jobs on '{self.job_queue_key}'...")
        while self.is_running:
            permit_acquired = False
            try:
                # Reserve worker capacity before removing durable work from
                # Redis. Producer supervision is already active, so a dequeued
                # job must be able to acknowledge and heartbeat immediately.
                await self._job_semaphore.acquire()
                permit_acquired = True
                if not self.is_running:
                    break

                # Block until a job is available or timeout (1 second for graceful shutdown check)
                result = await self._redis.brpop([self.job_queue_key], timeout=1)
                
                if not result:
                    continue
                    
                queue_name, payload_str = result
                logger.debug(f"Received raw job payload from {queue_name}")
                
                # Transfer ownership of the reserved permit to the job task.
                asyncio.create_task(self._handle_admitted_job(payload_str))
                permit_acquired = False
                
            except asyncio.CancelledError:
                break
            except RedisTimeoutError as error:
                logger.warning("Redis review queue read timed out; retrying: %s", error)
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Error in Redis consume loop: {e}", exc_info=True)
                await asyncio.sleep(2)  # Backoff on error
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
        """Process a single job popped from the queue."""
        job_id = "UNKNOWN"
        event_queue_key = None
        publish_tail: Optional[asyncio.Future] = None
        
        try:
            payload = json.loads(payload_str)
            job_id = payload.get("job_id")
            request_data = payload.get("request")
            
            if not job_id or not request_data:
                logger.error(f"Invalid job payload structure. Missing job_id or request: {payload_str[:100]}...")
                return

            event_queue_key = f"codecrow:analysis:events:{job_id}"
            logger.info(f"Processing Job ID: {job_id}")
            
            # Parse the request into DTO
            request_dto = ReviewRequestDto(**request_data)
            logger.info(
                "Job %s branch payload: source=%s target=%s pr=%s",
                job_id,
                request_dto.sourceBranchName,
                request_dto.targetBranchName,
                request_dto.pullRequestId,
            )
            
            # Serialize all progress and terminal events. Review stages expose a
            # synchronous callback, but Redis publication is asynchronous; launching
            # unrelated fire-and-forget tasks can let the terminal event overtake
            # Stage 0-3 evidence and cause the Java producer to delete the queue
            # before those events are observed.
            loop = asyncio.get_running_loop()
            publish_tail = loop.create_future()
            publish_tail.set_result(None)

            def event_callback(event: Dict[str, Any]):
                nonlocal publish_tail
                previous = publish_tail

                async def publish_after_previous():
                    await previous
                    await self._publish_event(event_queue_key, event)

                publish_tail = asyncio.create_task(publish_after_previous())

            # Tell the java engine we picked it up
            event_callback({
                "type": "status", 
                "state": "acknowledged", 
                "message": "Orchestrator picked up job from queue"
            })

            # Process the normal review path while emitting worker-liveness events.
            # The Java producer supervises inactivity rather than total elapsed time,
            # so a healthy large review is not orphaned at an arbitrary wall-clock
            # boundary.
            review_task = asyncio.create_task(
                self.review_service.process_review_request(request_dto, event_callback)
            )
            while True:
                done, _ = await asyncio.wait(
                    {review_task},
                    timeout=self.heartbeat_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if done:
                    break
                event_callback({
                    "type": "status",
                    "state": "processing",
                    "message": "Review pipeline is still processing",
                })

            result = await review_task
            
            # Determine if the result contains an error inside the 'result' key, or is a pure success
            if "result" in result and isinstance(result["result"], dict) and result["result"].get("status") == "error":
                event_callback({"type": "error", "message": result["result"].get("message", "Unknown error in processing")})
            else:
                event_callback({"type": "final", "result": result.get("result", result)})

            await publish_tail
            logger.info(f"Job ID {job_id} processing completed successfully.")

        except ValidationError as ve:
            logger.error(f"Job ID {job_id} Validation Error: {ve}")
            if event_queue_key:
                event = {
                    "type": "error",
                    "message": f"Input validation error: {str(ve)}"
                }
                if publish_tail is None:
                    await self._publish_event(event_queue_key, event)
                else:
                    event_callback(event)
                    await publish_tail
        except Exception as e:
            logger.error(f"Job ID {job_id} Unhandled Error: {e}", exc_info=True)
            if event_queue_key:
                event = {
                    "type": "error",
                    "message": f"Internal orchestrator error: {str(e)}"
                }
                if publish_tail is None:
                    await self._publish_event(event_queue_key, event)
                else:
                    event_callback(event)
                    await publish_tail

    async def _publish_event(self, key: str, event: Dict[str, Any]):
        """Publish an event back to the job's specific event list. LPUSH (Java uses rightPop)."""
        try:
            if not self._redis:
                return
            event_str = json.dumps(event, default=str) # Handle date/obj serialization
            # Expire the event queue after a reasonable TTL (e.g. 1 hour) so it doesn't leak memory
            pipeline = self._redis.pipeline()
            pipeline.lpush(key, event_str)
            pipeline.expire(key, 3600)
            await pipeline.execute()
        except Exception as e:
            logger.error(f"Failed to publish event to {key}: {e}")
