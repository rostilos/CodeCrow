from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from codecrow_evaluation.oracles import (
    OracleInputError,
    OracleSpec,
    _validate_result,
    run_executable_oracle,
    validate_label_record,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _spec() -> dict:
    return {
        "schemaVersion": 1,
        "oracleId": "oracle-v1",
        "oracleVersion": "v1",
        "kind": "executable",
        "executablePath": sys.executable,
        "executableSha256": _sha(Path(sys.executable)),
        "argv": ["-c", "pass"],
        "artifacts": [],
        "timeoutSeconds": 1,
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("schema", "schemaVersion"),
        ("kind", "kind"),
        ("argv_type", "argv"),
        ("argv_item", "argv"),
        ("timeout_bool", "timeoutSeconds"),
        ("timeout_negative", "timeoutSeconds"),
        ("placeholder", "placeholder"),
        ("artifacts_type", "artifacts"),
        ("artifact_entry", "object"),
        ("artifact_duplicate", "unique"),
        ("undeclared_file_argv", "file-valued argv"),
        ("undeclared_option_file", "file-valued argv"),
    ],
)
def test_oracle_spec_negative_matrix(tmp_path: Path, mutation: str, message: str) -> None:
    value = _spec()
    if mutation == "schema":
        value["schemaVersion"] = 2
    elif mutation == "kind":
        value["kind"] = "subjective"
    elif mutation == "argv_type":
        value["argv"] = "-c"
    elif mutation == "argv_item":
        value["argv"] = [1]
    elif mutation == "timeout_bool":
        value["timeoutSeconds"] = True
    elif mutation == "timeout_negative":
        value["timeoutSeconds"] = -1
    elif mutation == "placeholder":
        value["argv"] = ["{secret}"]
    elif mutation == "artifacts_type":
        value["artifacts"] = {}
    elif mutation == "artifact_entry":
        value["artifacts"] = ["artifact"]
    elif mutation == "artifact_duplicate":
        artifact = tmp_path / "oracle.py"
        artifact.write_text("pass\n", encoding="utf-8")
        entry = {"path": str(artifact), "sha256": _sha(artifact)}
        value["artifacts"] = [entry, dict(entry)]
    elif mutation in ("undeclared_file_argv", "undeclared_option_file"):
        artifact = tmp_path / "oracle.py"
        artifact.write_text("pass\n", encoding="utf-8")
        value["argv"] = [
            str(artifact)
            if mutation == "undeclared_file_argv"
            else f"--config={artifact}"
        ]

    with pytest.raises(OracleInputError, match=message):
        OracleSpec.from_mapping(value)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ({"schemaVersion": 2}, "schemaVersion"),
        (
            {
                "schemaVersion": 1,
                "oracleId": "other",
                "oracleVersion": "v1",
                "caseId": "pr-a",
                "status": "pass",
                "observedLabelIds": [],
            },
            "identity",
        ),
        (
            {
                "schemaVersion": 1,
                "oracleId": "oracle-v1",
                "oracleVersion": "v1",
                "caseId": "pr-a",
                "status": "unknown",
                "observedLabelIds": [],
            },
            "status",
        ),
        (
            {
                "schemaVersion": 1,
                "oracleId": "oracle-v1",
                "oracleVersion": "v1",
                "caseId": "pr-a",
                "status": "pass",
                "observedLabelIds": ["b", "a"],
            },
            "sorted unique",
        ),
        (
            {
                "schemaVersion": 1,
                "oracleId": "oracle-v1",
                "oracleVersion": "v1",
                "caseId": "pr-a",
                "status": "pass",
                "observedLabelIds": [1],
            },
            "sorted unique",
        ),
    ],
)
def test_oracle_result_negative_matrix(value: dict, message: str) -> None:
    with pytest.raises(OracleInputError, match=message):
        _validate_result(value, oracle_id="oracle-v1", oracle_version="v1")


def _runner_fixture(tmp_path: Path) -> tuple[OracleSpec, Path, Path, Path]:
    wrapper = tmp_path / "offline.sh"
    wrapper.write_text('#!/bin/sh\nexec "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    case_root = tmp_path / "case"
    case_root.mkdir()
    output = tmp_path / "result.json"
    return OracleSpec.from_mapping(_spec()), wrapper, case_root, output


def test_oracle_runtime_preflight_and_failure_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec, wrapper, case_root, output = _runner_fixture(tmp_path)
    with pytest.raises(OracleInputError, match="case_root"):
        run_executable_oracle(
            spec,
            case_root=tmp_path / "missing",
            output_path=output,
            offline_runner=wrapper,
            offline_runner_sha256=_sha(wrapper),
        )
    with pytest.raises(OracleInputError, match="offlineRunnerSha256"):
        run_executable_oracle(
            spec,
            case_root=case_root,
            output_path=output,
            offline_runner=wrapper,
            offline_runner_sha256="0" * 64,
        )

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=2),
    )
    with pytest.raises(OracleInputError, match="exited 2"):
        run_executable_oracle(
            spec,
            case_root=case_root,
            output_path=output,
            offline_runner=wrapper,
            offline_runner_sha256=_sha(wrapper),
        )

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    with pytest.raises(OracleInputError, match="without an output"):
        run_executable_oracle(
            spec,
            case_root=case_root,
            output_path=output,
            offline_runner=wrapper,
            offline_runner_sha256=_sha(wrapper),
        )

    def invalid_output(*args, **kwargs):
        output.write_text("{", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", invalid_output)
    with pytest.raises(OracleInputError, match="valid UTF-8 JSON"):
        run_executable_oracle(
            spec,
            case_root=case_root,
            output_path=output,
            offline_runner=wrapper,
            offline_runner_sha256=_sha(wrapper),
        )


def test_oracle_runtime_rechecks_file_arguments_created_after_registration(
    tmp_path: Path,
) -> None:
    future_script = tmp_path / "created-later.py"
    value = _spec()
    value["argv"] = [str(future_script)]
    spec = OracleSpec.from_mapping(value)
    future_script.write_text("pass\n", encoding="utf-8")
    wrapper = tmp_path / "offline.sh"
    wrapper.write_text('#!/bin/sh\nexec "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    case_root = tmp_path / "case"
    case_root.mkdir()

    with pytest.raises(OracleInputError, match="file-valued argv"):
        run_executable_oracle(
            spec,
            case_root=case_root,
            output_path=tmp_path / "result.json",
            offline_runner=wrapper,
            offline_runner_sha256=_sha(wrapper),
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("schema", "schemaVersion"),
        ("labels_type", "labels"),
        ("duplicate", "duplicate labelId"),
        ("severity", "severity"),
        ("labelers_type", "labelers"),
        ("labelers_short", "labelers"),
        ("labelers_duplicate", "labelers"),
        ("labelers_bad_item", "labelers"),
        ("kind", "oracleKind"),
    ],
)
def test_label_record_negative_matrix(mutation: str, message: str) -> None:
    value = {
        "schemaVersion": 1,
        "caseId": "pr-a",
        "labelVersion": "labels-v1",
        "oracleKind": "subjective",
        "labelers": ["a", "b"],
        "adjudicator": "c",
        "adjudication": "reviewed",
        "labels": [{"labelId": "bug-a", "severity": "high"}],
    }
    if mutation == "schema":
        value["schemaVersion"] = 2
    elif mutation == "labels_type":
        value["labels"] = {}
    elif mutation == "duplicate":
        value["labels"].append(dict(value["labels"][0]))
    elif mutation == "severity":
        value["labels"][0]["severity"] = "blocker"
    elif mutation == "labelers_type":
        value["labelers"] = {}
    elif mutation == "labelers_short":
        value["labelers"] = ["a"]
    elif mutation == "labelers_duplicate":
        value["labelers"] = ["a", "a"]
    elif mutation == "labelers_bad_item":
        value["labelers"] = ["a", ""]
    elif mutation == "kind":
        value["oracleKind"] = "llm"

    with pytest.raises(OracleInputError, match=message):
        validate_label_record(value)


@pytest.mark.parametrize("kind", ["executable", "static"])
def test_non_subjective_label_records_are_valid(kind: str) -> None:
    assert validate_label_record(
        {
            "schemaVersion": 1,
            "caseId": "pr-a",
            "labelVersion": "labels-v1",
            "oracleKind": kind,
            "labels": [],
        }
    )["oracleKind"] == kind
