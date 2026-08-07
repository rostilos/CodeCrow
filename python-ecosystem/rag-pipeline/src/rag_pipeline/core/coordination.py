"""Cross-process coordination for RAG mutations and embedding capacity."""

from __future__ import annotations

import hashlib
import logging
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional

import redis


logger = logging.getLogger(__name__)


class MutationLeaseUnavailable(RuntimeError):
    """Raised when another worker owns the project mutation lease."""


class MutationCoordinationUnavailable(RuntimeError):
    """Raised when mutation safety cannot be established through Redis."""


_RENEW_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  redis.call('expire', KEYS[1], ARGV[2])
  redis.call('expire', KEYS[2], ARGV[2])
  return 1
end
return 0
"""

_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  redis.call('del', KEYS[1])
  redis.call('del', KEYS[2])
  return 1
end
return 0
"""

_ACQUIRE_PERMIT_SCRIPT = """
redis.call('zremrangebyscore', KEYS[1], '-inf', ARGV[1])
if redis.call('zcard', KEYS[1]) < tonumber(ARGV[2]) then
  redis.call('zadd', KEYS[1], ARGV[3], ARGV[4])
  redis.call('expire', KEYS[1], ARGV[5])
  return 1
end
return 0
"""


@dataclass
class MutationLease:
    """One renewable project-scoped mutation lease."""

    client: Optional[redis.Redis]
    key: str
    operation_key: str
    token: str
    lease_seconds: int
    enabled: bool = True

    def __post_init__(self) -> None:
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start_renewal(self) -> None:
        if not self.enabled or self.client is None:
            return
        self._thread = threading.Thread(
            target=self._renew_loop,
            name=f"rag-mutation-lease-{self.token[:8]}",
            daemon=True,
        )
        self._thread.start()

    def _renew_loop(self) -> None:
        interval = max(1.0, self.lease_seconds / 3)
        while not self._stop.wait(interval):
            try:
                renewed = self.client.eval(
                    _RENEW_SCRIPT,
                    2,
                    self.key,
                    self.operation_key,
                    self.token,
                    self.lease_seconds,
                )
                if not renewed:
                    self._lost.set()
                    logger.error("Lost RAG project mutation lease %s", self.key)
                    return
            except Exception:
                self._lost.set()
                logger.exception("Could not renew RAG project mutation lease %s", self.key)
                return

    def assert_owned(self) -> None:
        """Fail before an irreversible mutation when ownership was lost."""
        if not self.enabled or self.client is None:
            return
        if self._lost.is_set():
            raise MutationCoordinationUnavailable(
                "RAG project mutation lease was lost before activation"
            )
        try:
            owner = self.client.get(self.key)
        except Exception as exception:
            raise MutationCoordinationUnavailable(
                "RAG project mutation ownership could not be verified"
            ) from exception
        if owner != self.token:
            self._lost.set()
            raise MutationLeaseUnavailable(
                "RAG project mutation was superseded before activation"
            )

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        if not self.enabled or self.client is None:
            return
        try:
            self.client.eval(
                _RELEASE_SCRIPT,
                2,
                self.key,
                self.operation_key,
                self.token,
            )
        except Exception:
            logger.exception("Could not release RAG project mutation lease %s", self.key)


class ProjectMutationCoordinator:
    """Serialize mutations that share one RAG publication resource.

    Legacy indexes share a project-wide collection. Exact generations have an
    immutable collection target, while a published branch also has one mutable
    human-readable head alias. ``publication_scope`` serializes only that
    branch head, so main and develop can build concurrently without allowing
    two generations of the same branch to race its current alias.
    """

    def __init__(
        self,
        redis_url: str,
        *,
        enabled: bool = True,
        lease_seconds: int = 300,
        acquire_timeout_seconds: float = 5.0,
    ) -> None:
        self.enabled = enabled
        self.lease_seconds = max(30, lease_seconds)
        self.acquire_timeout_seconds = max(0.0, acquire_timeout_seconds)
        self._client = (
            redis.Redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                health_check_interval=30,
            )
            if enabled
            else None
        )

    @staticmethod
    def _resource_key(
        workspace: str,
        project: str,
        collection_target: Optional[str] = None,
        publication_scope: Optional[str] = None,
    ) -> str:
        # Legacy indexing keeps the historical project-wide key because its
        # branches share one physical alias. Exact branch generations supply
        # their distinct collection target, so independent branch snapshots
        # may proceed concurrently without weakening same-collection safety.
        resource = (
            publication_scope
            or collection_target
            or "project-shared-collection"
        )
        digest = hashlib.sha256(
            f"{workspace}\0{project}\0{resource}".encode("utf-8")
        ).hexdigest()
        return f"codecrow:rag:mutation:{digest}"

    @contextmanager
    def acquire(
        self,
        workspace: str,
        project: str,
        operation: str,
        *,
        collection_target: Optional[str] = None,
        publication_scope: Optional[str] = None,
    ) -> Iterator[MutationLease]:
        token = uuid.uuid4().hex
        if not self.enabled or self._client is None:
            lease = MutationLease(None, "", "", token, self.lease_seconds, False)
            yield lease
            return

        key = self._resource_key(
            workspace,
            project,
            collection_target,
            publication_scope,
        )
        operation_key = f"codecrow:rag:operation:{token}"
        deadline = time.monotonic() + self.acquire_timeout_seconds
        while True:
            try:
                acquired = bool(
                    self._client.set(
                        key,
                        token,
                        nx=True,
                        ex=self.lease_seconds,
                    )
                )
                if acquired:
                    try:
                        self._client.set(
                            operation_key,
                            operation,
                            ex=self.lease_seconds,
                        )
                    except Exception:
                        self._client.eval(
                            _RELEASE_SCRIPT,
                            2,
                            key,
                            operation_key,
                            token,
                        )
                        raise
                    break
            except Exception as exception:
                raise MutationCoordinationUnavailable(
                    "Redis is unavailable; refusing an uncoordinated RAG mutation"
                ) from exception
            if time.monotonic() >= deadline:
                raise MutationLeaseUnavailable(
                    "another RAG mutation is active for "
                    f"{workspace}/{project}"
                    + (
                        f" publication {publication_scope}"
                        if publication_scope
                        else (
                            f" collection {collection_target}"
                            if collection_target else ""
                        )
                    )
                )
            time.sleep(0.1)

        lease = MutationLease(
            self._client,
            key,
            operation_key,
            token,
            self.lease_seconds,
        )
        lease.start_renewal()
        logger.info(
            "Acquired RAG mutation lease operation=%s workspace=%s project=%s collection=%s operation_id=%s",
            operation,
            workspace,
            project,
            publication_scope or collection_target or "project-shared-collection",
            token,
        )
        try:
            yield lease
        finally:
            lease.close()

    def is_operation_active(self, token: str) -> bool:
        if not self.enabled or self._client is None:
            return False
        try:
            return bool(self._client.exists(f"codecrow:rag:operation:{token}"))
        except Exception:
            # Cleanup is auxiliary and must fail open by retaining collections.
            logger.warning("Could not verify pending-collection operation %s", token)
            return True

    def close(self) -> None:
        if self._client is not None:
            self._client.close()


class RedisPermitPool:
    """Best-effort distributed cap with a process-local fail-open fallback."""

    def __init__(
        self,
        redis_url: str,
        limit: int,
        *,
        permit_seconds: int,
        acquire_timeout_seconds: float = 30.0,
    ) -> None:
        self.limit = max(1, limit)
        self.permit_seconds = max(30, permit_seconds)
        self.acquire_timeout_seconds = max(0.1, acquire_timeout_seconds)
        self._local = threading.BoundedSemaphore(self.limit)
        self._client = redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            health_check_interval=30,
        )
        self._disabled_until = 0.0
        self._state_lock = threading.Lock()
        self._key = "codecrow:rag:openrouter:index:permits"

    @contextmanager
    def permit(self) -> Iterator[None]:
        self._local.acquire()
        token = uuid.uuid4().hex
        distributed = False
        try:
            distributed = self._acquire_distributed(token)
            yield
        finally:
            if distributed:
                try:
                    self._client.zrem(self._key, token)
                except Exception:
                    logger.warning("Could not release distributed OpenRouter permit")
            self._local.release()

    def _acquire_distributed(self, token: str) -> bool:
        with self._state_lock:
            if time.monotonic() < self._disabled_until:
                return False
        deadline = time.monotonic() + self.acquire_timeout_seconds
        while True:
            now = time.time()
            try:
                acquired = self._client.eval(
                    _ACQUIRE_PERMIT_SCRIPT,
                    1,
                    self._key,
                    now,
                    self.limit,
                    now + self.permit_seconds,
                    token,
                    self.permit_seconds,
                )
                if acquired:
                    return True
            except Exception as exception:
                with self._state_lock:
                    self._disabled_until = time.monotonic() + 60
                logger.warning(
                    "Distributed OpenRouter capacity limit unavailable; "
                    "using process-local cap for 60s: %s",
                    exception,
                )
                return False
            if time.monotonic() >= deadline:
                logger.warning(
                    "Still waiting for distributed OpenRouter capacity after %.1fs",
                    self.acquire_timeout_seconds,
                )
                deadline = time.monotonic() + self.acquire_timeout_seconds
            time.sleep(0.05)

    def close(self) -> None:
        self._client.close()
