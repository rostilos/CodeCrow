from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

import pytest

from .environment import CredentialReintroductionError, CredentialScrubber
from .ledger import ExternalCallLedger, LiveExternalCallError, UnexpectedBlockedCallError
from .network import NetworkDenyGuard
from .process import ProcessDenyGuard


_STATE_ATTRIBUTE = "_codecrow_offline_harness_state"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


@dataclass(slots=True)
class OfflineHarnessState:
    ledger: ExternalCallLedger
    network: NetworkDenyGuard
    process: ProcessDenyGuard
    credentials: CredentialScrubber
    ledger_path: Path
    _closed: bool = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors: list[BaseException] = []
        actions = (
            self.credentials.assert_sanitized,
            self.ledger.assert_zero_live_calls,
            self.ledger.assert_no_unacknowledged_blocked_calls,
            self.network.assert_no_registered_test_services,
            lambda: self.process.__exit__(None, None, None),
            lambda: self.network.__exit__(None, None, None),
            lambda: self.credentials.__exit__(None, None, None),
            lambda: self.ledger.write(self.ledger_path),
        )
        for action in actions:
            try:
                action()
            except BaseException as error:
                errors.append(error)
        if errors:
            primary = errors[0]
            for suppressed in errors[1:]:
                primary.add_note(
                    f"suppressed offline teardown error: "
                    f"{type(suppressed).__name__}: {suppressed}"
                )
            raise primary


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("codecrow-offline")
    group.addoption(
        "--external-call-ledger",
        action="store",
        default=None,
        help="write the canonical offline external-call ledger to this path",
    )


def pytest_configure(config: pytest.Config) -> None:
    if hasattr(config, _STATE_ATTRIBUTE):
        return
    ledger = ExternalCallLedger()
    # Preserve absence so component tests can exercise their documented
    # no-service-secret behavior. Existing test-owned literals remain allowed;
    # any ambient non-test value is replaced before collection starts.
    credentials = CredentialScrubber(populate_service_secrets=False)
    network = NetworkDenyGuard(ledger=ledger)
    process = ProcessDenyGuard(ledger=ledger)
    entered: list[Any] = []
    try:
        credentials.__enter__()
        entered.append(credentials)
        network.__enter__()
        entered.append(network)
        process.__enter__()
        entered.append(process)
    except BaseException:
        for context in reversed(entered):
            context.__exit__(None, None, None)
        raise
    state = OfflineHarnessState(
        ledger=ledger,
        network=network,
        process=process,
        credentials=credentials,
        ledger_path=_resolve_ledger_path(config),
    )
    setattr(config, _STATE_ATTRIBUTE, state)
    config.addinivalue_line(
        "markers",
        "offline_local_service: test registers an exact test-owned loopback endpoint",
    )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    state = _state(session.config)
    try:
        state.credentials.assert_sanitized()
        state.ledger.assert_zero_live_calls()
        state.ledger.assert_no_unacknowledged_blocked_calls()
    except (CredentialReintroductionError, LiveExternalCallError, UnexpectedBlockedCallError):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_unconfigure(config: pytest.Config) -> None:
    state = getattr(config, _STATE_ATTRIBUTE, None)
    if state is None:
        return
    try:
        state.close()
    finally:
        delattr(config, _STATE_ATTRIBUTE)


@pytest.fixture(scope="session")
def offline_harness(request: pytest.FixtureRequest) -> OfflineHarnessState:
    return _state(request.config)


@pytest.fixture(scope="session")
def external_call_ledger(offline_harness: OfflineHarnessState) -> ExternalCallLedger:
    return offline_harness.ledger


@pytest.fixture(scope="session")
def network_deny_guard(offline_harness: OfflineHarnessState) -> NetworkDenyGuard:
    return offline_harness.network


@pytest.fixture(scope="session")
def process_deny_guard(offline_harness: OfflineHarnessState) -> ProcessDenyGuard:
    return offline_harness.process


def _state(config: pytest.Config) -> OfflineHarnessState:
    state = getattr(config, _STATE_ATTRIBUTE, None)
    if state is None:
        raise RuntimeError("CodeCrow offline pytest plugin is not configured")
    return state


def _resolve_ledger_path(config: pytest.Config) -> Path:
    configured = config.getoption("external_call_ledger")
    if configured:
        return Path(configured).resolve()
    from_environment = os.environ.get("CODECROW_EXTERNAL_CALL_LEDGER")
    if from_environment:
        return Path(from_environment).resolve()
    component = Path(str(config.rootpath)).name or "python"
    return (
        _REPOSITORY_ROOT
        / ".llm-handoff-artifacts"
        / "p0-03"
        / "test-ledgers"
        / f"{component}.json"
    )
