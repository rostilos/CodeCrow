import asyncio
import json
import logging
import os
import traceback
from typing import Dict, Any, Optional
import redis.asyncio as redis
from pydantic import ValidationError

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

    async def start(self):
        """Start the consumer background loop."""
        if self.is_running:
            return
            
        logger.info(f"Starting Redis Queue Consumer connected to {self.redis_url}")
        self._redis = redis.from_url(self.redis_url, decode_responses=True)
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
            try:
                # Block until a job is available or timeout (1 second for graceful shutdown check)
                result = await self._redis.brpop([self.job_queue_key], timeout=1)
                
                if not result:
                    continue
                    
                queue_name, payload_str = result
                logger.debug(f"Received raw job payload from {queue_name}")
                
                # Handle the job in a separate task, bounded by the semaphore
                asyncio.create_task(self._bounded_handle_job(payload_str))
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in Redis consume loop: {e}", exc_info=True)
                await asyncio.sleep(2)  # Backoff on error

    async def _bounded_handle_job(self, payload_str: str):
        """Acquire the concurrency semaphore before processing a job."""
        async with self._job_semaphore:
            await self._handle_job(payload_str)

    async def _handle_job(self, payload_str: str):
        """Process a single job popped from the queue."""
        job_id = "UNKNOWN"
        event_queue_key = None
        
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
            
            # Define the event callback that pushes to the event list
            def event_callback(event: Dict[str, Any]):
                # Needs to be scheduled on the event loop since the callback is sync but redis is async
                asyncio.create_task(self._publish_event(event_queue_key, event))

            # Tell the java engine we picked it up
            event_callback({
                "type": "status", 
                "state": "acknowledged", 
                "message": "Orchestrator picked up job from queue"
            })

            # Process it
            result = await self.review_service.process_review_request(request_dto, event_callback)
            
            # Determine if the result contains an error inside the 'result' key, or is a pure success
            if "result" in result and isinstance(result["result"], dict) and result["result"].get("status") == "error":
                event_callback({"type": "error", "message": result["result"].get("message", "Unknown error in processing")})
            else:
                event_callback({"type": "final", "result": result.get("result", result)})
                
            logger.info(f"Job ID {job_id} processing completed successfully.")

        except ValidationError as ve:
            logger.error(f"Job ID {job_id} Validation Error: {ve}")
            if event_queue_key:
                await self._publish_event(event_queue_key, {
                    "type": "error",
                    "message": f"Input validation error: {str(ve)}"
                })
        except Exception as e:
            logger.error(f"Job ID {job_id} Unhandled Error: {e}", exc_info=True)
            if event_queue_key:
                await self._publish_event(event_queue_key, {
                    "type": "error",
                    "message": f"Internal orchestrator error: {str(e)}"
                })

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

