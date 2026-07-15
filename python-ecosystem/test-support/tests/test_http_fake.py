from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest


TEST_SUPPORT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(TEST_SUPPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_SUPPORT_ROOT))

from codecrow_test_harness.http_fake import ProtocolFixtureError, ProtocolFixtureServer
from codecrow_test_harness.ledger import ExternalCallLedger
from codecrow_test_harness.network import NetworkDenyGuard, UnexpectedExternalCall


FIXTURES = REPOSITORY_ROOT / "tools" / "offline-harness" / "fixtures" / "protocol"


def test_shared_github_fixture_runs_through_exact_leased_http_port() -> None:
    ledger = ExternalCallLedger()
    guard = NetworkDenyGuard(ledger=ledger)
    server = ProtocolFixtureServer(
        FIXTURES / "github-v1.json", ledger=ledger, network_guard=guard
    )
    with pytest.raises(RuntimeError, match="not started"):
        _ = server.base_url
    assert server.start() is server
    assert server.start() is server
    base_url = server.base_url
    try:
        with guard, httpx.Client(trust_env=False, timeout=2) as client:
            first = client.get(
                f"{base_url}/repos/neutral/example/pulls/7/files?page=1"
            )
            second = client.get(
                f"{base_url}/repos/neutral/example/pulls/7/files?page=2"
            )
            assert first.status_code == 200
            assert first.json()[0]["filename"] == "src/example.py"
            assert 'rel="next"' in first.headers["link"]
            assert second.status_code == 429
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                assert client.request(method, f"{base_url}/unregistered").status_code == 599
            server.stop()
        assert [call.method for call in server.calls] == [
            "GET",
            "GET",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        ]
        assert [entry.outcome for entry in ledger.entries] == [
            "status_200",
            "status_429",
            "status_599",
            "status_599",
            "status_599",
            "status_599",
        ]
        assert all(entry.target == base_url for entry in ledger.entries)
    finally:
        server.stop()
        server.stop()
    host, port = base_url.removeprefix("http://").split(":")
    with guard:
        with pytest.raises(UnexpectedExternalCall):
            __import__("socket").create_connection((host, int(port)))


def test_context_manager_and_text_response_use_shared_bitbucket_fixture() -> None:
    ledger = ExternalCallLedger()
    guard = NetworkDenyGuard(ledger=ledger)
    with guard:
        with ProtocolFixtureServer(
            FIXTURES / "bitbucket-v1.json", ledger=ledger, network_guard=guard
        ) as server, httpx.Client(trust_env=False, timeout=2) as client:
            response = client.get(
                f"{server.base_url}/2.0/repositories/neutral/example/pullrequests/7/diff"
            )
            assert response.status_code == 200
            assert response.text.startswith("diff --git")


def test_fixture_can_bind_and_register_while_global_network_guard_is_active() -> None:
    ledger = ExternalCallLedger()
    guard = NetworkDenyGuard(ledger=ledger)
    with guard:
        with ProtocolFixtureServer(
            FIXTURES / "bitbucket-v1.json", ledger=ledger, network_guard=guard
        ) as server:
            with httpx.Client(trust_env=False, timeout=2) as client:
                response = client.get(
                    f"{server.base_url}/2.0/repositories/neutral/example/pullrequests/7/diff"
                )
                assert response.status_code == 200


@pytest.mark.parametrize(
    "document,error",
    [
        ({"schema_version": "2", "provider": "x", "routes": []}, "schema"),
        ({"schema_version": "1.0", "provider": "", "routes": []}, "provider"),
        ({"schema_version": "1.0", "provider": "x", "routes": {}}, "routes"),
        ({"schema_version": "1.0", "provider": "x", "routes": [1]}, "object"),
        (
            {"schema_version": "1.0", "provider": "x", "routes": [{"method": ""}]},
            "method/path",
        ),
        (
            {
                "schema_version": "1.0",
                "provider": "x",
                "routes": [{"method": "GET", "path": "relative", "response": {}}],
            },
            "response/path",
        ),
        (
            {
                "schema_version": "1.0",
                "provider": "x",
                "routes": [
                    {"method": "GET", "path": "/x", "response": {"status": 99}}
                ],
            },
            "status",
        ),
        (
            {
                "schema_version": "1.0",
                "provider": "x",
                "routes": [
                    {
                        "method": "GET",
                        "path": "/x",
                        "response": {"status": 200, "headers": {"x": 1}},
                    }
                ],
            },
            "headers",
        ),
        (
            {
                "schema_version": "1.0",
                "provider": "x",
                "routes": [
                    {"method": "GET", "path": "/x", "response": {"status": 200}},
                    {"method": "get", "path": "/x", "response": {"status": 201}},
                ],
            },
            "duplicate",
        ),
    ],
)
def test_invalid_protocol_fixture_fails_closed(
    tmp_path: Path, document: object, error: str
) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ProtocolFixtureError, match=error):
        ProtocolFixtureServer(
            fixture,
            ledger=ExternalCallLedger(),
            network_guard=NetworkDenyGuard(ledger=ExternalCallLedger()),
        )


def test_missing_and_malformed_protocol_fixture_fail_closed(tmp_path: Path) -> None:
    for fixture in (tmp_path / "missing.json", tmp_path / "malformed.json"):
        if fixture.name == "malformed.json":
            fixture.write_text("{", encoding="utf-8")
        with pytest.raises(ProtocolFixtureError, match="cannot load"):
            ProtocolFixtureServer(
                fixture,
                ledger=ExternalCallLedger(),
                network_guard=NetworkDenyGuard(ledger=ExternalCallLedger()),
            )


def test_stop_attempts_every_cleanup_and_preserves_primary_error() -> None:
    calls: list[str] = []

    class _Server:
        server_address = ("127.0.0.1", 1)

        def shutdown(self) -> None:
            calls.append("shutdown")
            raise RuntimeError("shutdown-primary")

        def server_close(self) -> None:
            calls.append("server-close")
            raise RuntimeError("server-close")

    class _Thread:
        def join(self, *, timeout: int) -> None:
            assert timeout == 2
            calls.append("join")
            raise RuntimeError("join")

        def is_alive(self) -> bool:
            raise AssertionError("is_alive is unreachable after join raises")

    class _Lease:
        def close(self) -> None:
            calls.append("lease-close")
            raise RuntimeError("lease-close")

    server = ProtocolFixtureServer(
        FIXTURES / "github-v1.json",
        ledger=ExternalCallLedger(),
        network_guard=NetworkDenyGuard(ledger=ExternalCallLedger()),
    )
    server._server = _Server()  # type: ignore[assignment]
    server._thread = _Thread()  # type: ignore[assignment]
    server._lease = _Lease()  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="shutdown-primary") as error:
        server.stop()
    assert calls == ["shutdown", "server-close", "join", "lease-close"]
    assert len(error.value.__notes__) == 3
    assert server._server is None
    assert server._thread is None
    assert server._lease is None
    server.stop()


def test_stop_rejects_missing_or_still_live_thread_and_missing_lease() -> None:
    calls: list[str] = []

    class _Server:
        server_address = ("127.0.0.1", 1)

        def shutdown(self) -> None:
            calls.append("shutdown")

        def server_close(self) -> None:
            calls.append("server-close")

    class _LiveThread:
        def join(self, *, timeout: int) -> None:
            assert timeout == 2
            calls.append("join")

        def is_alive(self) -> bool:
            return True

    server = ProtocolFixtureServer(
        FIXTURES / "github-v1.json",
        ledger=ExternalCallLedger(),
        network_guard=NetworkDenyGuard(ledger=ExternalCallLedger()),
    )
    server._server = _Server()  # type: ignore[assignment]
    server._thread = _LiveThread()  # type: ignore[assignment]
    server._lease = None
    with pytest.raises(RuntimeError, match="thread did not stop") as error:
        server.stop()
    assert len(error.value.__notes__) == 1

    server._server = _Server()  # type: ignore[assignment]
    server._thread = None
    server._lease = None
    with pytest.raises(RuntimeError, match="thread is missing") as error:
        server.stop()
    assert len(error.value.__notes__) == 1


def test_context_exit_preserves_body_error_over_cleanup_error() -> None:
    class _Server:
        server_address = ("127.0.0.1", 1)

        def shutdown(self) -> None:
            raise RuntimeError("cleanup")

        def server_close(self) -> None:
            return

    class _Thread:
        def join(self, *, timeout: int) -> None:
            assert timeout == 2

        def is_alive(self) -> bool:
            return False

    class _Lease:
        def close(self) -> None:
            return

    fixture = ProtocolFixtureServer(
        FIXTURES / "github-v1.json",
        ledger=ExternalCallLedger(),
        network_guard=NetworkDenyGuard(ledger=ExternalCallLedger()),
    )
    fixture._server = _Server()  # type: ignore[assignment]
    fixture._thread = _Thread()  # type: ignore[assignment]
    fixture._lease = _Lease()  # type: ignore[assignment]
    primary = RuntimeError("body-primary")
    fixture.__exit__(RuntimeError, primary, None)
    assert primary.__notes__ == [
        "suppressed protocol fixture context cleanup error: RuntimeError: cleanup"
    ]

    fixture._server = _Server()  # type: ignore[assignment]
    fixture._thread = _Thread()  # type: ignore[assignment]
    fixture._lease = _Lease()  # type: ignore[assignment]
    with pytest.raises(RuntimeError, match="cleanup"):
        fixture.__exit__(None, None, None)
