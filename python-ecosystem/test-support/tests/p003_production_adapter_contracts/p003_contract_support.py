from __future__ import annotations

import asyncio
import inspect
import json
import os
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from codecrow_test_harness import (
    ExternalCallLedger,
    NetworkDenyGuard,
    ProcessDenyGuard,
)
from codecrow_test_harness.environment import CredentialScrubber


IMPLEMENTED_REVIEW_CAPABILITIES = frozenset(
    {
        "streaming",
        "structured_output",
        "rate_limit_429",
        "malformed_payload",
        "timeout",
        "cancellation",
        "usage_overage",
        "retry_ceiling",
        "gemini_3_thinking_level",
        "vertex_adc",
        "vertex_express_key",
        "provider_headers",
        "cloudflare_payload_normalization",
        "embedding_model_identity",
        "embedding_empty_input",
        "embedding_partial_response",
        "embedding_dimension_mismatch",
        "embedding_dependency_failure",
        "primary_error_cleanup",
        "standalone_ledger_export",
    }
)


class Capability(str, Enum):
    STREAMING = "streaming"
    STRUCTURED_OUTPUT = "structured_output"
    RATE_LIMIT = "rate_limit_429"
    MALFORMED_PAYLOAD = "malformed_payload"
    TIMEOUT = "timeout"
    CANCELLATION = "cancellation"
    USAGE_OVERAGE = "usage_overage"
    RETRY_CEILING = "retry_ceiling"
    VERTEX_EXPRESS_KEY = "vertex_express_key"
    EMBEDDING_PARTIAL_RESPONSE = "embedding_partial_response"


class FailureKind(str, Enum):
    RATE_LIMIT = "rate_limit"
    MALFORMED_PAYLOAD = "malformed_payload"
    TIMEOUT = "timeout"
    CANCELLATION = "cancellation"
    DEPENDENCY = "dependency_failure"
    PARTIAL_RESPONSE = "partial_response"
    DIMENSION_MISMATCH = "dimension_mismatch"
    EMPTY_INPUT = "empty_input"


@dataclass(frozen=True, slots=True)
class UnsupportedCapability:
    capability: Capability
    adapter: str
    reason: str

    def __post_init__(self) -> None:
        if not self.adapter or not self.reason:
            raise ValueError("unsupported capability requires adapter and reason")


@dataclass(frozen=True, slots=True)
class CapturedHttpRequest:
    method: str
    path: str
    headers: dict[str, str]
    body: object


@dataclass(frozen=True, slots=True)
class HttpStep:
    method: str
    path: str
    status: int = 200
    headers: dict[str, str] | None = None
    body: object = None
    raw_body: bytes | None = None
    delay_seconds: float = 0.0
    transport_error: FailureKind | None = None


def assert_streaming_contract(operation: Callable[[], Iterable[Any]]) -> None:
    chunks = list(operation())
    assert chunks
    assert "".join(_message_text(chunk) for chunk in chunks) == (
        "offline production-adapter contract"
    )


def assert_structured_output_contract(operation: Callable[[], Any]) -> None:
    result = operation()
    if hasattr(result, "model_dump"):
        result = result.model_dump()
    assert result == {"answer": "offline", "confidence": 7}


def assert_overage_contract(
    operation: Callable[[], Any],
    *,
    token_budget: int,
) -> None:
    response = operation()
    usage = getattr(response, "usage_metadata", None)
    assert usage is not None
    assert usage["total_tokens"] > token_budget


def assert_model_identity_contract(adapter: Any, expected_model: str) -> None:
    assert adapter.model == expected_model


def assert_failure_contract(
    operation: Callable[[], Any],
    expected: FailureKind,
) -> BaseException:
    try:
        operation()
    except BaseException as error:
        assert classify_failure(error) == expected, _exception_chain(error)
        return error
    raise AssertionError(f"expected {expected.value} failure")


def assert_retry_ceiling_contract(
    operation: Callable[[], Any],
    call_count: Callable[[], int],
    *,
    maximum_calls: int,
) -> BaseException:
    error = assert_failure_contract(operation, FailureKind.DEPENDENCY)
    assert call_count() == maximum_calls
    return error


def assert_unsupported_capability(
    result: UnsupportedCapability,
    *,
    adapter: str,
    capability: Capability,
) -> None:
    assert isinstance(result, UnsupportedCapability)
    assert result.adapter == adapter
    assert result.capability == capability
    assert result.reason


def classify_failure(error: BaseException) -> FailureKind:
    chain = tuple(_walk_exceptions(error))
    type_names = " ".join(type(item).__name__.lower() for item in chain)
    if any(isinstance(item, asyncio.CancelledError) for item in chain):
        return FailureKind.CANCELLATION
    text = " ".join(str(item).lower() for item in chain)
    status_codes = {
        status
        for item in chain
        if (status := _status_code(item)) is not None
    }
    if 429 in status_codes or "rate limit" in text or "status code: 429" in text:
        return FailureKind.RATE_LIMIT
    if any(
        isinstance(item, (TimeoutError, httpx.TimeoutException)) for item in chain
    ) or "timed out" in text or "timeout" in text:
        return FailureKind.TIMEOUT
    if "dimension mismatch" in text or "expected embedding dimension" in text:
        return FailureKind.DIMENSION_MISMATCH
    if "batch size" in text or "embeddings for" in text:
        return FailureKind.PARTIAL_RESPONSE
    if "empty" in text and ("embed" in text or "text" in text):
        return FailureKind.EMPTY_INPUT
    if any(status >= 500 for status in status_codes):
        return FailureKind.DEPENDENCY
    malformed_markers = (
        "json",
        "decode",
        "validation",
        "malformed",
        "unexpected character",
        "invalid response",
    )
    if "jsondecode" in type_names or any(
        marker in text for marker in malformed_markers
    ):
        return FailureKind.MALFORMED_PAYLOAD
    if "simulated retryable" in text or "dependency" in text:
        return FailureKind.DEPENDENCY
    return FailureKind.DEPENDENCY


@contextmanager
def preserve_primary_error(*cleanup_actions: Callable[[], Any]) -> Iterator[None]:
    try:
        yield
    except BaseException as primary:
        for cleanup_error in _run_cleanup_actions(cleanup_actions):
            primary.add_note(
                "suppressed test cleanup error: "
                f"{type(cleanup_error).__name__}: {cleanup_error}"
            )
        raise
    cleanup_errors = _run_cleanup_actions(cleanup_actions)
    if cleanup_errors:
        primary = cleanup_errors[0]
        for cleanup_error in cleanup_errors[1:]:
            primary.add_note(
                "suppressed test cleanup error: "
                f"{type(cleanup_error).__name__}: {cleanup_error}"
            )
        raise primary


def close_adapter_clients(adapter: Any) -> None:
    actions: list[Callable[[], Any]] = []
    seen: set[int] = set()
    for attribute in (
        "root_client",
        "client",
        "root_async_client",
        "_client",
    ):
        client = getattr(adapter, attribute, None)
        if client is None or id(client) in seen:
            continue
        close = getattr(client, "close", None)
        if not callable(close):
            close = getattr(client, "aclose", None)
        if callable(close):
            seen.add(id(client))
            actions.append(close)
    cleanup_errors = _run_cleanup_actions(actions)
    if cleanup_errors:
        primary = cleanup_errors[0]
        for cleanup_error in cleanup_errors[1:]:
            primary.add_note(
                "suppressed adapter cleanup error: "
                f"{type(cleanup_error).__name__}: {cleanup_error}"
            )
        raise primary


class _LiteralThreadingHttpServer(ThreadingHTTPServer):
    def server_bind(self) -> None:
        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


class ScriptedHttpService:
    def __init__(
        self,
        steps: Sequence[HttpStep],
        *,
        ledger: ExternalCallLedger,
        network_guard: NetworkDenyGuard,
        boundary: str,
    ) -> None:
        self._steps = tuple(steps)
        self._ledger = ledger
        self._network_guard = network_guard
        self._boundary = boundary
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lease: Any = None
        self._lock = threading.Lock()
        self._requests: list[CapturedHttpRequest] = []
        self.request_started = threading.Event()
        self.request_finished = threading.Event()

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("scripted HTTP service is not started")
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    @property
    def requests(self) -> tuple[CapturedHttpRequest, ...]:
        with self._lock:
            return tuple(self._requests)

    @property
    def call_count(self) -> int:
        with self._lock:
            return len(self._requests)

    def start(self) -> ScriptedHttpService:
        if self._server is not None:
            return self
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                owner._handle(self)

            def do_POST(self) -> None:  # noqa: N802
                owner._handle(self)

            def log_message(self, *_: object) -> None:
                return

        self._server = _LiteralThreadingHttpServer(("127.0.0.1", 0), Handler)
        host, port = self._server.server_address
        self._lease = self._network_guard.register_test_service(
            host,
            port,
            f"{self._boundary}-capability-fixture",
        )
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is None:
            return
        server = self._server
        thread = self._thread
        lease = self._lease
        def join_thread() -> None:
            if thread is None:
                raise RuntimeError("scripted HTTP service thread is missing")
            thread.join(timeout=2)
            if thread.is_alive():
                raise RuntimeError("scripted HTTP service thread did not stop")

        def close_lease() -> None:
            if lease is None:
                raise RuntimeError("scripted HTTP service lease is missing")
            lease.close()

        def wait_for_request() -> None:
            if self.request_started.is_set() and not self.request_finished.wait(2):
                raise RuntimeError("scripted HTTP request handler did not finish")

        errors = _run_cleanup_actions(
            (
                server.shutdown,
                wait_for_request,
                server.server_close,
                join_thread,
                close_lease,
            )
        )
        self._server = None
        self._thread = None
        self._lease = None
        if errors:
            primary = errors[0]
            for error in errors[1:]:
                primary.add_note(
                    "suppressed scripted HTTP cleanup error: "
                    f"{type(error).__name__}: {error}"
                )
            raise primary

    def __enter__(self) -> ScriptedHttpService:
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.stop()

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        content_length = int(handler.headers.get("content-length", "0"))
        raw_request = handler.rfile.read(content_length)
        try:
            request_body: object = json.loads(raw_request) if raw_request else None
        except json.JSONDecodeError:
            request_body = raw_request.decode("utf-8", errors="replace")
        request = CapturedHttpRequest(
            method=handler.command,
            path=handler.path,
            headers={name.lower(): value for name, value in handler.headers.items()},
            body=request_body,
        )
        with self._lock:
            index = len(self._requests)
            self._requests.append(request)
        self.request_started.set()
        target = self.base_url
        step = self._steps[index] if index < len(self._steps) else None
        if step is None or (request.method, request.path) != (step.method, step.path):
            step = HttpStep(
                request.method,
                request.path,
                status=599,
                body={"error": "unexpected scripted request"},
            )
        try:
            if step.delay_seconds:
                time.sleep(step.delay_seconds)
            self._ledger.record(
                boundary=self._boundary,
                operation=request.method.lower(),
                outcome=f"status_{step.status}",
                phase="SIMULATED",
                target=target,
                simulated=True,
            )
            body = _response_bytes(step)
            handler.send_response(step.status)
            for name, value in (step.headers or {}).items():
                handler.send_header(name, value)
            handler.send_header("content-length", str(len(body)))
            handler.end_headers()
            try:
                handler.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                return
        finally:
            self.request_finished.set()


class MockHttpSequence:
    def __init__(
        self,
        steps: Sequence[HttpStep],
        *,
        ledger: ExternalCallLedger,
        boundary: str,
        target: str,
    ) -> None:
        self._steps = tuple(steps)
        self._ledger = ledger
        self._boundary = boundary
        self._target = target
        self._lock = threading.Lock()
        self._requests: list[CapturedHttpRequest] = []
        self.request_started = threading.Event()

    @property
    def requests(self) -> tuple[CapturedHttpRequest, ...]:
        with self._lock:
            return tuple(self._requests)

    @property
    def call_count(self) -> int:
        with self._lock:
            return len(self._requests)

    def sync_handler(self, request: httpx.Request) -> httpx.Response:
        step = self._take(request)
        if step.delay_seconds:
            time.sleep(step.delay_seconds)
        self._raise_transport_error(step, request)
        return _httpx_response(step, request)

    async def async_handler(self, request: httpx.Request) -> httpx.Response:
        step = self._take(request)
        if step.delay_seconds:
            await asyncio.sleep(step.delay_seconds)
        self._raise_transport_error(step, request)
        return _httpx_response(step, request)

    def _take(self, request: httpx.Request) -> HttpStep:
        raw_body = request.content
        try:
            body: object = json.loads(raw_body) if raw_body else None
        except json.JSONDecodeError:
            body = raw_body.decode("utf-8", errors="replace")
        path = request.url.raw_path.decode("ascii")
        captured = CapturedHttpRequest(
            method=request.method,
            path=path,
            headers={name.lower(): value for name, value in request.headers.items()},
            body=body,
        )
        with self._lock:
            index = len(self._requests)
            self._requests.append(captured)
        self.request_started.set()
        step = self._steps[index] if index < len(self._steps) else None
        if step is None or (captured.method, captured.path) != (
            step.method,
            step.path,
        ):
            step = HttpStep(
                captured.method,
                captured.path,
                status=599,
                body={"error": "unexpected mocked request"},
            )
        outcome = (
            step.transport_error.value
            if step.transport_error is not None
            else f"status_{step.status}"
        )
        self._ledger.record(
            boundary=self._boundary,
            operation=captured.method.lower(),
            outcome=outcome,
            phase="SIMULATED",
            target=self._target,
            simulated=True,
        )
        return step

    @staticmethod
    def _raise_transport_error(step: HttpStep, request: httpx.Request) -> None:
        if step.transport_error == FailureKind.TIMEOUT:
            raise httpx.ReadTimeout("simulated dependency timeout", request=request)
        if step.transport_error == FailureKind.CANCELLATION:
            raise asyncio.CancelledError("simulated cancellation")
        if step.transport_error == FailureKind.DEPENDENCY:
            raise httpx.ConnectError("simulated dependency failure", request=request)


@pytest.fixture(scope="session")
def adapter_harness(
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Any]:
    try:
        state = request.getfixturevalue("offline_harness")
    except pytest.FixtureLookupError:
        ledger = ExternalCallLedger()
        credentials = CredentialScrubber(populate_service_secrets=False)
        network = NetworkDenyGuard(ledger=ledger)
        process = ProcessDenyGuard(ledger=ledger)
        ledger_path = Path(
            os.environ.get(
                "CODECROW_EXTERNAL_CALL_LEDGER",
                str(
                    tmp_path_factory.mktemp("p003-standalone-ledger")
                    / "external-call-ledger.json"
                ),
            )
        ).resolve()
        credentials.__enter__()
        try:
            network.__enter__()
            try:
                process.__enter__()
            except BaseException:
                network.__exit__(None, None, None)
                raise
        except BaseException:
            credentials.__exit__(None, None, None)
            raise
        state = SimpleNamespace(
            ledger=ledger,
            network=network,
            process=process,
            credentials=credentials,
            ledger_path=ledger_path,
            standalone=True,
        )
        try:
            yield state
            credentials.assert_sanitized()
            ledger.assert_zero_live_calls()
            ledger.assert_no_unacknowledged_blocked_calls()
            network.assert_no_registered_test_services()
        finally:
            errors = _run_cleanup_actions(
                (
                    lambda: process.__exit__(None, None, None),
                    lambda: network.__exit__(None, None, None),
                    lambda: credentials.__exit__(None, None, None),
                    lambda: ledger.write(ledger_path),
                )
            )
            if errors:
                primary = errors[0]
                for error in errors[1:]:
                    primary.add_note(
                        "suppressed standalone teardown error: "
                        f"{type(error).__name__}: {error}"
                    )
                raise primary
        return

    yield state


def _run_cleanup_actions(
    actions: Iterable[Callable[[], Any]],
) -> list[BaseException]:
    errors: list[BaseException] = []
    for action in actions:
        try:
            result = action()
            if inspect.isawaitable(result):
                asyncio.run(result)
        except BaseException as error:
            errors.append(error)
    return errors


def _message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content or "")


def _walk_exceptions(error: BaseException) -> Iterator[BaseException]:
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop(0)
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        for nested in (current.__cause__, current.__context__):
            if nested is not None:
                pending.append(nested)


def _exception_chain(error: BaseException) -> str:
    return " -> ".join(
        f"{type(item).__name__}: {item}" for item in _walk_exceptions(error)
    )


def _status_code(error: BaseException) -> int | None:
    status_code = getattr(error, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    return status_code if isinstance(status_code, int) else None


def _response_bytes(step: HttpStep) -> bytes:
    if step.raw_body is not None:
        return step.raw_body
    if isinstance(step.body, str):
        return step.body.encode("utf-8")
    return json.dumps(step.body, separators=(",", ":")).encode("utf-8")


def _httpx_response(step: HttpStep, request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        step.status,
        headers=step.headers,
        content=_response_bytes(step),
        request=request,
    )
