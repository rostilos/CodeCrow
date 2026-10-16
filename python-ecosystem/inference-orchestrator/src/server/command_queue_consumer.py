import json
import logging
import os
from typing import Dict, Any, Optional
from pydantic import ValidationError

from server.redis_job_consumer import RedisJobConsumer
from server.job_events import OrderedJobEvents

from model.dtos import SummarizeRequestDto, AskRequestDto
from service.command.command_service import CommandService
from service.command import results as command_results

logger = logging.getLogger(__name__)

class CommandQueueConsumer(RedisJobConsumer):
    """
    Consumes command jobs (summarize, ask) from a Redis List queue and processes them
    using the CommandService. Events and final results are pushed back 
    to a job-specific Redis event queue.
    """

    def __init__(self, command_service: CommandService):
        self.command_service = command_service
        super().__init__(
            job_queue_key="codecrow:queue:commands",
            consumer_heartbeat_key="codecrow:commands:consumer:heartbeat",
            heartbeat_seconds=float(os.environ.get("COMMAND_CONSUMER_HEARTBEAT_SECONDS", "5")),
            max_concurrent=int(os.environ.get("MAX_CONCURRENT_COMMANDS", "10")),
            event_ttl_seconds=max(60, int(os.environ.get("COMMAND_EVENT_TTL_SECONDS", "3600"))),
            operation_label="command",
            logger=logger,
        )

    async def _handle_job(self, payload_str: str):
        """Process a single command job popped from the queue."""
        job_id = "UNKNOWN"
        event_queue_key = None
        command_type = "UNKNOWN"
        events: Optional[OrderedJobEvents] = None
        
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

            events = OrderedJobEvents(
                lambda event: self._publish_event(event_queue_key, event),
                logger=logger,
                job_id=job_id,
            )
            event_callback = events.emit

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
                event_callback({
                    "type": "error",
                    "message": str(error_message)
                })
                await events.drain()
                logger.info(f"Command Job ID {job_id} failed: {error_message}")
                return

            # Format output correctly depending on command type based on their DTO responses
            final_payload = {}
            if command_type == "summarize":
                summary = self._get_result_value(result, "summary")
                if not command_results.has_usable_text(summary):
                    event_callback({
                        "type": "error",
                        "message": "AI service returned an empty summary"
                    })
                    await events.drain()
                    logger.info(f"Command Job ID {job_id} failed: empty summarize result")
                    return

                final_payload = {
                    "summary": str(summary),
                    "diagram": command_results.string_or_empty(self._get_result_value(result, "diagram")),
                    "diagramType": command_results.string_or_empty(self._get_result_value(result, "diagramType", "MERMAID")) or "MERMAID"
                }
            elif command_type == "ask":
                answer = self._get_result_value(result, "answer")
                if not command_results.has_usable_text(answer):
                    event_callback({
                        "type": "error",
                        "message": "AI service returned an empty answer"
                    })
                    await events.drain()
                    logger.info(f"Command Job ID {job_id} failed: empty ask result")
                    return

                final_payload = {
                    "answer": str(answer)
                }

            event_callback({"type": "final", "result": final_payload})
            await events.drain()
            logger.info(f"Command Job ID {job_id} processing completed successfully.")

        except ValidationError as ve:
            logger.error(f"Command Job ID {job_id} Validation Error: {ve}")
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
            logger.error(f"Command Job ID {job_id} Unhandled Error: {e}", exc_info=True)
            if event_queue_key:
                event = {
                    "type": "error",
                    "message": f"Internal orchestrator command error: {str(e)}"
                }
                if events is None:
                    await self._publish_event(event_queue_key, event)
                else:
                    event_callback(event)
                    await events.drain()

    async def _publish_event(self, key: str, event: Dict[str, Any]):
        """Publish an event back to the job's specific event list. LPUSH (Java uses rightPop)."""
        try:
            if not self._redis:
                return
            event_json = json.dumps(event)
            pipeline = self._redis.pipeline()
            pipeline.lpush(key, event_json)
            pipeline.expire(key, self.event_ttl_seconds)
            await pipeline.execute()
            self._record_redis_success("command event publication")
        except Exception as e:
            self._record_redis_failure("command event publication", e)




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
