import asyncio
import json
import logging
import os
from typing import Dict, Any, Optional
from pydantic import ValidationError

from server.redis_job_consumer import RedisJobConsumer
from server.job_events import OrderedJobEvents

from llm.request_capture import queue_capture_context
from model.dtos import ReviewRequestDto
from service.review.review_service import ReviewService
from service.runtime_capacity import review_concurrency

logger = logging.getLogger(__name__)

class RedisQueueConsumer(RedisJobConsumer):
    """
    Consumes analysis jobs from a Redis List queue and processes them
    using the ReviewService. Events and final results are pushed back 
    to a job-specific Redis event queue.
    
    Uses Redis DB 1 by default to isolate from Spring Session data (DB 0).
    """
    
    def __init__(self, review_service: ReviewService):
        self.review_service = review_service
        super().__init__(
            job_queue_key="codecrow:analysis:jobs",
            consumer_heartbeat_key="codecrow:analysis:consumer:heartbeat",
            heartbeat_seconds=float(os.environ.get("ANALYSIS_CONSUMER_HEARTBEAT_SECONDS", "5")),
            max_concurrent=review_concurrency(),
            operation_label="review",
            logger=logger,
        )
        self.heartbeat_seconds = max(1.0, float(os.environ.get("ANALYSIS_QUEUE_HEARTBEAT_SECONDS", "30")))

    async def _handle_job(self, payload_str: str):
        """Process a single job popped from the queue."""
        job_id = "UNKNOWN"
        event_queue_key = None
        events: Optional[OrderedJobEvents] = None
        review_task: asyncio.Task | None = None
        
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
            
            events = OrderedJobEvents(
                lambda event: self._publish_event(event_queue_key, event),
                logger=logger,
                job_id=job_id,
            )
            event_callback = events.emit

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
            with queue_capture_context(job_id):
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

            await events.drain()
            logger.info(f"Job ID {job_id} processing completed successfully.")

        except ValidationError as ve:
            logger.error(f"Job ID {job_id} Validation Error: {ve}")
            if event_queue_key:
                event = {
                    "type": "error",
                    "message": f"Input validation error: {str(ve)}"
                }
                if events is None:
                    await self._publish_event(event_queue_key, event)
                else:
                    event_callback(event)
                    await events.drain()
        except Exception as e:
            logger.error(f"Job ID {job_id} Unhandled Error: {e}", exc_info=True)
            if event_queue_key:
                event = {
                    "type": "error",
                    "message": f"Internal orchestrator error: {str(e)}"
                }
                if events is None:
                    await self._publish_event(event_queue_key, event)
                else:
                    event_callback(event)
                    await events.drain()
        finally:
            # Cancellation of an admitted handler must join its request-owned
            # review before returning its admission permit.
            if review_task is not None and not review_task.done():
                review_task.cancel()
                await asyncio.gather(review_task, return_exceptions=True)

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
            self._record_redis_success("review event publication")
        except Exception as e:
            self._record_redis_failure("review event publication", e)

    def _record_redis_failure(self, operation: str, error: Exception) -> None:
        """Emit one actionable diagnostic per continuous Redis outage."""
        channel = self._redis_diagnostic_channel(operation)
        if channel not in self._redis_outage_channels:
            self._redis_outage_channels.add(channel)
            logger.warning(
                "Redis unavailable during %s; queue/event delivery is "
                "degraded: %s",
                operation,
                error,
            )
            return
        logger.debug(
            "Redis remains unavailable during %s: %s",
            operation,
            error,
        )

    def _record_redis_success(self, operation: str) -> None:
        channel = self._redis_diagnostic_channel(operation)
        if channel not in self._redis_outage_channels:
            return
        self._redis_outage_channels.discard(channel)
        logger.info("Redis connectivity restored during %s", operation)

    @staticmethod
    def _redis_diagnostic_channel(operation: str) -> str:
        # A successful blocking read does not prove that Redis accepts event
        # writes (for example during READONLY/OOM states).
        return "read" if operation.endswith("queue read") else "write"
