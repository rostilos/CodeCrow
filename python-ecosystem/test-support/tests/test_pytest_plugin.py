from __future__ import annotations

import os
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


TEST_SUPPORT_ROOT = Path(__file__).resolve().parents[1]
if str(TEST_SUPPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_SUPPORT_ROOT))

from codecrow_test_harness.environment import CredentialReintroductionError
from codecrow_test_harness.ledger import (
    ExternalCallLedger,
    LiveExternalCallError,
    UnexpectedBlockedCallError,
)
from codecrow_test_harness.pytest_plugin import (
    OfflineHarnessState,
    _resolve_ledger_path,
    _state,
    external_call_ledger,
    network_deny_guard,
    offline_harness,
    process_deny_guard,
    pytest_addoption,
    pytest_configure,
    pytest_sessionfinish,
    pytest_unconfigure,
)


class _Group:
    def __init__(self) -> None:
        self.options: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def addoption(self, *args: object, **kwargs: object) -> None:
        self.options.append((args, kwargs))


class _Parser:
    def __init__(self) -> None:
        self.group = _Group()

    def getgroup(self, name: str) -> _Group:
        assert name == "codecrow-offline"
        return self.group


class _Config:
    def __init__(self, rootpath: Path, option: str | None = None) -> None:
        self.rootpath = rootpath
        self.option = option
        self.ini_lines: list[tuple[str, str]] = []

    def getoption(self, name: str) -> str | None:
        assert name == "external_call_ledger"
        return self.option

    def addinivalue_line(self, name: str, value: str) -> None:
        self.ini_lines.append((name, value))


def test_plugin_option_path_resolution_and_state_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parser = _Parser()
    pytest_addoption(parser)  # type: ignore[arg-type]
    assert parser.group.options[0][0] == ("--external-call-ledger",)

    configured = _Config(tmp_path, str(tmp_path / "explicit.json"))
    assert _resolve_ledger_path(configured) == (tmp_path / "explicit.json").resolve()  # type: ignore[arg-type]
    configured.option = None
    monkeypatch.setenv("CODECROW_EXTERNAL_CALL_LEDGER", str(tmp_path / "environment.json"))
    assert _resolve_ledger_path(configured) == (tmp_path / "environment.json").resolve()  # type: ignore[arg-type]
    monkeypatch.delenv("CODECROW_EXTERNAL_CALL_LEDGER")
    default = _resolve_ledger_path(configured)  # type: ignore[arg-type]
    assert default.name == f"{tmp_path.name}.json"
    with pytest.raises(RuntimeError, match="not configured"):
        _state(configured)  # type: ignore[arg-type]


def test_plugin_lifecycle_fixtures_live_failure_and_idempotent_close(
    tmp_path: Path,
) -> None:
    config = _Config(tmp_path, str(tmp_path / "ledger.json"))
    pytest_configure(config)  # type: ignore[arg-type]
    pytest_configure(config)  # type: ignore[arg-type]
    state = _state(config)  # type: ignore[arg-type]
    assert config.ini_lines

    request = SimpleNamespace(config=config)
    assert offline_harness.__wrapped__(request) is state
    assert external_call_ledger.__wrapped__(state) is state.ledger
    assert network_deny_guard.__wrapped__(state) is state.network
    assert process_deny_guard.__wrapped__(state) is state.process

    session = SimpleNamespace(config=config, exitstatus=pytest.ExitCode.OK)
    pytest_sessionfinish(session, 0)  # type: ignore[arg-type]
    assert session.exitstatus == pytest.ExitCode.OK
    state.ledger.record(
        boundary="telemetry",
        operation="export",
        outcome="attempted",
        phase="PRE_DNS",
        target="telemetry.invalid:443",
        live=True,
    )
    pytest_sessionfinish(session, 0)  # type: ignore[arg-type]
    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED

    with pytest.raises(LiveExternalCallError, match="1 live external call"):
        pytest_unconfigure(config)  # type: ignore[arg-type]
    assert (tmp_path / "ledger.json").exists()
    state.close()
    pytest_unconfigure(config)  # type: ignore[arg-type]


def test_plugin_configure_rolls_back_when_context_entry_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _Config(tmp_path, str(tmp_path / "never.json"))
    calls: list[str] = []

    class _FailingNetwork:
        def __init__(self, **_: object) -> None:
            return

        def __enter__(self) -> None:
            calls.append("network-enter")
            raise RuntimeError("network setup failed")

    original_exit = __import__(
        "codecrow_test_harness.environment", fromlist=["CredentialScrubber"]
    ).CredentialScrubber.__exit__

    def tracking_exit(self: object, *args: object) -> Any:
        calls.append("credentials-exit")
        return original_exit(self, *args)

    monkeypatch.setattr(
        "codecrow_test_harness.pytest_plugin.NetworkDenyGuard", _FailingNetwork
    )
    monkeypatch.setattr(
        "codecrow_test_harness.environment.CredentialScrubber.__exit__", tracking_exit
    )
    with pytest.raises(RuntimeError, match="network setup failed"):
        pytest_configure(config)  # type: ignore[arg-type]
    assert calls == ["network-enter", "credentials-exit"]


def test_plugin_marks_swallowed_denials_failed_until_exactly_acknowledged(
    tmp_path: Path,
) -> None:
    config = _Config(tmp_path, str(tmp_path / "blocked.json"))
    pytest_configure(config)  # type: ignore[arg-type]
    state = _state(config)  # type: ignore[arg-type]
    blocked = state.ledger.record(
        boundary="network",
        operation="connect",
        outcome="blocked",
        phase="PRE_DNS",
        target="api.openai.invalid:443",
    )
    session = SimpleNamespace(config=config, exitstatus=pytest.ExitCode.OK)
    pytest_sessionfinish(session, 0)  # type: ignore[arg-type]
    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
    state.ledger.acknowledge_blocked(
        blocked,
        boundary="network",
        operation="connect",
        phase="PRE_DNS",
        target="api.openai.invalid:443",
    )
    session.exitstatus = pytest.ExitCode.OK
    pytest_sessionfinish(session, 0)  # type: ignore[arg-type]
    assert session.exitstatus == pytest.ExitCode.OK
    pytest_unconfigure(config)  # type: ignore[arg-type]


def test_close_attempts_every_validation_cleanup_and_export_preserving_primary(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    class _Ledger:
        def assert_zero_live_calls(self) -> None:
            calls.append("zero-live")
            raise LiveExternalCallError("live")

        def assert_no_unacknowledged_blocked_calls(self) -> None:
            calls.append("no-unacknowledged")
            raise UnexpectedBlockedCallError("blocked")

        def write(self, path: Path) -> Path:
            calls.append(f"write:{path.name}")
            return path

    class _Credentials:
        def assert_sanitized(self) -> None:
            calls.append("credentials-assert")
            raise CredentialReintroductionError("credential")

        def __exit__(self, *_: object) -> None:
            calls.append("credentials-exit")
            raise RuntimeError("credentials-exit")

    class _Context:
        def __init__(self, name: str, fail: bool = False) -> None:
            self.name = name
            self.fail = fail

        def __exit__(self, *_: object) -> None:
            calls.append(f"{self.name}-exit")
            if self.fail:
                raise RuntimeError(self.name)

        def assert_no_registered_test_services(self) -> None:
            calls.append(f"{self.name}-lease-assert")
            raise RuntimeError(f"{self.name}-lease")

    state = OfflineHarnessState(
        ledger=_Ledger(),  # type: ignore[arg-type]
        network=_Context("network"),  # type: ignore[arg-type]
        process=_Context("process", fail=True),  # type: ignore[arg-type]
        credentials=_Credentials(),  # type: ignore[arg-type]
        ledger_path=tmp_path / "always.json",
    )
    with pytest.raises(CredentialReintroductionError, match="credential") as error:
        state.close()
    assert calls == [
        "credentials-assert",
        "zero-live",
        "no-unacknowledged",
        "network-lease-assert",
        "process-exit",
        "network-exit",
        "credentials-exit",
        "write:always.json",
    ]
    assert len(error.value.__notes__) == 5
    state.close()
    assert len(calls) == 8


def test_real_pytest_process_fails_swallowed_denial_and_still_writes_ledger(
    tmp_path: Path,
) -> None:
    test_file = tmp_path / "test_swallowed.py"
    test_file.write_text(
        """\
import socket

def test_application_swallows_boundary_error():
    try:
        socket.create_connection((\"api.openai.invalid\", 443))
    except RuntimeError:
        pass
""",
        encoding="utf-8",
    )
    ledger_path = tmp_path / "subprocess-ledger.json"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(TEST_SUPPORT_ROOT)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "codecrow_test_harness.pytest_plugin",
            "--external-call-ledger",
            str(ledger_path),
            "-q",
            str(test_file),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    document = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert document["live_call_count"] == 0
    assert document["calls"][0]["outcome"] == "blocked"
    assert document["calls"][0]["phase"] == "PRE_DNS"
