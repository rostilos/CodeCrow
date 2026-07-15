from __future__ import annotations

import copy
import hashlib
import io
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest


QUALITY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(QUALITY_ROOT))

from quality_gates import GateInputError  # noqa: E402
from quality_gates import mutation_gate  # noqa: E402


def _mutation(category: str) -> dict[str, Any]:
    expected = f"test_{category}"
    return {
        "id": category.replace("_", "-"),
        "category": category,
        "language": "python",
        "sourcePath": "rules.py",
        "preimageSha256": "a" * 64,
        "before": "True",
        "after": "False",
        "workingDirectory": "tests",
        "argv": [
            "{python}",
            "-m",
            "pytest",
            f"test_rules.py::{expected}",
            "--junitxml={receipt}",
        ],
        "expectedTest": expected,
        "timeoutSeconds": 30,
        "snapshotPaths": ["rules.py", "tests"],
    }


def _profile() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "mutations": [
            _mutation(category) for category in sorted(mutation_gate.REQUIRED_CATEGORIES)
        ],
    }


def _profile_with(field: str, value: Any) -> dict[str, Any]:
    profile = _profile()
    profile["mutations"][0][field] = value
    return profile


@pytest.mark.parametrize("value", [None, "", r"dir\file", "/absolute", "a/../b"])
def test_safe_relative_rejects_every_unsafe_path_form(value: object) -> None:
    with pytest.raises(GateInputError, match="safe repository-relative path"):
        mutation_gate._safe_relative(value, "field")


def test_safe_relative_accepts_a_normalized_repository_path() -> None:
    assert mutation_gate._safe_relative("path/to/file.py", "field") == "path/to/file.py"


@pytest.mark.parametrize(
    ("profile", "message"),
    [
        ({"schemaVersion": 2, "mutations": []}, "schema version 1"),
        ({"schemaVersion": 1, "mutations": "not-a-list"}, "schema version 1"),
        ({"schemaVersion": 1, "mutations": []}, "schema version 1"),
        ({"schemaVersion": 1, "mutations": ["not-an-object"]}, "entry must be an object"),
    ],
)
def test_profile_rejects_invalid_envelope(profile: dict[str, Any], message: str) -> None:
    with pytest.raises(GateInputError, match=message):
        mutation_gate.validate_mutation_profile(profile)


@pytest.mark.parametrize("identifier", [None, "Uppercase", "a" * 65])
def test_profile_rejects_invalid_mutation_identifiers(identifier: object) -> None:
    with pytest.raises(GateInputError, match="id must be unique and path-safe"):
        mutation_gate.validate_mutation_profile(_profile_with("id", identifier))


def test_profile_rejects_duplicate_mutation_identifier() -> None:
    profile = _profile()
    profile["mutations"][1]["id"] = profile["mutations"][0]["id"]
    with pytest.raises(GateInputError, match="id must be unique and path-safe"):
        mutation_gate.validate_mutation_profile(profile)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("category", "unknown", "unsupported mutation category"),
        ("language", "ruby", "language must be java or python"),
        ("snapshotPaths", None, "snapshotPaths must be non-empty"),
        ("snapshotPaths", [], "snapshotPaths must be non-empty"),
        ("snapshotPaths", ["rules.py", "rules.py"], "snapshot paths must be unique"),
        ("snapshotPaths", ["tree", "tree/child"], "snapshot paths cannot overlap"),
        ("preimageSha256", None, "preimageSha256 must be lowercase SHA-256"),
        ("preimageSha256", "a" * 63, "preimageSha256 must be lowercase SHA-256"),
        ("preimageSha256", "A" + "a" * 63, "preimageSha256 must be lowercase SHA-256"),
        ("before", None, "before/after replacement is invalid"),
        ("before", "", "before/after replacement is invalid"),
        ("after", None, "before/after replacement is invalid"),
        ("after", "True", "before/after replacement is invalid"),
        ("argv", None, "argv must be a non-empty string array"),
        ("argv", [], "argv must be a non-empty string array"),
        ("argv", ["{python}", "-m", "pytest", 1], "argv must be a non-empty string array"),
        ("argv", ["{python}", "-m", "pytest", ""], "argv must be a non-empty string array"),
        ("expectedTest", None, "expectedTest must be one safe test name"),
        ("expectedTest", "bad/test", "expectedTest must be one safe test name"),
        ("timeoutSeconds", None, "timeoutSeconds must be between 1 and 600"),
        ("timeoutSeconds", True, "timeoutSeconds must be between 1 and 600"),
        ("timeoutSeconds", 0, "timeoutSeconds must be between 1 and 600"),
        ("timeoutSeconds", 601, "timeoutSeconds must be between 1 and 600"),
    ],
)
def test_profile_rejects_invalid_mutation_fields(
    field: str, value: object, message: str
) -> None:
    with pytest.raises(GateInputError, match=message):
        mutation_gate.validate_mutation_profile(_profile_with(field, value))


def test_profile_accepts_both_languages_and_parameterized_test_name() -> None:
    profile = _profile()
    profile["mutations"][0]["language"] = "java"
    profile["mutations"][0]["expectedTest"] = "test_budget[boundary]"
    profile["mutations"][0]["argv"][3] = "test_rules.py::test_budget[boundary]"
    mutation_gate.validate_mutation_profile(profile)


def test_profile_requires_command_to_select_expected_test() -> None:
    profile = _profile()
    profile["mutations"][0]["argv"][3] = "test_rules.py::test_other"
    with pytest.raises(GateInputError, match="command must select expectedTest"):
        mutation_gate.validate_mutation_profile(profile)


def test_profile_requires_every_category() -> None:
    profile = _profile()
    profile["mutations"].pop()
    with pytest.raises(GateInputError, match="lacks required categories"):
        mutation_gate.validate_mutation_profile(profile)


def test_exact_mutation_rejects_non_file_symlink_and_non_utf8(tmp_path: Path) -> None:
    missing = tmp_path / "missing.py"
    with pytest.raises(GateInputError, match="regular file"):
        mutation_gate.apply_exact_mutation(missing, before="a", after="b")

    target = tmp_path / "target.py"
    target.write_text("a", encoding="utf-8")
    symlink = tmp_path / "link.py"
    symlink.symlink_to(target)
    with pytest.raises(GateInputError, match="regular file"):
        mutation_gate.apply_exact_mutation(symlink, before="a", after="b")

    binary = tmp_path / "binary.py"
    binary.write_bytes(b"\xff")
    with pytest.raises(GateInputError, match="must be UTF-8"):
        mutation_gate.apply_exact_mutation(binary, before="a", after="b")


def test_classification_rejects_malformed_and_empty_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    malformed = tmp_path / "malformed.xml"
    malformed.write_text("<testsuite", encoding="utf-8")
    assert mutation_gate.classify_mutation_result(1, malformed, "test_guard") == "INVALID"

    empty = tmp_path / "empty.xml"
    empty.write_text("<testsuites/>", encoding="utf-8")
    assert mutation_gate.classify_mutation_result(1, empty, "test_guard") == "INVALID"

    monkeypatch.setattr(mutation_gate.ET, "parse", lambda _path: (_ for _ in ()).throw(OSError()))
    assert mutation_gate.classify_mutation_result(1, malformed, "test_guard") == "INVALID"


@pytest.mark.parametrize(
    "attributes",
    [
        'tests="-1" failures="0" errors="0" skipped="0"',
        'tests="bad" failures="0" errors="0" skipped="0"',
    ],
)
def test_classification_rejects_invalid_suite_counters(
    tmp_path: Path, attributes: str
) -> None:
    receipt = tmp_path / "receipt.xml"
    receipt.write_text(f"<testsuite {attributes}/>", encoding="utf-8")
    assert mutation_gate.classify_mutation_result(1, receipt, "test_guard") == "INVALID"


def test_classification_aggregates_suites_and_requires_one_matching_failure(
    tmp_path: Path,
) -> None:
    receipt = tmp_path / "receipt.xml"
    receipt.write_text(
        "<testsuites>"
        '<testsuite tests="0"><testcase name="test_other"><failure/></testcase></testsuite>'
        '<testsuite tests="1" failures="1">'
        '<testcase name="test_guard"/><testcase name="test_guard"><failure/></testcase>'
        "</testsuite>"
        "</testsuites>",
        encoding="utf-8",
    )
    assert mutation_gate.classify_mutation_result(1, receipt, "test_guard") == "KILLED"

    receipt.write_text(
        '<testsuite tests="1" failures="1">'
        '<testcase name="test_other"><failure/></testcase>'
        "</testsuite>",
        encoding="utf-8",
    )
    assert mutation_gate.classify_mutation_result(1, receipt, "test_guard") == "INVALID"


def test_snapshot_copy_accepts_files_and_directories(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    workspace = tmp_path / "workspace"
    (repository / "tree").mkdir(parents=True)
    (repository / "tree/nested.txt").write_text("nested", encoding="utf-8")
    (repository / "file.txt").write_text("file", encoding="utf-8")

    mutation_gate._copy_snapshot(repository, workspace, ["tree", "file.txt"])

    assert (workspace / "tree/nested.txt").read_text(encoding="utf-8") == "nested"
    assert (workspace / "file.txt").read_text(encoding="utf-8") == "file"


def test_snapshot_copy_rejects_missing_and_symlink_sources(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    workspace = tmp_path / "workspace"
    repository.mkdir()
    with pytest.raises(GateInputError, match="missing or a symlink"):
        mutation_gate._copy_snapshot(repository, workspace, ["missing"])

    target = repository / "target"
    target.write_text("target", encoding="utf-8")
    (repository / "link").symlink_to(target)
    with pytest.raises(GateInputError, match="missing or a symlink"):
        mutation_gate._copy_snapshot(repository, workspace, ["link"])


def test_snapshot_copy_rejects_nested_symlink(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    workspace = tmp_path / "workspace"
    tree = repository / "tree"
    tree.mkdir(parents=True)
    (tree / "target").write_text("target", encoding="utf-8")
    (tree / "link").symlink_to(tree / "target")
    with pytest.raises(GateInputError, match="snapshot contains a symlink"):
        mutation_gate._copy_snapshot(repository, workspace, ["tree"])


@pytest.mark.parametrize("relative", ["tree", "file.txt"])
def test_snapshot_copy_rejects_existing_destination(
    tmp_path: Path, relative: str
) -> None:
    repository = tmp_path / "repository"
    workspace = tmp_path / "workspace"
    if relative == "tree":
        (repository / relative).mkdir(parents=True)
        (workspace / relative).mkdir(parents=True)
    else:
        repository.mkdir()
        workspace.mkdir()
        (repository / relative).write_text("source", encoding="utf-8")
        (workspace / relative).write_text("destination", encoding="utf-8")
    with pytest.raises(GateInputError, match="overlapping mutation snapshot path"):
        mutation_gate._copy_snapshot(repository, workspace, [relative])


def test_snapshot_copy_rejects_special_files(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    fifo = repository / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(GateInputError, match="not a regular file/directory"):
        mutation_gate._copy_snapshot(repository, tmp_path / "workspace", ["fifo"])


def test_render_argv_replaces_all_supported_placeholders(tmp_path: Path) -> None:
    result = mutation_gate._render_argv(
        ["{python}", "--root={workspace}", "--junitxml={receipt}"],
        python_runtime=tmp_path / "python",
        workspace=tmp_path / "workspace",
        receipt=tmp_path / "receipt.xml",
    )
    assert result == [
        str(tmp_path / "python"),
        f"--root={tmp_path / 'workspace'}",
        f"--junitxml={tmp_path / 'receipt.xml'}",
    ]


def test_render_argv_rejects_unknown_placeholder(tmp_path: Path) -> None:
    with pytest.raises(GateInputError, match="unsupported mutation command placeholder"):
        mutation_gate._render_argv(
            ["{unknown}"],
            python_runtime=tmp_path / "python",
            workspace=tmp_path / "workspace",
            receipt=tmp_path / "receipt.xml",
        )


def _runner_fixture(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, Any]]:
    repository = tmp_path / "repository"
    repository.mkdir()
    source = repository / "rules.py"
    source.write_text("return True\n", encoding="utf-8")
    runner = repository / "offline-runner"
    runner.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    runner.chmod(0o700)
    profile = {
        "schemaVersion": 1,
        "mutations": [
            {
                "id": "state",
                "category": "state",
                "language": "python",
                "sourcePath": "rules.py",
                "preimageSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "before": "True",
                "after": "False",
                "workingDirectory": ".",
                "argv": [
                    "{python}",
                    "-m",
                    "pytest",
                    "test_rules.py::test_state",
                    "--junitxml={receipt}",
                ],
                "expectedTest": "test_state",
                "timeoutSeconds": 30,
                "snapshotPaths": ["rules.py"],
            }
        ],
    }
    return repository, source, runner, profile


def _run_one(
    repository: Path,
    runner: Path | None,
    profile: dict[str, Any],
    *,
    artifact_root: Path | None = None,
    python_runtime: Path | None = None,
) -> dict[str, Any]:
    return mutation_gate.run_mutation_profile(
        repository_root=repository,
        profile=profile,
        artifact_root=artifact_root or repository / "artifacts",
        python_runtime=python_runtime or Path(sys.executable),
        offline_runner=runner,
    )


def _receipt_from_command(command: list[str]) -> Path:
    argument = next(item for item in command if item.startswith("--junitxml="))
    return Path(argument.removeprefix("--junitxml="))


def _write_clean_control_receipt(command: list[str], expected_test: str = "test_state") -> None:
    _receipt_from_command(command).write_text(
        '<testsuite tests="1" failures="0" errors="0" skipped="0">'
        f'<testcase name="{expected_test}"/>'
        "</testsuite>",
        encoding="utf-8",
    )


@pytest.mark.parametrize("runner_kind", ["none", "missing", "directory", "symlink", "nonexec"])
def test_runner_rejects_every_invalid_isolation_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner_kind: str
) -> None:
    repository, _source, valid_runner, profile = _runner_fixture(tmp_path)
    monkeypatch.setattr(mutation_gate, "validate_mutation_profile", lambda _profile: None)
    runner: Path | None
    if runner_kind == "none":
        runner = None
    elif runner_kind == "missing":
        runner = repository / "missing-runner"
    elif runner_kind == "directory":
        runner = repository / "runner-directory"
        runner.mkdir()
    elif runner_kind == "symlink":
        runner = repository / "runner-link"
        runner.symlink_to(valid_runner)
    else:
        runner = repository / "non-executable-runner"
        runner.write_text("#!/bin/sh\n", encoding="utf-8")
        runner.chmod(0o600)
    with pytest.raises(GateInputError, match="executable offline isolation runner"):
        _run_one(repository, runner, profile)


def test_runner_rejects_missing_python_and_artifacts_outside_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, _source, runner, profile = _runner_fixture(tmp_path)
    monkeypatch.setattr(mutation_gate, "validate_mutation_profile", lambda _profile: None)
    with pytest.raises(GateInputError, match="active regular Python runtime"):
        _run_one(repository, runner, profile, python_runtime=repository / "missing-python")
    with pytest.raises(GateInputError, match="artifacts must stay inside"):
        _run_one(repository, runner, profile, artifact_root=tmp_path / "outside")


@pytest.mark.parametrize("kind", ["artifact", "work", "results"])
def test_runner_rejects_symlinked_artifact_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    repository, _source, runner, profile = _runner_fixture(tmp_path)
    monkeypatch.setattr(mutation_gate, "validate_mutation_profile", lambda _profile: None)
    target = repository / "target"
    target.mkdir()
    artifacts = repository / "artifacts"
    if kind == "artifact":
        artifacts.symlink_to(target, target_is_directory=True)
    else:
        artifacts.mkdir()
        (artifacts / kind).symlink_to(target, target_is_directory=True)
    with pytest.raises(GateInputError, match=f"mutation {kind} root cannot be a symlink"):
        _run_one(repository, runner, profile)


def test_runner_removes_existing_work_and_results_before_failing_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, _source, runner, profile = _runner_fixture(tmp_path)
    monkeypatch.setattr(mutation_gate, "validate_mutation_profile", lambda _profile: None)
    artifacts = repository / "artifacts"
    (artifacts / "work/stale").mkdir(parents=True)
    (artifacts / "results").mkdir()
    (artifacts / "results/stale.xml").write_text("stale", encoding="utf-8")
    profile["mutations"][0]["sourcePath"] = "missing.py"
    with pytest.raises(GateInputError, match="source is missing"):
        _run_one(repository, runner, profile)
    assert not (artifacts / "work").exists()
    assert not (artifacts / "results/stale.xml").exists()


def test_runner_rejects_symlink_source_and_digest_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, source, runner, profile = _runner_fixture(tmp_path)
    monkeypatch.setattr(mutation_gate, "validate_mutation_profile", lambda _profile: None)
    link = repository / "link.py"
    link.symlink_to(source)
    link_profile = copy.deepcopy(profile)
    link_profile["mutations"][0]["sourcePath"] = "link.py"
    with pytest.raises(GateInputError, match="source is missing or a symlink"):
        _run_one(repository, runner, link_profile)

    profile["mutations"][0]["preimageSha256"] = "0" * 64
    with pytest.raises(GateInputError, match="preimage digest mismatch"):
        _run_one(repository, runner, profile)


def test_runner_rejects_missing_working_directory_and_cleans_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, _source, runner, profile = _runner_fixture(tmp_path)
    monkeypatch.setattr(mutation_gate, "validate_mutation_profile", lambda _profile: None)
    profile["mutations"][0]["workingDirectory"] = "missing"
    with pytest.raises(GateInputError, match="working directory is missing"):
        _run_one(repository, runner, profile)
    assert not (repository / "artifacts/work").exists()


@pytest.mark.parametrize("output", [None, "partial text", b"partial bytes\xff"])
def test_runner_records_timeouts_for_all_subprocess_output_forms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output: str | bytes | None,
) -> None:
    repository, _source, runner, profile = _runner_fixture(tmp_path)
    monkeypatch.setattr(mutation_gate, "validate_mutation_profile", lambda _profile: None)

    def time_out(command: list[str], **kwargs: object) -> tuple[int | None, bool]:
        log = Path(kwargs["log"])
        if "control" in log.name:
            _write_clean_control_receipt(command)
            log.write_bytes(b"control passed")
            return 0, False
        encoded = output.encode("utf-8") if isinstance(output, str) else output or b""
        log.write_bytes(encoded)
        return None, True

    monkeypatch.setattr(mutation_gate, "_run_bounded_command", time_out)
    result = _run_one(repository, runner, profile)
    record = result["mutations"][0]
    assert result["passed"] is False
    assert result["summary"]["TIMED_OUT"] == 1
    assert record["status"] == "TIMED_OUT"
    assert record["exitCode"] is None
    expected = output.encode("utf-8") if isinstance(output, str) else output or b""
    assert (repository / record["log"]).read_bytes() == expected
    assert record["receipt"] is None


def test_runner_rejects_any_original_source_modification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, source, runner, profile = _runner_fixture(tmp_path)
    monkeypatch.setattr(mutation_gate, "validate_mutation_profile", lambda _profile: None)

    def alter_original(command: list[str], **kwargs: object) -> tuple[int, bool]:
        log = Path(kwargs["log"])
        if "control" in log.name:
            _write_clean_control_receipt(command)
            log.write_bytes(b"control passed")
            return 0, False
        source.write_text("tampered\n", encoding="utf-8")
        log.write_bytes(b"test output")
        return 1, False

    monkeypatch.setattr(mutation_gate, "_run_bounded_command", alter_original)
    with pytest.raises(GateInputError, match="altered the original source"):
        _run_one(repository, runner, profile)
    assert not (repository / "artifacts/work").exists()


def test_runner_finalizer_tolerates_an_already_removed_work_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, _source, runner, profile = _runner_fixture(tmp_path)
    monkeypatch.setattr(mutation_gate, "validate_mutation_profile", lambda _profile: None)
    def invalid_mutant(command: list[str], **kwargs: object) -> tuple[int, bool]:
        log = Path(kwargs["log"])
        if "control" in log.name:
            _write_clean_control_receipt(command)
            log.write_bytes(b"control passed")
            return 0, False
        log.write_bytes(b"failed")
        return 1, False

    monkeypatch.setattr(mutation_gate, "_run_bounded_command", invalid_mutant)
    real_rmtree = mutation_gate.shutil.rmtree

    def remove_disposable_root(path: Path, *args: object, **kwargs: object) -> None:
        candidate = Path(path)
        if candidate.parent.name == "work":
            real_rmtree(candidate.parent, *args, **kwargs)
        else:
            real_rmtree(candidate, *args, **kwargs)

    monkeypatch.setattr(mutation_gate.shutil, "rmtree", remove_disposable_root)

    result = _run_one(repository, runner, profile)

    assert result["summary"]["INVALID"] == 1
    assert not (repository / "artifacts/work").exists()


def test_control_selector_contract_is_fail_closed(tmp_path: Path) -> None:
    receipt = tmp_path / "control.xml"
    assert not mutation_gate._control_selector_passed(1, receipt, "test_guard")
    assert not mutation_gate._control_selector_passed(0, receipt, "test_guard")

    receipt.write_text(
        '<testsuite tests="2" failures="0" errors="0" skipped="0">'
        '<testcase name="test_guard"/></testsuite>',
        encoding="utf-8",
    )
    assert not mutation_gate._control_selector_passed(0, receipt, "test_guard")
    receipt.write_text(
        '<testsuite tests="1" failures="0" errors="0" skipped="0">'
        '<testcase name="test_other"/></testsuite>',
        encoding="utf-8",
    )
    assert not mutation_gate._control_selector_passed(0, receipt, "test_guard")
    receipt.write_text(
        '<testsuite tests="1" failures="0" errors="0" skipped="0">'
        '<testcase name="test_guard"><failure/></testcase></testsuite>',
        encoding="utf-8",
    )
    assert not mutation_gate._control_selector_passed(0, receipt, "test_guard")
    receipt.write_text(
        '<testsuite tests="1" failures="0" errors="0" skipped="0">'
        '<testcase name="test_guard"/></testsuite>',
        encoding="utf-8",
    )
    assert mutation_gate._control_selector_passed(0, receipt, "test_guard")

    target = tmp_path / "target.xml"
    target.write_text(receipt.read_text(encoding="utf-8"), encoding="utf-8")
    receipt.unlink()
    receipt.symlink_to(target)
    assert not mutation_gate._control_selector_passed(0, receipt, "test_guard")


@pytest.mark.parametrize("control_mode", ["timeout", "red", "missing-receipt"])
def test_runner_rejects_any_non_green_control_selector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, control_mode: str
) -> None:
    repository, _source, runner, profile = _runner_fixture(tmp_path)
    monkeypatch.setattr(mutation_gate, "validate_mutation_profile", lambda _profile: None)
    calls = 0

    def control(command: list[str], **kwargs: object) -> tuple[int | None, bool]:
        nonlocal calls
        calls += 1
        Path(kwargs["log"]).write_bytes(b"control evidence")
        if control_mode == "timeout":
            return None, True
        if control_mode == "red":
            return 1, False
        return 0, False

    monkeypatch.setattr(mutation_gate, "_run_bounded_command", control)
    with pytest.raises(GateInputError, match="control selector did not pass exactly once"):
        _run_one(repository, runner, profile)
    assert calls == 1
    assert (repository / "rules.py").read_text(encoding="utf-8") == "return True\n"


def test_runner_rejects_control_that_modifies_original_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, source, runner, profile = _runner_fixture(tmp_path)
    monkeypatch.setattr(mutation_gate, "validate_mutation_profile", lambda _profile: None)

    def corrupt_control(command: list[str], **kwargs: object) -> tuple[int, bool]:
        _write_clean_control_receipt(command)
        Path(kwargs["log"]).write_bytes(b"control passed")
        source.write_text("tampered by control\n", encoding="utf-8")
        return 0, False

    monkeypatch.setattr(mutation_gate, "_run_bounded_command", corrupt_control)
    with pytest.raises(GateInputError, match="control altered the original source"):
        _run_one(repository, runner, profile)


def test_runner_rejects_control_that_modifies_snapshot_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, _source, runner, profile = _runner_fixture(tmp_path)
    monkeypatch.setattr(mutation_gate, "validate_mutation_profile", lambda _profile: None)

    def corrupt_snapshot(command: list[str], **kwargs: object) -> tuple[int, bool]:
        _write_clean_control_receipt(command)
        Path(kwargs["log"]).write_bytes(b"control passed")
        (Path(kwargs["working_directory"]) / "rules.py").write_text(
            "changed in snapshot\n", encoding="utf-8"
        )
        return 0, False

    monkeypatch.setattr(mutation_gate, "_run_bounded_command", corrupt_snapshot)
    with pytest.raises(GateInputError, match="control altered the snapshot source"):
        _run_one(repository, runner, profile)


def test_runtime_hash_requires_executable_regular_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(GateInputError, match="active regular Python runtime"):
        mutation_gate._runtime_sha256(missing)

    directory = tmp_path / "directory"
    directory.mkdir()
    with pytest.raises(GateInputError, match="active regular Python runtime"):
        mutation_gate._runtime_sha256(directory)

    non_executable = tmp_path / "python"
    non_executable.write_bytes(b"runtime")
    non_executable.chmod(0o600)
    with pytest.raises(GateInputError, match="active regular Python runtime"):
        mutation_gate._runtime_sha256(non_executable)

    non_executable.chmod(0o700)
    assert mutation_gate._runtime_sha256(non_executable) == hashlib.sha256(b"runtime").hexdigest()


def test_runtime_identity_detects_content_replacement(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "python"
    runtime.write_bytes(b"first")
    runtime.chmod(0o700)
    digest = mutation_gate._runtime_sha256(runtime)
    runtime.write_bytes(b"second")
    with pytest.raises(GateInputError, match="identity changed"):
        mutation_gate._verify_runtime_identity(runtime, digest)


def test_runner_rejects_runtime_other_than_active_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, _source, runner, profile = _runner_fixture(tmp_path)
    monkeypatch.setattr(mutation_gate, "validate_mutation_profile", lambda _profile: None)
    other = repository / "other-python"
    other.write_bytes(Path(sys.executable).resolve().read_bytes())
    other.chmod(0o700)
    with pytest.raises(GateInputError, match="active locked Python runtime"):
        _run_one(repository, runner, profile, python_runtime=other)


def test_runner_resolves_runtime_symlink_once_and_ignores_retargeting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, _source, runner, profile = _runner_fixture(tmp_path)
    monkeypatch.setattr(mutation_gate, "validate_mutation_profile", lambda _profile: None)
    runtime_link = repository / "python-link"
    active_runtime = Path(sys.executable).resolve()
    runtime_link.symlink_to(active_runtime)
    captured_commands: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> tuple[int, bool]:
        captured_commands.append(command)
        log = Path(kwargs["log"])
        log.write_bytes(b"evidence")
        if "control" in log.name:
            _write_clean_control_receipt(command)
            runtime_link.unlink()
            runtime_link.symlink_to(repository / "untrusted-python")
            return 0, False
        receipt = _receipt_from_command(command)
        receipt.write_text(
            '<testsuite tests="1" failures="1" errors="0" skipped="0">'
            '<testcase name="test_state"><failure/></testcase></testsuite>',
            encoding="utf-8",
        )
        return 1, False

    monkeypatch.setattr(mutation_gate, "_run_bounded_command", run)
    result = _run_one(repository, runner, profile, python_runtime=runtime_link)
    assert result["passed"] is True
    assert len(captured_commands) == 2
    assert all(command[1] == str(active_runtime) for command in captured_commands)


def test_runner_detects_runtime_identity_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, _source, runner, profile = _runner_fixture(tmp_path)
    monkeypatch.setattr(mutation_gate, "validate_mutation_profile", lambda _profile: None)
    identities = iter(["stable", "stable", "changed", "changed"])
    monkeypatch.setattr(mutation_gate, "_runtime_sha256", lambda _runtime: next(identities))
    with pytest.raises(GateInputError, match="identity changed"):
        _run_one(repository, runner, profile)


def test_mutation_environment_is_exact_and_never_inherits_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JAVA_HOME", "/approved/jdk-17")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "credential")
    monkeypatch.setenv("GITHUB_TOKEN", "credential")
    monkeypatch.setenv("CODECROW_INTERNAL_SECRET", "credential")
    environment = mutation_gate._mutation_environment()
    assert environment == {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": "/tmp",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
    }
    assert not (
        {"AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN", "CODECROW_INTERNAL_SECRET"}
        & set(environment)
    )

    assert "JAVA_HOME" not in environment


def test_bounded_stream_preserves_small_output_and_caps_large_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    small_log = io.BytesIO()
    errors: list[BaseException] = []
    mutation_gate._stream_bounded_output(io.BytesIO(b"small"), small_log, errors)
    assert small_log.getvalue() == b"small"
    assert errors == []

    monkeypatch.setattr(mutation_gate, "MAX_MUTATION_LOG_BYTES", 128)
    large_log = io.BytesIO()
    mutation_gate._stream_bounded_output(io.BytesIO(b"x" * 1_000), large_log, errors)
    assert len(large_log.getvalue()) == 128
    assert large_log.getvalue().endswith(mutation_gate._LOG_TRUNCATION_MARKER)

    payload_limit = 128 - len(mutation_gate._LOG_TRUNCATION_MARKER)

    class ChunkedStream:
        def __init__(self) -> None:
            self.chunks = iter([b"a" * payload_limit, b"b", b""])

        def read(self, _size: int) -> bytes:
            return next(self.chunks)

    chunked_log = io.BytesIO()
    mutation_gate._stream_bounded_output(
        ChunkedStream(), chunked_log, errors  # type: ignore[arg-type]
    )
    assert len(chunked_log.getvalue()) == 128
    assert chunked_log.getvalue().endswith(mutation_gate._LOG_TRUNCATION_MARKER)


def test_bounded_stream_propagates_reader_errors() -> None:
    class BrokenStream:
        def read(self, _size: int) -> bytes:
            raise OSError("broken pipe")

    errors: list[BaseException] = []
    mutation_gate._stream_bounded_output(
        BrokenStream(), io.BytesIO(), errors  # type: ignore[arg-type]
    )
    assert len(errors) == 1
    assert isinstance(errors[0], OSError)


class _FakeProcess:
    def __init__(self, waits: list[object], *, stdout: object = b"") -> None:
        self.pid = 4242
        self.waits = iter(waits)
        self.last_exit_code = 0
        self.stdout = io.BytesIO(stdout) if isinstance(stdout, bytes) else stdout

    def wait(self, timeout: float) -> int:
        value = next(self.waits, self.last_exit_code)
        if isinstance(value, BaseException):
            raise value
        self.last_exit_code = int(value)
        return self.last_exit_code


def test_process_group_termination_escalates_and_tolerates_missing_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(mutation_gate.os, "killpg", lambda pid, sig: calls.append((pid, sig)))
    process = _FakeProcess([subprocess.TimeoutExpired(["cmd"], 1), 1])
    mutation_gate._terminate_process_group(process)  # type: ignore[arg-type]
    assert calls == [(4242, signal.SIGTERM), (4242, signal.SIGKILL)]

    monkeypatch.setattr(
        mutation_gate.os,
        "killpg",
        lambda _pid, _sig: (_ for _ in ()).throw(ProcessLookupError()),
    )
    mutation_gate._signal_process_group(process, signal.SIGTERM)  # type: ignore[arg-type]


def test_process_group_termination_fails_if_group_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mutation_gate.os, "killpg", lambda _pid, _sig: None)
    timeout = subprocess.TimeoutExpired(["cmd"], 1)
    process = _FakeProcess([0, timeout])
    with pytest.raises(GateInputError, match="process group did not terminate"):
        mutation_gate._terminate_process_group(process)  # type: ignore[arg-type]


def test_bounded_command_runs_in_new_session_and_caps_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mutation_gate, "MAX_MUTATION_LOG_BYTES", 128)
    log = tmp_path / "command.log"
    exit_code, timed_out = mutation_gate._run_bounded_command(
        [str(Path(sys.executable).resolve()), "-c", "print('x' * 1000)"],
        working_directory=tmp_path,
        environment=mutation_gate._mutation_environment(),
        timeout_seconds=10,
        log=log,
    )
    assert (exit_code, timed_out) == (0, False)
    assert len(log.read_bytes()) == 128
    assert log.read_bytes().endswith(mutation_gate._LOG_TRUNCATION_MARKER)

    with pytest.raises(GateInputError, match="log must be a new regular file"):
        mutation_gate._run_bounded_command(
            [str(Path(sys.executable).resolve()), "-c", "pass"],
            working_directory=tmp_path,
            environment={},
            timeout_seconds=10,
            log=log,
        )


def test_bounded_command_kills_quiet_descendant_after_normal_parent_exit(
    tmp_path: Path,
) -> None:
    child_pid_file = tmp_path / "child.pid"
    program = (
        "import os, pathlib, time\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    os.close(1)\n"
        "    os.close(2)\n"
        "    time.sleep(60)\n"
        "    os._exit(0)\n"
        f"pathlib.Path({str(child_pid_file)!r}).write_text(str(child))\n"
    )
    exit_code, timed_out = mutation_gate._run_bounded_command(
        [str(Path(sys.executable).resolve()), "-c", program],
        working_directory=tmp_path,
        environment=mutation_gate._mutation_environment(),
        timeout_seconds=5,
        log=tmp_path / "quiet-child.log",
    )
    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 2
    while Path(f"/proc/{child_pid}").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    child_is_gone = not Path(f"/proc/{child_pid}").exists()
    if not child_is_gone:
        os.kill(child_pid, signal.SIGKILL)
    assert (exit_code, timed_out) == (0, False)
    assert child_is_gone, f"quiet descendant survived: {child_pid}"


def test_bounded_command_wraps_spawn_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        mutation_gate.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("spawn failed")),
    )
    with pytest.raises(GateInputError, match="command could not start"):
        mutation_gate._run_bounded_command(
            ["missing"],
            working_directory=tmp_path,
            environment={},
            timeout_seconds=1,
            log=tmp_path / "spawn.log",
        )


def test_bounded_command_timeout_uses_group_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = _FakeProcess([subprocess.TimeoutExpired(["cmd"], 1)], stdout=b"")
    captured: dict[str, object] = {}

    def popen(*_args: object, **kwargs: object) -> _FakeProcess:
        captured.update(kwargs)
        return process

    terminated: list[object] = []
    monkeypatch.setattr(mutation_gate.subprocess, "Popen", popen)
    monkeypatch.setattr(
        mutation_gate, "_terminate_process_group", lambda item: terminated.append(item)
    )
    result = mutation_gate._run_bounded_command(
        ["command"],
        working_directory=tmp_path,
        environment={"ONLY": "SAFE"},
        timeout_seconds=1,
        log=tmp_path / "timeout.log",
    )
    assert result == (None, True)
    assert terminated == [process]
    assert captured["start_new_session"] is True
    assert captured["env"] == {"ONLY": "SAFE"}


def test_bounded_command_propagates_process_group_cleanup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = _FakeProcess([0], stdout=b"")
    monkeypatch.setattr(mutation_gate.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        mutation_gate,
        "_terminate_process_group",
        lambda _process: (_ for _ in ()).throw(GateInputError("cleanup failed")),
    )
    with pytest.raises(GateInputError, match="cleanup failed"):
        mutation_gate._run_bounded_command(
            ["command"],
            working_directory=tmp_path,
            environment={},
            timeout_seconds=1,
            log=tmp_path / "cleanup-failed.log",
        )


def test_bounded_command_rejects_missing_output_pipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = _FakeProcess([], stdout=None)
    terminated: list[object] = []
    monkeypatch.setattr(mutation_gate.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        mutation_gate, "_terminate_process_group", lambda item: terminated.append(item)
    )
    with pytest.raises(GateInputError, match="output pipe is unavailable"):
        mutation_gate._run_bounded_command(
            ["command"],
            working_directory=tmp_path,
            environment={},
            timeout_seconds=1,
            log=tmp_path / "no-pipe.log",
        )
    assert terminated == [process]


@pytest.mark.parametrize("stays_alive", [False, True])
def test_bounded_command_cleans_reader_holding_inherited_pipe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stays_alive: bool,
) -> None:
    process = _FakeProcess([0], stdout=b"")
    terminated: list[object] = []

    class Reader:
        def __init__(self, **_kwargs: object) -> None:
            self.checks = 0

        def start(self) -> None:
            pass

        def join(self, timeout: float) -> None:
            assert timeout == mutation_gate._OUTPUT_READER_JOIN_SECONDS

        def is_alive(self) -> bool:
            self.checks += 1
            return True if self.checks == 1 else stays_alive

    monkeypatch.setattr(mutation_gate.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(mutation_gate.threading, "Thread", Reader)
    monkeypatch.setattr(
        mutation_gate, "_terminate_process_group", lambda item: terminated.append(item)
    )
    if stays_alive:
        with pytest.raises(GateInputError, match="output reader did not terminate"):
            mutation_gate._run_bounded_command(
                ["command"],
                working_directory=tmp_path,
                environment={},
                timeout_seconds=1,
                log=tmp_path / "reader.log",
            )
    else:
        assert mutation_gate._run_bounded_command(
            ["command"],
            working_directory=tmp_path,
            environment={},
            timeout_seconds=1,
            log=tmp_path / "reader.log",
        ) == (0, False)
    assert terminated == [process]


def test_bounded_command_propagates_reader_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = _FakeProcess([0], stdout=b"")

    class FailedReader:
        def __init__(self, *, args: tuple[object, ...], **_kwargs: object) -> None:
            self.errors = args[2]

        def start(self) -> None:
            self.errors.append(OSError("write failed"))

        def join(self, timeout: float) -> None:
            pass

        def is_alive(self) -> bool:
            return False

    monkeypatch.setattr(mutation_gate.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(mutation_gate.threading, "Thread", FailedReader)
    with pytest.raises(GateInputError, match="output could not be recorded"):
        mutation_gate._run_bounded_command(
            ["command"],
            working_directory=tmp_path,
            environment={},
            timeout_seconds=1,
            log=tmp_path / "reader-failed.log",
        )


@pytest.mark.parametrize("entry_kind", ["symlink", "directory", "fifo"])
def test_runner_rejects_nonregular_existing_result_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entry_kind: str,
) -> None:
    repository, _source, runner, profile = _runner_fixture(tmp_path)
    monkeypatch.setattr(mutation_gate, "validate_mutation_profile", lambda _profile: None)
    artifacts = repository / "artifacts"
    artifacts.mkdir()
    result = artifacts / "mutation-results.json"
    if entry_kind == "symlink":
        victim = repository / "victim"
        victim.write_text("must remain", encoding="utf-8")
        result.symlink_to(victim)
    elif entry_kind == "directory":
        result.mkdir()
    else:
        os.mkfifo(result)
    with pytest.raises(GateInputError, match="result target must be a regular file or absent"):
        _run_one(repository, runner, profile)
    if entry_kind == "symlink":
        assert victim.read_text(encoding="utf-8") == "must remain"


def test_result_target_inspection_and_artifact_open_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    descriptor = os.open(artifacts, os.O_RDONLY)
    real_stat = mutation_gate.os.stat

    def fail_stat(path: object, **kwargs: object) -> os.stat_result:
        if kwargs.get("dir_fd") is not None:
            raise PermissionError("denied")
        return real_stat(path, **kwargs)

    monkeypatch.setattr(mutation_gate.os, "stat", fail_stat)
    with pytest.raises(GateInputError, match="could not be inspected"):
        mutation_gate._validate_result_entry(descriptor, "mutation-results.json")
    os.close(descriptor)
    monkeypatch.setattr(
        mutation_gate.os,
        "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError()),
    )
    with pytest.raises(GateInputError, match="artifact root must be a real directory"):
        mutation_gate._open_artifact_directory(artifacts)


def test_atomic_result_replaces_regular_file_and_never_follows_racing_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    result = artifacts / "mutation-results.json"
    result.write_text("old", encoding="utf-8")
    mutation_gate._atomic_write_result(artifacts, {"schemaVersion": 1})
    assert '"schemaVersion": 1' in result.read_text(encoding="utf-8")
    assert not (artifacts / ".mutation-results.json.tmp").exists()

    result.unlink()
    victim = tmp_path / "victim.json"
    victim.write_text("untouched", encoding="utf-8")
    real_replace = mutation_gate.os.replace

    def race_replace(*args: object, **kwargs: object) -> None:
        result.symlink_to(victim)
        real_replace(*args, **kwargs)

    monkeypatch.setattr(mutation_gate.os, "replace", race_replace)
    mutation_gate._atomic_write_result(artifacts, {"safe": True})
    assert victim.read_text(encoding="utf-8") == "untouched"
    assert result.is_file() and not result.is_symlink()


def test_atomic_result_rejects_stale_temporary_and_closes_partial_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    temporary = artifacts / ".mutation-results.json.tmp"
    temporary.write_text("stale", encoding="utf-8")
    with pytest.raises(GateInputError, match="temporary file must be absent"):
        mutation_gate._atomic_write_result(artifacts, {"safe": True})
    temporary.unlink()

    real_fdopen = mutation_gate.os.fdopen
    monkeypatch.setattr(
        mutation_gate.os,
        "fdopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("fdopen failed")),
    )
    with pytest.raises(OSError, match="fdopen failed"):
        mutation_gate._atomic_write_result(artifacts, {"safe": True})
    monkeypatch.setattr(mutation_gate.os, "fdopen", real_fdopen)
    assert not temporary.exists()
