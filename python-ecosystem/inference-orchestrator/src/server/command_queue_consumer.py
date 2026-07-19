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

    EMPTY_RESULT_SENTINELS = {
        "null",
        "none",
        "no output generated",
        "failed to generate summary",
        "i couldn't generate an answer. please try rephrasing your question.",
    }
    
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
        await self._redis.ping()
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

            if self._has_error(result):
                error_message = self._get_result_value(result, "error", "AI command failed")
                await self._publish_event(event_queue_key, {
                    "type": "error",
                    "message": str(error_message)
                })
                logger.warning(f"Command Job ID {job_id} failed: {error_message}")
                return

            # Format output correctly depending on command type based on their DTO responses
            final_payload = {}
            if command_type == "summarize":
                summary = self._get_result_value(result, "summary")
                if not self._has_usable_text(summary):
                    await self._publish_event(event_queue_key, {
                        "type": "error",
                        "message": "AI service returned an empty summary"
                    })
                    logger.warning(f"Command Job ID {job_id} failed: empty summarize result")
                    return

                final_payload = {
                    "summary": str(summary),
                    "diagram": self._string_or_empty(self._get_result_value(result, "diagram")),
                    "diagramType": self._string_or_empty(self._get_result_value(result, "diagramType", "MERMAID")) or "MERMAID"
                }
            elif command_type == "ask":
                answer = self._get_result_value(result, "answer")
                if not self._has_usable_text(answer):
                    await self._publish_event(event_queue_key, {
                        "type": "error",
                        "message": "AI service returned an empty answer"
                    })
                    logger.warning(f"Command Job ID {job_id} failed: empty ask result")
                    return

                final_payload = {
                    "answer": str(answer)
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

    @staticmethod
    def _get_result_value(result: Any, key: str, default: Any = None) -> Any:
        if isinstance(result, dict):
            return result.get(key, default)
        if hasattr(result, key):
            return getattr(result, key)
        return default

    @classmethod
    def _has_error(cls, result: Any) -> bool:
        error = cls._get_result_value(result, "error")
        return error is not None and str(error).strip() != ""

    @staticmethod
    def _has_usable_text(value: Any) -> bool:
        if value is None:
            return False
        text = str(value).strip()
        return bool(text) and text.lower() not in CommandQueueConsumer.EMPTY_RESULT_SENTINELS

    @staticmethod
    def _string_or_empty(value: Any) -> str:
        return "" if value is None else str(value)
