import asyncio
import hashlib
import json
import logging
import os
from typing import Dict, Any, Optional
import redis.asyncio as redis
from pydantic import ValidationError

from model.dtos import (
    ExecutionManifestV1,
    ReviewRequestDto,
    parse_review_queue_envelope,
)
from service.review.execution_context import (
    ExecutionContextBindingError,
    bind_execution_context,
    bind_owned_execution_event,
    require_execution_event_binding,
)
from service.review.review_service import ReviewService
from service.review.agentic.workspace import AgenticWorkspace

logger = logging.getLogger(__name__)

JOB_QUEUE_KEY = "codecrow:analysis:jobs"
INPUT_VALIDATION_REASON = "input_validation_error"
INTERNAL_ERROR_REASON = "internal_orchestrator_error"
REVIEW_FAILURE_REASON = "review_processing_failed"


class LatestHeadControlError(RuntimeError):
    """A candidate worker cannot prove the durable latest-head fence."""


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
        # Java has one producer queue. Envelope versions are validated inside
        # the payload; duplicating Redis queues only creates routing states.
        self.job_queue_keys = (JOB_QUEUE_KEY,)
        self.job_queue_key = JOB_QUEUE_KEY
        self.is_running = False
        self._redis: Optional[redis.Redis] = None
        self._task: Optional[asyncio.Task] = None
        # Bound concurrent job processing to prevent memory pressure
        max_concurrent = int(os.environ.get("MAX_CONCURRENT_REVIEWS", "4"))
        self._job_semaphore = asyncio.Semaphore(max_concurrent)
        self.latest_head_poll_seconds = float(
            os.environ.get("LATEST_HEAD_POLL_SECONDS", "0.25")
        )
        if self.latest_head_poll_seconds <= 0:
            raise ValueError("LATEST_HEAD_POLL_SECONDS must be positive")

    async def start(self):
        """Start the consumer background loop."""
        if self.is_running:
            return
            
        # REDIS_URL may contain a username/password. Connection coordinates are
        # intentionally kept out of logs; the Redis client reports health via
        # metrics without exposing its DSN.
        logger.info("Starting Redis Queue Consumer")
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
        logger.info("Listening for jobs on %s...", self.job_queue_keys)
        while self.is_running:
            try:
                # Block until a job is available or timeout (1 second for graceful shutdown check)
                result = await self._redis.brpop(self.job_queue_keys, timeout=1)
                
                if not result:
                    continue
                    
                queue_name, payload_str = result
                logger.debug(f"Received raw job payload from {queue_name}")
                
                # Handle the job in a separate task, bounded by the semaphore
                asyncio.create_task(self._bounded_handle_job(payload_str))
                
            except asyncio.CancelledError:
                break
            except Exception as error:
                logger.error(
                    "Redis consume loop failed: error_type=%s",
                    type(error).__name__,
                )
                await asyncio.sleep(2)  # Backoff on error

    async def _bounded_handle_job(self, payload_str: str):
        """Acquire the concurrency semaphore before processing a job."""
        async with self._job_semaphore:
            return await self._handle_job(payload_str)

    async def _handle_job(self, payload_str: str):
        """Process a single job popped from the queue."""
        job_id = "UNKNOWN"
        event_queue_key = None
        bound_manifest = None
        staged_workspace_key: Optional[str] = None
        publish_tail: Optional[asyncio.Task] = None

        async def await_pending_events() -> None:
            """Wait until every event accepted for this job is committed in order."""
            nonlocal publish_tail
            if publish_tail is not None:
                await publish_tail
                publish_tail = None
        
        try:
            payload = json.loads(payload_str)
            job_id = payload.get("job_id")
            request_data = payload.get("request")
            staged_workspace_key = self._staged_agentic_workspace_key(request_data)
            
            if not job_id or not request_data:
                logger.error(
                    "Invalid job payload structure: reason_code=%s",
                    INPUT_VALIDATION_REASON,
                )
                return

            event_queue_key = f"codecrow:analysis:events:{job_id}"
            logger.info("Processing job: job_ref=%s", self._job_ref(job_id))
            
            request_data = dict(request_data)
            # Preserve a self-verifying nested manifest for a terminal
            # validation error even when another request alias is mixed. The
            # untrusted raw shape is never echoed as execution identity.
            candidate_manifest = request_data.get("executionManifest")
            if candidate_manifest is not None:
                try:
                    bound_manifest = ExecutionManifestV1.model_validate(
                        candidate_manifest
                    )
                except ValidationError:
                    bound_manifest = None

            if "schemaVersion" in payload:
                try:
                    envelope = parse_review_queue_envelope(payload)
                except ValueError as error:
                    raise ExecutionContextBindingError(str(error)) from error
                job_id = envelope.job_id
                if isinstance(envelope.request, ReviewRequestDto):
                    request_dto = envelope.request
                else:
                    request_dto = ReviewRequestDto(**dict(envelope.request))
            else:
                if candidate_manifest is not None:
                    raise ExecutionContextBindingError(
                        "candidate queue envelopes require an explicit schemaVersion"
                    )
                # Preserve the bounded legacy adapter until its existing
                # compatibility sunset; candidate traffic always uses v2.
                request_dto = ReviewRequestDto(**request_data)
            request_dto = bind_execution_context(
                request_dto,
                transport_execution_id=job_id,
            )
            bound_manifest = request_dto.executionManifest
            logger.info(
                "Job request accepted: job_ref=%s pr=%s",
                self._job_ref(job_id),
                request_dto.pullRequestId,
            )
            
            # Define the event callback that pushes to the event list
            def event_callback(event: Dict[str, Any]):
                nonlocal publish_tail
                forwarded_event = require_execution_event_binding(
                    event,
                    bound_manifest,
                )
                previous_publish = publish_tail

                async def publish_after_previous() -> None:
                    if previous_publish is not None:
                        await previous_publish
                    await self._publish_event(event_queue_key, forwarded_event)

                # The review callback is synchronous while Redis is asynchronous.
                # Chain publications so callback order is retained, then join the
                # chain before this job is reported as complete.
                publish_tail = asyncio.create_task(publish_after_previous())

            if (
                bound_manifest is not None
                and self._latest_head_monitor_available()
                and not await self._is_latest_head(request_dto)
            ):
                event_callback(
                    self._superseded_event(request_dto, "not_started")
                )
                await await_pending_events()
                logger.info(
                    "Job was superseded before model work: job_ref=%s",
                    self._job_ref(job_id),
                )
                return "superseded"

            # Tell the java engine we picked it up
            event_callback(bind_owned_execution_event({
                "type": "status",
                "state": "acknowledged",
                "message": "Orchestrator picked up job from queue"
            }, bound_manifest))

            # Process it. Candidate work observes the same durable latest-head
            # record that Java updates before queueing a newer execution. The
            # review task is cancelled at an async boundary as soon as that
            # record advances, while the immutable manifest remains available
            # for the terminal supersession event.
            if bound_manifest is not None and self._latest_head_monitor_available():
                result, superseded_compute_state = (
                    await self._process_with_latest_head_monitor(
                        request_dto,
                        event_callback,
                    )
                )
                if superseded_compute_state is not None:
                    event_callback(
                        self._superseded_event(
                            request_dto,
                            superseded_compute_state,
                        )
                    )
                    await await_pending_events()
                    logger.info(
                        "Job model work ended as superseded: job_ref=%s state=%s",
                        self._job_ref(job_id),
                        superseded_compute_state,
                    )
                    return "superseded"
            else:
                # Direct unit/component invocations may replace Redis with a
                # publication-only test double. Production consumers always
                # install the redis.asyncio client, which supports MGET.
                result = await self.review_service.process_review_request(
                    request_dto,
                    event_callback,
                )
            
            nested_result = result.get("result") if isinstance(result, dict) else None
            nested_error = (
                nested_result
                if isinstance(nested_result, dict)
                and nested_result.get("status") == "error"
                else None
            )
            top_level_error = result.get("error") if isinstance(result, dict) else None
            if nested_error is not None or top_level_error:
                event_callback(bind_owned_execution_event(
                    {
                        "type": "error",
                        "message": "Review processing failed",
                        "reasonCode": REVIEW_FAILURE_REASON,
                    },
                    bound_manifest,
                ))
                await await_pending_events()
                logger.info(
                    "Job processing failed: job_ref=%s reason_code=%s",
                    self._job_ref(job_id),
                    REVIEW_FAILURE_REASON,
                )
                return "failed"
            else:
                final_event = bind_owned_execution_event(
                    {
                        "type": "final",
                        "result": result.get("result", result),
                    },
                    bound_manifest,
                )
                event_callback(final_event)

            await await_pending_events()
            logger.info(
                "Job processing completed: job_ref=%s",
                self._job_ref(job_id),
            )
            return "complete"

        except (ValidationError, ExecutionContextBindingError) as error:
            logger.error(
                "Job validation rejected: job_ref=%s reason_code=%s error_type=%s",
                self._job_ref(job_id),
                INPUT_VALIDATION_REASON,
                type(error).__name__,
            )
            # DTO validation happens only after a structurally valid payload has
            # established the per-job event key above.
            error_event = {
                "type": "error",
                "message": "Input validation error",
                "reasonCode": INPUT_VALIDATION_REASON,
            }
            await await_pending_events()
            await self._publish_event(
                event_queue_key,
                bind_owned_execution_event(error_event, bound_manifest),
            )
            return "failed"
        except Exception as error:
            logger.error(
                "Job failed unexpectedly: job_ref=%s reason_code=%s error_type=%s",
                self._job_ref(job_id),
                INTERNAL_ERROR_REASON,
                type(error).__name__,
            )
            if event_queue_key:
                error_event = {
                    "type": "error",
                    "message": "Internal orchestrator error",
                    "reasonCode": INTERNAL_ERROR_REASON,
                }
                await await_pending_events()
                await self._publish_event(
                    event_queue_key,
                    bind_owned_execution_event(error_event, bound_manifest),
                )
            return "failed"
        finally:
            if staged_workspace_key is not None:
                try:
                    removed = AgenticWorkspace.cleanup_workspace(
                        self.review_service.agentic_workspace_root,
                        staged_workspace_key,
                    )
                    logger.info(
                        "Agentic workspace cleanup complete: job_ref=%s removed=%s",
                        self._job_ref(job_id),
                        removed,
                    )
                except Exception as cleanup_error:
                    # Periodic TTL cleanup remains the crash/race recovery path.
                    # Never log the workspace path or repository contents.
                    logger.warning(
                        "Agentic workspace cleanup failed: job_ref=%s error_type=%s",
                        self._job_ref(job_id),
                        type(cleanup_error).__name__,
                    )

    @staticmethod
    def _job_ref(job_id: Any) -> str:
        """Return a non-reversible correlation label for untrusted job IDs."""

        if not isinstance(job_id, str) or not job_id:
            return "unknown"
        return hashlib.sha256(job_id.encode("utf-8")).hexdigest()[:12]

    @staticmethod
    def _staged_agentic_workspace_key(request_data: Any) -> Optional[str]:
        """Recover a validated cleanup key before full request validation."""

        if not isinstance(request_data, dict):
            return None
        descriptor = request_data.get("agenticRepository")
        if descriptor is None:
            return None
        if not isinstance(descriptor, dict):
            return None
        workspace_key = descriptor.get("workspaceKey")
        return (
            workspace_key
            if AgenticWorkspace.is_valid_workspace_key(workspace_key)
            else None
        )

    def _latest_head_monitor_available(self) -> bool:
        return self._redis is not None and callable(
            getattr(self._redis, "mget", None)
        )

    async def _process_with_latest_head_monitor(
        self,
        request: ReviewRequestDto,
        event_callback,
    ) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
        review_task = asyncio.create_task(
            self.review_service.process_review_request(request, event_callback)
        )
        monitor_task = asyncio.create_task(
            self._wait_until_superseded(request)
        )
        try:
            done, _ = await asyncio.wait(
                {review_task, monitor_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if monitor_task in done:
                # Re-raise a control-store error before deciding that the work
                # is stale. A missing/unreadable fence must not become a clean
                # candidate result.
                monitor_task.result()
                if review_task.done():
                    await review_task
                    return None, "completed_discarded"
                review_task.cancel()
                await asyncio.gather(review_task, return_exceptions=True)
                return None, "cancelled"

            result = await review_task
            if not await self._is_latest_head(request):
                return None, "completed_discarded"
            return result, None
        finally:
            for task in (review_task, monitor_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                review_task,
                monitor_task,
                return_exceptions=True,
            )

    async def _wait_until_superseded(self, request: ReviewRequestDto) -> None:
        while True:
            if not await self._is_latest_head(request):
                return
            await asyncio.sleep(self.latest_head_poll_seconds)

    async def _is_latest_head(self, request: ReviewRequestDto) -> bool:
        manifest = request.executionManifest
        if manifest is None:
            return True
        if self._redis is None:
            raise LatestHeadControlError(
                "candidate latest-head check requires Redis"
            )
        execution_key, revision_key = self._latest_head_keys(request)
        observed = await self._redis.mget(execution_key, revision_key)
        if observed is None or len(observed) != 2 or any(
            value is None for value in observed
        ):
            raise LatestHeadControlError(
                "candidate latest-head fence is missing"
            )
        return (
            observed[0] == manifest.executionId
            and observed[1] == manifest.headSha
        )

    @staticmethod
    def _latest_head_keys(request: ReviewRequestDto) -> tuple[str, str]:
        manifest = request.executionManifest
        provider = request.vcsProvider
        if manifest is None or provider is None or request.pullRequestId is None:
            raise LatestHeadControlError(
                "candidate latest-head coordinates are incomplete"
            )
        # Java builds PublicationKey from EVcsProvider.name().toLowerCase(),
        # while the request uses provider IDs (for example bitbucket-server).
        # EVcsProvider.fromId normalizes '-' to '_'; mirror that stable identity
        # here before hashing the cross-runtime publication scope.
        normalized_provider = provider.lower().replace("-", "_")
        canonical_scope = (
            f"{normalized_provider}:{request.projectId}:{request.pullRequestId}"
        )
        scope_id = hashlib.sha256(
            canonical_scope.encode("utf-8")
        ).hexdigest()
        slot = f"{{pr-{scope_id}}}"
        prefix = "codecrow:llm-handoff:policy:v1:"
        return (
            f"{prefix}{slot}:latest-execution",
            f"{prefix}{slot}:latest-revision",
        )

    @staticmethod
    def _superseded_event(
        request: ReviewRequestDto,
        compute_state: str,
    ) -> Dict[str, Any]:
        if compute_state not in {
            "not_started",
            "cancelled",
            "completed_discarded",
        }:
            raise ValueError("invalid superseded compute state")
        return bind_owned_execution_event(
            {
                "type": "superseded",
                "reasonCode": "latest_head_advanced",
                "computeState": compute_state,
                "message": "A newer pull-request head superseded this analysis",
            },
            request.executionManifest,
        )

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
        except Exception as error:
            logger.error(
                "Failed to publish queue event: error_type=%s",
                type(error).__name__,
            )
