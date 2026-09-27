"""Redis worker lifecycle shared by review and command job handlers.

Domain handlers own request parsing and results. This worker owns admission,
connection lifetime, heartbeat health, and ordered delivery primitives.
"""
import asyncio
import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

import redis.asyncio as redis
from redis.exceptions import TimeoutError as RedisTimeoutError


class RedisJobConsumer(ABC):
    def __init__(
        self,
        *,
        job_queue_key: str,
        consumer_heartbeat_key: str,
        heartbeat_seconds: float,
        max_concurrent: int,
        event_ttl_seconds: int = 3600,
        operation_label: str,
        logger: logging.Logger,
    ):
        self.redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/1")
        self.job_queue_key = job_queue_key
        self.consumer_heartbeat_key = consumer_heartbeat_key
        self.consumer_heartbeat_seconds = max(1.0, heartbeat_seconds)
        self.consumer_heartbeat_ttl_seconds = max(15, int(self.consumer_heartbeat_seconds * 3))
        self.event_ttl_seconds = event_ttl_seconds
        self.is_running = False
        self._redis: Optional[redis.Redis] = None
        self._task: Optional[asyncio.Task] = None
        self._consumer_heartbeat_task: Optional[asyncio.Task] = None
        self._job_tasks: set[asyncio.Task] = set()
        self._redis_outage_channels: set[str] = set()
        self._job_semaphore = asyncio.Semaphore(max_concurrent)
        self._operation_label = operation_label
        self._logger = logger

    @abstractmethod
    async def _handle_job(self, payload_str: str):
        """Process an admitted payload and await its final publication."""

    async def start(self):
        """Start the consumer background loop."""
        if self.is_running:
            return
            
        self._logger.info("Starting %s queue consumer", self._operation_label)
        self._redis = redis.from_url(
            self.redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=30,
            health_check_interval=30,
        )
        self.is_running = True
        try:
            await self._publish_consumer_heartbeat()
        except BaseException:
            # A failed initial heartbeat must not leave a half-started worker
            # that refuses to start on retry and leaks its connection pool.
            self.is_running = False
            try:
                await self._redis.aclose()
            except Exception:
                self._logger.warning("Failed to close Redis after startup failure", exc_info=True)
            self._redis = None
            raise
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

        # Removing a job from Redis admits durable work. Keep its shared
        # Redis/RAG clients alive until every admitted review has finished.
        active_jobs = tuple(self._job_tasks)
        if active_jobs:
            self._logger.info(
                "Waiting for %s admitted %s jobs before shutdown",
                len(active_jobs),
                self._operation_label,
            )
            await asyncio.gather(*active_jobs, return_exceptions=True)
        
        if self._redis:
            await self._redis.aclose()
            self._redis = None
            self._logger.info("%s queue consumer stopped", self._operation_label)


    async def _publish_consumer_heartbeat(self):
        if self._redis:
            try:
                await self._redis.set(
                    self.consumer_heartbeat_key,
                    "alive",
                    ex=self.consumer_heartbeat_ttl_seconds,
                )
                self._record_redis_success(f"{self._operation_label} consumer heartbeat")
            except Exception as error:
                self._record_redis_failure(
                    f"{self._operation_label} consumer heartbeat",
                    error,
                )
                raise


    async def _consumer_heartbeat_loop(self):
        while self.is_running:
            try:
                await self._publish_consumer_heartbeat()
            except asyncio.CancelledError:
                raise
            except Exception:
                # The transition diagnostic is owned by
                # _publish_consumer_heartbeat; keep the loop alive quietly.
                pass
            await asyncio.sleep(self.consumer_heartbeat_seconds)


    async def is_healthy(self) -> bool:
        if (
            not self.is_running
            or self._redis is None
            or self._task is None
            or self._task.done()
            or self._consumer_heartbeat_task is None
            or self._consumer_heartbeat_task.done()
        ):
            return False
        try:
            return bool(await asyncio.wait_for(
                self._redis.exists(self.consumer_heartbeat_key),
                timeout=2,
            ))
        except Exception:
            return False


    async def _consume_loop(self):
        """Infinite loop blocking on the Redis queue for new jobs."""
        self._logger.info(f"Listening for jobs on '{self.job_queue_key}'...")
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
                self._record_redis_success(f"{self._operation_label} queue read")
                
                if not result:
                    continue
                    
                queue_name, payload_str = result
                self._logger.debug(f"Received raw job payload from {queue_name}")
                
                # Transfer ownership of the reserved permit to the job task.
                job_task = asyncio.create_task(
                    self._handle_admitted_job(payload_str)
                )
                self._job_tasks.add(job_task)
                job_task.add_done_callback(self._job_tasks.discard)
                permit_acquired = False
                
            except asyncio.CancelledError:
                break
            except RedisTimeoutError as error:
                self._record_redis_failure(f"{self._operation_label} queue read", error)
                await asyncio.sleep(1)
            except Exception as e:
                self._record_redis_failure(f"{self._operation_label} queue read", e)
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


    async def _publish_event(self, key: str, event: Dict[str, Any]):
        """Publish an event back to the job's specific event list. LPUSH (Java uses rightPop)."""
        try:
            if not self._redis:
                return
            event_str = json.dumps(event, default=str) # Handle date/obj serialization
            # Expire the event queue after a reasonable TTL (e.g. 1 hour) so it doesn't leak memory
            pipeline = self._redis.pipeline()
            pipeline.lpush(key, event_str)
            pipeline.expire(key, self.event_ttl_seconds)
            await pipeline.execute()
            self._record_redis_success(f"{self._operation_label} event publication")
        except Exception as e:
            self._record_redis_failure(f"{self._operation_label} event publication", e)


    def _record_redis_failure(self, operation: str, error: Exception) -> None:
        """Emit one actionable diagnostic per continuous Redis outage."""
        channel = self._redis_diagnostic_channel(operation)
        if channel not in self._redis_outage_channels:
            self._redis_outage_channels.add(channel)
            self._logger.warning(
                "Redis unavailable during %s; queue/event delivery is "
                "degraded: %s",
                operation,
                error,
            )
            return
        self._logger.debug(
            "Redis remains unavailable during %s: %s",
            operation,
            error,
        )


    def _record_redis_success(self, operation: str) -> None:
        channel = self._redis_diagnostic_channel(operation)
        if channel not in self._redis_outage_channels:
            return
        self._redis_outage_channels.discard(channel)
        self._logger.info("Redis connectivity restored during %s", operation)


    @staticmethod
    def _redis_diagnostic_channel(operation: str) -> str:
        # A successful blocking read does not prove that Redis accepts event
        # writes (for example during READONLY/OOM states).
        return "read" if operation.endswith("queue read") else "write"
