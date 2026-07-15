from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pytest


QUALITY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(QUALITY_ROOT))

from quality_gates import GateInputError  # noqa: E402
from quality_gates import cli  # noqa: E402
from quality_gates import changed_coverage as gate  # noqa: E402


HEAD = "1" * 40
SOURCE = "configs/quality/runtime/config.yml"
SELECTOR = "tests/test_config.py::test_config"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _bundle(tmp_path: Path) -> tuple[argparse.Namespace, dict[str, Path], dict, dict]:
    repository = tmp_path / "repository"
    repository.mkdir(parents=True)
    source = repository / SOURCE
    source.parent.mkdir(parents=True)
    source.write_text("enabled: true\n", encoding="utf-8")
    runner = repository / "tools/runner"
    runner.parent.mkdir(parents=True)
    runner.write_text("#!/bin/sh\nexec \"$@\"\n", encoding="utf-8")
    runner.chmod(0o755)
    runtime = repository / "tools/runtime"
    runtime.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    runtime.chmod(0o755)
    evidence = repository / ".llm-handoff-artifacts/p0-07/receipts"
    evidence.mkdir(parents=True)
    junit = evidence / "config.junit.xml"
    junit.write_text(
        '<testsuite tests="1" failures="0" errors="0" skipped="0">'
        '<testcase classname="tests.test_config" name="test_config"/>'
        "</testsuite>",
        encoding="utf-8",
    )
    ledger = evidence / "ledger.json"
    _write_json(
        ledger,
        {
            "schema_version": "1.0",
            "live_call_count": 0,
            "simulated_call_count": 0,
            "calls": [],
        },
    )
    changes = {
        "schemaVersion": 1,
        "headCommit": HEAD,
        "files": [
            {
                "path": SOURCE,
                "status": "modified",
                "correctnessCritical": True,
                "language": None,
                "changedLines": [],
                "contentSha256": _sha(source),
            }
        ],
    }
    exclusions = {
        "schemaVersion": 1,
        "entries": [
            {
                "id": "config",
                "fileGlob": SOURCE,
                "reason": "non-instrumentable configuration",
                "owner": "owner",
                "reviewer": "reviewer",
                "expiresOn": "2026-08-01",
                "compensatingIntegrationTest": {
                    "selector": SELECTOR,
                    "executionPolicy": {
                        "runner": {
                            "artifact": "tools/runner",
                            "sha256": _sha(runner),
                        },
                        "runtime": {
                            "artifact": "tools/runtime",
                            "sha256": _sha(runtime),
                        },
                        "argvTemplate": [
                            "tools/runner",
                            "{runtime}",
                            "--flag",
                            "{selector}",
                        ],
                    },
                    "receipt": {
                        "artifact": ".llm-handoff-artifacts/p0-07/receipts/config.json"
                    },
                },
            }
        ],
    }
    changes_path = repository / ".llm-handoff-artifacts/p0-07/changes.json"
    exclusions_path = repository / "tools/exclusions.json"
    _write_json(changes_path, changes)
    _write_json(exclusions_path, exclusions)
    output = evidence / "index.json"
    arguments = argparse.Namespace(
        changes=changes_path,
        exclusions=exclusions_path,
        junit=junit,
        ledger=ledger,
        as_of="2026-07-14",
        repository_root=repository,
        output=output,
    )
    return arguments, {
        "repository": repository,
        "source": source,
        "runner": runner,
        "runtime": runtime,
        "junit": junit,
        "ledger": ledger,
        "changes": changes_path,
        "exclusions": exclusions_path,
        "output": output,
    }, changes, exclusions


def test_capture_writes_source_bound_receipts_without_policy_self_reference(
    tmp_path: Path,
) -> None:
    arguments, paths, changes, exclusions = _bundle(tmp_path)

    assert cli._capture_exclusion_receipts(arguments) == 0

    receipt_metadata = exclusions["entries"][0]["compensatingIntegrationTest"]["receipt"]
    assert receipt_metadata == {
        "artifact": ".llm-handoff-artifacts/p0-07/receipts/config.json"
    }
    receipt_path = paths["repository"] / receipt_metadata["artifact"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["headCommit"] == HEAD
    assert receipt["source"] == {"path": SOURCE, "sha256": _sha(paths["source"])}
    assert receipt["argv"] == [
        "tools/runner",
        paths["runtime"].as_posix(),
        "--flag",
        SELECTOR,
    ]
    result = gate._verify_compensating_receipt(
        repository_root=paths["repository"],
        selector=SELECTOR,
        expected_head=HEAD,
        change_inventory_sha256=cli._canonical_json_sha256(
            changes, field="change inventory"
        ),
        source_path=SOURCE,
        metadata=receipt_metadata,
        execution_policy=exclusions["entries"][0]["compensatingIntegrationTest"][
            "executionPolicy"
        ],
    )
    assert result == {"artifact": receipt_metadata["artifact"], "sha256": _sha(receipt_path)}
    index = json.loads(paths["output"].read_text(encoding="utf-8"))
    assert index["receipts"] == [result]


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("date", "ISO date"),
        ("head", "change inventory is malformed"),
        ("files", "change inventory is malformed"),
        ("match", "must match exactly one"),
        ("source", "source identity is malformed"),
        ("runner", "runner digest mismatch"),
        ("runtime", "must be executable"),
        ("argv", "argv template is malformed"),
        ("junit", "exact passing selector"),
        ("ledger", "ledger contract is malformed"),
    ),
)
def test_capture_rejects_malformed_or_unbound_inputs(
    tmp_path: Path, mutation: str, message: str
) -> None:
    arguments, paths, changes, exclusions = _bundle(tmp_path)
    if mutation == "date":
        arguments.as_of = "not-a-date"
    elif mutation == "head":
        changes["headCommit"] = "bad"
        _write_json(paths["changes"], changes)
    elif mutation == "files":
        changes["files"] = {}
        _write_json(paths["changes"], changes)
    elif mutation == "match":
        exclusions["entries"][0]["fileGlob"] = "configs/quality/runtime/other.yml"
        _write_json(paths["exclusions"], exclusions)
    elif mutation == "source":
        changes["files"][0]["contentSha256"] = "bad"
        _write_json(paths["changes"], changes)
    elif mutation == "runner":
        paths["runner"].write_text("changed\n", encoding="utf-8")
    elif mutation == "runtime":
        paths["runtime"].chmod(0o644)
    elif mutation == "argv":
        exclusions["entries"][0]["compensatingIntegrationTest"]["executionPolicy"][
            "argvTemplate"
        ] = ["tools/runner", "{selector}"]
        _write_json(paths["exclusions"], exclusions)
    elif mutation == "junit":
        paths["junit"].write_text(
            '<testsuite tests="1"><testcase classname="x" name="y"/></testsuite>',
            encoding="utf-8",
        )
    elif mutation == "ledger":
        _write_json(
            paths["ledger"],
            {
                "schema_version": "1.0",
                "live_call_count": 1,
                "simulated_call_count": 0,
                "calls": [],
            },
        )
    else:  # pragma: no cover - parametrization is exhaustive.
        raise AssertionError(mutation)

    with pytest.raises(GateInputError, match=message):
        cli._capture_exclusion_receipts(arguments)


def test_capture_rejects_duplicate_receipt_targets_and_input_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments, paths, changes, exclusions = _bundle(tmp_path)
    second = json.loads(json.dumps(exclusions["entries"][0]))
    second["id"] = "second"
    second["fileGlob"] = "configs/quality/runtime/second.yml"
    second_source = paths["repository"] / second["fileGlob"]
    second_source.write_text("enabled: true\n", encoding="utf-8")
    changes["files"].append(
        {
            "path": second["fileGlob"],
            "status": "modified",
            "correctnessCritical": True,
            "language": None,
            "changedLines": [],
            "contentSha256": _sha(second_source),
        }
    )
    exclusions["entries"].append(second)
    _write_json(paths["changes"], changes)
    _write_json(paths["exclusions"], exclusions)
    with pytest.raises(GateInputError, match="artifact must be unique"):
        cli._capture_exclusion_receipts(arguments)

    arguments, paths, _, _ = _bundle(tmp_path / "drift")
    original = cli._write_json

    def write_and_drift(path: Path, value: dict) -> None:
        original(path, value)
        if path.name == "config.json":
            paths["changes"].write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(cli, "_write_json", write_and_drift)
    with pytest.raises(GateInputError):
        cli._capture_exclusion_receipts(arguments)


def test_receipt_artifacts_must_be_repository_local_evidence(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    outside = tmp_path / "outside.xml"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(GateInputError, match="inside the repository"):
        cli._artifact_reference(outside, repository_root=repository, field="artifact")
    inside = repository / "ordinary.xml"
    inside.write_text("x", encoding="utf-8")
    with pytest.raises(GateInputError, match="under .llm-handoff-artifacts"):
        cli._artifact_reference(inside, repository_root=repository, field="artifact")


def test_capture_receipt_command_dispatches_from_the_public_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_capture_exclusion_receipts", lambda arguments: 7)
    assert cli.main(
        [
            "capture-exclusion-receipts",
            "--changes", str(tmp_path / "changes.json"),
            "--exclusions", str(tmp_path / "exclusions.json"),
            "--junit", str(tmp_path / "junit.xml"),
            "--ledger", str(tmp_path / "ledger.json"),
            "--as-of", "2026-07-14",
            "--repository-root", str(tmp_path),
            "--output", str(tmp_path / "index.json"),
        ]
    ) == 7
