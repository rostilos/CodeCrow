from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


TEST_SUPPORT_ROOT = Path(__file__).resolve().parents[1]
if str(TEST_SUPPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_SUPPORT_ROOT))

from codecrow_test_harness.ledger import (
    ExternalCallLedger,
    LiveExternalCallError,
    UnexpectedBlockedCallError,
)


def _record(ledger: ExternalCallLedger, target: str, **flags: bool) -> None:
    ledger.record(
        boundary="llm",
        operation="invoke",
        outcome="response",
        phase="SIMULATED",
        target=target,
        **flags,
    )


def test_ledger_redacts_targets_writes_atomically_and_asserts_live_calls(tmp_path: Path) -> None:
    ledger = ExternalCallLedger()
    _record(ledger, "https://user:secret@example.com:8443/private?q=prompt", simulated=True)
    _record(ledger, "[::1]:6333", simulated=True)
    _record(ledger, "https://example.com:invalid/path", live=True)
    _record(ledger, "https:///missing-host", simulated=True)
    _record(ledger, "customer source text", simulated=True)

    assert [entry.target for entry in ledger.entries] == [
        "https://example.com:8443",
        "[::1]:6333",
        "<redacted-target>",
        "<redacted-target>",
        "<redacted-target>",
    ]
    assert ledger.live_call_count == 1
    assert ledger.simulated_call_count == 4
    with pytest.raises(LiveExternalCallError, match="1 live external call"):
        ledger.assert_zero_live_calls()

    path = ledger.write(tmp_path / "nested" / "ledger.json")
    document = json.loads(path.read_text(encoding="utf-8"))
    schema = json.loads(
        (
            Path(__file__).resolve().parents[3]
            / "tools/offline-harness/schema/external-call-ledger-v1.schema.json"
        ).read_text()
    )
    Draft202012Validator(schema).validate(document)
    assert document["schema_version"] == "1.0"
    assert document["live_call_count"] == 1
    assert document["simulated_call_count"] == 4
    assert [entry["sequence"] for entry in document["calls"]] == [1, 2, 3, 4, 5]
    assert not path.with_name(f".{path.name}.tmp").exists()


def test_ledger_rejects_invalid_records_and_accepts_zero_live() -> None:
    ledger = ExternalCallLedger()
    for field in ("boundary", "operation", "outcome", "phase", "target"):
        values = {
            "boundary": "network",
            "operation": "connect",
            "outcome": "blocked",
            "phase": "PRE_DNS",
            "target": "example.invalid:443",
        }
        values[field] = ""
        with pytest.raises(ValueError, match="non-empty"):
            ledger.record(**values)
    with pytest.raises(ValueError, match="both live and simulated"):
        _record(ledger, "example.invalid:443", live=True, simulated=True)
    for field, value in (
        ("boundary", "Bad Boundary"),
        ("operation", "bad operation"),
        ("outcome", "?"),
        ("phase", "AFTER_NETWORK"),
    ):
        values = {
            "boundary": "network",
            "operation": "connect",
            "outcome": "blocked",
            "phase": "PRE_DNS",
            "target": "example.invalid:443",
        }
        values[field] = value
        with pytest.raises(ValueError, match="external-call"):
            ledger.record(**values)
    with pytest.raises(ValueError, match="flags must be booleans"):
        ledger.record(
            boundary="network",
            operation="connect",
            outcome="blocked",
            phase="PRE_DNS",
            target="example.invalid:443",
            live=1,  # type: ignore[arg-type]
        )
    canonical = ledger.record(
        boundary="Network",
        operation="Connect.HTTP",
        outcome="Blocked",
        phase="pre_dns",
        target="EXAMPLE.INVALID:443",
    )
    assert (canonical.boundary, canonical.operation, canonical.outcome, canonical.phase) == (
        "network",
        "connect.http",
        "blocked",
        "PRE_DNS",
    )
    assert canonical.target == "example.invalid:443"
    ledger.assert_zero_live_calls()


def test_ledger_sequences_are_thread_safe() -> None:
    ledger = ExternalCallLedger()
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(
            executor.map(
                lambda ordinal: _record(ledger, f"fake-{ordinal}.invalid:443", simulated=True),
                range(64),
            )
        )
    assert [entry.sequence for entry in ledger.entries] == list(range(1, 65))


def test_blocked_calls_require_exact_acknowledgement_from_the_owning_ledger() -> None:
    ledger = ExternalCallLedger()
    blocked = ledger.record(
        boundary="network",
        operation="connect",
        outcome="blocked",
        phase="PRE_DNS",
        target="api.openai.invalid:443",
    )
    with pytest.raises(UnexpectedBlockedCallError, match=r"sequence\(s\): 1"):
        ledger.assert_no_unacknowledged_blocked_calls()
    with pytest.raises(ValueError, match="does not match"):
        ledger.acknowledge_blocked(
            blocked,
            boundary="network",
            operation="connect",
            phase="PRE_DNS",
            target="different.invalid:443",
        )

    other = ExternalCallLedger()
    forged_equal = other.record(
        boundary="network",
        operation="connect",
        outcome="blocked",
        phase="PRE_DNS",
        target="api.openai.invalid:443",
    )
    assert forged_equal == blocked and forged_equal is not blocked
    with pytest.raises(ValueError, match="recorded blocked"):
        ledger.acknowledge_blocked(
            forged_equal,
            boundary="network",
            operation="connect",
            phase="PRE_DNS",
            target="api.openai.invalid:443",
        )

    ledger.acknowledge_blocked(
        blocked,
        boundary="NETWORK",
        operation="CONNECT",
        phase="pre_dns",
        target="API.OPENAI.INVALID:443",
    )
    ledger.acknowledge_blocked(
        blocked,
        boundary="network",
        operation="connect",
        phase="PRE_DNS",
        target="api.openai.invalid:443",
    )
    ledger.assert_no_unacknowledged_blocked_calls()

    response = ledger.record(
        boundary="llm",
        operation="invoke",
        outcome="response",
        phase="SIMULATED",
        target="fake-llm:1",
        simulated=True,
    )
    with pytest.raises(ValueError, match="recorded blocked"):
        ledger.acknowledge_blocked(
            response,
            boundary="llm",
            operation="invoke",
            phase="SIMULATED",
            target="fake-llm:1",
        )
