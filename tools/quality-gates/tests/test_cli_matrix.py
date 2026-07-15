from __future__ import annotations

import argparse
import hashlib
import json
import runpy
import sys
from pathlib import Path

import pytest


QUALITY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(QUALITY_ROOT))

from quality_gates import GateInputError  # noqa: E402
from quality_gates import cli  # noqa: E402


def _json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "raw",
    [
        '{"key": 1, "key": 2}',
        '{"key": NaN}',
        '[1, 2]',
        '{broken',
    ],
)
def test_cli_strict_json_input_rejects_ambiguous_values(tmp_path: Path, raw: str) -> None:
    path = tmp_path / "input.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(GateInputError):
        cli._read_json(path)


def test_cli_bound_json_input_rejects_escape_symlink_and_oversize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.json"
    outside.write_text("{}\n", encoding="utf-8")
    with pytest.raises(GateInputError, match="inside the repository"):
        cli._read_json(outside, repository_root=tmp_path)

    target = tmp_path / "target.json"
    target.write_text("{}\n", encoding="utf-8")
    linked = tmp_path / "linked.json"
    linked.symlink_to(target)
    with pytest.raises(GateInputError):
        cli._read_json(linked, repository_root=tmp_path)

    monkeypatch.setattr(cli, "_MAX_JSON_INPUT_BYTES", 1)
    with pytest.raises(GateInputError, match="size limit"):
        cli._read_json(target, repository_root=tmp_path)


def test_cli_atomic_output_refuses_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("preserve", encoding="utf-8")
    output = tmp_path / "output.json"
    output.symlink_to(target)
    with pytest.raises(GateInputError, match="symlink output"):
        cli._write_json(output, {"ok": True})
    assert target.read_text(encoding="utf-8") == "preserve"


def test_cli_atomic_output_cleans_temporary_file_after_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "output.json"

    def fail_replace(self: Path, target: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        cli._write_json(output, {"ok": True})
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"schemaVersion": 1, "domains": []},
        {"schemaVersion": 1, "domains": ["python:z", "python:a"]},
        {"schemaVersion": 1, "domains": ["python:a", "python:a"]},
        {"schemaVersion": 1, "domains": [1]},
    ],
)
def test_domain_policy_fails_closed(tmp_path: Path, value: dict) -> None:
    with pytest.raises(GateInputError, match="domain policy"):
        cli._domain_policy(_json(tmp_path / "policy.json", value))


@pytest.mark.parametrize(
    "entry",
    [
        None,
        {},
        {"module": "one", "reportGroup": "group", "sourceRoot": "../escape"},
        {"module": "one", "reportGroup": "group", "sourceRoot": "/absolute"},
    ],
)
def test_java_module_policy_fails_closed(
    tmp_path: Path, entry: object
) -> None:
    value = {"schemaVersion": 1, "modules": [entry]} if entry is not None else {}
    with pytest.raises(GateInputError, match="Java module policy"):
        cli._java_module_policy(
            _json(tmp_path / "modules.json", value), repository_root=tmp_path
        )


def test_java_module_policy_rejects_non_object_entry(tmp_path: Path) -> None:
    with pytest.raises(GateInputError, match="entry is malformed"):
        cli._java_module_policy(
            _json(
                tmp_path / "modules.json",
                {"schemaVersion": 1, "modules": ["not-an-object"]},
            ),
            repository_root=tmp_path,
        )


def test_cli_dispatches_report_adapters_aggregates_and_snapshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    input_json = _json(tmp_path / "input.json", {"schemaVersion": 1})
    input_xml = tmp_path / "input.xml"
    input_xml.write_text("<report/>", encoding="utf-8")
    module_policy = _json(
        tmp_path / "modules.json",
        {
            "schemaVersion": 1,
            "modules": [
                {"module": "java:one", "reportGroup": "one", "sourceRoot": "src"}
            ],
        },
    )
    report = {"schemaVersion": 1, "module": "one"}

    monkeypatch.setattr(cli, "aggregate_normalized_reports", lambda reports, language: report)
    monkeypatch.setattr(cli, "normalize_jacoco_xml", lambda *args, **kwargs: report)
    monkeypatch.setattr(cli, "normalize_coveragepy_json", lambda *args, **kwargs: report)
    monkeypatch.setattr(
        cli,
        "normalize_jacoco_aggregate_xml",
        lambda *args, **kwargs: (report, [{"schemaVersion": 1, "module": "java:one"}]),
    )
    monkeypatch.setattr(cli, "capture_source_snapshot", lambda *args, **kwargs: report)
    monkeypatch.setattr(cli, "verify_trust_bundle", lambda *args, **kwargs: report)
    monkeypatch.setattr(cli, "create_trust_bundle", lambda *args, **kwargs: report)
    monkeypatch.setattr(
        cli,
        "_resolved_source_inventory",
        lambda *args, **kwargs: {"inventorySha256": "f" * 64},
    )

    commands = [
        [
            "aggregate",
            "--report",
            str(input_json),
            "--language",
            "python",
            "--output",
            str(tmp_path / "aggregate.json"),
        ],
        [
            "normalize-jacoco",
            "--input",
            str(input_xml),
            "--module",
            "one",
            "--source-root",
            str(source),
            "--repository-root",
            str(tmp_path),
            "--tool-version",
            "0.8.11",
            "--source-inventory-sha256",
            "f" * 64,
            "--output",
            str(tmp_path / "jacoco.json"),
        ],
        [
            "normalize-coveragepy",
            "--input",
            str(input_json),
            "--module",
            "one",
            "--source-prefix",
            "src",
            "--repository-root",
            str(tmp_path),
            "--source-inventory-sha256",
            "f" * 64,
            "--output",
            str(tmp_path / "coverage.json"),
        ],
        [
            "normalize-jacoco-aggregate",
            "--input",
            str(input_xml),
            "--module-policy",
            str(module_policy),
            "--repository-root",
            str(tmp_path),
            "--tool-version",
            "0.8.11",
            "--source-inventory-sha256",
            "f" * 64,
            "--aggregate-output",
            str(tmp_path / "java-aggregate.json"),
            "--module-output-root",
            str(tmp_path / "modules"),
        ],
        [
            "capture-source-snapshot",
            "--report",
            str(input_json),
            "--repository-root",
            str(tmp_path),
            "--source-inventory-policy",
            str(input_json),
            "--output",
            str(tmp_path / "snapshot.json"),
        ],
        [
            "verify-trust-bundle",
            "--bundle",
            str(input_json),
            "--bundle-sha256",
            "f" * 64,
            "--repository-root",
            str(tmp_path),
        ],
        [
            "capture-trust-bundle",
            "--repository-root",
            str(tmp_path),
            "--output",
            str(tmp_path / "trust-bundle.json"),
        ],
        [
            "resolve-source-inventory",
            "--policy",
            str(input_json),
            "--repository-root",
            str(tmp_path),
            "--output",
            str(tmp_path / "resolved-inventory.json"),
        ],
    ]
    for command in commands:
        assert cli.main(command) == 0
    assert (tmp_path / "modules/java:one/coverage.json").is_file()

    snapshot = _json(tmp_path / "verify.json", {"schemaVersion": 1})
    digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    observed: list[dict] = []
    monkeypatch.setattr(
        cli,
        "verify_source_snapshot",
        lambda value, repository_root, source_inventory: observed.append(dict(value)),
    )
    assert (
        cli.main(
            [
                "verify-source-snapshot",
                "--snapshot",
                str(snapshot),
                "--snapshot-sha256",
                digest,
                "--repository-root",
                str(tmp_path),
                "--source-inventory-policy",
                str(input_json),
            ]
        )
        == 0
    )
    assert observed == [{"schemaVersion": 1}]
    assert (
        cli.main(
            [
                "verify-source-snapshot",
                "--snapshot",
                str(snapshot),
                "--snapshot-sha256",
                "0" * 64,
                "--repository-root",
                str(tmp_path),
                "--source-inventory-policy",
                str(input_json),
            ]
        )
        == 2
    )


def test_cli_requires_attested_dirty_base_worktree_and_correctness_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _json(tmp_path / "source.json", {})
    calls: list[str] = []
    monkeypatch.setattr(
        cli,
        "load_correctness_policy",
        lambda *args, **kwargs: ({}, "policy/correctness.json", "e" * 64),
    )
    monkeypatch.setattr(
        cli,
        "load_comparison_base_attestation",
        lambda *args, **kwargs: calls.append("attestation")
        or (
            "a" * 40,
            (
                {
                    "path": "attested",
                    "status": "??",
                    "contentSha256": "d" * 64,
                },
            ),
        ),
    )
    monkeypatch.setattr(
        cli,
        "resolve_git_changes",
        lambda *args, **kwargs: {"schemaVersion": 1},
    )
    common = [
        "resolve-changes",
        "--repository-root",
        str(tmp_path),
        "--baseline-manifest-sha256",
        "b" * 64,
        "--correctness-policy",
        str(source),
    ]
    assert cli.main(
        common
        + [
            "--baseline-manifest",
            str(source),
            "--include-worktree",
            "--output",
            str(tmp_path / "m.json"),
        ]
    ) == 2
    assert (
        cli.main(
            common
            + [
                "--base-attestation",
                str(source),
                "--base-attestation-sha256",
                "c" * 64,
                "--include-worktree",
                "--output",
                str(tmp_path / "a.json"),
            ]
        )
        == 0
    )
    assert calls == ["attestation"]
    assert (
        cli.main(
            common
            + ["--base-attestation", str(source), "--output", str(tmp_path / "bad.json")]
        )
        == 2
    )


@pytest.mark.parametrize(("passed", "expected"), [(True, 0), (False, 1)])
def test_cli_runs_mutation_profile_with_pinned_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    passed: bool,
    expected: int,
) -> None:
    profile = _json(tmp_path / "profile.json", {})
    runner = tmp_path / "runner"
    runner.write_text("runner", encoding="utf-8")
    digest = hashlib.sha256(runner.read_bytes()).hexdigest()
    monkeypatch.setattr(
        cli,
        "run_mutation_profile",
        lambda **kwargs: {"schemaVersion": 1, "passed": passed},
    )
    args = [
        "run-mutations",
        "--repository-root",
        str(tmp_path),
        "--profile",
        str(profile),
        "--artifact-root",
        str(tmp_path / "artifacts"),
        "--python-runtime",
        sys.executable,
        "--offline-runner",
        str(runner),
        "--offline-runner-sha256",
        digest,
        "--output",
        str(tmp_path / "result.json"),
    ]
    assert cli.main(args) == expected
    args[args.index(digest)] = "0" * 64
    assert cli.main(args) == 2


def test_cli_reports_input_error_and_module_entrypoint_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing.json"
    assert (
        cli.main(
            [
                "aggregate",
                "--report",
                str(missing),
                "--language",
                "python",
                "--output",
                str(tmp_path / "out.json"),
            ]
        )
        == 2
    )
    assert "quality gate input error" in capsys.readouterr().err
    with pytest.raises(GateInputError, match="unsupported command"):
        cli._dispatch(argparse.Namespace(command="unknown"))

    monkeypatch.setattr(cli, "main", lambda: 7)
    with pytest.raises(SystemExit) as error:
        runpy.run_module("quality_gates.__main__", run_name="__main__")
    assert error.value.code == 7


def test_cli_reports_unreadable_snapshot_and_runner(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing"
    profile = _json(tmp_path / "profile.json", {})
    assert (
        cli.main(
            [
                "verify-source-snapshot",
                "--snapshot",
                str(missing),
                "--snapshot-sha256",
                "0" * 64,
                "--repository-root",
                str(tmp_path),
                "--source-inventory-policy",
                str(profile),
            ]
        )
        == 2
    )
    assert (
        cli.main(
            [
                "run-mutations",
                "--repository-root",
                str(tmp_path),
                "--profile",
                str(profile),
                "--artifact-root",
                str(tmp_path / "artifacts"),
                "--python-runtime",
                sys.executable,
                "--offline-runner",
                str(missing),
                "--offline-runner-sha256",
                "0" * 64,
                "--output",
                str(tmp_path / "result.json"),
            ]
        )
        == 2
    )
    error = capsys.readouterr().err
    assert "source snapshot is unreadable" in error
    assert "offline isolation runner is unreadable" in error


def test_resolve_changes_dispatch_requires_worktree_policy_and_dirty_tuple(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    common = {
        "command": "resolve-changes",
        "baseline_manifest": None,
        "base_attestation_sha256": "a" * 64,
        "include_worktree": True,
        "correctness_policy": tmp_path / "policy.json",
        "base_attestation": tmp_path / "attestation.json",
        "baseline_manifest_sha256": "b" * 64,
        "repository_root": tmp_path,
        "output": tmp_path / "changes.json",
    }
    with pytest.raises(GateInputError, match="exact worktree"):
        cli._dispatch(argparse.Namespace(**{**common, "include_worktree": False}))
    with pytest.raises(GateInputError, match="correctness policy"):
        cli._dispatch(argparse.Namespace(**{**common, "correctness_policy": None}))
    monkeypatch.setattr(
        cli, "load_comparison_base_attestation", lambda *args, **kwargs: "a" * 40
    )
    with pytest.raises(GateInputError, match="dirty state is missing"):
        cli._dispatch(argparse.Namespace(**common))
