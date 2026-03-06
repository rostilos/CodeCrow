import asyncio
import json
import logging
import os
import traceback
from typing import Dict, Any, Optional
import redis.asyncio as redis
from pydantic import ValidationError

from model.dtos import SummarizeRequestDto, AskRequestDto
from service.command.command_service import CommandService

logger = logging.getLogger(__name__)

class CommandQueueConsumer:
    """
    Consumes command jobs (summarize, ask) from a Redis List queue and processes them
    using the CommandService. Events and final results are pushed back 
    to a job-specific Redis event queue.
    """
    
    def __init__(self, command_service: CommandService):
        self.command_service = command_service
        self.redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/1")
        self.job_queue_key = "codecrow:queue:commands"
        self.is_running = False
        self._redis: Optional[redis.Redis] = None
        self._task: Optional[asyncio.Task] = None
        max_concurrent = int(os.environ.get("MAX_CONCURRENT_COMMANDS", "10"))
        self._job_semaphore = asyncio.Semaphore(max_concurrent)

    async def start(self):
        """Start the consumer background loop."""
        if self.is_running:
            return
            
        logger.info(f"Starting Command Queue Consumer connected to {self.redis_url}")
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
            logger.info("Command Queue Consumer stopped")

    async def _consume_loop(self):
        """Infinite loop blocking on the Redis queue for new jobs."""
        logger.info(f"Listening for jobs on '{self.job_queue_key}'...")
        while self.is_running:
            try:
                result = await self._redis.brpop([self.job_queue_key], timeout=1)
                if not result:
                    continue
                    
                queue_name, payload_str = result
                logger.debug(f"Received raw command job payload from {queue_name}")
                asyncio.create_task(self._bounded_handle_job(payload_str))
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in Command Queue consume loop: {e}", exc_info=True)
                await asyncio.sleep(2)

    async def _bounded_handle_job(self, payload_str: str):
        """Acquire the concurrency semaphore before processing a job."""
        async with self._job_semaphore:
            await self._handle_job(payload_str)

    async def _handle_job(self, payload_str: str):
        """Process a single command job popped from the queue."""
        job_id = "UNKNOWN"
        event_queue_key = None
        command_type = "UNKNOWN"
        
        try:
            payload = json.loads(payload_str)
            job_id = payload.get("job_id")
            command_type = payload.get("command_type", "").lower()
            request_data = payload.get("request")
            
            if not job_id or not request_data or not command_type:
                logger.error(f"Invalid command job payload structure. Missing fields: {payload_str[:100]}...")
                return

            event_queue_key = f"codecrow:analysis:events:{job_id}"
            logger.info(f"Processing Command Job ID: {job_id} (Type: {command_type})")
            
            def event_callback(event: Dict[str, Any]):
                asyncio.create_task(self._publish_event(event_queue_key, event))

            event_callback({
                "type": "status", 
                "state": "acknowledged", 
                "message": f"Orchestrator picked up {command_type} command from queue"
            })

            result = None
            if command_type == "summarize":
                request_dto = SummarizeRequestDto(**request_data)
                result = await self.command_service.process_summarize(request_dto, event_callback)
            elif command_type == "ask":
                request_dto = AskRequestDto(**request_data)
                result = await self.command_service.process_ask(request_dto, event_callback)
            else:
                raise ValueError(f"Unknown command type: {command_type}")
            
            # Format output correctly depending on command type based on their DTO responses
            final_payload = {}
            if command_type == "summarize":
                final_payload = {
                    "summary": result.summary if hasattr(result, "summary") else result.get("summary"),
                    "diagram": result.diagram if hasattr(result, "diagram") else result.get("diagram"),
                    "diagramType": result.diagramType if hasattr(result, "diagramType") else result.get("diagramType", "MERMAID")
                }
            elif command_type == "ask":
                final_payload = {
                    "answer": result.answer if hasattr(result, "answer") else result.get("answer")
                }

            event_callback({"type": "final", "result": final_payload})
                
            logger.info(f"Command Job ID {job_id} processing completed successfully.")

        except ValidationError as ve:
            logger.error(f"Command Job ID {job_id} Validation Error: {ve}")
            if event_queue_key:
                await self._publish_event(event_queue_key, {
                    "type": "error",
                    "message": f"Input validation error: {str(ve)}"
                })
        except Exception as e:
            logger.error(f"Command Job ID {job_id} Unhandled Error: {e}", exc_info=True)
            if event_queue_key:
                await self._publish_event(event_queue_key, {
                    "type": "error",
                    "message": f"Internal orchestrator command error: {str(e)}"
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
