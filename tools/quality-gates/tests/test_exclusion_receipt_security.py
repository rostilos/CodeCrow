from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import pytest


QUALITY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(QUALITY_ROOT))

from quality_gates import GateInputError  # noqa: E402
from quality_gates import changed_coverage as gate  # noqa: E402


SELECTOR = "tests/integration/test_generated.py::test_generated_contract"
HEAD = "1" * 40
CHANGE_SHA = "c" * 64
SOURCE_PATH = "python-ecosystem/demo/src/generated.py"


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bundle(repository: Path) -> tuple[dict, dict, dict[str, Path]]:
    source = repository / SOURCE_PATH
    source.parent.mkdir(parents=True)
    source.write_text("GENERATED = True\n", encoding="utf-8")
    evidence = repository / ".llm-handoff-artifacts/p0-07/receipts"
    evidence.mkdir(parents=True)
    junit = evidence / "junit.xml"
    junit.write_text(
        '<testsuite tests="1" failures="0" errors="0" skipped="0">'
        '<testcase classname="tests.integration.test_generated" '
        'name="test_generated_contract"/>'
        "</testsuite>",
        encoding="utf-8",
    )
    runner = repository / "tools/offline-runner"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text("#!/bin/sh\nexec \"$@\"\n", encoding="utf-8")
    runner.chmod(0o755)
    runtime = repository / "runtime"
    runtime.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    runtime.chmod(0o755)
    ledger = evidence / "ledger.json"
    ledger.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "live_call_count": 0,
                "simulated_call_count": 1,
                "calls": [
                    {
                        "boundary": "test_double",
                        "live": False,
                        "operation": "generated.contract",
                        "outcome": "success",
                        "phase": "SIMULATED",
                        "sequence": 1,
                        "simulated": True,
                        "target": "<redacted-target>",
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    manifest_value = {
        "schemaVersion": 1,
        "selector": SELECTOR,
        "headCommit": HEAD,
        "changeInventorySha256": CHANGE_SHA,
        "source": {"path": SOURCE_PATH, "sha256": _digest(source)},
        "runner": {
            "artifact": runner.relative_to(repository).as_posix(),
            "sha256": _digest(runner),
        },
        "runtime": {"realPath": runtime.as_posix(), "sha256": _digest(runtime)},
        "argv": [
            runner.relative_to(repository).as_posix(),
            runtime.as_posix(),
            SELECTOR,
        ],
        "junit": {
            "artifact": junit.relative_to(repository).as_posix(),
            "sha256": _digest(junit),
        },
        "ledger": {
            "artifact": ledger.relative_to(repository).as_posix(),
            "sha256": _digest(ledger),
        },
    }
    manifest = evidence / "receipt.json"
    manifest.write_text(json.dumps(manifest_value, sort_keys=True), encoding="utf-8")
    metadata = {
        "artifact": manifest.relative_to(repository).as_posix(),
    }
    execution_policy = {
        "runner": dict(manifest_value["runner"]),
        "runtime": {
            "artifact": runtime.relative_to(repository).as_posix(),
            "sha256": _digest(runtime),
        },
        "argvTemplate": [
            manifest_value["runner"]["artifact"],
            "{runtime}",
            "{selector}",
        ],
    }
    return metadata, manifest_value, {
        "manifest": manifest,
        "junit": junit,
        "ledger": ledger,
        "runner": runner,
        "runtime": runtime,
        "execution_policy": execution_policy,
    }


def _rewrite_manifest(
    repository: Path, metadata: dict, manifest: dict, path: Path
) -> None:
    path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")


def _rewrite_nested(
    repository: Path,
    metadata: dict,
    manifest: dict,
    paths: dict[str, Path],
    name: str,
    content: str,
) -> None:
    paths[name].write_text(content, encoding="utf-8")
    manifest[name]["sha256"] = _digest(paths[name])
    _rewrite_manifest(repository, metadata, manifest, paths["manifest"])


def test_receipt_manifest_junit_and_zero_live_ledger_are_transitively_verified(
    tmp_path: Path,
) -> None:
    metadata, _, paths = _bundle(tmp_path)
    gate._verify_compensating_receipt(
        repository_root=tmp_path,
        selector=SELECTOR,
        expected_head=HEAD,
        change_inventory_sha256=CHANGE_SHA,
        source_path=SOURCE_PATH,
        metadata=metadata,
        execution_policy=paths["execution_policy"],
    )


def test_evidence_open_rejects_missing_symlink_directory_digest_and_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    metadata, _, paths = _bundle(tmp_path)
    with pytest.raises(GateInputError, match="trusted evidence file"):
        gate._read_evidence_file(
            tmp_path,
            ".llm-handoff-artifacts/p0-07/receipts/missing.json",
            expected_sha256="0" * 64,
            field="receipt",
        )
    with pytest.raises(GateInputError, match="digest mismatch"):
        gate._read_evidence_file(
            tmp_path,
            metadata["artifact"],
            expected_sha256="0" * 64,
            field="receipt",
        )
    with pytest.raises(GateInputError, match="regular file"):
        gate._read_evidence_file(
            tmp_path,
            ".llm-handoff-artifacts/p0-07/receipts",
            expected_sha256="0" * 64,
            field="receipt",
        )
    monkeypatch.setattr(gate, "_MAX_RECEIPT_BYTES", 2)
    with pytest.raises(GateInputError, match="size limit"):
        gate._read_evidence_file(
            tmp_path,
            metadata["artifact"],
            expected_sha256=_digest(paths["manifest"]),
            field="receipt",
        )

    target = paths["manifest"].with_name("target.json")
    paths["manifest"].rename(target)
    paths["manifest"].symlink_to(target)
    with pytest.raises(GateInputError, match="trusted evidence file"):
        gate._read_evidence_file(
            tmp_path,
            metadata["artifact"],
            expected_sha256=_digest(target),
            field="receipt",
        )


@pytest.mark.parametrize(
    "path",
    ["./artifact", "a//b", "a/./b", "a/b/", "a\x00b", "a\nb"],
)
def test_repository_path_identity_rejects_noncanonical_and_control_spellings(
    path: str,
) -> None:
    with pytest.raises(GateInputError, match="repository-relative path"):
        gate._safe_path(path, "artifact")


def test_evidence_rejects_symlinked_repository_root_and_root_runtime(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    metadata, _, _ = _bundle(repository)
    root_link = tmp_path / "repository-link"
    root_link.symlink_to(repository, target_is_directory=True)
    with pytest.raises(GateInputError, match="trusted evidence file"):
        gate._read_evidence_file(
            root_link,
            metadata["artifact"],
            expected_sha256=_digest(repository / metadata["artifact"]),
            field="receipt",
        )
    with pytest.raises(GateInputError, match="runtime identity"):
        gate._read_runtime_identity({"realPath": "/", "sha256": "a" * 64})


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(schemaVersion=2),
        lambda value: value.update(selector="tests/other.py::test_other"),
        lambda value: value.update(headCommit="2" * 40),
        lambda value: value.update(changeInventorySha256="d" * 64),
        lambda value: value.update(argv=["pytest", "different"]),
        lambda value: value.update(extra=True),
        lambda value: value.update(junit={"artifact": "x"}),
    ],
)
def test_receipt_manifest_identity_and_shape_fail_closed(
    tmp_path: Path, mutation
) -> None:
    metadata, manifest, paths = _bundle(tmp_path)
    mutation(manifest)
    _rewrite_manifest(tmp_path, metadata, manifest, paths["manifest"])
    with pytest.raises(GateInputError):
        gate._verify_compensating_receipt(
            repository_root=tmp_path,
            selector=SELECTOR,
            expected_head=HEAD,
            change_inventory_sha256=CHANGE_SHA,
            source_path=SOURCE_PATH,
            metadata=metadata,
            execution_policy=paths["execution_policy"],
        )


@pytest.mark.parametrize(
    "xml",
    [
        "<broken",
        "<report/>",
        '<testsuite tests="1" failures="0" errors="0" skipped="0"/>',
        (
            '<testsuite tests="1" failures="0" errors="0" skipped="0">'
            '<testcase name="test_generated_contract"><failure/></testcase></testsuite>'
        ),
        (
            '<testsuite tests="1" failures="0" errors="0" skipped="0">'
            '<testcase name="test_other"/></testsuite>'
        ),
        (
            '<testsuite tests="1" failures="0" errors="0" skipped="0">'
            '<testcase classname="tests.other" name="test_generated_contract"/>'
            "</testsuite>"
        ),
        (
            '<testsuite xmlns="urn:forged" tests="1" failures="0" errors="0" '
            'skipped="0"><testcase classname="tests.integration.test_generated" '
            'name="test_generated_contract"/></testsuite>'
        ),
        (
            '<testsuite tests="-1" failures="0" errors="0" skipped="0">'
            '<testcase name="test_generated_contract"/></testsuite>'
        ),
        '<!DOCTYPE testsuite><testsuite/>',
    ],
)
def test_junit_is_recomputed_and_bound_to_exact_selector(tmp_path: Path, xml: str) -> None:
    metadata, manifest, paths = _bundle(tmp_path)
    _rewrite_nested(tmp_path, metadata, manifest, paths, "junit", xml)
    with pytest.raises(GateInputError):
        gate._verify_compensating_receipt(
            repository_root=tmp_path,
            selector=SELECTOR,
            expected_head=HEAD,
            change_inventory_sha256=CHANGE_SHA,
            source_path=SOURCE_PATH,
            metadata=metadata,
            execution_policy=paths["execution_policy"],
        )


def test_receipt_binds_current_runner_runtime_and_exact_argv(tmp_path: Path) -> None:
    metadata, manifest, paths = _bundle(tmp_path)
    paths["runner"].write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    with pytest.raises(GateInputError, match="runner digest mismatch"):
        gate._verify_compensating_receipt(
            repository_root=tmp_path,
            selector=SELECTOR,
            expected_head=HEAD,
            change_inventory_sha256=CHANGE_SHA,
            source_path=SOURCE_PATH,
            metadata=metadata,
            execution_policy=paths["execution_policy"],
        )

    metadata, manifest, paths = _bundle(tmp_path / "runtime-case")
    paths["runtime"].chmod(0o644)
    with pytest.raises(GateInputError, match="must be executable"):
        gate._verify_compensating_receipt(
            repository_root=tmp_path / "runtime-case",
            selector=SELECTOR,
            expected_head=HEAD,
            change_inventory_sha256=CHANGE_SHA,
            source_path=SOURCE_PATH,
            metadata=metadata,
            execution_policy=paths["execution_policy"],
        )

    metadata, manifest, paths = _bundle(tmp_path / "argv-case")
    manifest["argv"] = [
        manifest["runner"]["artifact"],
        manifest["runtime"]["realPath"],
        f"--selector={SELECTOR}",
    ]
    _rewrite_manifest(tmp_path / "argv-case", metadata, manifest, paths["manifest"])
    with pytest.raises(GateInputError, match="manifest identity"):
        gate._verify_compensating_receipt(
            repository_root=tmp_path / "argv-case",
            selector=SELECTOR,
            expected_head=HEAD,
            change_inventory_sha256=CHANGE_SHA,
            source_path=SOURCE_PATH,
            metadata=metadata,
            execution_policy=paths["execution_policy"],
        )


def test_junit_selector_binds_java_class_and_method() -> None:
    xml = (
        '<testsuite tests="1" failures="0" errors="0" skipped="0">'
        '<testcase classname="example.GuardIT" name="checksBoundary"/>'
        "</testsuite>"
    ).encode()
    assert gate._junit_counts(xml, selector="example.GuardIT#checksBoundary")["tests"] == 1
    with pytest.raises(GateInputError, match="exact passing selector"):
        gate._junit_counts(xml, selector="example.OtherIT#checksBoundary")


@pytest.mark.parametrize(
    "ledger",
    [
        {},
        {
            "schema_version": "1.0",
            "live_call_count": 1,
            "simulated_call_count": 0,
            "calls": [],
        },
        {
            "schema_version": "1.0",
            "live_call_count": 0,
            "simulated_call_count": 1,
            "calls": [{"live": False}],
        },
    ],
)
def test_external_call_ledger_contract_and_counters_fail_closed(
    tmp_path: Path, ledger: dict
) -> None:
    metadata, manifest, paths = _bundle(tmp_path)
    _rewrite_nested(
        tmp_path,
        metadata,
        manifest,
        paths,
        "ledger",
        json.dumps(ledger),
    )
    with pytest.raises(GateInputError):
        gate._verify_compensating_receipt(
            repository_root=tmp_path,
            selector=SELECTOR,
            expected_head=HEAD,
            change_inventory_sha256=CHANGE_SHA,
            source_path=SOURCE_PATH,
            metadata=metadata,
            execution_policy=paths["execution_policy"],
        )


def test_receipt_is_stale_after_excluded_worktree_source_changes(tmp_path: Path) -> None:
    metadata, _, paths = _bundle(tmp_path)
    (tmp_path / SOURCE_PATH).write_text("GENERATED = False\n", encoding="utf-8")
    with pytest.raises(GateInputError, match="source digest mismatch"):
        gate._verify_compensating_receipt(
            repository_root=tmp_path,
            selector=SELECTOR,
            expected_head=HEAD,
            change_inventory_sha256=CHANGE_SHA,
            source_path=SOURCE_PATH,
            metadata=metadata,
            execution_policy=paths["execution_policy"],
        )


def test_exclusion_matching_is_repository_root_anchored() -> None:
    entry = {"fileGlob": "a/b/c/d.py"}
    assert gate._matching_exclusion("a/b/c/d.py", [entry]) == entry
    assert gate._matching_exclusion("x/y/a/b/c/d.py", [entry]) is None


def test_exclusion_registry_rejects_cross_domain_prefix_glob() -> None:
    entry = {
        "id": "cross-domain",
        "fileGlob": "python-ecosystem/*/src/generated.py",
        "reason": "generated",
        "owner": "one",
        "reviewer": "two",
        "expiresOn": "2026-08-01",
        "compensatingIntegrationTest": {
            "selector": SELECTOR,
            "receipt": {"artifact": ".llm-handoff-artifacts/x.json"},
        },
    }
    with pytest.raises(GateInputError, match="glob is too broad"):
        gate._validate_exclusions(
            {"schemaVersion": 1, "entries": [entry]},
            as_of=date(2026, 7, 14),
            expected_head=HEAD,
            repository_root=None,
        )


def test_exclusion_registry_accepts_an_exact_shallow_repository_file() -> None:
    entry = {
        "id": "workflow",
        "fileGlob": ".github/workflows/offline-tests.yml",
        "reason": "exact non-instrumentable workflow",
        "owner": "one",
        "reviewer": "two",
        "expiresOn": "2026-08-01",
        "compensatingIntegrationTest": {
            "selector": SELECTOR,
            "executionPolicy": {
                "runner": {"artifact": "tools/runner", "sha256": "a" * 64},
                "runtime": {"artifact": "tools/runtime", "sha256": "b" * 64},
                "argvTemplate": ["tools/runner", "{runtime}", "{selector}"],
            },
            "receipt": {"artifact": ".llm-handoff-artifacts/workflow.json"},
        },
    }
    assert gate._validate_exclusions(
        {"schemaVersion": 1, "entries": [entry]},
        as_of=date(2026, 7, 14),
        expected_head=HEAD,
        repository_root=None,
    ) == (entry,)


def test_receipt_json_reference_junit_runtime_and_ledger_residual_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(GateInputError, match="duplicate receipt JSON key"):
        gate._strict_json_object(b'{"a": 1, "a": 2}', "receipt")
    with pytest.raises(GateInputError, match="malformed JSON"):
        gate._strict_json_object(b"\xff", "receipt")
    with pytest.raises(GateInputError, match="JSON object"):
        gate._strict_json_object(b"[]", "receipt")
    with pytest.raises(GateInputError, match="reference is malformed"):
        gate._evidence_reference({"artifact": "a"}, "artifact")
    with pytest.raises(GateInputError, match="must stay under"):
        gate._read_evidence_file(
            tmp_path, "outside.json", expected_sha256="0" * 64, field="artifact"
        )

    suites = (
        '<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0">'
        '<testcase classname="tests.a.Case" name="test_value"/>'
        '</testsuite></testsuites>'
    ).encode()
    assert gate._junit_counts(
        suites, selector="tests/a.py::Case::test_value"
    )["tests"] == 1
    nested = (
        '<testsuite tests="1"><testsuite tests="1">'
        '<testcase classname="x" name="y"/></testsuite></testsuite>'
    ).encode()
    with pytest.raises(GateInputError, match="no test suites"):
        gate._junit_counts(nested, selector="x#y")
    for selector in ("tests/value.txt::test_value", "Class#", "invalid"):
        with pytest.raises(GateInputError, match="selector is malformed"):
            gate._junit_counts(suites, selector=selector)

    with pytest.raises(GateInputError, match="runtime identity is malformed"):
        gate._read_runtime_identity({"realPath": "/bin/sh"})
    runtime = tmp_path / "runtime"
    runtime.write_text("#!/bin/sh\n", encoding="utf-8")
    runtime.chmod(0o755)
    with pytest.raises(GateInputError, match="runtime digest mismatch"):
        gate._read_runtime_identity(
            {"realPath": runtime.as_posix(), "sha256": "0" * 64}
        )
    monkeypatch.setattr(gate, "_MAX_RUNTIME_BYTES", 1)
    with pytest.raises(GateInputError, match="runtime exceeds"):
        gate._read_runtime_identity(
            {"realPath": runtime.as_posix(), "sha256": _digest(runtime)}
        )
    with pytest.raises(GateInputError, match="runtime is not trusted"):
        gate._read_runtime_identity(
            {"realPath": (tmp_path / "missing").as_posix(), "sha256": "0" * 64}
        )

    runtime.chmod(0o644)
    with pytest.raises(GateInputError, match="regular executable"):
        gate._read_runtime_identity(
            {"realPath": runtime.as_posix(), "sha256": _digest(runtime)}
        )

    valid_call = {
        "boundary": "test_double",
        "live": False,
        "operation": "run",
        "outcome": "success",
        "phase": "SIMULATED",
        "sequence": 1,
        "simulated": True,
        "target": "redacted",
    }
    ledger = {
        "schema_version": "1.0",
        "live_call_count": 0,
        "simulated_call_count": 1,
        "calls": [dict(valid_call, boundary="INVALID TOKEN")],
    }
    with pytest.raises(GateInputError, match="call is malformed"):
        gate._validate_zero_live_ledger(json.dumps(ledger).encode())
    ledger["calls"] = [valid_call]
    ledger["simulated_call_count"] = 0
    with pytest.raises(GateInputError, match="does not prove zero live calls"):
        gate._validate_zero_live_ledger(json.dumps(ledger).encode())


def test_approved_runtime_rejects_evidence_paths_and_stat_races(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    metadata, _, paths = _bundle(tmp_path)
    with pytest.raises(GateInputError, match="must be a repository tool"):
        gate._read_approved_runtime(
            tmp_path,
            {"artifact": metadata["artifact"], "sha256": _digest(paths["manifest"])},
        )
    with monkeypatch.context() as scoped:
        def fail_stat(self: Path, *, follow_symlinks: bool = True) -> object:
            raise OSError("simulated replacement")

        scoped.setattr(gate.Path, "stat", fail_stat)
        with pytest.raises(GateInputError, match="runtime is not trusted"):
            gate._read_approved_runtime(
                tmp_path, paths["execution_policy"]["runtime"]
            )


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("execution-policy", "execution policy is malformed"),
        ("runtime-policy", "approved compensating runtime reference is malformed"),
        ("argv-policy", "argv template is malformed"),
        ("runner-evidence", "runner must be a repository tool"),
        ("source-shape", "source identity is malformed"),
        ("source-value", "source identity is malformed"),
    ),
)
def test_compensating_receipt_residual_contract_paths(
    tmp_path: Path, mutation: str, message: str
) -> None:
    metadata, manifest, paths = _bundle(tmp_path)
    policy = paths["execution_policy"]
    if mutation == "execution-policy":
        policy = []
    elif mutation == "runtime-policy":
        policy["runtime"] = []
    elif mutation == "argv-policy":
        policy["argvTemplate"] = ["bad"]
    elif mutation == "runner-evidence":
        evidence_runner = metadata["artifact"]
        manifest["runner"] = {
            "artifact": evidence_runner,
            "sha256": _digest(paths["manifest"]),
        }
        policy["runner"] = dict(manifest["runner"])
        policy["argvTemplate"][0] = evidence_runner
        manifest["argv"][0] = evidence_runner
        _rewrite_manifest(tmp_path, metadata, manifest, paths["manifest"])
    elif mutation == "source-shape":
        manifest["source"] = []
        _rewrite_manifest(tmp_path, metadata, manifest, paths["manifest"])
    elif mutation == "source-value":
        manifest["source"]["sha256"] = "bad"
        _rewrite_manifest(tmp_path, metadata, manifest, paths["manifest"])
    else:  # pragma: no cover - parametrization is exhaustive.
        raise AssertionError(mutation)
    with pytest.raises(GateInputError, match=message):
        gate._verify_compensating_receipt(
            repository_root=tmp_path,
            selector=SELECTOR,
            expected_head=HEAD,
            change_inventory_sha256=CHANGE_SHA,
            source_path=SOURCE_PATH,
            metadata=metadata,
            execution_policy=policy,
        )


def test_exclusion_requires_complete_execution_policy() -> None:
    entry = {
        "id": "one",
        "fileGlob": "python-ecosystem/demo/src/generated.py",
        "reason": "generated",
        "owner": "one",
        "reviewer": "two",
        "expiresOn": "2026-08-01",
        "compensatingIntegrationTest": {
            "selector": SELECTOR,
            "executionPolicy": [],
            "receipt": {"artifact": ".llm-handoff-artifacts/x.json"},
        },
    }
    with pytest.raises(GateInputError, match="execution policy is malformed"):
        gate._validate_exclusions(
            {"schemaVersion": 1, "entries": [entry]},
            as_of=date(2026, 7, 14), expected_head=HEAD, repository_root=None,
        )


def test_receipt_file_and_runtime_close_failures_are_contained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "artifact"
    artifact.write_text("value\n", encoding="utf-8")
    original_close = gate.os.close
    with monkeypatch.context() as scoped:
        def noisy_close(descriptor: int) -> None:
            original_close(descriptor)
            raise OSError("simulated close failure")
        scoped.setattr(gate.os, "close", noisy_close)
        assert gate._read_trusted_repository_file(
            tmp_path,
            "artifact",
            expected_sha256=_digest(artifact),
            field="artifact",
            evidence_only=False,
        ) == b"value\n"

    runtime = tmp_path / "runtime-close"
    runtime.write_text("#!/bin/sh\n", encoding="utf-8")
    runtime.chmod(0o755)
    with monkeypatch.context() as scoped:
        def noisy_close(descriptor: int) -> None:
            original_close(descriptor)
            raise OSError("simulated close failure")
        scoped.setattr(gate.os, "close", noisy_close)
        assert gate._read_runtime_identity(
            {"realPath": runtime.as_posix(), "sha256": _digest(runtime)}
        ) == (runtime.as_posix(), _digest(runtime))

    with pytest.raises(GateInputError, match="reference is malformed"):
        gate._evidence_reference(
            {"artifact": 1, "sha256": "bad"}, "artifact"
        )
    with pytest.raises(GateInputError, match="no test suites"):
        gate._junit_counts(b"<property/>", selector="Class#method")
