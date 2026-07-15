from __future__ import annotations

import socket
import sys
import threading
from dataclasses import dataclass
from ipaddress import ip_address
from types import TracebackType
from typing import Callable

from .ledger import ExternalCall, ExternalCallLedger


class UnexpectedExternalCall(RuntimeError):
    """Raised before an unregistered network target can be resolved."""

    def __init__(self, message: str, *, call: ExternalCall | None = None) -> None:
        super().__init__(message)
        self.call = call


class LeakedEndpointLeaseError(AssertionError):
    """Raised when a test-owned endpoint lease survives guard teardown."""


@dataclass(slots=True)
class EndpointLease:
    _guard: NetworkDenyGuard
    host: str
    port: int
    boundary: str
    _closed: bool = False

    def close(self) -> None:
        if not self._closed:
            self._guard._unregister(self.host, self.port)
            self._closed = True

    def __enter__(self) -> EndpointLease:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class NetworkDenyGuard:
    _activation_lock = threading.Lock()
    _active_guard: NetworkDenyGuard | None = None

    def __init__(
        self,
        *,
        ledger: ExternalCallLedger,
        resolver: Callable[..., object] = socket.getaddrinfo,
        connector: Callable[..., socket.socket] = socket.create_connection,
    ) -> None:
        self._ledger = ledger
        self._resolver = resolver
        self._connector = connector
        self._previous_resolver: Callable[..., object] | None = None
        self._previous_connector: Callable[..., socket.socket] | None = None
        self._previous_socket: type[socket.socket] | None = None
        self._registered: dict[tuple[str, int], int] = {}
        self._registry_lock = threading.RLock()
        self._entered = False

    def register_test_service(self, host: str, port: int, boundary: str) -> EndpointLease:
        normalized_host = _normalize_loopback(host)
        normalized_port = _normalize_port(port)
        if not isinstance(boundary, str) or not boundary.strip():
            raise ValueError("boundary must be a non-empty string")
        endpoint = (normalized_host, normalized_port)
        with self._registry_lock:
            self._registered[endpoint] = self._registered.get(endpoint, 0) + 1
        return EndpointLease(self, *endpoint, boundary.strip())

    def __enter__(self) -> NetworkDenyGuard:
        if self._entered or not self._activation_lock.acquire(blocking=False):
            raise RuntimeError("another network deny guard is already active")
        self._previous_resolver = socket.getaddrinfo
        self._previous_connector = socket.create_connection
        self._previous_socket = socket.socket
        socket.getaddrinfo = self._deny_resolution  # type: ignore[assignment]
        socket.create_connection = self._deny_connection  # type: ignore[assignment]
        socket.socket = self._guarded_socket_class(self._previous_socket)  # type: ignore[assignment,misc]
        type(self)._active_guard = self
        self._entered = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._entered:
            leak_error: LeakedEndpointLeaseError | None = None
            with self._registry_lock:
                if self._registered:
                    lease_count = sum(self._registered.values())
                    leak_error = LeakedEndpointLeaseError(
                        f"network guard closed with {lease_count} test-service lease(s) still active"
                    )
                    self._registered.clear()
            type(self)._active_guard = None
            try:
                socket.getaddrinfo = self._previous_resolver  # type: ignore[assignment]
                socket.create_connection = self._previous_connector  # type: ignore[assignment]
                socket.socket = self._previous_socket  # type: ignore[assignment,misc]
            finally:
                self._entered = False
                self._activation_lock.release()
            if leak_error is not None:
                if exc_value is not None:
                    exc_value.add_note(str(leak_error))
                else:
                    raise leak_error

    def assert_no_registered_test_services(self) -> None:
        with self._registry_lock:
            lease_count = sum(self._registered.values())
        if lease_count:
            raise LeakedEndpointLeaseError(
                f"network guard contains {lease_count} active test-service lease(s)"
            )

    def _deny_resolution(self, host: str, port: int | str, *args: object, **kwargs: object) -> object:
        self._check(host, port, "PRE_DNS")
        return self._resolver(host, port, *args, **kwargs)

    def _deny_connection(
        self,
        address: tuple[object, ...],
        *args: object,
        **kwargs: object,
    ) -> socket.socket:
        host, port = _address_parts(address)
        self._check(host, port, "PRE_DNS")
        return self._connector(address, *args, **kwargs)

    def _check(
        self,
        host: object,
        port: object,
        phase: str,
        *,
        operation: str = "connect",
    ) -> None:
        normalized = _normalize_endpoint(host, port)
        with self._registry_lock:
            if normalized in self._registered:
                return
        target = _format_target(host, port)
        call = self._ledger.record(
            boundary="network",
            operation=operation,
            outcome="blocked",
            phase=phase,
            target=target,
        )
        raise UnexpectedExternalCall(
            f"unregistered outbound call blocked at {phase}: {target}", call=call
        )

    def _check_resolution_host(self, host: object) -> None:
        normalized_host: str | None
        try:
            normalized_host = _normalize_loopback(str(host))
        except ValueError:
            normalized_host = None
        with self._registry_lock:
            if normalized_host is not None and any(
                registered_host == normalized_host
                for registered_host, _ in self._registered
            ):
                return
        target = _format_target(host, 0)
        call = self._ledger.record(
            boundary="network",
            operation="resolve",
            outcome="blocked",
            phase="PRE_DNS",
            target=target,
        )
        raise UnexpectedExternalCall(
            f"unregistered outbound call blocked at PRE_DNS: {target}", call=call
        )

    def _audit(self, event: str, arguments: tuple[object, ...]) -> None:
        if event == "socket.getaddrinfo":
            self._check(arguments[0], arguments[1], "PRE_DNS")
        elif event == "socket.getnameinfo":
            host, port = _address_parts(arguments[0])
            self._check(host, port, "PRE_DNS", operation="resolve")
        elif event in {
            "socket.gethostbyaddr",
            "socket.gethostbyname",
            "socket.gethostbyname_ex",
        }:
            self._check_resolution_host(arguments[0])
        elif event == "socket.connect":
            host, port = _address_parts(arguments[1])
            self._check(host, port, "PRE_SOCKET")
        elif event in {"socket.sendto", "socket.sendmsg"}:
            address = arguments[1]
            if address is None:
                address = _connected_peer(arguments[0])
            host, port = _address_parts(address)
            self._check(host, port, "PRE_SOCKET", operation="send")

    def _unregister(self, host: str, port: int) -> None:
        endpoint = (host, port)
        with self._registry_lock:
            count = self._registered.get(endpoint, 0)
            if count <= 1:
                self._registered.pop(endpoint, None)
            else:
                self._registered[endpoint] = count - 1

    def _guarded_socket_class(self, base: type[socket.socket]) -> type[socket.socket]:
        guard = self

        class GuardedSocket(base):
            def connect(self, address: object) -> None:
                host, port = _address_parts(address)
                guard._check(host, port, "PRE_SOCKET")
                return super().connect(address)  # type: ignore[arg-type]

            def connect_ex(self, address: object) -> int:
                host, port = _address_parts(address)
                guard._check(host, port, "PRE_SOCKET")
                return super().connect_ex(address)  # type: ignore[arg-type]

            def sendto(self, data: object, *args: object) -> int:
                if not args:
                    return super().sendto(data, *args)  # type: ignore[arg-type]
                address = args[-1] if args else None
                host, port = _address_parts(address)
                guard._check(host, port, "PRE_SOCKET", operation="send")
                return super().sendto(data, *args)  # type: ignore[arg-type]

            def sendmsg(self, buffers: object, *args: object) -> int:
                address = args[2] if len(args) >= 3 else _connected_peer(self)
                host, port = _address_parts(address)
                guard._check(host, port, "PRE_SOCKET", operation="send")
                return super().sendmsg(buffers, *args)  # type: ignore[arg-type]

        return GuardedSocket


def _normalize_loopback(host: str) -> str:
    if not isinstance(host, str) or not host.strip():
        raise ValueError("test service host must be a literal loopback address")
    normalized = host.strip().lower().rstrip(".")
    try:
        address = ip_address(normalized)
    except ValueError as error:
        raise ValueError("test service host must be a literal loopback address") from error
    if not address.is_loopback:
        raise ValueError("only test-owned loopback services may be registered")
    return address.compressed


def _normalize_port(port: object) -> int:
    if isinstance(port, bool):
        raise ValueError("test service port must be an integer from 1 to 65535")
    try:
        normalized = int(port)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise ValueError("test service port must be an integer from 1 to 65535") from error
    if not 1 <= normalized <= 65535:
        raise ValueError("test service port must be an integer from 1 to 65535")
    return normalized


def _normalize_endpoint(host: object, port: object) -> tuple[str, int] | None:
    try:
        return _normalize_loopback(str(host)), _normalize_port(port)
    except ValueError:
        return None


def _address_parts(address: object) -> tuple[object, object]:
    if isinstance(address, tuple) and len(address) >= 2:
        return address[0], address[1]
    return "unix", 0


def _connected_peer(candidate: object) -> object:
    try:
        return candidate.getpeername()  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return None


def _format_target(host: object, port: object) -> str:
    text = str(host).strip().lower().rstrip(".")
    if ":" in text and not text.startswith("["):
        text = f"[{text}]"
    return f"{text}:{port}"


def _network_audit_hook(event: str, arguments: tuple[object, ...]) -> None:
    guard = NetworkDenyGuard._active_guard
    if guard is not None:
        guard._audit(event, arguments)


_network_audit_hook.__cantrace__ = True  # type: ignore[attr-defined]
sys.addaudithook(_network_audit_hook)
