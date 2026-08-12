import asyncio
import json
import logging
import os
from typing import Dict, Any, Optional
import redis.asyncio as redis
from pydantic import ValidationError
from redis.exceptions import TimeoutError as RedisTimeoutError

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
        self._consumer_heartbeat_task: Optional[asyncio.Task] = None
        self._job_tasks: set[asyncio.Task] = set()
        self._redis_outage_channels: set[str] = set()
        self.consumer_heartbeat_key = "codecrow:commands:consumer:heartbeat"
        self.consumer_heartbeat_seconds = max(
            1.0,
            float(os.environ.get("COMMAND_CONSUMER_HEARTBEAT_SECONDS", "5")),
        )
        self.consumer_heartbeat_ttl_seconds = max(
            15,
            int(self.consumer_heartbeat_seconds * 3),
        )
        self.event_ttl_seconds = max(
            60,
            int(os.environ.get("COMMAND_EVENT_TTL_SECONDS", "3600")),
        )
        max_concurrent = int(os.environ.get("MAX_CONCURRENT_COMMANDS", "10"))
        self._job_semaphore = asyncio.Semaphore(max_concurrent)

    async def start(self):
        """Start the consumer background loop."""
        if self.is_running:
            return
            
        logger.info(f"Starting Command Queue Consumer connected to {self.redis_url}")
        self._redis = redis.from_url(
            self.redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=30,
            health_check_interval=30,
        )
        self.is_running = True
        await self._publish_consumer_heartbeat()
        self._consumer_heartbeat_task = asyncio.create_task(
            self._consumer_heartbeat_loop()
        )
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
        if self._consumer_heartbeat_task:
            self._consumer_heartbeat_task.cancel()
            try:
                await self._consumer_heartbeat_task
            except asyncio.CancelledError:
                pass

        # A dequeued command is admitted work. Do not close Redis or the
        # shared RagClient underneath an in-flight command.
        active_jobs = tuple(self._job_tasks)
        if active_jobs:
            logger.info(
                "Waiting for %s admitted command jobs before shutdown",
                len(active_jobs),
            )
            await asyncio.gather(*active_jobs, return_exceptions=True)
        
        if self._redis:
            await self._redis.aclose()
            logger.info("Command Queue Consumer stopped")

    async def _publish_consumer_heartbeat(self):
        if self._redis:
            try:
                await self._redis.set(
                    self.consumer_heartbeat_key,
                    "alive",
                    ex=self.consumer_heartbeat_ttl_seconds,
                )
                self._record_redis_success("command consumer heartbeat")
            except Exception as error:
                self._record_redis_failure("command consumer heartbeat", error)
                raise

    async def _consumer_heartbeat_loop(self):
        while self.is_running:
            try:
                await self._publish_consumer_heartbeat()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(self.consumer_heartbeat_seconds)

    async def _consume_loop(self):
        """Infinite loop blocking on the Redis queue for new jobs."""
        logger.info(f"Listening for jobs on '{self.job_queue_key}'...")
        while self.is_running:
            permit_acquired = False
            try:
                # Reserve capacity before removing durable work from Redis.
                await self._job_semaphore.acquire()
                permit_acquired = True
                if not self.is_running:
                    break

                result = await self._redis.brpop([self.job_queue_key], timeout=1)
                self._record_redis_success("command queue read")
                if not result:
                    continue
                    
                queue_name, payload_str = result
                logger.debug(f"Received raw command job payload from {queue_name}")
                job_task = asyncio.create_task(
                    self._handle_admitted_job(payload_str)
                )
                self._job_tasks.add(job_task)
                job_task.add_done_callback(self._job_tasks.discard)
                permit_acquired = False
                
            except asyncio.CancelledError:
                break
            except RedisTimeoutError as error:
                self._record_redis_failure("command queue read", error)
                await asyncio.sleep(1)
            except Exception as e:
                self._record_redis_failure("command queue read", e)
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
        """Acquire the concurrency semaphore before processing a job."""
        async with self._job_semaphore:
            await self._handle_job(payload_str)

    async def _handle_job(self, payload_str: str):
        """Process a single command job popped from the queue."""
        job_id = "UNKNOWN"
        event_queue_key = None
        command_type = "UNKNOWN"
        publish_tail: Optional[asyncio.Future] = None
        terminal_event_type: Optional[str] = None
        
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

            # Serialize callback publications so progress cannot overtake the
            # terminal result, and retain the tail until the job completes.
            loop = asyncio.get_running_loop()
            publish_tail = loop.create_future()
            publish_tail.set_result(None)

            def event_callback(event: Dict[str, Any]):
                nonlocal publish_tail, terminal_event_type
                event_type = event.get("type")
                if terminal_event_type is not None:
                    logger.debug(
                        "Ignoring command event type=%s after terminal type=%s "
                        "for job=%s",
                        event_type,
                        terminal_event_type,
                        job_id,
                    )
                    return
                if event_type in {"final", "error"}:
                    terminal_event_type = event_type
                previous = publish_tail

                async def publish_after_previous():
                    await previous
                    await self._publish_event(event_queue_key, event)

                publish_tail = asyncio.create_task(publish_after_previous())

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
                await publish_tail
                logger.info(f"Command Job ID {job_id} failed: {error_message}")
                return

            # Format output correctly depending on command type based on their DTO responses
            final_payload = {}
            if command_type == "summarize":
                summary = self._get_result_value(result, "summary")
                if not self._has_usable_text(summary):
                    event_callback({
                        "type": "error",
                        "message": "AI service returned an empty summary"
                    })
                    await publish_tail
                    logger.info(f"Command Job ID {job_id} failed: empty summarize result")
                    return

                final_payload = {
                    "summary": str(summary),
                    "diagram": self._string_or_empty(self._get_result_value(result, "diagram")),
                    "diagramType": self._string_or_empty(self._get_result_value(result, "diagramType", "MERMAID")) or "MERMAID"
                }
            elif command_type == "ask":
                answer = self._get_result_value(result, "answer")
                if not self._has_usable_text(answer):
                    event_callback({
                        "type": "error",
                        "message": "AI service returned an empty answer"
                    })
                    await publish_tail
                    logger.info(f"Command Job ID {job_id} failed: empty ask result")
                    return

                final_payload = {
                    "answer": str(answer)
                }

            event_callback({"type": "final", "result": final_payload})
            await publish_tail
            logger.info(f"Command Job ID {job_id} processing completed successfully.")

        except ValidationError as ve:
            logger.error(f"Command Job ID {job_id} Validation Error: {ve}")
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
            logger.error(f"Command Job ID {job_id} Unhandled Error: {e}", exc_info=True)
            if event_queue_key:
                event = {
                    "type": "error",
                    "message": f"Internal orchestrator command error: {str(e)}"
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
            event_json = json.dumps(event)
            pipeline = self._redis.pipeline()
            pipeline.lpush(key, event_json)
            pipeline.expire(key, self.event_ttl_seconds)
            await pipeline.execute()
            self._record_redis_success("command event publication")
        except Exception as e:
            self._record_redis_failure("command event publication", e)

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
        return "read" if operation.endswith("queue read") else "write"

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

    @classmethod
    def _has_usable_text(cls, value: Any) -> bool:
        if value is None:
            return False
        text = str(value).strip()
        return bool(text) and text.lower() not in cls.EMPTY_RESULT_SENTINELS

    @staticmethod
    def _string_or_empty(value: Any) -> str:
        return "" if value is None else str(value)
