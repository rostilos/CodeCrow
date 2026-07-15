from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest


TEST_SUPPORT_ROOT = Path(__file__).resolve().parents[1]
if str(TEST_SUPPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_SUPPORT_ROOT))

from codecrow_test_harness.ledger import ExternalCallLedger
from codecrow_test_harness.network import NetworkDenyGuard, UnexpectedExternalCall


def test_unregistered_outbound_fails_before_dns_and_socket() -> None:
    resolver_calls: list[tuple[object, ...]] = []
    connector_calls: list[tuple[object, ...]] = []

    def resolver_spy(*args: object, **kwargs: object) -> list[object]:
        resolver_calls.append((*args, kwargs))
        raise AssertionError("the real resolver must not be called")

    def connector_spy(*args: object, **kwargs: object) -> socket.socket:
        connector_calls.append((*args, kwargs))
        raise AssertionError("the real connector must not be called")

    ledger = ExternalCallLedger()
    guard = NetworkDenyGuard(
        ledger=ledger,
        resolver=resolver_spy,
        connector=connector_spy,
    )

    with guard:
        with pytest.raises(UnexpectedExternalCall, match="unregistered.invalid:443"):
            socket.create_connection(("unregistered.invalid", 443))

    assert resolver_calls == []
    assert connector_calls == []
    assert [entry.to_dict() for entry in ledger.entries] == [
        {
            "boundary": "network",
            "live": False,
            "operation": "connect",
            "outcome": "blocked",
            "phase": "PRE_DNS",
            "sequence": 1,
            "simulated": False,
            "target": "unregistered.invalid:443",
        }
    ]
