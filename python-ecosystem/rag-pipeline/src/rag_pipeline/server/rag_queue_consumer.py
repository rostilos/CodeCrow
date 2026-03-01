import asyncio
import json
import logging
import os
from typing import Dict, Any, Optional
import redis.asyncio as redis
from pydantic import ValidationError

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

    async def start(self):
        """Start the consumer background loop."""
        if self.is_running:
            return
            
        logger.info(f"Starting RAG Queue Consumer connected to {self.redis_url}")
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
            logger.info("RAG Queue Consumer stopped")

    async def _consume_loop(self):
        """Infinite loop blocking on the Redis queue for new jobs."""
        logger.info(f"Listening for RAG jobs on '{self.job_queue_key}'...")
        while self.is_running:
            try:
                result = await self._redis.brpop([self.job_queue_key], timeout=1)
                if not result:
                    continue
                    
                queue_name, payload_str = result
                logger.debug(f"Received raw RAG job payload from {queue_name}")
                asyncio.create_task(self._bounded_handle_job(payload_str))
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in RAG Queue consume loop: {e}", exc_info=True)
                await asyncio.sleep(2)

    async def _bounded_handle_job(self, payload_str: str):
        """Acquire the concurrency semaphore before processing a job."""
        async with self._job_semaphore:
            await self._handle_job(payload_str)

    async def _handle_job(self, payload_str: str):
        """Process a single RAG job popped from the queue."""
        job_id = "UNKNOWN"
        event_queue_key = None
        
        try:
            payload = json.loads(payload_str)
            job_id = payload.get("job_id")
            request_data = payload.get("request")
            
            if not job_id or not request_data:
                logger.error(f"Invalid RAG job payload structure. Missing job_id or request: {payload_str[:100]}...")
                return

            event_queue_key = f"codecrow:analysis:events:{job_id}"
            logger.info(f"Processing RAG Index Job ID: {job_id}")
            
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
            loop = asyncio.get_event_loop()
            result_obj = await loop.run_in_executor(
                None,
                lambda: self.index_manager.index_repository(
                    repo_path=request_dto.repo_path,
                    workspace=request_dto.workspace,
                    project=request_dto.project,
                    branch=request_dto.branch,
                    commit=request_dto.commit,
                    include_patterns=request_dto.include_patterns,
                    exclude_patterns=request_dto.exclude_patterns
                )
            )
            
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

    async def _publish_event(self, key: str, event: Dict[str, Any]):
        """Publish an event back to the job's specific event list. LPUSH (Java uses rightPop)."""
        try:
            if not self._redis:
                return
            event_json = json.dumps(event)
            await self._redis.lpush(key, event_json)
        except Exception as e:
            logger.error(f"Failed to publish event to {key}: {e}")
