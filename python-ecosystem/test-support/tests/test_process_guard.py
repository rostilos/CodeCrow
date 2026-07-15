from __future__ import annotations

import asyncio
import os
import platform
import subprocess
import sys
import threading
from pathlib import Path

import pytest


TEST_SUPPORT_ROOT = Path(__file__).resolve().parents[1]
if str(TEST_SUPPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_SUPPORT_ROOT))

from codecrow_test_harness.ledger import ExternalCallLedger
from codecrow_test_harness.network import UnexpectedExternalCall
from codecrow_test_harness.process import ProcessDenyGuard


def test_unregistered_subprocess_surfaces_fail_before_exec_and_are_ledgered() -> None:
    ledger = ExternalCallLedger()
    with ProcessDenyGuard(ledger=ledger):
        with pytest.raises(UnexpectedExternalCall, match="curl"):
            subprocess.run(["curl", "https://api.openai.invalid"], check=False)
        with pytest.raises(UnexpectedExternalCall, match="curl"):
            subprocess.Popen("curl https://api.openai.invalid", shell=True)
        with pytest.raises(UnexpectedExternalCall, match="curl"):
            subprocess.Popen(b"curl https://api.openai.invalid", shell=True)
        with pytest.raises(UnexpectedExternalCall, match="curl"):
            subprocess.Popen(b"curl https://api.openai.invalid")
        with pytest.raises(UnexpectedExternalCall, match="wget"):
            os.system("wget https://example.invalid")
        with pytest.raises(UnexpectedExternalCall, match="empty-shell"):
            os.popen("")
        with pytest.raises(UnexpectedExternalCall, match="malformed-shell"):
            os.system("'unterminated")
        asyncio.run(_assert_async_processes_blocked())
    assert len(ledger.entries) == 9
    assert all(entry.phase == "PRE_EXEC" for entry in ledger.entries)


async def _assert_async_processes_blocked() -> None:
    with pytest.raises(UnexpectedExternalCall, match="curl"):
        await asyncio.create_subprocess_exec("curl", "https://example.invalid")
    with pytest.raises(UnexpectedExternalCall, match="curl"):
        await asyncio.create_subprocess_shell("curl https://example.invalid")


def test_exact_process_allowlist_runs_and_invalid_entries_fail() -> None:
    executable = str(Path("/usr/bin/true").resolve())
    guard = ProcessDenyGuard(ledger=ExternalCallLedger())
    assert guard.register_test_process([executable]) == (executable,)
    with guard:
        assert subprocess.Popen[bytes]
        assert subprocess.run([executable], check=False).returncode == 0
        process = asyncio.run(_run_allowed_async(executable))
        assert process == 0
        with pytest.raises(UnexpectedExternalCall):
            subprocess.run([executable, "extra"], check=False)
    for argv in ([], [""], ["relative-command"], "string-command"):
        with pytest.raises((ValueError, UnexpectedExternalCall)):
            guard.register_test_process(argv)


async def _run_allowed_async(executable: str) -> int:
    process = await asyncio.create_subprocess_exec(executable)
    return await process.wait()


def test_nested_and_concurrent_process_guards_fail_and_exit_is_idempotent() -> None:
    first = ProcessDenyGuard(ledger=ExternalCallLedger())
    second = ProcessDenyGuard(ledger=ExternalCallLedger())
    errors: list[BaseException] = []
    with first:
        with pytest.raises(RuntimeError, match="already active"):
            first.__enter__()
        thread = threading.Thread(target=lambda: _enter_process_guard(second, errors), daemon=True)
        thread.start()
        thread.join(timeout=2)
    first.__exit__(None, None, None)
    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)


def test_cached_process_aliases_and_exec_audit_cannot_create_marker(tmp_path: Path) -> None:
    cached_popen = subprocess.Popen
    cached_system = os.system
    cached_posix_spawn = getattr(os, "posix_spawn", None)
    cached_spawnv = os.spawnv
    executable = str(Path("/usr/bin/touch").resolve())
    marker = tmp_path / "must-not-exist"
    argv = [executable, str(marker)]
    ledger = ExternalCallLedger()
    guard = ProcessDenyGuard(ledger=ledger)

    with guard:
        errors: list[UnexpectedExternalCall] = []
        with pytest.raises(UnexpectedExternalCall) as popen_error:
            cached_popen(argv)
        errors.append(popen_error.value)
        with pytest.raises(UnexpectedExternalCall) as system_error:
            cached_system(f"{executable} {marker}")
        errors.append(system_error.value)
        if cached_posix_spawn is not None:
            with pytest.raises(UnexpectedExternalCall) as spawn_error:
                cached_posix_spawn(executable, argv, dict(os.environ))
            errors.append(spawn_error.value)
        with pytest.raises(UnexpectedExternalCall) as spawnv_error:
            cached_spawnv(os.P_WAIT, executable, argv)
        errors.append(spawnv_error.value)
        with pytest.raises(UnexpectedExternalCall) as fork_error:
            os.fork()
        errors.append(fork_error.value)
        if hasattr(os, "forkpty"):
            with pytest.raises(UnexpectedExternalCall) as forkpty_error:
                os.forkpty()
            errors.append(forkpty_error.value)
        with pytest.raises(UnexpectedExternalCall) as exec_error:
            sys.audit("os.exec", executable, argv, None)
        errors.append(exec_error.value)
        for command in (f"{executable} {marker}", os.fsencode(f"{executable} {marker}")):
            with pytest.raises(UnexpectedExternalCall) as popen_audit_error:
                sys.audit("subprocess.Popen", executable, command, None, None)
            errors.append(popen_audit_error.value)
        with pytest.raises(UnexpectedExternalCall) as popen_override_error:
            sys.audit("subprocess.Popen", "/usr/bin/false", argv, None, None)
        errors.append(popen_override_error.value)
        with pytest.raises(UnexpectedExternalCall) as exec_override_error:
            sys.audit("os.exec", "/usr/bin/false", argv, None)
        errors.append(exec_override_error.value)

        for error in errors:
            assert error.call is not None
            ledger.acknowledge_blocked(
                error.call,
                boundary="process",
                operation="spawn",
                phase="PRE_EXEC",
                target=error.call.target,
            )
        ledger.assert_no_unacknowledged_blocked_calls()

    assert not marker.exists()

    guard.register_test_process(argv)
    with guard:
        process = cached_popen(argv)
        assert process.wait(timeout=2) == 0
    assert marker.exists()


def test_shell_and_executable_override_cannot_reuse_allowed_argv() -> None:
    executable = str(Path("/usr/bin/true").resolve())
    guard = ProcessDenyGuard(ledger=ExternalCallLedger())
    guard.register_test_process([executable])
    with guard:
        with pytest.raises(UnexpectedExternalCall, match="true"):
            subprocess.Popen(executable)
        with pytest.raises(UnexpectedExternalCall, match="false"):
            subprocess.Popen([executable], executable="/usr/bin/false")


def test_platform_metadata_is_deterministic_and_never_spawns_uname() -> None:
    previous = getattr(platform, "_uname_cache", None)
    platform._uname_cache = None  # type: ignore[attr-defined]
    ledger = ExternalCallLedger()
    try:
        with ProcessDenyGuard(ledger=ledger):
            assert platform.processor() == "x86_64"
            assert platform.node() == "codecrow-offline"
            assert platform.platform().startswith("Linux-0-x86_64")
            assert subprocess.Popen[bytes]
        assert ledger.entries == ()
        assert platform._uname_cache is None  # type: ignore[attr-defined]
    finally:
        platform._uname_cache = previous  # type: ignore[attr-defined]


def test_process_guard_entry_is_transactional_on_setup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous_popen = subprocess.Popen
    previous_system = os.system
    previous_os_popen = os.popen
    previous_async_exec = asyncio.create_subprocess_exec
    previous_async_shell = asyncio.create_subprocess_shell
    previous_uname = getattr(platform, "_uname_cache", None)
    original_constructor = platform.uname_result

    def fail_uname(*_: object) -> object:
        raise RuntimeError("deterministic platform setup failed")

    monkeypatch.setattr(platform, "uname_result", fail_uname)
    with pytest.raises(RuntimeError, match="platform setup failed"):
        ProcessDenyGuard(ledger=ExternalCallLedger()).__enter__()
    assert subprocess.Popen is previous_popen
    assert os.system is previous_system
    assert os.popen is previous_os_popen
    assert asyncio.create_subprocess_exec is previous_async_exec
    assert asyncio.create_subprocess_shell is previous_async_shell
    assert getattr(platform, "_uname_cache", None) is previous_uname

    monkeypatch.setattr(platform, "uname_result", original_constructor)
    with ProcessDenyGuard(ledger=ExternalCallLedger()):
        assert subprocess.Popen[bytes]


def test_process_guard_restores_absent_platform_cache() -> None:
    previous = platform._uname_cache  # type: ignore[attr-defined]
    delattr(platform, "_uname_cache")
    try:
        with ProcessDenyGuard(ledger=ExternalCallLedger()):
            assert platform.processor() == "x86_64"
        assert not hasattr(platform, "_uname_cache")
    finally:
        platform._uname_cache = previous  # type: ignore[attr-defined]


def test_process_guard_tolerates_platform_cache_removed_during_teardown() -> None:
    previous = platform._uname_cache  # type: ignore[attr-defined]
    delattr(platform, "_uname_cache")
    try:
        with ProcessDenyGuard(ledger=ExternalCallLedger()):
            delattr(platform, "_uname_cache")
        assert not hasattr(platform, "_uname_cache")
    finally:
        platform._uname_cache = previous  # type: ignore[attr-defined]


def _enter_process_guard(guard: ProcessDenyGuard, errors: list[BaseException]) -> None:
    try:
        with guard:
            raise AssertionError("concurrent process guard unexpectedly entered")
    except BaseException as error:
        errors.append(error)
