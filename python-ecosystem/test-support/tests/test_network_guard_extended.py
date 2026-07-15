from __future__ import annotations

import socket
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import requests


TEST_SUPPORT_ROOT = Path(__file__).resolve().parents[1]
if str(TEST_SUPPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_SUPPORT_ROOT))

from codecrow_test_harness.ledger import ExternalCallLedger
from codecrow_test_harness.network import (
    LeakedEndpointLeaseError,
    NetworkDenyGuard,
    UnexpectedExternalCall,
)


class _Handler(BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
        type(self).hits += 1
        body = b'{"offline":true}'
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: object) -> None:
        return


def test_direct_dns_and_raw_socket_paths_are_denied() -> None:
    ledger = ExternalCallLedger()
    guard = NetworkDenyGuard(ledger=ledger)
    with guard:
        with pytest.raises(UnexpectedExternalCall, match="PRE_DNS"):
            socket.getaddrinfo("api.openai.invalid", 443)
        with socket.socket() as client:
            with pytest.raises(UnexpectedExternalCall, match="PRE_SOCKET"):
                client.connect(("203.0.113.10", 443))
            with pytest.raises(UnexpectedExternalCall, match="PRE_SOCKET"):
                client.connect_ex(("203.0.113.10", 443))
        with socket.socket(socket.AF_UNIX) as unix_client:
            with pytest.raises(UnexpectedExternalCall, match="PRE_SOCKET"):
                unix_client.connect("/tmp/not-registered.sock")
        with socket.socket(socket.AF_INET6) as ipv6_client:
            with pytest.raises(UnexpectedExternalCall, match=r"\[2001:db8::1\]:443"):
                ipv6_client.connect(("2001:db8::1", 443, 0, 0))
    assert [entry.phase for entry in ledger.entries] == [
        "PRE_DNS",
        "PRE_SOCKET",
        "PRE_SOCKET",
        "PRE_SOCKET",
        "PRE_SOCKET",
    ]


def test_exact_literal_loopback_lease_allows_real_http_clients_then_tears_down() -> None:
    _Handler.hits = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    ledger = ExternalCallLedger()
    guard = NetworkDenyGuard(ledger=ledger)
    lease = guard.register_test_service(host, port, "test-http")
    try:
        with guard:
            try:
                url = f"http://{host}:{port}/fixture"
                with socket.socket() as raw_client:
                    assert raw_client.connect_ex((host, port)) == 0
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(url, timeout=2) as response:
                    assert response.status == 200
                with httpx.Client(trust_env=False, timeout=2) as client:
                    assert client.get(url).json() == {"offline": True}
                session = requests.Session()
                session.trust_env = False
                try:
                    assert session.get(url, timeout=2).json() == {"offline": True}
                finally:
                    session.close()
            finally:
                lease.close()
        assert _Handler.hits == 3
        lease.close()
        with guard:
            with pytest.raises(UnexpectedExternalCall, match=f"{host}:{port}"):
                socket.create_connection((host, port), timeout=1)
    finally:
        lease.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_duplicate_leases_reference_count_and_delegate_calls() -> None:
    sentinel = object()
    resolver_calls: list[tuple[object, ...]] = []
    connector_calls: list[tuple[object, ...]] = []

    def resolver(*args: object, **kwargs: object) -> object:
        resolver_calls.append((*args, kwargs))
        return sentinel

    def connector(*args: object, **kwargs: object) -> object:
        connector_calls.append((*args, kwargs))
        return sentinel

    guard = NetworkDenyGuard(
        ledger=ExternalCallLedger(),
        resolver=resolver,
        connector=connector,  # type: ignore[arg-type]
    )
    first = guard.register_test_service("127.0.0.1", 43210, "fake")
    second = guard.register_test_service("127.0.0.1", 43210, "fake")
    with guard:
        assert socket.getaddrinfo("127.0.0.1", 43210) is sentinel
        assert socket.create_connection(("127.0.0.1", 43210)) is sentinel
        first.close()
        assert socket.create_connection(("127.0.0.1", 43210)) is sentinel
        second.close()
        with pytest.raises(UnexpectedExternalCall):
            socket.create_connection(("127.0.0.1", 43210))
    assert len(resolver_calls) == 1
    assert len(connector_calls) == 2


def test_lease_context_manager_unregisters_exact_endpoint() -> None:
    sentinel = object()
    guard = NetworkDenyGuard(
        ledger=ExternalCallLedger(),
        connector=lambda *_args, **_kwargs: sentinel,  # type: ignore[arg-type]
    )
    with guard:
        with guard.register_test_service("127.0.0.1", 43211, "fake") as lease:
            assert lease.boundary == "fake"
            assert socket.create_connection(("127.0.0.1", 43211)) is sentinel
    with guard:
        with pytest.raises(UnexpectedExternalCall):
            socket.create_connection(("127.0.0.1", 43211))


@pytest.mark.parametrize(
    ("host", "port", "boundary"),
    [
        ("localhost", 1234, "fake"),
        ("example.com", 1234, "fake"),
        ("0.0.0.0", 1234, "fake"),
        ("", 1234, "fake"),
        ("127.0.0.1", 0, "fake"),
        ("127.0.0.1", 65536, "fake"),
        ("127.0.0.1", True, "fake"),
        ("127.0.0.1", "not-a-port", "fake"),
        ("127.0.0.1", 1234, ""),
    ],
)
def test_registration_is_exact_and_fail_closed(host: object, port: object, boundary: object) -> None:
    guard = NetworkDenyGuard(ledger=ExternalCallLedger())
    with pytest.raises(ValueError):
        guard.register_test_service(host, port, boundary)  # type: ignore[arg-type]


def test_nested_and_concurrent_guards_fail_and_exit_is_idempotent() -> None:
    first = NetworkDenyGuard(ledger=ExternalCallLedger())
    second = NetworkDenyGuard(ledger=ExternalCallLedger())
    errors: list[BaseException] = []
    with first:
        with pytest.raises(RuntimeError, match="already active"):
            first.__enter__()

        thread = threading.Thread(
            target=lambda: _capture_guard_entry(second, errors),
            daemon=True,
        )
        thread.start()
        thread.join(timeout=2)
    first.__exit__(None, None, None)
    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)


def test_guard_rejects_and_clears_leaked_endpoint_leases() -> None:
    guard = NetworkDenyGuard(ledger=ExternalCallLedger())
    lease = guard.register_test_service("127.0.0.1", 43212, "leaked")
    with pytest.raises(LeakedEndpointLeaseError, match="1 active test-service lease"):
        guard.assert_no_registered_test_services()
    with pytest.raises(LeakedEndpointLeaseError, match="1 test-service lease"):
        with guard:
            pass
    guard.assert_no_registered_test_services()
    lease.close()
    with guard:
        pass


def test_guard_preserves_body_error_and_attaches_leak_diagnostic() -> None:
    guard = NetworkDenyGuard(ledger=ExternalCallLedger())
    with pytest.raises(RuntimeError, match="body-primary") as error:
        with guard:
            guard.register_test_service("127.0.0.1", 43213, "leaked")
            raise RuntimeError("body-primary")
    assert error.value.__notes__ == [
        "network guard closed with 1 test-service lease(s) still active"
    ]


def test_cached_resolution_and_udp_surfaces_are_denied_until_exactly_leased() -> None:
    cached_getaddrinfo = socket.getaddrinfo
    cached_gethostbyname = socket.gethostbyname
    cached_gethostbyname_ex = socket.gethostbyname_ex
    cached_gethostbyaddr = socket.gethostbyaddr
    cached_getnameinfo = socket.getnameinfo
    cached_socket = socket.socket
    receiver = cached_socket(socket.AF_INET, socket.SOCK_DGRAM)
    sender = cached_socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    receiver.settimeout(0.05)
    host, port = receiver.getsockname()
    ledger = ExternalCallLedger()
    guard = NetworkDenyGuard(ledger=ledger)
    try:
        with guard:
            with pytest.raises(UnexpectedExternalCall) as address_info:
                cached_getaddrinfo("203.0.113.20", 443)
            with pytest.raises(UnexpectedExternalCall) as host_lookup:
                cached_gethostbyname("api.openai.invalid")
            with pytest.raises(UnexpectedExternalCall) as extended_host_lookup:
                cached_gethostbyname_ex("api.openai.invalid")
            with pytest.raises(UnexpectedExternalCall) as address_lookup:
                cached_gethostbyaddr("203.0.113.20")
            with pytest.raises(UnexpectedExternalCall) as reverse_lookup:
                cached_getnameinfo(
                    ("203.0.113.20", 443),
                    socket.NI_NUMERICHOST | socket.NI_NUMERICSERV,
                )
            with pytest.raises(UnexpectedExternalCall) as datagram:
                sender.sendto(b"must-not-arrive", (host, port))
            sendmsg_error: UnexpectedExternalCall | None = None
            if hasattr(sender, "sendmsg"):
                with pytest.raises(UnexpectedExternalCall) as sendmsg:
                    sender.sendmsg([b"must-not-arrive"], [], 0, (host, port))
                sendmsg_error = sendmsg.value
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as guarded_sender:
                with pytest.raises(UnexpectedExternalCall) as guarded_datagram:
                    guarded_sender.sendto(b"must-not-arrive", (host, port))
                guarded_sendmsg_error: UnexpectedExternalCall | None = None
                if hasattr(guarded_sender, "sendmsg"):
                    with pytest.raises(UnexpectedExternalCall) as guarded_sendmsg:
                        guarded_sender.sendmsg(
                            [b"must-not-arrive"], [], 0, (host, port)
                        )
                    guarded_sendmsg_error = guarded_sendmsg.value

            _acknowledge(address_info.value, ledger, "connect", "PRE_DNS", "203.0.113.20:443")
            _acknowledge(
                host_lookup.value,
                ledger,
                "resolve",
                "PRE_DNS",
                "api.openai.invalid:0",
            )
            _acknowledge(
                extended_host_lookup.value,
                ledger,
                "resolve",
                "PRE_DNS",
                "api.openai.invalid:0",
            )
            _acknowledge(
                address_lookup.value,
                ledger,
                "resolve",
                "PRE_DNS",
                "203.0.113.20:0",
            )
            _acknowledge(reverse_lookup.value, ledger, "resolve", "PRE_DNS", "203.0.113.20:443")
            _acknowledge(datagram.value, ledger, "send", "PRE_SOCKET", f"{host}:{port}")
            if sendmsg_error is not None:
                _acknowledge(sendmsg_error, ledger, "send", "PRE_SOCKET", f"{host}:{port}")
            _acknowledge(
                guarded_datagram.value,
                ledger,
                "send",
                "PRE_SOCKET",
                f"{host}:{port}",
            )
            if guarded_sendmsg_error is not None:
                _acknowledge(
                    guarded_sendmsg_error,
                    ledger,
                    "send",
                    "PRE_SOCKET",
                    f"{host}:{port}",
                )
            ledger.assert_no_unacknowledged_blocked_calls()

        with pytest.raises(TimeoutError):
            receiver.recvfrom(64)

        with guard:
            with guard.register_test_service(host, port, "udp-fixture"):
                assert cached_getnameinfo(
                    (host, port), socket.NI_NUMERICHOST | socket.NI_NUMERICSERV
                ) == (host, str(port))
                assert cached_gethostbyname(host) == host
                assert cached_gethostbyname_ex(host)[2] == [host]
                # Minimal network namespaces may intentionally omit the NSS
                # files needed for reverse lookup.  Either result proves the
                # audit hook admitted the exact leased literal; a guard denial
                # would raise UnexpectedExternalCall before libc is reached.
                try:
                    assert cached_gethostbyaddr(host)[2] == [host]
                except socket.herror as error:
                    assert error.errno == 2
                assert sender.sendto(b"leased", (host, port)) == len(b"leased")
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as guarded_sender:
                    assert guarded_sender.sendto(b"guarded", (host, port)) == len(b"guarded")
                    if hasattr(guarded_sender, "sendmsg"):
                        assert guarded_sender.sendmsg(
                            [b"guarded-msg"], [], 0, (host, port)
                        ) == len(b"guarded-msg")
            received = {receiver.recvfrom(64)[0], receiver.recvfrom(64)[0]}
            if hasattr(socket.socket, "sendmsg"):
                received.add(receiver.recvfrom(64)[0])
            assert received == {b"leased", b"guarded", b"guarded-msg"}

        # Permanent audit hooks are inert after teardown.
        assert sender.sendto(b"after-exit", (host, port)) == len(b"after-exit")
        assert receiver.recvfrom(64)[0] == b"after-exit"
    finally:
        sender.close()
        receiver.close()


def test_connected_datagram_sendmsg_uses_the_exact_leased_peer() -> None:
    cached_socket = socket.socket
    receiver = cached_socket(socket.AF_INET, socket.SOCK_DGRAM)
    cached_sender = cached_socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    receiver.settimeout(0.2)
    host, port = receiver.getsockname()
    cached_sender.connect((host, port))
    ledger = ExternalCallLedger()
    guard = NetworkDenyGuard(ledger=ledger)
    try:
        if hasattr(cached_sender, "sendmsg"):
            with guard:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as unconnected:
                    with pytest.raises(UnexpectedExternalCall) as missing_peer:
                        unconnected.sendmsg([b"missing-peer"])
                    _acknowledge(
                        missing_peer.value,
                        ledger,
                        "send",
                        "PRE_SOCKET",
                        "unix:0",
                    )
        if hasattr(cached_sender, "sendmsg"):
            with guard:
                with pytest.raises(UnexpectedExternalCall) as blocked:
                    cached_sender.sendmsg([b"blocked-connected"])
                _acknowledge(
                    blocked.value,
                    ledger,
                    "send",
                    "PRE_SOCKET",
                    f"{host}:{port}",
                )
            with pytest.raises(TimeoutError):
                receiver.recvfrom(64)

        with guard:
            with guard.register_test_service(host, port, "connected-udp"):
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as guarded_sender:
                    guarded_sender.connect((host, port))
                    if hasattr(guarded_sender, "sendmsg"):
                        assert guarded_sender.sendmsg([b"guarded-connected"]) == len(
                            b"guarded-connected"
                        )
                    with pytest.raises(TypeError):
                        guarded_sender.sendto(b"missing-address")
                if hasattr(cached_sender, "sendmsg"):
                    assert cached_sender.sendmsg([b"cached-connected"]) == len(
                        b"cached-connected"
                    )
            expected = {b"guarded-connected", b"cached-connected"}
            received = {receiver.recvfrom(64)[0] for _ in expected}
            assert received == expected
        ledger.assert_no_unacknowledged_blocked_calls()
    finally:
        cached_sender.close()
        receiver.close()


def _acknowledge(
    error: UnexpectedExternalCall,
    ledger: ExternalCallLedger,
    operation: str,
    phase: str,
    target: str,
) -> None:
    assert error.call is not None
    ledger.acknowledge_blocked(
        error.call,
        boundary="network",
        operation=operation,
        phase=phase,
        target=target,
    )


def _capture_guard_entry(guard: NetworkDenyGuard, errors: list[BaseException]) -> None:
    try:
        with guard:
            raise AssertionError("concurrent guard unexpectedly entered")
    except BaseException as error:
        errors.append(error)
