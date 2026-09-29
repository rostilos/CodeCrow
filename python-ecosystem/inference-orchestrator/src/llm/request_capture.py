"""Opt-in, private capture of the actual SDK HTTP bodies used by a review.

No LangChain message reconstruction, credentials, headers, or URL queries are
recorded. Source in the body is retained verbatim. Capture failures never change
the model call. Response streams are copied as consumed, not eagerly drained.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

import httpx

# Current OpenAI/Anthropic SDKs use httpx2; Google and the SSRF adapter use
# httpx. Their stream protocols are equivalent but use distinct base classes.
try:
    from httpx2 import AsyncByteStream as _AsyncStream2, SyncByteStream as _SyncStream2
except ImportError:
    _AsyncStream2 = _SyncStream2 = object

logger = logging.getLogger(__name__)
_REVIEW: ContextVar[dict[str, Any] | None] = ContextVar("review_capture", default=None)
_CALL: ContextVar["CallCapture | None"] = ContextVar("review_capture_call", default=None)
_JOB: ContextVar[str | None] = ContextVar("review_capture_job", default=None)
_EXTENSION = "codecrow_capture_attempt"


def capture_enabled() -> bool:
    return os.getenv("REVIEW_QUALITY_CAPTURE_ENABLED", "false").lower() in {"true", "1", "yes"}


def _warning(operation: str, error: Exception) -> None:
    # Exception messages can contain provider URLs or request bodies.
    logger.warning("Review wire capture unavailable: operation=%s error_type=%s",
                   operation, type(error).__name__)


@contextmanager
def queue_capture_context(job_id: str) -> Iterator[None]:
    token = _JOB.set(str(job_id))
    try:
        yield
    finally:
        _JOB.reset(token)


@contextmanager
def review_capture(request: Any) -> Iterator[None]:
    """Establish one run identity, inherited by this review's concurrent tasks."""
    identity = None
    if capture_enabled():
        project_id = getattr(request, "projectId", None)
        selected = {value.strip() for value in os.getenv("REVIEW_QUALITY_CAPTURE_PROJECT_IDS", "").split(",") if value.strip()}
        if not selected or str(project_id) in selected:
            identity = {key: getattr(request, key, None) for key in (
                "projectId", "projectWorkspace", "projectNamespace", "pullRequestId",
                "sourceBranchName", "targetBranchName", "currentCommitHash", "commitHash",
                "targetHeadCommitHash", "aiProvider", "aiModel",
            )}
            identity.update(run_id=uuid4().hex, job_id=_JOB.get())
    token = _REVIEW.set(identity)
    try:
        yield
    finally:
        _REVIEW.reset(token)


@dataclass
class CallCapture:
    metadata: dict[str, Any]
    attempts: list["AttemptCapture"] = field(default_factory=list)


@contextmanager
def model_capture(request: Any, *, stage: str, turn: int = 1,
                  batch_ids: list[str] | None = None) -> Iterator[None]:
    """Correlate each paid invocation; each SDK retry becomes a separate attempt."""
    if _REVIEW.get() is None:
        with review_capture(request):
            with _call_capture(stage, turn, batch_ids):
                yield
    else:
        with _call_capture(stage, turn, batch_ids):
            yield


@contextmanager
def _call_capture(stage: str, turn: int, batch_ids: list[str] | None) -> Iterator[None]:
    identity = _REVIEW.get()
    call = None if identity is None else CallCapture({
        **identity, "call_id": uuid4().hex, "stage": stage, "turn": turn,
        "batch_ids": batch_ids or [],
    })
    token = _CALL.set(call)
    try:
        yield
    except BaseException as error:
        if call:
            for attempt in call.attempts:
                attempt.finish(error_type=type(error).__name__)
        raise
    finally:
        if call:
            for attempt in call.attempts:
                attempt.finish()
        _CALL.reset(token)


def _private_directory(path: Path) -> int:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    stat = os.fstat(descriptor)
    # Captures are written beneath opaque tenant directories. Docker commonly
    # creates the mount root as 0755; privacy belongs to the 0700 children and 0600
    # files below them. It must still be service-owned and not writable by
    # another user, so they cannot replace a tenant directory between opens.
    if stat.st_uid != os.geteuid() or stat.st_mode & 0o022:
        os.close(descriptor)
        raise PermissionError("capture root must be service-owned and not group/world writable")
    return descriptor


def _child_directory(parent: int, name: str) -> int:
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent)
    except FileExistsError:
        pass
    descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    stat = os.fstat(descriptor)
    if stat.st_uid != os.geteuid() or stat.st_mode & 0o077:
        os.close(descriptor)
        raise PermissionError("capture subdirectory must be private")
    return descriptor


class AttemptCapture:
    def __init__(self, call: CallCapture, request: httpx.Request):
        self.metadata = {**call.metadata, "attempt": len(call.attempts) + 1,
                         "started_at": datetime.now(timezone.utc).isoformat(),
                         "method": request.method, "endpoint_path": request.url.path,
                         "response_complete": False}
        self.done = False
        self.response_file = None
        self.encoding = ""
        self.directory = -1
        root = Path(os.getenv("REVIEW_QUALITY_CAPTURE_OUTPUT_DIR", "/app/logs/review-quality-captures"))
        root_fd = _private_directory(root)
        tenant = hashlib.sha256(json.dumps([
            call.metadata.get("projectWorkspace"), call.metadata.get("projectNamespace"),
            call.metadata.get("projectId"),
        ], ensure_ascii=False).encode()).hexdigest()
        try:
            tenant_fd = _child_directory(root_fd, tenant)
            try:
                self.directory = _child_directory(tenant_fd, call.metadata["run_id"])
            finally:
                os.close(tenant_fd)
        finally:
            os.close(root_fd)
        self.prefix = f'{call.metadata["call_id"]}-{self.metadata["attempt"]}'
        try:
            with self._file("request.body") as target:
                target.write(request.content)
            self._write_metadata()
        except Exception:
            os.close(self.directory)
            self.directory = -1
            raise

    def _file(self, suffix: str):
        descriptor = os.open(f"{self.prefix}.{suffix}", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=self.directory)
        return os.fdopen(descriptor, "wb")

    def _write_metadata(self) -> None:
        # Separate begin/end manifests are immutable, including interrupted runs.
        with self._file("complete.json" if self.done else "start.json") as target:
            target.write(json.dumps(self.metadata, ensure_ascii=False, indent=2).encode())

    def response(self, response: httpx.Response) -> None:
        self.metadata["http_status"] = response.status_code
        # Store only encoding needed to decode the body, never response headers.
        self.encoding = response.headers.get("content-encoding", "")
        self.metadata["body_encoding"] = self.encoding or "identity"
        self.response_file = self._file("response.body")

    def chunk(self, data: bytes) -> None:
        if self.response_file is None or self.done:
            return
        try:
            self.response_file.write(data)
        except Exception as error:
            _warning("response_write", error)
            self.finish(error_type=type(error).__name__)

    def finish(self, *, complete: bool = False, error_type: str | None = None) -> None:
        if self.done:
            return
        self.done = True
        try:
            if self.response_file:
                self.response_file.close()
            self.metadata.update(response_complete=complete, ended_at=datetime.now(timezone.utc).isoformat())
            if error_type:
                self.metadata["capture_or_transport_error_type"] = error_type
            if complete:
                try:
                    self._response_identity()
                except Exception as error:
                    # Non-JSON/error bodies still have useful completion records.
                    _warning("response_identity", error)
            self._write_metadata()
        except Exception as error:
            _warning("finish", error)
        finally:
            if self.directory >= 0:
                try:
                    os.close(self.directory)
                except OSError as error:
                    _warning("directory_close", error)
                self.directory = -1

    def _response_identity(self) -> None:
        descriptor = os.open(f"{self.prefix}.response.body", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.directory)
        with os.fdopen(descriptor, "rb") as source:
            body = source.read()
        if self.encoding:
            # Use the same installed HTTP content decoders (including optional
            # Brotli/Zstandard) as the SDK instead of assuming gzip-only responses.
            body = httpx.Response(200, headers={"content-encoding": self.encoding}, content=body).content
        try:
            events = [json.loads(body)]
        except (ValueError, UnicodeError):
            events = []
            for line in body.splitlines():
                if line.startswith(b"data:") and line[5:].strip() != b"[DONE]":
                    try:
                        events.append(json.loads(line[5:]))
                    except ValueError:
                        continue
        for event in events:
            if isinstance(event, dict):
                event = event.get("message") or event.get("response") or event
                if isinstance(event, dict):
                    generation = event.get("id") or event.get("responseId")
                    if generation:
                        self.metadata["provider_generation_id"] = generation


class _SyncTee(httpx.SyncByteStream, _SyncStream2):
    def __init__(self, stream, attempt):
        self.stream, self.attempt = stream, attempt

    def __iter__(self):
        try:
            for chunk in self.stream:
                self.attempt.chunk(chunk)
                yield chunk
            self.attempt.finish(complete=True)
        except BaseException as error:
            self.attempt.finish(error_type=type(error).__name__)
            raise

    def close(self):
        try:
            self.stream.close()
        finally:
            self.attempt.finish()


class _AsyncTee(httpx.AsyncByteStream, _AsyncStream2):
    def __init__(self, stream, attempt):
        self.stream, self.attempt = stream, attempt

    async def __aiter__(self):
        try:
            async for chunk in self.stream:
                self.attempt.chunk(chunk)
                yield chunk
            self.attempt.finish(complete=True)
        except BaseException as error:
            self.attempt.finish(error_type=type(error).__name__)
            raise

    async def aclose(self):
        try:
            await self.stream.aclose()
        finally:
            self.attempt.finish()


def _request_hook(request: httpx.Request) -> None:
    call = _CALL.get()
    if call is None:
        return
    try:
        attempt = AttemptCapture(call, request)
        call.attempts.append(attempt)
        request.extensions[_EXTENSION] = attempt
    except Exception as error:
        _warning("request", error)


def _response_hook(response: httpx.Response, *, asynchronous: bool = False) -> None:
    attempt = response.request.extensions.get(_EXTENSION)
    if attempt is None:
        return
    try:
        attempt.response(response)
        if response.is_stream_consumed:
            # Mock/in-process transports may already have decoded the response.
            attempt.encoding = ""
            attempt.metadata["body_encoding"] = "identity"
            attempt.chunk(response.content)
            attempt.finish(complete=True)
        else:
            response.stream = (_AsyncTee if asynchronous else _SyncTee)(response.stream, attempt)
    except Exception as error:
        _warning("response", error)
        attempt.finish(error_type=type(error).__name__)


async def _async_request_hook(request: httpx.Request) -> None:
    _request_hook(request)


async def _async_response_hook(response: httpx.Response) -> None:
    _response_hook(response, asynchronous=True)


def attach_http_capture(client: httpx.Client | httpx.AsyncClient) -> None:
    """Keep the original transport, pool, timeout, SSRF checks and existing hooks."""
    asynchronous = callable(getattr(client, "aclose", None))
    for event, hook in (("request", _async_request_hook if asynchronous else _request_hook),
                        ("response", _async_response_hook if asynchronous else _response_hook)):
        if hook not in client.event_hooks[event]:
            client.event_hooks[event].append(hook)


def configure_capture(model: Any, provider: str) -> Any:
    """Attach at SDK serialization seams, without wrapping or changing the model."""
    if not capture_enabled():
        return model
    try:
        if provider in {"openrouter", "openai", "openai_compatible"}:
            clients = (model.root_client._client, model.root_async_client._client)
        elif provider == "anthropic":
            clients = (model._client._client, model._async_client._client)
        elif provider in {"google", "google_vertex"}:
            api = model.client._api_client
            clients = (api._httpx_client, api._async_httpx_client)
            # SDK HttpOptions supports an explicit httpx client. Select its
            # already configured instance so optional aiohttp cannot bypass the
            # capture; authentication and provider serialization remain SDK-owned.
            api._http_options.httpx_async_client = api._async_httpx_client
        else:
            raise ValueError("unsupported provider capture seam")
        for client in clients:
            attach_http_capture(client)
    except Exception as error:
        _warning("provider_attach", error)
    return model
