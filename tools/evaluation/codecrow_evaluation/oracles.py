from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ._util import (
    canonical_bytes,
    require_mapping,
    require_sha256,
    require_string,
    sha256_bytes,
    sha256_file,
)


class OracleInputError(ValueError):
    """An oracle definition or result is unsafe, stale, or malformed."""


def _resolved_file_arguments(
    arguments: list[str] | tuple[str, ...], *, cwd: Path
) -> set[Path]:
    resolved: set[Path] = set()
    for argument in arguments:
        candidate = (
            argument.split("=", 1)[1]
            if argument.startswith("-") and "=" in argument
            else argument
        )
        path = Path(candidate)
        if not path.is_absolute():
            path = cwd / path
        path = path.resolve()
        if path.is_file():
            resolved.add(path)
    return resolved


@dataclass(frozen=True)
class OracleSpec:
    oracle_id: str
    oracle_version: str
    kind: str
    executable_path: Path
    executable_sha256: str
    argv: tuple[str, ...]
    artifacts: tuple[tuple[Path, str], ...]
    timeout_seconds: float
    spec_sha256: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "OracleSpec":
        item = require_mapping(value, "oracle spec", OracleInputError)
        if item.get("schemaVersion") != 1:
            raise OracleInputError("schemaVersion must be 1")
        kind = require_string(item.get("kind"), "kind", OracleInputError)
        if kind != "executable":
            raise OracleInputError("OracleSpec kind must be executable")
        path = Path(require_string(item.get("executablePath"), "executablePath", OracleInputError))
        argv = item.get("argv")
        if not isinstance(argv, list) or any(not isinstance(value, str) for value in argv):
            raise OracleInputError("argv must be an array of strings")
        timeout = item.get("timeoutSeconds")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise OracleInputError("timeoutSeconds must be a positive number")
        allowed = {"{case_root}", "{output}"}
        for argument in argv:
            for token in (part for part in argument.split("{")[1:] if "}" in part):
                placeholder = "{" + token.split("}", 1)[0] + "}"
                if placeholder not in allowed:
                    raise OracleInputError(f"unsupported argv placeholder {placeholder}")
        raw_artifacts = item.get("artifacts")
        if not isinstance(raw_artifacts, list):
            raise OracleInputError("artifacts must be an array")
        artifacts: list[tuple[Path, str]] = []
        artifact_paths: set[Path] = set()
        for raw_artifact in raw_artifacts:
            artifact = require_mapping(raw_artifact, "artifacts[]", OracleInputError)
            artifact_path = Path(
                require_string(artifact.get("path"), "artifact.path", OracleInputError)
            )
            if artifact_path in artifact_paths:
                raise OracleInputError("oracle artifact paths must be unique")
            artifact_paths.add(artifact_path)
            artifacts.append(
                (
                    artifact_path,
                    require_sha256(
                        artifact.get("sha256"), "artifact.sha256", OracleInputError
                    ),
                )
            )
        declared_artifacts = {artifact_path.resolve() for artifact_path in artifact_paths}
        undeclared = _resolved_file_arguments(argv, cwd=Path.cwd()) - declared_artifacts
        if undeclared:
            raise OracleInputError(
                "file-valued argv must be declared in artifacts: "
                + str(sorted(undeclared)[0])
            )
        return cls(
            oracle_id=require_string(item.get("oracleId"), "oracleId", OracleInputError),
            oracle_version=require_string(
                item.get("oracleVersion"), "oracleVersion", OracleInputError
            ),
            kind=kind,
            executable_path=path,
            executable_sha256=require_sha256(
                item.get("executableSha256"), "executableSha256", OracleInputError
            ),
            argv=tuple(argv),
            artifacts=tuple(artifacts),
            timeout_seconds=float(timeout),
            spec_sha256=sha256_bytes(canonical_bytes(dict(item))),
        )


def _validate_result(
    value: object,
    *,
    oracle_id: str,
    oracle_version: str,
) -> dict[str, Any]:
    result = dict(require_mapping(value, "oracle result", OracleInputError))
    if result.get("schemaVersion") != 1:
        raise OracleInputError("oracle result schemaVersion must be 1")
    if result.get("oracleId") != oracle_id or result.get("oracleVersion") != oracle_version:
        raise OracleInputError("oracle result identity does not match the executed oracle")
    require_string(result.get("caseId"), "oracle result caseId", OracleInputError)
    if result.get("status") not in ("pass", "fail"):
        raise OracleInputError("oracle result status must be pass or fail")
    labels = result.get("observedLabelIds")
    if (
        not isinstance(labels, list)
        or any(not isinstance(item, str) or not item for item in labels)
        or labels != sorted(set(labels))
    ):
        raise OracleInputError("observedLabelIds must be a sorted unique string array")
    return result


def run_executable_oracle(
    spec: OracleSpec,
    *,
    case_root: Path,
    output_path: Path,
    offline_runner: Path,
    offline_runner_sha256: str,
) -> dict[str, Any]:
    case_root = case_root.resolve()
    output_path = output_path.resolve()
    offline_runner = offline_runner.resolve()
    executable = spec.executable_path.resolve()
    if not case_root.is_dir():
        raise OracleInputError("case_root must be an existing directory")
    if not offline_runner.is_file() or sha256_file(offline_runner) != require_sha256(
        offline_runner_sha256, "offlineRunnerSha256", OracleInputError
    ):
        raise OracleInputError("offlineRunnerSha256 does not match offline runner bytes")
    if not executable.is_file() or sha256_file(executable) != spec.executable_sha256:
        raise OracleInputError("executableSha256 does not match executable bytes")
    artifact_digests: list[str] = []
    declared_artifacts: set[Path] = set()
    for artifact_path, expected_digest in spec.artifacts:
        resolved_artifact = artifact_path.resolve()
        if not resolved_artifact.is_file() or sha256_file(resolved_artifact) != expected_digest:
            raise OracleInputError(
                f"oracle artifact SHA-256 does not match: {artifact_path}"
            )
        artifact_digests.append(expected_digest)
        declared_artifacts.add(resolved_artifact)
    if output_path.exists():
        output_path.unlink()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    substitutions = {"{case_root}": str(case_root), "{output}": str(output_path)}
    arguments = []
    for argument in spec.argv:
        expanded = argument
        for placeholder, replacement in substitutions.items():
            expanded = expanded.replace(placeholder, replacement)
        arguments.append(expanded)
    undeclared = _resolved_file_arguments(arguments, cwd=case_root) - declared_artifacts
    if undeclared:
        raise OracleInputError(
            "file-valued argv must be declared in artifacts: "
            + str(sorted(undeclared)[0])
        )
    command = [str(offline_runner), str(executable), *arguments]
    environment = {
        "HOME": os.environ.get("HOME", "/nonexistent"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.environ.get("PATH", ""),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    started = time.monotonic_ns()
    try:
        completed = subprocess.run(
            command,
            cwd=case_root,
            env=environment,
            check=False,
            capture_output=True,
            timeout=spec.timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise OracleInputError(
            f"oracle {spec.oracle_id}@{spec.oracle_version} timed out"
        ) from exc
    duration_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
    if completed.returncode != 0:
        raise OracleInputError(
            f"oracle {spec.oracle_id}@{spec.oracle_version} exited {completed.returncode}"
        )
    if not output_path.is_file():
        raise OracleInputError("oracle exited successfully without an output artifact")
    try:
        raw_result = json.loads(output_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise OracleInputError("oracle output is not valid UTF-8 JSON") from exc
    result = _validate_result(
        raw_result,
        oracle_id=spec.oracle_id,
        oracle_version=spec.oracle_version,
    )
    result["execution"] = {
        "durationMs": duration_ms,
        "executableSha256": spec.executable_sha256,
        "exitCode": completed.returncode,
        "offlineRunnerSha256": offline_runner_sha256,
        "oracleArtifactSha256": sorted(artifact_digests),
        "oracleSpecSha256": spec.spec_sha256,
    }
    return result


def validate_label_record(value: Mapping[str, Any]) -> dict[str, Any]:
    record = dict(require_mapping(value, "label record", OracleInputError))
    if record.get("schemaVersion") != 1:
        raise OracleInputError("label record schemaVersion must be 1")
    require_string(record.get("caseId"), "caseId", OracleInputError)
    require_string(record.get("labelVersion"), "labelVersion", OracleInputError)
    kind = require_string(record.get("oracleKind"), "oracleKind", OracleInputError)
    raw_labels = record.get("labels")
    if not isinstance(raw_labels, list):
        raise OracleInputError("labels must be an array")
    label_ids: set[str] = set()
    for raw in raw_labels:
        label = require_mapping(raw, "labels[]", OracleInputError)
        label_id = require_string(label.get("labelId"), "labelId", OracleInputError)
        if label_id in label_ids:
            raise OracleInputError(f"duplicate labelId {label_id}")
        label_ids.add(label_id)
        if label.get("severity") not in ("low", "medium", "high", "critical"):
            raise OracleInputError(f"{label_id}.severity is invalid")
    if kind == "subjective":
        labelers = record.get("labelers")
        if (
            not isinstance(labelers, list)
            or len(labelers) < 2
            or len(labelers) != len(set(labelers))
            or any(not isinstance(item, str) or not item for item in labelers)
        ):
            raise OracleInputError("subjective labels require at least two distinct labelers")
        adjudicator = require_string(
            record.get("adjudicator"), "adjudicator", OracleInputError
        )
        if adjudicator in labelers:
            raise OracleInputError("subjective labels require an independent adjudicator")
        require_string(record.get("adjudication"), "adjudication", OracleInputError)
    elif kind not in ("executable", "static"):
        raise OracleInputError("oracleKind must be executable, static, or subjective")
    return record
