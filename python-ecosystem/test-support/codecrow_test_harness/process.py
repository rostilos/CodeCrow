from __future__ import annotations

import asyncio
import os
import platform
import shlex
import subprocess
import sys
import threading
from collections.abc import Sequence
from pathlib import Path
from types import TracebackType
from typing import Any, Callable

from .ledger import ExternalCallLedger
from .network import UnexpectedExternalCall


_MISSING = object()


class ProcessDenyGuard:
    _activation_lock = threading.Lock()
    _active_guard: ProcessDenyGuard | None = None

    def __init__(self, *, ledger: ExternalCallLedger) -> None:
        self._ledger = ledger
        self._allowed: set[tuple[str, ...]] = set()
        self._entered = False
        self._previous_popen: Callable[..., subprocess.Popen[Any]] | None = None
        self._previous_system: Callable[[str], int] | None = None
        self._previous_os_popen: Callable[..., Any] | None = None
        self._previous_async_exec: Callable[..., Any] | None = None
        self._previous_async_shell: Callable[..., Any] | None = None
        self._previous_uname_cache: object = _MISSING

    def register_test_process(self, argv: Sequence[str]) -> tuple[str, ...]:
        normalized = _normalize_argv(argv)
        executable = Path(normalized[0])
        if not executable.is_absolute():
            raise ValueError("allowed test process executable must be an absolute path")
        self._allowed.add(normalized)
        return normalized

    def __enter__(self) -> ProcessDenyGuard:
        if self._entered or not self._activation_lock.acquire(blocking=False):
            raise RuntimeError("another process deny guard is already active")
        self._previous_popen = subprocess.Popen
        self._previous_system = os.system
        self._previous_os_popen = os.popen
        self._previous_async_exec = asyncio.create_subprocess_exec
        self._previous_async_shell = asyncio.create_subprocess_shell
        self._previous_uname_cache = getattr(platform, "_uname_cache", _MISSING)
        try:
            subprocess.Popen = self._guarded_popen_class(self._previous_popen)  # type: ignore[assignment,misc]
            os.system = self._deny_shell  # type: ignore[assignment]
            os.popen = self._deny_os_popen  # type: ignore[assignment]
            asyncio.create_subprocess_exec = self._guarded_async_exec  # type: ignore[assignment]
            asyncio.create_subprocess_shell = self._deny_async_shell  # type: ignore[assignment]
            deterministic_uname = platform.uname_result(
                "Linux", "codecrow-offline", "0", "0", "x86_64"
            )
            deterministic_uname.__dict__["processor"] = "x86_64"
            platform._uname_cache = deterministic_uname  # type: ignore[attr-defined]
            type(self)._active_guard = self
            self._entered = True
            return self
        except BaseException:
            try:
                self._restore_runtime()
            finally:
                self._entered = False
                self._activation_lock.release()
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._entered:
            type(self)._active_guard = None
            try:
                self._restore_runtime()
            finally:
                self._entered = False
                self._activation_lock.release()

    def _restore_runtime(self) -> None:
        subprocess.Popen = self._previous_popen  # type: ignore[assignment,misc]
        os.system = self._previous_system  # type: ignore[assignment]
        os.popen = self._previous_os_popen  # type: ignore[assignment]
        asyncio.create_subprocess_exec = self._previous_async_exec  # type: ignore[assignment]
        asyncio.create_subprocess_shell = self._previous_async_shell  # type: ignore[assignment]
        if self._previous_uname_cache is _MISSING:
            if hasattr(platform, "_uname_cache"):
                delattr(platform, "_uname_cache")
        else:
            platform._uname_cache = self._previous_uname_cache  # type: ignore[attr-defined]

    def _check_popen(self, args: object, keywords: dict[str, object]) -> None:
        if keywords.get("shell"):
            self._block(_shell_target(args.decode(errors="replace") if isinstance(args, bytes) else str(args)))
        if isinstance(args, bytes):
            self._block(_shell_target(args.decode(errors="replace")))
        if isinstance(args, str):
            self._block(_shell_target(args))
        argv = _normalize_argv(args)
        self._check(argv)
        executable = keywords.get("executable")
        if executable is not None and str(executable) != argv[0]:
            self._block(Path(str(executable)).name)

    def _guarded_popen_class(
        self, base: type[subprocess.Popen[Any]]
    ) -> type[subprocess.Popen[Any]]:
        guard = self

        class GuardedPopen(base):
            def __init__(self, args: object, *positional: object, **keywords: object) -> None:
                guard._check_popen(args, keywords)
                super().__init__(args, *positional, **keywords)

        return GuardedPopen

    async def _guarded_async_exec(self, *args: str, **keywords: object) -> Any:
        argv = _normalize_argv(args)
        self._check(argv)
        return await self._previous_async_exec(*args, **keywords)  # type: ignore[misc]

    def _deny_shell(self, command: str) -> int:
        self._block(_shell_target(command))

    def _deny_os_popen(self, command: str, *args: object, **kwargs: object) -> Any:
        self._block(_shell_target(command))

    async def _deny_async_shell(self, command: str, **kwargs: object) -> Any:
        self._block(_shell_target(command))

    def _check(self, argv: tuple[str, ...]) -> None:
        if argv not in self._allowed:
            self._block(Path(argv[0]).name)

    def _block(self, target: str) -> None:
        call = self._ledger.record(
            boundary="process",
            operation="spawn",
            outcome="blocked",
            phase="PRE_EXEC",
            target=f"{target}:0",
        )
        raise UnexpectedExternalCall(
            f"unregistered subprocess blocked before exec: {target}", call=call
        )

    def _audit(self, event: str, arguments: tuple[object, ...]) -> None:
        if event in {"os.fork", "os.forkpty"}:
            self._block(event.removeprefix("os."))
        elif event == "subprocess.Popen":
            command = arguments[1]
            if isinstance(command, (str, bytes)):
                text = command.decode(errors="replace") if isinstance(command, bytes) else command
                self._block(_shell_target(text))
            argv = _normalize_argv(command)
            executable = str(arguments[0])
            if executable != argv[0]:
                self._block(Path(executable).name)
            self._check(argv)
        elif event == "os.system":
            command = arguments[0]
            text = command.decode(errors="replace") if isinstance(command, bytes) else str(command)
            self._block(_shell_target(text))
        elif event in {"os.posix_spawn", "os.exec"}:
            argv = _normalize_argv(arguments[1])
            executable = str(arguments[0])
            if executable != argv[0]:
                self._block(Path(executable).name)
            self._check(argv)


def _normalize_argv(args: object) -> tuple[str, ...]:
    if isinstance(args, (str, bytes)):
        raise UnexpectedExternalCall("shell/string subprocess arguments are not allowlistable")
    if not isinstance(args, Sequence) or not args:
        raise ValueError("subprocess argv must be a non-empty sequence")
    normalized = tuple(str(value) for value in args)
    if any(not value for value in normalized):
        raise ValueError("subprocess argv entries must not be empty")
    return normalized


def _shell_target(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        return "<malformed-shell>"
    return Path(parts[0]).name if parts else "<empty-shell>"


def _process_audit_hook(event: str, arguments: tuple[object, ...]) -> None:
    guard = ProcessDenyGuard._active_guard
    if guard is not None:
        guard._audit(event, arguments)


_process_audit_hook.__cantrace__ = True  # type: ignore[attr-defined]
sys.addaudithook(_process_audit_hook)
