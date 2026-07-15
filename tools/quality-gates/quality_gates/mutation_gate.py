"""Deterministic deliberate-mutant validation and result classification."""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import shutil
import stat
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Mapping

from .changed_coverage import GateInputError


REQUIRED_CATEGORIES = frozenset(
    {"state", "identity_evidence", "budget", "fencing", "reconciliation"}
)
_MUTATION_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_EXPECTED_TEST = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]\r\n]+\])?$")
MAX_MUTATION_LOG_BYTES = 1_048_576
_LOG_TRUNCATION_MARKER = b"\n[codecrow mutation output truncated]\n"
_PROCESS_TERMINATION_GRACE_SECONDS = 1.0
_OUTPUT_READER_JOIN_SECONDS = 2.0


def _safe_relative(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise GateInputError(f"{field} must be a safe repository-relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise GateInputError(f"{field} must be a safe repository-relative path")
    return value


def validate_mutation_profile(profile: Mapping[str, Any]) -> None:
    """Validate the narrow, dependency-free mutation profile contract."""

    mutations = profile.get("mutations")
    if profile.get("schemaVersion") != 1 or not isinstance(mutations, list) or not mutations:
        raise GateInputError("mutation profile must use schema version 1 and contain mutations")
    categories: set[str] = set()
    identifiers: set[str] = set()
    for mutation in mutations:
        if not isinstance(mutation, Mapping):
            raise GateInputError("mutation entry must be an object")
        identifier = mutation.get("id")
        category = mutation.get("category")
        if (
            not isinstance(identifier, str)
            or not _MUTATION_ID.fullmatch(identifier)
            or identifier in identifiers
        ):
            raise GateInputError("mutation id must be unique and path-safe")
        identifiers.add(identifier)
        if category not in REQUIRED_CATEGORIES:
            raise GateInputError(f"unsupported mutation category: {category}")
        categories.add(category)
        if mutation.get("language") not in {"java", "python"}:
            raise GateInputError("mutation language must be java or python")
        _safe_relative(mutation.get("sourcePath"), "mutation sourcePath")
        _safe_relative(mutation.get("workingDirectory"), "mutation workingDirectory")
        snapshot_paths = mutation.get("snapshotPaths")
        if not isinstance(snapshot_paths, list) or not snapshot_paths:
            raise GateInputError("mutation snapshotPaths must be non-empty")
        normalized_snapshots: list[PurePosixPath] = []
        for snapshot_path in snapshot_paths:
            safe_snapshot = _safe_relative(snapshot_path, "mutation snapshot path")
            if safe_snapshot == ".":
                raise GateInputError("mutation snapshot path cannot be the repository root")
            normalized_snapshots.append(PurePosixPath(safe_snapshot))
        if len(normalized_snapshots) != len(set(normalized_snapshots)):
            raise GateInputError("mutation snapshot paths must be unique")
        for index, snapshot in enumerate(normalized_snapshots):
            if any(
                snapshot != other and snapshot.is_relative_to(other)
                for other in normalized_snapshots[index + 1 :] + normalized_snapshots[:index]
            ):
                raise GateInputError("mutation snapshot paths cannot overlap")
        digest = mutation.get("preimageSha256")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise GateInputError("mutation preimageSha256 must be lowercase SHA-256")
        before = mutation.get("before")
        after = mutation.get("after")
        if not isinstance(before, str) or not before or not isinstance(after, str) or before == after:
            raise GateInputError("mutation before/after replacement is invalid")
        argv = mutation.get("argv")
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(argument, str) or not argument for argument in argv)
        ):
            raise GateInputError("mutation argv must be a non-empty string array")
        if argv[:3] != ["{python}", "-m", "pytest"]:
            raise GateInputError("mutation command must use locked Python pytest")
        if argv.count("--junitxml={receipt}") != 1:
            raise GateInputError("mutation command must write one JUnit receipt")
        expected_test = mutation.get("expectedTest")
        timeout = mutation.get("timeoutSeconds")
        if not isinstance(expected_test, str) or not _EXPECTED_TEST.fullmatch(expected_test):
            raise GateInputError("mutation expectedTest must be one safe test name")
        if not any(argument.endswith(f"::{expected_test}") for argument in argv):
            raise GateInputError("mutation command must select expectedTest")
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0 or timeout > 600:
            raise GateInputError("mutation timeoutSeconds must be between 1 and 600")
    missing = REQUIRED_CATEGORIES - categories
    if missing:
        raise GateInputError(f"mutation profile lacks required categories: {', '.join(sorted(missing))}")


def apply_exact_mutation(source: Path, *, before: str, after: str) -> dict[str, str]:
    """Replace exactly one UTF-8 preimage and return immutable content identities."""

    if not source.is_file() or source.is_symlink():
        raise GateInputError("mutation source must be a regular file")
    try:
        original = source.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise GateInputError("mutation source must be UTF-8") from error
    if original.count(before) != 1:
        raise GateInputError("mutation preimage must occur exactly once")
    mutated = original.replace(before, after, 1)
    source.write_text(mutated, encoding="utf-8")
    return {
        "beforeSha256": hashlib.sha256(original.encode("utf-8")).hexdigest(),
        "afterSha256": hashlib.sha256(mutated.encode("utf-8")).hexdigest(),
    }


def _junit_receipt_summary(
    receipt: Path, expected_test: str
) -> tuple[dict[str, int], int, int] | None:
    if not receipt.is_file() or receipt.is_symlink():
        return None
    try:
        root = ET.parse(receipt).getroot()
    except (ET.ParseError, OSError):
        return None
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    if not suites:
        return None
    counters = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    matching_tests = 0
    matching_failures = 0
    for suite in suites:
        try:
            for name in counters:
                value = int(suite.attrib.get(name, "0"))
                if value < 0:
                    return None
                counters[name] += value
        except (TypeError, ValueError):
            return None
        for case in suite.findall(".//testcase"):
            if case.attrib.get("name") == expected_test:
                matching_tests += 1
                if case.find("failure") is not None:
                    matching_failures += 1
    return counters, matching_tests, matching_failures


def classify_mutation_result(exit_code: int, receipt: Path, expected_test: str) -> str:
    """Only an expected assertion failure kills a mutant."""

    if exit_code == 0:
        return "SURVIVED"
    summary = _junit_receipt_summary(receipt, expected_test)
    if summary is None:
        return "INVALID"
    counters, _matching_tests, matching_failures = summary
    expected_counters = {"tests": 1, "failures": 1, "errors": 0, "skipped": 0}
    return "KILLED" if counters == expected_counters and matching_failures == 1 else "INVALID"


def _control_selector_passed(exit_code: int | None, receipt: Path, expected_test: str) -> bool:
    if exit_code != 0:
        return False
    summary = _junit_receipt_summary(receipt, expected_test)
    if summary is None:
        return False
    counters, matching_tests, matching_failures = summary
    return (
        counters == {"tests": 1, "failures": 0, "errors": 0, "skipped": 0}
        and matching_tests == 1
        and matching_failures == 0
    )


def _copy_snapshot(repository_root: Path, workspace: Path, paths: list[str]) -> None:
    for relative in paths:
        source = repository_root / relative
        destination = workspace / relative
        if source.is_symlink() or not source.exists():
            raise GateInputError(f"mutation snapshot path is missing or a symlink: {relative}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            if destination.exists():
                raise GateInputError(f"overlapping mutation snapshot path: {relative}")
            for nested in source.rglob("*"):
                if nested.is_symlink():
                    raise GateInputError(f"mutation snapshot contains a symlink: {relative}")
            shutil.copytree(source, destination)
        elif source.is_file():
            if destination.exists():
                raise GateInputError(f"overlapping mutation snapshot path: {relative}")
            shutil.copy2(source, destination)
        else:
            raise GateInputError(f"mutation snapshot path is not a regular file/directory: {relative}")


def _render_argv(
    argv: list[str], *, python_runtime: Path, workspace: Path, receipt: Path
) -> list[str]:
    replacements = {
        "{python}": str(python_runtime),
        "{workspace}": str(workspace),
        "{receipt}": str(receipt),
    }
    rendered: list[str] = []
    for argument in argv:
        for placeholder, value in replacements.items():
            argument = argument.replace(placeholder, value)
        if "{" in argument or "}" in argument:
            raise GateInputError(f"unsupported mutation command placeholder: {argument}")
        rendered.append(argument)
    return rendered


def _runtime_sha256(runtime: Path) -> str:
    """Hash one executable regular file without following a final symlink."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(runtime, flags)
    except OSError as error:
        raise GateInputError("mutation run requires the active regular Python runtime") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or not os.access(runtime, os.X_OK):
            raise GateInputError("mutation run requires the active regular Python runtime")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _verify_runtime_identity(runtime: Path, expected_sha256: str) -> None:
    if _runtime_sha256(runtime) != expected_sha256:
        raise GateInputError("locked Python runtime identity changed during mutation run")


def _mutation_environment() -> dict[str, str]:
    """Build the credential-free host environment needed by the pinned wrapper."""

    return {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": "/tmp",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
    }


def _stream_bounded_output(
    stream: BinaryIO,
    log_handle: BinaryIO,
    errors: list[BaseException],
) -> None:
    """Drain subprocess output without retaining or writing more than the fixed cap."""

    payload_limit = MAX_MUTATION_LOG_BYTES - len(_LOG_TRUNCATION_MARKER)
    written = 0
    truncated = False
    try:
        while chunk := stream.read(64 * 1024):
            available = max(0, payload_limit - written)
            if available:
                retained = chunk[:available]
                log_handle.write(retained)
                written += len(retained)
            if len(chunk) > available:
                truncated = True
        if truncated:
            log_handle.write(_LOG_TRUNCATION_MARKER)
        log_handle.flush()
    except BaseException as error:  # pragma: no branch - propagated on the owning thread
        errors.append(error)


def _signal_process_group(process: subprocess.Popen[bytes], requested_signal: int) -> None:
    try:
        os.killpg(process.pid, requested_signal)
    except ProcessLookupError:
        pass


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    """Terminate the complete isolated process group, escalating deterministically."""

    _signal_process_group(process, signal.SIGTERM)
    try:
        process.wait(timeout=_PROCESS_TERMINATION_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass
    _signal_process_group(process, signal.SIGKILL)
    try:
        process.wait(timeout=_PROCESS_TERMINATION_GRACE_SECONDS)
    except subprocess.TimeoutExpired as error:
        raise GateInputError("mutation process group did not terminate") from error


def _run_bounded_command(
    command: list[str],
    *,
    working_directory: Path,
    environment: Mapping[str, str],
    timeout_seconds: int,
    log: Path,
) -> tuple[int | None, bool]:
    """Run in a new session while streaming output into one bounded evidence log."""

    try:
        log_handle = log.open("xb")
    except OSError as error:
        raise GateInputError("mutation log must be a new regular file") from error
    with log_handle:
        try:
            process = subprocess.Popen(
                command,
                cwd=working_directory,
                env=dict(environment),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as error:
            raise GateInputError("mutation command could not start") from error
        if process.stdout is None:
            _terminate_process_group(process)
            raise GateInputError("mutation command output pipe is unavailable")
        reader_errors: list[BaseException] = []
        reader = threading.Thread(
            target=_stream_bounded_output,
            args=(process.stdout, log_handle, reader_errors),
            name="codecrow-mutation-output",
            daemon=True,
        )
        reader.start()
        timed_out = False
        termination_error: GateInputError | None = None
        try:
            try:
                exit_code: int | None = process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                exit_code = None
        finally:
            try:
                _terminate_process_group(process)
            except GateInputError as error:
                termination_error = error
        reader.join(timeout=_OUTPUT_READER_JOIN_SECONDS)
        if reader.is_alive():
            process.stdout.close()
            reader.join(timeout=_OUTPUT_READER_JOIN_SECONDS)
        if reader.is_alive():
            raise GateInputError("mutation output reader did not terminate")
        if reader_errors:
            raise GateInputError("mutation output could not be recorded") from reader_errors[0]
        if termination_error is not None:
            raise termination_error
        return exit_code, timed_out


def _validate_result_entry(directory_descriptor: int, name: str) -> None:
    try:
        metadata = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as error:
        raise GateInputError("mutation result target could not be inspected") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise GateInputError("mutation result target must be a regular file or absent")


def _open_artifact_directory(artifacts: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(artifacts, flags)
    except OSError as error:
        raise GateInputError("mutation artifact root must be a real directory") from error


def _validate_result_target(artifacts: Path) -> None:
    directory_descriptor = _open_artifact_directory(artifacts)
    try:
        _validate_result_entry(directory_descriptor, "mutation-results.json")
    finally:
        os.close(directory_descriptor)


def _atomic_write_result(artifacts: Path, result: Mapping[str, Any]) -> None:
    """Atomically replace the contained result without following filesystem links."""

    payload = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode("utf-8")
    final_name = "mutation-results.json"
    temporary_name = ".mutation-results.json.tmp"
    directory_descriptor = _open_artifact_directory(artifacts)
    temporary_descriptor: int | None = None
    temporary_created = False
    try:
        _validate_result_entry(directory_descriptor, final_name)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            temporary_descriptor = os.open(
                temporary_name,
                flags,
                0o600,
                dir_fd=directory_descriptor,
            )
            temporary_created = True
        except OSError as error:
            raise GateInputError("mutation result temporary file must be absent") from error
        with os.fdopen(temporary_descriptor, "wb") as output:
            temporary_descriptor = None
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        _validate_result_entry(directory_descriptor, final_name)
        os.replace(
            temporary_name,
            final_name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
        os.fsync(directory_descriptor)
    finally:
        if temporary_descriptor is not None:
            os.close(temporary_descriptor)
        try:
            if temporary_created:
                try:
                    os.unlink(temporary_name, dir_fd=directory_descriptor)
                except FileNotFoundError:
                    pass
        finally:
            os.close(directory_descriptor)


def run_mutation_profile(
    *,
    repository_root: Path,
    profile: Mapping[str, Any],
    artifact_root: Path,
    python_runtime: Path,
    offline_runner: Path | None,
) -> dict[str, Any]:
    """Run one mutant at a time in disposable allowlisted snapshots."""

    validate_mutation_profile(profile)
    if (
        offline_runner is None
        or not offline_runner.is_file()
        or offline_runner.is_symlink()
        or not os.access(offline_runner, os.X_OK)
    ):
        raise GateInputError("mutation run requires an executable offline isolation runner")
    repo = repository_root.resolve()
    try:
        resolved_runtime = python_runtime.resolve(strict=True)
        active_runtime = Path(sys.executable).resolve(strict=True)
    except OSError as error:
        raise GateInputError("mutation run requires the active regular Python runtime") from error
    if resolved_runtime != active_runtime:
        raise GateInputError("mutation run requires the active locked Python runtime")
    runtime_sha256 = _runtime_sha256(resolved_runtime)
    artifacts = artifact_root.resolve()
    try:
        artifacts.relative_to(repo)
    except ValueError as error:
        raise GateInputError("mutation artifacts must stay inside the repository") from error
    if artifact_root.is_symlink():
        raise GateInputError("mutation artifact root cannot be a symlink")
    artifacts.mkdir(parents=True, exist_ok=True)
    _validate_result_target(artifacts)
    work_root = artifacts / "work"
    results_root = artifacts / "results"
    if work_root.exists():
        if work_root.is_symlink():
            raise GateInputError("mutation work root cannot be a symlink")
        shutil.rmtree(work_root)
    if results_root.exists():
        if results_root.is_symlink():
            raise GateInputError("mutation results root cannot be a symlink")
        shutil.rmtree(results_root)
    results_root.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    summary = {status: 0 for status in ("KILLED", "SURVIVED", "INVALID", "TIMED_OUT")}
    try:
        for mutation in profile["mutations"]:
            _verify_runtime_identity(resolved_runtime, runtime_sha256)
            try:
                identifier = mutation["id"]
                original_source = repo / mutation["sourcePath"]
                if not original_source.is_file() or original_source.is_symlink():
                    raise GateInputError(f"mutation source is missing or a symlink: {identifier}")
                original_digest = hashlib.sha256(original_source.read_bytes()).hexdigest()
                if original_digest != mutation["preimageSha256"]:
                    raise GateInputError(f"mutation preimage digest mismatch: {identifier}")

                workspace = work_root / identifier
                workspace.mkdir(parents=True)
                _copy_snapshot(repo, workspace, mutation["snapshotPaths"])
                mutated_source = workspace / mutation["sourcePath"]
                working_directory = workspace / mutation["workingDirectory"]
                if not working_directory.is_dir():
                    raise GateInputError(f"mutation working directory is missing: {identifier}")

                control_receipt = results_root / f"{identifier}-control-junit.xml"
                control_log = results_root / f"{identifier}-control.log"
                control_argv = _render_argv(
                    mutation["argv"],
                    python_runtime=resolved_runtime,
                    workspace=workspace,
                    receipt=control_receipt,
                )
                control_exit_code, control_timed_out = _run_bounded_command(
                    [str(offline_runner.resolve())] + control_argv,
                    working_directory=working_directory,
                    environment=_mutation_environment(),
                    timeout_seconds=mutation["timeoutSeconds"],
                    log=control_log,
                )
                if control_timed_out or not _control_selector_passed(
                    control_exit_code,
                    control_receipt,
                    mutation["expectedTest"],
                ):
                    raise GateInputError(
                        f"mutation control selector did not pass exactly once: {identifier}"
                    )
                if hashlib.sha256(original_source.read_bytes()).hexdigest() != original_digest:
                    raise GateInputError(
                        f"mutation control altered the original source: {identifier}"
                    )
                if hashlib.sha256(mutated_source.read_bytes()).hexdigest() != original_digest:
                    raise GateInputError(
                        f"mutation control altered the snapshot source: {identifier}"
                    )

                digests = apply_exact_mutation(
                    mutated_source,
                    before=mutation["before"],
                    after=mutation["after"],
                )
                receipt = results_root / f"{identifier}-junit.xml"
                log = results_root / f"{identifier}.log"
                argv = _render_argv(
                    mutation["argv"],
                    python_runtime=resolved_runtime,
                    workspace=workspace,
                    receipt=receipt,
                )
                command = [str(offline_runner.resolve())] + argv
                exit_code, timed_out = _run_bounded_command(
                    command,
                    working_directory=working_directory,
                    environment=_mutation_environment(),
                    timeout_seconds=mutation["timeoutSeconds"],
                    log=log,
                )
                status = (
                    "TIMED_OUT"
                    if timed_out
                    else classify_mutation_result(
                        exit_code if exit_code is not None else -1,
                        receipt,
                        mutation["expectedTest"],
                    )
                )
                if hashlib.sha256(original_source.read_bytes()).hexdigest() != original_digest:
                    raise GateInputError(f"mutation altered the original source: {identifier}")
                summary[status] += 1
                records.append(
                    {
                        "id": identifier,
                        "category": mutation["category"],
                        "language": mutation["language"],
                        "status": status,
                        "exitCode": exit_code,
                        "beforeSha256": digests["beforeSha256"],
                        "afterSha256": digests["afterSha256"],
                        "expectedTest": mutation["expectedTest"],
                        "controlLog": control_log.relative_to(repo).as_posix(),
                        "controlReceipt": control_receipt.relative_to(repo).as_posix(),
                        "log": log.relative_to(repo).as_posix(),
                        "receipt": receipt.relative_to(repo).as_posix()
                        if receipt.exists()
                        else None,
                    }
                )
                shutil.rmtree(workspace)
            finally:
                _verify_runtime_identity(resolved_runtime, runtime_sha256)
    finally:
        try:
            _verify_runtime_identity(resolved_runtime, runtime_sha256)
        finally:
            if work_root.exists():
                shutil.rmtree(work_root)

    result = {
        "schemaVersion": 1,
        "passed": summary["KILLED"] == len(records),
        "pythonRuntimeSha256": runtime_sha256,
        "summary": summary,
        "mutations": records,
    }
    _atomic_write_result(artifacts, result)
    return result
