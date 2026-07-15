from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from types import TracebackType
from typing import Any, Mapping

from .ledger import ExternalCallLedger
from .network import EndpointLease, NetworkDenyGuard


class ProtocolFixtureError(ValueError):
    pass


class _LiteralThreadingHTTPServer(ThreadingHTTPServer):
    """HTTP fixture server that never reverse-resolves its literal bind address."""

    def server_bind(self) -> None:
        TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


@dataclass(frozen=True, slots=True)
class ProtocolCall:
    method: str
    path: str


@dataclass(frozen=True, slots=True)
class _Response:
    status: int
    headers: Mapping[str, str]
    body: Any


class ProtocolFixtureServer:
    def __init__(
        self,
        fixture: str | Path,
        *,
        ledger: ExternalCallLedger,
        network_guard: NetworkDenyGuard,
    ) -> None:
        self._fixture = Path(fixture)
        self._ledger = ledger
        self._network_guard = network_guard
        self._provider, self._routes = _load_fixture(self._fixture)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lease: EndpointLease | None = None
        self._calls: list[ProtocolCall] = []
        self._calls_lock = threading.Lock()

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("protocol fixture server is not started")
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    @property
    def calls(self) -> tuple[ProtocolCall, ...]:
        with self._calls_lock:
            return tuple(self._calls)

    def start(self) -> ProtocolFixtureServer:
        if self._server is not None:
            return self
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                owner._handle(self)

            def do_POST(self) -> None:  # noqa: N802
                owner._handle(self)

            def do_PUT(self) -> None:  # noqa: N802
                owner._handle(self)

            def do_PATCH(self) -> None:  # noqa: N802
                owner._handle(self)

            def do_DELETE(self) -> None:  # noqa: N802
                owner._handle(self)

            def log_message(self, *_: object) -> None:
                return

        self._server = _LiteralThreadingHTTPServer(("127.0.0.1", 0), Handler)
        host, port = self._server.server_address
        self._lease = self._network_guard.register_test_service(
            host, port, f"{self._provider}-fixture"
        )
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is None:
            return
        server = self._server
        thread = self._thread
        lease = self._lease
        errors: list[BaseException] = []

        def join_thread() -> None:
            if thread is None:
                raise RuntimeError("protocol fixture server thread is missing")
            thread.join(timeout=2)
            if thread.is_alive():
                raise RuntimeError("protocol fixture server thread did not stop")

        def close_lease() -> None:
            if lease is None:
                raise RuntimeError("protocol fixture endpoint lease is missing")
            lease.close()

        actions = (server.shutdown, server.server_close, join_thread, close_lease)
        try:
            for action in actions:
                try:
                    action()
                except BaseException as error:
                    errors.append(error)
        finally:
            self._server = None
            self._thread = None
            self._lease = None
        if errors:
            primary = errors[0]
            for suppressed in errors[1:]:
                primary.add_note(
                    f"suppressed protocol fixture cleanup error: "
                    f"{type(suppressed).__name__}: {suppressed}"
                )
            raise primary

    def __enter__(self) -> ProtocolFixtureServer:
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            self.stop()
        except BaseException as cleanup_error:
            if exc_value is None:
                raise
            exc_value.add_note(
                f"suppressed protocol fixture context cleanup error: "
                f"{type(cleanup_error).__name__}: {cleanup_error}"
            )

    def _handle(self, handler: BaseHTTPRequestHandler) -> None:
        call = ProtocolCall(handler.command, handler.path)
        with self._calls_lock:
            self._calls.append(call)
        response = self._routes.get((call.method, call.path))
        if response is None:
            response = _Response(599, {}, {"error": "unregistered fixture route"})
        self._ledger.record(
            boundary=self._provider,
            operation=call.method.lower(),
            outcome=f"status_{response.status}",
            phase="SIMULATED",
            target=self.base_url,
            simulated=True,
        )
        body = (
            response.body.encode("utf-8")
            if isinstance(response.body, str)
            else json.dumps(response.body, separators=(",", ":")).encode("utf-8")
        )
        handler.send_response(response.status)
        for name, value in response.headers.items():
            handler.send_header(name, value)
        handler.send_header("content-length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)


def _load_fixture(path: Path) -> tuple[str, dict[tuple[str, str], _Response]]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProtocolFixtureError(f"cannot load protocol fixture: {path.name}") from error
    if document.get("schema_version") != "1.0":
        raise ProtocolFixtureError("unsupported protocol fixture schema version")
    provider = document.get("provider")
    routes = document.get("routes")
    if not isinstance(provider, str) or not provider:
        raise ProtocolFixtureError("protocol fixture provider must not be empty")
    if not isinstance(routes, list):
        raise ProtocolFixtureError("protocol fixture routes must be a list")
    parsed: dict[tuple[str, str], _Response] = {}
    for route in routes:
        if not isinstance(route, dict):
            raise ProtocolFixtureError("protocol fixture route must be an object")
        method = route.get("method")
        request_path = route.get("path")
        response = route.get("response")
        if not isinstance(method, str) or not method or not isinstance(request_path, str):
            raise ProtocolFixtureError("protocol fixture route method/path is invalid")
        if not request_path.startswith("/") or not isinstance(response, dict):
            raise ProtocolFixtureError("protocol fixture route response/path is invalid")
        status = response.get("status")
        headers = response.get("headers", {})
        if not isinstance(status, int) or not 100 <= status <= 599:
            raise ProtocolFixtureError("protocol fixture response status is invalid")
        if not isinstance(headers, dict) or not all(
            isinstance(name, str) and isinstance(value, str) for name, value in headers.items()
        ):
            raise ProtocolFixtureError("protocol fixture response headers are invalid")
        key = (method.upper(), request_path)
        if key in parsed:
            raise ProtocolFixtureError("protocol fixture contains a duplicate route")
        parsed[key] = _Response(status, dict(headers), response.get("body"))
    return provider.lower(), parsed
