"""Evaluate normalized changed-path and aggregate coverage reports."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


class GateInputError(ValueError):
    """Raised when gate input is incomplete, ambiguous, or malformed."""


@dataclass(frozen=True)
class GateResult:
    passed: bool
    changed_lines: dict[str, int]
    changed_branches: dict[str, int]
    failures: tuple[str, ...]
    excluded_files: tuple[str, ...] = ()
    compensating_receipts: tuple[dict[str, str], ...] = ()


_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_LEDGER_TOKEN = re.compile(r"^[a-z][a-z0-9_.-]*$")
_MAX_RECEIPT_BYTES = 16 * 1024 * 1024
_MAX_RUNTIME_BYTES = 64 * 1024 * 1024


def _counter(value: Any, field: str) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise GateInputError(f"{field} must be an object")
    covered = value.get("covered")
    total = value.get("total")
    if (
        not isinstance(covered, int)
        or isinstance(covered, bool)
        or not isinstance(total, int)
        or isinstance(total, bool)
        or covered < 0
        or total < 0
        or covered > total
    ):
        raise GateInputError(f"{field} has invalid exact counters")
    return {"covered": covered, "total": total}


def _ratio_regressed(current: Mapping[str, int], baseline: Mapping[str, int]) -> bool:
    if baseline["total"] == 0:
        return False
    if current["total"] == 0:
        return baseline["covered"] != 0
    return current["covered"] * baseline["total"] < baseline["covered"] * current["total"]


def _safe_path(value: Any, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise GateInputError(f"{field} must be a repository-relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or value == "."
        or path.as_posix() != value
    ):
        raise GateInputError(f"{field} must be a repository-relative path")
    return value


def _line_list(value: Any, field: str) -> list[int]:
    if (
        not isinstance(value, list)
        or any(not isinstance(line, int) or isinstance(line, bool) or line <= 0 for line in value)
        or value != sorted(set(value))
    ):
        raise GateInputError(f"{field} must be positive, unique, and sorted")
    return value


def validate_normalized_report(report: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate the complete normalized contract and recompute exact totals."""

    language = report.get("language")
    module = report.get("module")
    adapter = report.get("adapter")
    version = report.get("toolVersion")
    source_inventory_sha256 = report.get("sourceInventorySha256")
    files = report.get("files")
    if (
        set(report)
        != {
            "schemaVersion",
            "adapter",
            "language",
            "module",
            "toolVersion",
            "sourceInventorySha256",
            "branchInstrumentation",
            "files",
            "totals",
        }
        or
        report.get("schemaVersion") != 1
        or not isinstance(language, str)
        or not language.strip()
        or not isinstance(module, str)
        or not module.strip()
        or not isinstance(adapter, str)
        or not adapter.strip()
        or not isinstance(version, str)
        or not version.strip()
        or not isinstance(source_inventory_sha256, str)
        or not _SHA256.fullmatch(source_inventory_sha256)
        or not isinstance(files, Mapping)
        or not isinstance(report.get("branchInstrumentation"), bool)
    ):
        raise GateInputError("normalized report identity is malformed")

    computed = {
        "lines": {"covered": 0, "total": 0},
        "branches": {"covered": 0, "total": 0},
    }
    for path, file_report in files.items():
        safe_path = _safe_path(path, "normalized source path")
        if not isinstance(file_report, Mapping):
            raise GateInputError(f"{safe_path} file report is malformed")
        executable = _line_list(
            file_report.get("executableLines"), f"{safe_path} executableLines"
        )
        covered = _line_list(file_report.get("coveredLines"), f"{safe_path} coveredLines")
        if not set(covered) <= set(executable):
            raise GateInputError(f"{safe_path} covered lines must be executable")
        branches = file_report.get("branches")
        if not isinstance(branches, Mapping):
            raise GateInputError(f"{safe_path} branches must be an object")
        branch_covered = branch_total = 0
        for origin, branch in branches.items():
            if (
                not isinstance(origin, str)
                or not origin.isascii()
                or not origin.isdigit()
                or str(int(origin)) != origin
                or int(origin) not in executable
                or not isinstance(branch, Mapping)
            ):
                raise GateInputError(f"{safe_path} has a malformed branch origin")
            covered_count = branch.get("covered")
            missed_count = branch.get("missed")
            counter = _counter(
                {
                    "covered": covered_count,
                    "total": (
                        covered_count + missed_count
                        if isinstance(covered_count, int)
                        and not isinstance(covered_count, bool)
                        and isinstance(missed_count, int)
                        and not isinstance(missed_count, bool)
                        else None
                    ),
                },
                f"{safe_path}:{origin} branches",
            )
            if counter["total"] == 0:
                raise GateInputError(f"{safe_path}:{origin} has an empty branch counter")
            branch_covered += counter["covered"]
            branch_total += counter["total"]
        computed["lines"]["covered"] += len(covered)
        computed["lines"]["total"] += len(executable)
        computed["branches"]["covered"] += branch_covered
        computed["branches"]["total"] += branch_total

    totals = report.get("totals")
    if not isinstance(totals, Mapping):
        raise GateInputError(f"{language}:{module} totals are malformed")
    declared = {
        kind: _counter(totals.get(kind), f"{language}:{module} {kind}")
        for kind in ("lines", "branches")
    }
    if declared != computed:
        raise GateInputError(f"{language}:{module} totals do not match normalized files")
    return totals


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GateInputError(f"duplicate receipt JSON key: {key}")
        result[key] = value
    return result


def _read_trusted_repository_file(
    repository_root: Path,
    relative_path: str,
    *,
    expected_sha256: str | None,
    field: str,
    evidence_only: bool,
) -> bytes:
    """Open a repository file through no-follow dirfds and stream its digest."""

    safe = _safe_path(relative_path, field)
    parts = PurePosixPath(safe).parts
    if evidence_only and (not parts or parts[0] != ".llm-handoff-artifacts"):
        raise GateInputError(f"{field} must stay under .llm-handoff-artifacts")
    root = Path(os.path.abspath(repository_root))
    descriptors: list[int] = []
    try:
        current = os.open(
            root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        descriptors.append(current)
        for component in parts[:-1]:
            current = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=current,
            )
            descriptors.append(current)
        file_descriptor = os.open(
            parts[-1],
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=current,
        )
        descriptors.append(file_descriptor)
        if not stat.S_ISREG(os.fstat(file_descriptor).st_mode):
            raise GateInputError(f"{field} must be a regular file")
        digest = hashlib.sha256()
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(file_descriptor, 64 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > _MAX_RECEIPT_BYTES:
                raise GateInputError(f"{field} exceeds the evidence size limit")
            digest.update(chunk)
            chunks.append(chunk)
        if expected_sha256 is not None and digest.hexdigest() != expected_sha256:
            raise GateInputError(f"{field} digest mismatch")
        return b"".join(chunks)
    except GateInputError:
        raise
    except OSError as error:
        raise GateInputError(f"{field} is not a trusted evidence file") from error
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _read_evidence_file(
    repository_root: Path, relative_path: str, *, expected_sha256: str, field: str
) -> bytes:
    return _read_trusted_repository_file(
        repository_root,
        relative_path,
        expected_sha256=expected_sha256,
        field=field,
        evidence_only=True,
    )


def _read_current_evidence_file(
    repository_root: Path, relative_path: str, *, field: str
) -> tuple[bytes, str]:
    raw = _read_trusted_repository_file(
        repository_root,
        relative_path,
        expected_sha256=None,
        field=field,
        evidence_only=True,
    )
    return raw, hashlib.sha256(raw).hexdigest()


def _strict_json_object(raw: bytes, field: str) -> Mapping[str, Any]:
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                GateInputError(f"invalid receipt JSON constant: {constant}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GateInputError(f"{field} is malformed JSON") from error
    if not isinstance(value, Mapping):
        raise GateInputError(f"{field} must be a JSON object")
    return value


def _evidence_reference(value: Any, field: str) -> tuple[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"artifact", "sha256"}:
        raise GateInputError(f"{field} reference is malformed")
    artifact = value.get("artifact")
    digest = value.get("sha256")
    if not isinstance(artifact, str) or not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise GateInputError(f"{field} reference is malformed")
    return artifact, digest


def _junit_counts(raw: bytes, *, selector: str) -> dict[str, int]:
    if b"<!ENTITY" in raw or b"<!DOCTYPE" in raw:
        raise GateInputError("compensating JUnit receipt contains an unsafe declaration")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise GateInputError("compensating JUnit receipt is malformed") from error
    allowed_tags = {
        "testsuites",
        "testsuite",
        "testcase",
        "failure",
        "error",
        "skipped",
        "properties",
        "property",
        "system-out",
        "system-err",
    }
    if any(element.tag not in allowed_tags for element in root.iter()):
        raise GateInputError("compensating JUnit contains an unsafe namespace or element")
    if root.tag == "testsuite":
        suites = [root]
    elif root.tag == "testsuites" and all(child.tag == "testsuite" for child in root):
        suites = list(root)
    else:
        suites = []
    if not suites or any(suite.findall(".//testsuite") for suite in suites):
        raise GateInputError("compensating JUnit receipt has no test suites")
    declared = {name: 0 for name in ("tests", "failures", "errors", "skipped")}
    try:
        for suite in suites:
            for name in declared:
                value = int(suite.attrib.get(name, "0"))
                if value < 0:
                    raise ValueError(name)
                declared[name] += value
    except (TypeError, ValueError) as error:
        raise GateInputError("compensating JUnit counters are malformed") from error
    cases = [case for suite in suites for case in suite if case.tag == "testcase"]
    recomputed = {
        "tests": len(cases),
        "failures": sum(case.find("failure") is not None for case in cases),
        "errors": sum(case.find("error") is not None for case in cases),
        "skipped": sum(case.find("skipped") is not None for case in cases),
    }
    if declared != recomputed:
        raise GateInputError("compensating JUnit counters do not match testcases")
    names = [case.attrib.get("name", "") for case in cases]
    classnames = [case.attrib.get("classname", "") for case in cases]
    if "::" in selector:
        selector_parts = selector.split("::")
        source_path = _safe_path(selector_parts[0], "compensating selector source")
        if not source_path.endswith(".py") or len(selector_parts) < 2:
            raise GateInputError("compensating selector is malformed")
        expected_name = selector_parts[-1]
        expected_classname = source_path[:-3].replace("/", ".")
        if len(selector_parts) > 2:
            expected_classname += "." + ".".join(selector_parts[1:-1])
    elif "#" in selector and selector.count("#") == 1:
        expected_classname, expected_name = selector.split("#", 1)
        if not expected_classname or not expected_name:
            raise GateInputError("compensating selector is malformed")
    else:
        raise GateInputError("compensating selector is malformed")
    if (
        recomputed["tests"] <= 0
        or recomputed["failures"] != 0
        or recomputed["errors"] != 0
        or recomputed["skipped"] != 0
        or not expected_name
        or any(
            name != expected_name and not name.startswith(expected_name + "[")
            for name in names
        )
        or any(classname != expected_classname for classname in classnames)
    ):
        raise GateInputError("compensating JUnit receipt is not an exact passing selector")
    return recomputed


def _read_runtime_identity(value: Any) -> tuple[str, str]:
    """Verify one absolute, real, executable runtime through no-follow dirfds."""

    if not isinstance(value, Mapping) or set(value) != {"realPath", "sha256"}:
        raise GateInputError("compensating runtime identity is malformed")
    path = value.get("realPath")
    expected_digest = value.get("sha256")
    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or path == "/"
        or "\\" in path
        or any(ord(character) < 32 or ord(character) == 127 for character in path)
        or ".." in PurePosixPath(path).parts
        or PurePosixPath(path).as_posix() != path
        or os.path.realpath(path) != path
        or not isinstance(expected_digest, str)
        or not _SHA256.fullmatch(expected_digest)
    ):
        raise GateInputError("compensating runtime identity is malformed")
    descriptors: list[int] = []
    try:
        current = os.open(
            "/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
        )
        descriptors.append(current)
        parts = PurePosixPath(path).parts[1:]
        for component in parts[:-1]:
            current = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=current,
            )
            descriptors.append(current)
        descriptor = os.open(
            parts[-1],
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=current,
        )
        descriptors.append(descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o111 == 0:
            raise GateInputError("compensating runtime must be a regular executable")
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > _MAX_RUNTIME_BYTES:
                raise GateInputError("compensating runtime exceeds the size limit")
            digest.update(chunk)
        if digest.hexdigest() != expected_digest:
            raise GateInputError("compensating runtime digest mismatch")
        return path, expected_digest
    except GateInputError:
        raise
    except OSError as error:
        raise GateInputError("compensating runtime is not trusted") from error
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _read_approved_runtime(
    repository_root: Path, value: Any
) -> tuple[str, str]:
    artifact, digest = _evidence_reference(value, "approved compensating runtime")
    if artifact.startswith(".llm-handoff-artifacts/"):
        raise GateInputError("approved compensating runtime must be a repository tool")
    _read_trusted_repository_file(
        repository_root,
        artifact,
        expected_sha256=digest,
        field="approved compensating runtime",
        evidence_only=False,
    )
    path = Path(os.path.abspath(repository_root / artifact))
    try:
        mode = path.stat(follow_symlinks=False).st_mode
    except OSError as error:
        raise GateInputError("approved compensating runtime is not trusted") from error
    if not stat.S_ISREG(mode) or mode & 0o111 == 0:
        raise GateInputError("approved compensating runtime must be executable")
    return path.as_posix(), digest


def _validate_zero_live_ledger(raw: bytes) -> None:
    ledger = _strict_json_object(raw, "compensating external-call ledger")
    if set(ledger) != {"schema_version", "live_call_count", "simulated_call_count", "calls"}:
        raise GateInputError("compensating external-call ledger contract is malformed")
    calls = ledger.get("calls")
    required_call_keys = {
        "boundary",
        "live",
        "operation",
        "outcome",
        "phase",
        "sequence",
        "simulated",
        "target",
    }
    if (
        ledger.get("schema_version") != "1.0"
        or ledger.get("live_call_count") != 0
        or not isinstance(ledger.get("simulated_call_count"), int)
        or isinstance(ledger.get("simulated_call_count"), bool)
        or ledger["simulated_call_count"] < 0
        or not isinstance(calls, list)
        or any(not isinstance(call, Mapping) or set(call) != required_call_keys for call in calls)
    ):
        raise GateInputError("compensating external-call ledger contract is malformed")
    for index, call in enumerate(calls, start=1):
        if (
            not isinstance(call["boundary"], str)
            or not _LEDGER_TOKEN.fullmatch(call["boundary"])
            or not isinstance(call["operation"], str)
            or not _LEDGER_TOKEN.fullmatch(call["operation"])
            or not isinstance(call["outcome"], str)
            or not _LEDGER_TOKEN.fullmatch(call["outcome"])
            or call["phase"] not in {"PRE_DNS", "PRE_SOCKET", "PRE_EXEC", "SIMULATED"}
            or not isinstance(call["live"], bool)
            or not isinstance(call["simulated"], bool)
            or (call["live"] and call["simulated"])
            or call["sequence"] != index
            or not isinstance(call["target"], str)
            or not call["target"]
        ):
            raise GateInputError("compensating external-call ledger call is malformed")
    live_count = sum(call["live"] for call in calls)
    simulated_count = sum(call["simulated"] for call in calls)
    if (
        ledger["live_call_count"] != live_count
        or ledger["simulated_call_count"] != simulated_count
        or live_count != 0
    ):
        raise GateInputError("compensating external-call ledger does not prove zero live calls")


def _verify_compensating_receipt(
    *,
    repository_root: Path,
    selector: str,
    expected_head: str,
    change_inventory_sha256: str,
    source_path: str,
    metadata: Mapping[str, Any],
    execution_policy: Mapping[str, Any],
) -> dict[str, str]:
    artifact = metadata["artifact"]
    raw_manifest, manifest_digest = _read_current_evidence_file(
        repository_root,
        artifact,
        field="compensating receipt artifact",
    )
    manifest = _strict_json_object(raw_manifest, "compensating receipt artifact")
    if not isinstance(execution_policy, Mapping) or set(execution_policy) != {
        "runner",
        "runtime",
        "argvTemplate",
    }:
        raise GateInputError("compensating execution policy is malformed")
    approved_runner_path, approved_runner_digest = _evidence_reference(
        execution_policy.get("runner"), "approved compensating runner"
    )
    approved_runtime_path, approved_runtime_digest = _read_approved_runtime(
        repository_root, execution_policy.get("runtime")
    )
    argv_template = execution_policy.get("argvTemplate")
    if (
        not isinstance(argv_template, list)
        or len(argv_template) < 3
        or any(not isinstance(argument, str) or not argument for argument in argv_template)
        or argv_template.count("{selector}") != 1
        or argv_template.count("{runtime}") != 1
        or argv_template[-1] != "{selector}"
        or argv_template[0] != approved_runner_path
        or argv_template[1] != "{runtime}"
    ):
        raise GateInputError("approved compensating argv template is malformed")
    approved_argv = [
        selector
        if argument == "{selector}"
        else approved_runtime_path
        if argument == "{runtime}"
        else argument
        for argument in argv_template
    ]
    if set(manifest) != {
        "schemaVersion",
        "selector",
        "headCommit",
        "changeInventorySha256",
        "source",
        "runner",
        "runtime",
        "argv",
        "junit",
        "ledger",
    }:
        raise GateInputError("compensating receipt manifest contract is malformed")
    argv = manifest.get("argv")
    runner_path, runner_digest = _evidence_reference(
        manifest.get("runner"), "compensating runner"
    )
    if (
        runner_path.startswith(".llm-handoff-artifacts/")
        or runner_path != approved_runner_path
        or runner_digest != approved_runner_digest
    ):
        raise GateInputError("compensating runner must be a repository tool")
    _read_trusted_repository_file(
        repository_root,
        runner_path,
        expected_sha256=runner_digest,
        field="compensating runner",
        evidence_only=False,
    )
    runtime_path, runtime_digest = _read_runtime_identity(manifest.get("runtime"))
    if (
        manifest.get("schemaVersion") != 1
        or manifest.get("selector") != selector
        or manifest.get("headCommit") != expected_head
        or manifest.get("changeInventorySha256") != change_inventory_sha256
        or not isinstance(argv, list)
        or len(argv) < 3
        or any(not isinstance(argument, str) or not argument for argument in argv)
        or runtime_path != approved_runtime_path
        or runtime_digest != approved_runtime_digest
        or argv != approved_argv
    ):
        raise GateInputError("compensating receipt manifest identity is malformed")
    source = manifest.get("source")
    if not isinstance(source, Mapping) or set(source) != {"path", "sha256"}:
        raise GateInputError("compensating receipt source identity is malformed")
    if source.get("path") != source_path or not isinstance(source.get("sha256"), str) or not _SHA256.fullmatch(source["sha256"]):
        raise GateInputError("compensating receipt source identity is malformed")
    _read_trusted_repository_file(
        repository_root,
        source_path,
        expected_sha256=source["sha256"],
        field="compensating receipt source",
        evidence_only=False,
    )
    junit_path, junit_digest = _evidence_reference(manifest.get("junit"), "compensating JUnit")
    junit = _read_evidence_file(
        repository_root,
        junit_path,
        expected_sha256=junit_digest,
        field="compensating JUnit artifact",
    )
    _junit_counts(junit, selector=selector)
    ledger_path, ledger_digest = _evidence_reference(
        manifest.get("ledger"), "compensating ledger"
    )
    ledger = _read_evidence_file(
        repository_root,
        ledger_path,
        expected_sha256=ledger_digest,
        field="compensating ledger artifact",
    )
    _validate_zero_live_ledger(ledger)
    return {"artifact": artifact, "sha256": manifest_digest}


def _validate_exclusions(
    exclusions: Mapping[str, Any],
    *,
    as_of: date,
    expected_head: str,
    repository_root: Path | None,
) -> tuple[Mapping[str, Any], ...]:
    if (
        set(exclusions) != {"schemaVersion", "entries"}
        or exclusions.get("schemaVersion") != 1
        or not isinstance(exclusions.get("entries"), list)
    ):
        raise GateInputError("exclusions must use schema version 1 and contain entries")
    validated: list[Mapping[str, Any]] = []
    identifiers: set[str] = set()
    for entry in exclusions["entries"]:
        if not isinstance(entry, Mapping) or set(entry) != {
            "id",
            "fileGlob",
            "reason",
            "owner",
            "reviewer",
            "expiresOn",
            "compensatingIntegrationTest",
        }:
            raise GateInputError("coverage exclusion must be an object")
        identifier = entry.get("id")
        pattern = entry.get("fileGlob")
        reason = entry.get("reason")
        owner = entry.get("owner")
        reviewer = entry.get("reviewer")
        if not isinstance(identifier, str) or not identifier or identifier in identifiers:
            raise GateInputError("coverage exclusion id must be unique and non-empty")
        identifiers.add(identifier)
        if not isinstance(pattern, str) or not pattern:
            raise GateInputError("coverage exclusion glob is too broad")
        try:
            _safe_path(pattern, "coverage exclusion glob")
        except GateInputError as error:
            raise GateInputError("coverage exclusion glob is too broad") from error
        pattern_parts = PurePosixPath(pattern).parts
        wildcard_parts = [
            part for part in pattern_parts if any(token in part for token in "*?[")
        ]
        if (
            wildcard_parts
            and (
                len(pattern_parts) < 4
                or pattern_parts[-1] in {"*", "**"}
                or any(
                    any(token in part for token in "*?[")
                    for part in pattern_parts[:3]
                )
            )
        ):
            raise GateInputError("coverage exclusion glob is too broad")
        if not isinstance(reason, str) or not reason.strip():
            raise GateInputError("coverage exclusion reason is required")
        if (
            not isinstance(owner, str)
            or not owner.strip()
            or not isinstance(reviewer, str)
            or not reviewer.strip()
        ):
            raise GateInputError("coverage exclusion owner and reviewer are required")
        if owner == reviewer:
            raise GateInputError("coverage exclusion owner and reviewer must differ")
        try:
            expiry = date.fromisoformat(entry.get("expiresOn"))
        except (TypeError, ValueError) as error:
            raise GateInputError("coverage exclusion expiry must be an ISO date") from error
        if expiry < as_of:
            raise GateInputError(f"coverage exclusion expired: {identifier}")
        integration = entry.get("compensatingIntegrationTest")
        if not isinstance(integration, Mapping) or set(integration) != {
            "selector",
            "executionPolicy",
            "receipt",
        }:
            raise GateInputError("compensating integration test contract is malformed")
        selector = integration.get("selector") if isinstance(integration, Mapping) else None
        if not isinstance(selector, str) or not selector.strip():
            raise GateInputError("compensating integration test selector is required")
        execution_policy = integration.get("executionPolicy")
        if not isinstance(execution_policy, Mapping) or set(execution_policy) != {
            "runner",
            "runtime",
            "argvTemplate",
        }:
            raise GateInputError("compensating execution policy is malformed")
        receipt = integration.get("receipt")
        if not isinstance(receipt, Mapping) or set(receipt) != {"artifact"}:
            raise GateInputError("compensating integration test has no passing receipt")
        try:
            artifact = _safe_path(receipt.get("artifact"), "compensating receipt artifact")
        except GateInputError as error:
            raise GateInputError("compensating integration test receipt artifact is invalid") from error
        if not artifact.startswith(".llm-handoff-artifacts/"):
            raise GateInputError("compensating integration test receipt artifact is invalid")
        validated.append(entry)
    return tuple(validated)


def _matching_exclusion(
    path: str, exclusions: Sequence[Mapping[str, Any]]
) -> Mapping[str, Any] | None:
    matches = [
        entry
        for entry in exclusions
        if PurePosixPath("/" + path).match("/" + entry["fileGlob"])
    ]
    if len(matches) > 1:
        raise GateInputError(f"multiple coverage exclusions match {path}")
    return matches[0] if matches else None


def _evaluate_unbound_gate(
    *,
    changes: Mapping[str, Any],
    reports: Sequence[Mapping[str, Any]],
    baseline: Mapping[str, Any],
    exclusions: Mapping[str, Any],
    as_of: str,
    repository_root: Path | None = None,
    source_inventory: Mapping[str, Any] | None = None,
    correctness_policy: Mapping[str, Any] | None = None,
    correctness_policy_path: str | None = None,
    correctness_policy_sha256: str | None = None,
) -> GateResult:
    """Legacy test helper for gate mechanics without release provenance.

    Release acceptance must call :func:`evaluate_gate`, whose source inventory
    argument is mandatory.  This private helper remains solely so focused unit
    tests can exercise malformed legacy inputs without manufacturing evidence.
    """

    if changes.get("schemaVersion") != 1 or not isinstance(changes.get("files"), list):
        raise GateInputError("changes must use schema version 1 and contain files")
    if (
        not isinstance(changes.get("baseCommit"), str)
        or not _COMMIT.fullmatch(changes["baseCommit"])
        or not isinstance(changes.get("headCommit"), str)
        or not _COMMIT.fullmatch(changes["headCommit"])
    ):
        raise GateInputError("change inventory commits are malformed")
    policy_identity = changes.get("correctnessPolicy")
    if policy_identity is not None:
        from .correctness_policy import correctness_policy_identity

        if (
            correctness_policy is None
            or correctness_policy_path is None
            or correctness_policy_sha256 is None
            or policy_identity
            != correctness_policy_identity(
                correctness_policy,
                path=correctness_policy_path,
                sha256=correctness_policy_sha256,
            )
        ):
            raise GateInputError("change inventory correctness policy identity is invalid")
        expected_change_keys = {
            "schemaVersion",
            "baseCommit",
            "headCommit",
            "mergeBase",
            "dirty",
            "files",
            "correctnessPolicy",
        }
        if "comparisonBaseDirtyStateSha256" in changes:
            expected_change_keys.add("comparisonBaseDirtyStateSha256")
        if (
            set(changes) != expected_change_keys
            or changes.get("mergeBase") != changes.get("baseCommit")
            or not isinstance(changes.get("dirty"), bool)
            or (
                "comparisonBaseDirtyStateSha256" in changes
                and (
                    not isinstance(changes["comparisonBaseDirtyStateSha256"], str)
                    or not _SHA256.fullmatch(
                        changes["comparisonBaseDirtyStateSha256"]
                    )
                )
            )
        ):
            raise GateInputError("change inventory contract is malformed")
    if baseline.get("schemaVersion") != 1 or not isinstance(baseline.get("domains"), Mapping):
        raise GateInputError("baseline must use schema version 1 and contain domains")
    baseline_has_file_contract = (
        "files" in baseline
        or "sourceInventoryPolicyPath" in baseline
        or "sourceInventoryPolicySha256" in baseline
    )
    if source_inventory is not None and not baseline_has_file_contract:
        raise GateInputError("source-inventory evaluation requires a file-level baseline")
    if baseline_has_file_contract and (
        not isinstance(baseline.get("files"), Mapping)
        or not isinstance(baseline.get("sourceInventoryPolicyPath"), str)
        or _safe_path(
            baseline.get("sourceInventoryPolicyPath"),
            "baseline source inventory policy path",
        )
        != baseline["sourceInventoryPolicyPath"]
        or not isinstance(baseline.get("sourceInventoryPolicySha256"), str)
        or not _SHA256.fullmatch(baseline["sourceInventoryPolicySha256"])
        or source_inventory is None
    ):
        raise GateInputError("baseline source inventory contract is malformed")
    try:
        effective_date = date.fromisoformat(as_of)
    except (TypeError, ValueError) as error:
        raise GateInputError("as_of must be a non-empty ISO date") from error
    validated_exclusions = _validate_exclusions(
        exclusions,
        as_of=effective_date,
        expected_head=changes["headCommit"],
        repository_root=repository_root,
    )
    if baseline.get("comparisonBase") != changes.get("baseCommit"):
        raise GateInputError("comparison base does not match coverage baseline")
    try:
        change_inventory_sha256 = hashlib.sha256(
            json.dumps(
                changes,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
    except (TypeError, ValueError) as error:
        raise GateInputError("change inventory cannot be canonically hashed") from error

    expected_report_inventory_sha256: str | None = None
    if source_inventory is not None:
        from .source_inventory import source_inventory_digest

        expected_report_inventory_sha256 = source_inventory_digest(source_inventory)

    report_by_file: dict[str, Mapping[str, Any]] = {}
    reports_by_domain: dict[str, Mapping[str, Any]] = {}
    for report in reports:
        validate_normalized_report(report)
        if (
            expected_report_inventory_sha256 is not None
            and report["sourceInventorySha256"]
            != expected_report_inventory_sha256
        ):
            raise GateInputError("coverage report source inventory is stale")
        language = report.get("language")
        module = report.get("module")
        files = report.get("files")
        domain = f"{language}:{module}"
        if domain in reports_by_domain:
            raise GateInputError(f"duplicate report domain: {domain}")
        reports_by_domain[domain] = report
        if module == "@repository":
            continue
        for path, file_report in files.items():
            if not isinstance(path, str) or not isinstance(file_report, Mapping):
                raise GateInputError(f"{domain} contains a malformed file report")
            if path in report_by_file:
                raise GateInputError(f"ambiguous report path: {path}")
            report_by_file[path] = report

    inventory_by_path: dict[str, Mapping[str, Any]] = {}
    reconciled_files: dict[str, Mapping[str, Any]] = {}
    if baseline_has_file_contract:
        from .source_inventory import (  # Local import avoids a contract import cycle.
            reconcile_reports_with_inventory,
            validate_source_inventory,
        )

        inventory_by_path = validate_source_inventory(source_inventory)
        if (
            source_inventory["policyPath"] != baseline["sourceInventoryPolicyPath"]
            or source_inventory["policySha256"]
            != baseline["sourceInventoryPolicySha256"]
        ):
            raise GateInputError("source inventory policy does not match coverage baseline")
        reconciled_files = reconcile_reports_with_inventory(
            reports, source_inventory, require_aggregates=True
        )

    validated_changes: list[tuple[Mapping[str, Any], str, list[int]]] = []
    seen_changes: set[str] = set()
    for change in changes["files"]:
        if not isinstance(change, Mapping):
            raise GateInputError("change entry is malformed")
        status = change.get("status")
        expected_keys = {
            "path",
            "status",
            "correctnessCritical",
            "language",
            "changedLines",
        }
        if status in {"renamed", "copied"}:
            expected_keys.add("oldPath")
        if policy_identity is not None and status != "deleted":
            expected_keys.add("contentSha256")
        if policy_identity is not None and status != "added":
            expected_keys.add("previousContentSha256")
        if (
            set(change) != expected_keys
            or status
            not in {"added", "modified", "deleted", "renamed", "copied", "type_changed"}
            or not isinstance(change.get("correctnessCritical"), bool)
            or change.get("language") not in {None, "java", "python"}
            or (
                change.get("correctnessCritical") is False
                and change.get("language") is not None
            )
            or (
                "contentSha256" in expected_keys
                and (
                    not isinstance(change.get("contentSha256"), str)
                    or not _SHA256.fullmatch(change["contentSha256"])
                )
            )
            or (
                "previousContentSha256" in expected_keys
                and (
                    not isinstance(change.get("previousContentSha256"), str)
                    or not _SHA256.fullmatch(change["previousContentSha256"])
                )
            )
        ):
            raise GateInputError("change entry contract is malformed")
        path = _safe_path(change.get("path"), "changed path")
        if status in {"renamed", "copied"}:
            old_path = _safe_path(change.get("oldPath"), "changed old path")
            if old_path == path:
                raise GateInputError("change entry old path must differ")
        if path in seen_changes:
            raise GateInputError(f"duplicate changed path: {path}")
        seen_changes.add(path)
        changed = _line_list(change.get("changedLines"), f"{path} changedLines")
        if status == "deleted" and changed:
            raise GateInputError("deleted change cannot contain changed lines")
        if policy_identity is not None:
            if repository_root is None:
                raise GateInputError("content-bound changes require a repository root")
            if status != "deleted":
                _read_trusted_repository_file(
                    repository_root,
                    path,
                    expected_sha256=change["contentSha256"],
                    field="changed repository file",
                    evidence_only=False,
                )
            if status != "added":
                from .git_changes import _git_blob_sha256

                previous_path = change.get("oldPath", path)
                if (
                    _git_blob_sha256(
                        Path(os.path.abspath(repository_root)),
                        changes["baseCommit"],
                        previous_path,
                    )
                    != change["previousContentSha256"]
                ):
                    raise GateInputError(
                        f"previous source identity does not match Git base: {previous_path}"
                    )
        if correctness_policy is not None:
            from .correctness_policy import classify_path

            expected_critical, expected_language = classify_path(path, correctness_policy)
            if status in {"renamed", "copied"}:
                old_critical, old_language = classify_path(
                    change["oldPath"], correctness_policy
                )
                if old_critical:
                    expected_critical = True
                    languages = {
                        value
                        for value in (expected_language, old_language)
                        if value is not None
                    }
                    expected_language = (
                        next(iter(languages)) if len(languages) == 1 else None
                    )
            if (
                change["correctnessCritical"] != expected_critical
                or change["language"] != expected_language
            ):
                raise GateInputError(f"change classification does not match policy: {path}")
        validated_changes.append((change, path, changed))

    if baseline_has_file_contract:
        changes_by_path = {path: change for change, path, _ in validated_changes}
        baseline_files = baseline["files"]
        previous_baseline_path = ""
        for path, entry in baseline_files.items():
            if (
                not isinstance(path, str)
                or path <= previous_baseline_path
                or _safe_path(path, "baseline source path") != path
                or not isinstance(entry, Mapping)
                or set(entry) != {
                    "domain",
                    "sourceSha256",
                    "executableLines",
                    "branchShape",
                }
            ):
                raise GateInputError("baseline file contract is malformed or unsorted")
            previous_baseline_path = path
            domain = entry.get("domain")
            source_digest = entry.get("sourceSha256")
            executable = _line_list(
                entry.get("executableLines"), f"{path} baseline executableLines"
            )
            branch_shape = entry.get("branchShape")
            if (
                not isinstance(domain, str)
                or domain not in baseline["domains"]
                or not isinstance(source_digest, str)
                or not _SHA256.fullmatch(source_digest)
                or not isinstance(branch_shape, Mapping)
            ):
                raise GateInputError(f"{path} baseline file contract is malformed")
            inventory_source = inventory_by_path.get(path)
            if inventory_source is not None and (
                inventory_source["coverageDisposition"] != "required"
                or domain
                != f"{inventory_source['language']}:{inventory_source['module']}"
            ):
                raise GateInputError(f"{path} baseline file is not a required inventory source")
            for origin, total in branch_shape.items():
                if (
                    not isinstance(origin, str)
                    or not origin.isascii()
                    or not origin.isdigit()
                    or str(int(origin)) != origin
                    or int(origin) not in executable
                    or not isinstance(total, int)
                    or isinstance(total, bool)
                    or total <= 0
                ):
                    raise GateInputError(f"{path} baseline branch shape is malformed")

        for path, source in inventory_by_path.items():
            if source["coverageDisposition"] != "required":
                continue
            current_file = reconciled_files[path]
            current_domain = f"{source['language']}:{source['module']}"
            current_shape = {
                origin: branch["covered"] + branch["missed"]
                for origin, branch in current_file["branches"].items()
            }
            baseline_file = baseline_files.get(path)
            change = changes_by_path.get(path)
            if baseline_file is None:
                if change is None or change.get("status") not in {
                    "added",
                    "copied",
                    "renamed",
                }:
                    raise GateInputError(f"new inventory source lacks a declared Git change: {path}")
                if not current_file["executableLines"]:
                    raise GateInputError(f"new required source has no executable lines: {path}")
                if (
                    len(current_file["coveredLines"]) != len(current_file["executableLines"])
                    or any(branch["missed"] for branch in current_file["branches"].values())
                ):
                    raise GateInputError(f"new inventory source is not fully covered: {path}")
                continue
            if source["sha256"] == baseline_file["sourceSha256"]:
                if (
                    current_domain != baseline_file["domain"]
                    or current_file["executableLines"] != baseline_file["executableLines"]
                    or current_shape != baseline_file["branchShape"]
                ):
                    raise GateInputError(f"unchanged source coverage shape drifted: {path}")
            elif not current_file["executableLines"]:
                raise GateInputError(f"changed required source has no executable lines: {path}")
            elif (
                len(current_file["coveredLines"])
                != len(current_file["executableLines"])
                or any(
                    branch["missed"]
                    for branch in current_file["branches"].values()
                )
            ):
                # The plan fixes Git comparison at the P0-01 commit while this
                # file map freezes the later, reviewed pre-runtime epoch. A
                # source can therefore change relative to the baseline without
                # appearing as a useful P0-01 hunk (for example, a revert or a
                # second edit to a file introduced after P0-01). Full-file
                # coverage closes that epoch gap deterministically.
                raise GateInputError(
                    f"changed baseline source is not fully covered: {path}"
                )

        # The source policy identity is frozen in the baseline. Consequently a
        # baseline path absent from the current complete inventory is a proven
        # deletion even when the fixed P0-01 diff has no deletion record (for a
        # file that was introduced after P0-01 and later removed). Deletions
        # contain no executable current lines and need no synthetic coverage.

    compensating_receipts: list[dict[str, str]] = []
    for exclusion in validated_exclusions:
        matched_changes = [
            path
            for change, path, _ in validated_changes
            if change.get("correctnessCritical") is True
            and change.get("status") != "deleted"
            and PurePosixPath("/" + path).match("/" + exclusion["fileGlob"])
        ]
        if len(matched_changes) != 1:
            raise GateInputError(
                f"coverage exclusion must match exactly one changed correctness file: {exclusion['id']}"
            )
        if repository_root is None:
            raise GateInputError("coverage exclusions require a repository root")
        integration = exclusion["compensatingIntegrationTest"]
        compensating_receipts.append(_verify_compensating_receipt(
            repository_root=repository_root,
            selector=integration["selector"],
            expected_head=changes["headCommit"],
            change_inventory_sha256=change_inventory_sha256,
            source_path=matched_changes[0],
            metadata=integration["receipt"],
            execution_policy=integration["executionPolicy"],
        ))

    failures: list[str] = []
    changed_line_covered = 0
    changed_line_total = 0
    changed_branch_covered = 0
    changed_branch_total = 0
    excluded_files: list[str] = []

    for change, path, changed in validated_changes:
        if change.get("correctnessCritical") is not True or change.get("status") == "deleted":
            continue
        if _matching_exclusion(path, validated_exclusions) is not None:
            excluded_files.append(path)
            continue
        report = report_by_file.get(path)
        if report is None:
            failures.append(f"{path} has no coverage report")
            continue
        if report.get("branchInstrumentation") is not True:
            failures.append(f"{path} lacks branch instrumentation")
            continue
        file_report = report["files"][path]
        executable = file_report.get("executableLines")
        covered = file_report.get("coveredLines")
        branches = file_report.get("branches")
        if not isinstance(executable, list) or not isinstance(covered, list) or not isinstance(branches, Mapping):
            raise GateInputError(f"{path} file report is malformed")
        executable_set = set(executable)
        covered_set = set(covered)
        for line in changed:
            if line not in executable_set:
                continue
            changed_line_total += 1
            if line in covered_set:
                changed_line_covered += 1
            else:
                failures.append(f"{path}:{line} is an uncovered changed line")
            branch = branches.get(str(line))
            if branch is None:
                continue
            branch_counter = _counter(
                {
                    "covered": branch.get("covered") if isinstance(branch, Mapping) else None,
                    "total": (
                        branch.get("covered", 0) + branch.get("missed", 0)
                        if isinstance(branch, Mapping)
                        and isinstance(branch.get("covered"), int)
                        and isinstance(branch.get("missed"), int)
                        else None
                    ),
                },
                f"{path}:{line} branches",
            )
            changed_branch_covered += branch_counter["covered"]
            changed_branch_total += branch_counter["total"]
            missed = branch_counter["total"] - branch_counter["covered"]
            if missed:
                failures.append(f"{path}:{line} has {missed} uncovered changed branch")
        if change.get("status") in {"modified", "renamed", "copied", "type_changed"}:
            file_missed_branches = sum(
                branch["missed"]
                for origin, branch in branches.items()
                if isinstance(branch, Mapping) and int(origin) not in set(changed)
            )
            if file_missed_branches:
                failures.append(
                    f"{path} modified correctness file has "
                    f"{file_missed_branches} uncovered branch(es)"
                )

    for domain, baseline_domain in baseline["domains"].items():
        if not isinstance(domain, str) or not isinstance(baseline_domain, Mapping):
            raise GateInputError("baseline domain is malformed")
        report = reports_by_domain.get(domain)
        if report is None:
            failures.append(f"{domain} has no current aggregate report")
            continue
        totals = report.get("totals")
        if not isinstance(totals, Mapping):
            raise GateInputError(f"{domain} totals are malformed")
        for kind in ("lines", "branches"):
            current_counter = _counter(totals.get(kind), f"{domain} current {kind}")
            baseline_counter = _counter(baseline_domain.get(kind), f"{domain} baseline {kind}")
            if _ratio_regressed(current_counter, baseline_counter):
                failures.append(f"{domain} aggregate {kind} coverage regressed")

    for domain, report in reports_by_domain.items():
        if domain in baseline["domains"]:
            continue
        totals = report.get("totals")
        if not isinstance(totals, Mapping):
            raise GateInputError(f"{domain} totals are malformed")
        for kind in ("lines", "branches"):
            current_counter = _counter(totals.get(kind), f"{domain} current {kind}")
            if current_counter["covered"] != current_counter["total"]:
                failures.append(f"{domain} new domain {kind} coverage is not 100%")

    return GateResult(
        passed=not failures,
        changed_lines={"covered": changed_line_covered, "total": changed_line_total},
        changed_branches={"covered": changed_branch_covered, "total": changed_branch_total},
        failures=tuple(failures),
        excluded_files=tuple(sorted(excluded_files)),
        compensating_receipts=tuple(
            sorted(compensating_receipts, key=lambda item: item["artifact"])
        ),
    )


def evaluate_gate(
    *,
    changes: Mapping[str, Any],
    reports: Sequence[Mapping[str, Any]],
    baseline: Mapping[str, Any],
    exclusions: Mapping[str, Any],
    as_of: str,
    source_inventory: Mapping[str, Any],
    repository_root: Path | None = None,
    correctness_policy: Mapping[str, Any] | None = None,
    correctness_policy_path: str | None = None,
    correctness_policy_sha256: str | None = None,
) -> GateResult:
    """Evaluate one fail-closed, source-bound release acceptance gate."""

    if source_inventory is None:
        raise GateInputError("release gate evaluation requires a source inventory")
    if (
        set(baseline)
        != {
            "schemaVersion",
            "comparisonBase",
            "sourceSnapshotSha256",
            "sourceInventoryPolicyPath",
            "sourceInventoryPolicySha256",
            "domains",
            "files",
        }
        or baseline.get("schemaVersion") != 1
        or not isinstance(baseline.get("comparisonBase"), str)
        or not _COMMIT.fullmatch(baseline["comparisonBase"])
        or not isinstance(baseline.get("sourceSnapshotSha256"), str)
        or not _SHA256.fullmatch(baseline["sourceSnapshotSha256"])
        or not isinstance(baseline.get("sourceInventoryPolicyPath"), str)
        or _safe_path(
            baseline["sourceInventoryPolicyPath"],
            "baseline source inventory policy path",
        )
        != baseline["sourceInventoryPolicyPath"]
        or not isinstance(baseline.get("sourceInventoryPolicySha256"), str)
        or not _SHA256.fullmatch(baseline["sourceInventoryPolicySha256"])
        or not isinstance(baseline.get("domains"), Mapping)
        or not baseline["domains"]
        or not isinstance(baseline.get("files"), Mapping)
        or not baseline["files"]
    ):
        raise GateInputError("release coverage baseline contract is malformed")
    return _evaluate_unbound_gate(
        changes=changes,
        reports=reports,
        baseline=baseline,
        exclusions=exclusions,
        as_of=as_of,
        repository_root=repository_root,
        source_inventory=source_inventory,
        correctness_policy=correctness_policy,
        correctness_policy_path=correctness_policy_path,
        correctness_policy_sha256=correctness_policy_sha256,
    )
