from __future__ import annotations

import hashlib
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


QUALITY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(QUALITY_ROOT))

from quality_gates import GateInputError  # noqa: E402
from quality_gates.mutation_gate import (  # noqa: E402
    apply_exact_mutation,
    classify_mutation_result,
    run_mutation_profile,
    validate_mutation_profile,
)


def _receipt(
    path: Path,
    *,
    failures: int,
    errors: int,
    failed_test: str | None,
    tests: int = 1,
    skipped: int = 0,
) -> None:
    suite = ET.Element(
        "testsuite",
        tests=str(tests),
        failures=str(failures),
        errors=str(errors),
        skipped=str(skipped),
    )
    case = ET.SubElement(suite, "testcase", classname="test_rules", name="test_guard")
    if failed_test:
        case.set("name", failed_test)
        ET.SubElement(case, "failure", message="assertion failed")
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)


def test_mutation_classification_requires_the_expected_assertion_failure(tmp_path: Path) -> None:
    receipt = tmp_path / "junit.xml"
    _receipt(receipt, failures=1, errors=0, failed_test="test_guard")
    assert classify_mutation_result(1, receipt, "test_guard") == "KILLED"

    _receipt(receipt, failures=0, errors=1, failed_test=None)
    assert classify_mutation_result(1, receipt, "test_guard") == "INVALID"
    _receipt(receipt, failures=2, errors=0, failed_test="test_guard", tests=2)
    assert classify_mutation_result(1, receipt, "test_guard") == "INVALID"
    _receipt(receipt, failures=1, errors=0, failed_test="test_guard", skipped=1)
    assert classify_mutation_result(1, receipt, "test_guard") == "INVALID"
    assert classify_mutation_result(0, receipt, "test_guard") == "SURVIVED"
    assert classify_mutation_result(1, tmp_path / "missing.xml", "test_guard") == "INVALID"


def test_exact_mutation_checks_preimage_and_single_occurrence(tmp_path: Path) -> None:
    source = tmp_path / "rules.py"
    source.write_text("return remaining >= amount\n", encoding="utf-8")
    before = source.read_text(encoding="utf-8")

    result = apply_exact_mutation(
        source,
        before="remaining >= amount",
        after="remaining > amount",
    )
    assert result["beforeSha256"] != result["afterSha256"]
    assert source.read_text(encoding="utf-8") == "return remaining > amount\n"

    source.write_text("x == y or x == y\n", encoding="utf-8")
    with pytest.raises(GateInputError, match="mutation preimage must occur exactly once"):
        apply_exact_mutation(source, before="x == y", after="x != y")


def test_mutation_profile_requires_all_critical_categories_and_safe_argv() -> None:
    mutations = []
    for category in ("state", "identity_evidence", "budget", "fencing", "reconciliation"):
        mutations.append(
            {
                "id": category.replace("_", "-"),
                "category": category,
                "language": "python",
                "sourcePath": "rules.py",
                "preimageSha256": "a" * 64,
                "before": "True",
                "after": "False",
                "workingDirectory": ".",
                "argv": [
                    "{python}",
                    "-m",
                    "pytest",
                    "test_rules.py::test_guard",
                    "--junitxml={receipt}",
                ],
                "expectedTest": "test_guard",
                "timeoutSeconds": 30,
                "snapshotPaths": ["rules.py", "test_rules.py"],
            }
        )
    profile = {"schemaVersion": 1, "mutations": mutations}
    validate_mutation_profile(profile)

    profile["mutations"][0]["argv"] = "pytest test_rules.py"
    with pytest.raises(GateInputError, match="mutation argv must be a non-empty string array"):
        validate_mutation_profile(profile)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("id", "../../escape", "mutation id"),
        ("snapshotPaths", ["."], "snapshot path"),
        ("argv", ["bash", "-c", "pytest"], "locked Python pytest"),
        (
            "argv",
            ["{python}", "-m", "pytest", "test_rules.py::test_state"],
            "JUnit receipt",
        ),
    ],
)
def test_mutation_profile_rejects_path_escape_and_arbitrary_commands(
    field: str, value: object, message: str
) -> None:
    mutations = []
    for category in ("state", "identity_evidence", "budget", "fencing", "reconciliation"):
        mutations.append(
            {
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
                    "test_rules.py::test_state",
                    "--junitxml={receipt}",
                ],
                "expectedTest": "test_state",
                "timeoutSeconds": 30,
                "snapshotPaths": ["rules.py", "tests"],
            }
        )
    mutations[0][field] = value
    with pytest.raises(GateInputError, match=message):
        validate_mutation_profile({"schemaVersion": 1, "mutations": mutations})


def test_mutation_runner_uses_disposable_snapshot_and_kills_expected_assertions(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    source = repository / "rules.py"
    source.write_text(
        "def state(value): return value == 'READY'\n"
        "def identity(value): return value == 'execution-1'\n"
        "def budget(remaining, amount): return remaining >= amount\n"
        "def fence(current, expected): return current == expected\n"
        "def reconcile(total, emitted, retained): return total == emitted + retained\n",
        encoding="utf-8",
    )
    (repository / "test_rules.py").write_text(
        "from rules import budget, fence, identity, reconcile, state\n"
        "def test_state(): assert state('READY') and not state('RUNNING')\n"
        "def test_identity(): assert identity('execution-1') and not identity('execution-2')\n"
        "def test_budget(): assert budget(1, 1) and not budget(0, 1)\n"
        "def test_fence(): assert fence(2, 2) and not fence(2, 3)\n"
        "def test_reconcile(): assert reconcile(3, 1, 2) and not reconcile(4, 1, 2)\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    replacements = {
        "state": ("value == 'READY'", "value != 'READY'", "test_state"),
        "identity_evidence": (
            "value == 'execution-1'",
            "value != 'execution-1'",
            "test_identity",
        ),
        "budget": ("remaining >= amount", "remaining > amount", "test_budget"),
        "fencing": ("current == expected", "current != expected", "test_fence"),
        "reconciliation": (
            "total == emitted + retained",
            "total != emitted + retained",
            "test_reconcile",
        ),
    }
    mutations = []
    for category, (before, after, expected) in replacements.items():
        mutations.append(
            {
                "id": category.replace("_", "-"),
                "category": category,
                "language": "python",
                "sourcePath": "rules.py",
                "preimageSha256": digest,
                "before": before,
                "after": after,
                "workingDirectory": ".",
                "argv": [
                    "{python}",
                    "-m",
                    "pytest",
                    "-q",
                    f"test_rules.py::{expected}",
                    "--junitxml={receipt}",
                ],
                "expectedTest": expected,
                "timeoutSeconds": 30,
                "snapshotPaths": ["rules.py", "test_rules.py"],
            }
        )
    profile = {"schemaVersion": 1, "mutations": mutations}

    runner = repository / "offline-runner"
    runner.write_text("#!/bin/sh\nexec \"$@\"\n", encoding="utf-8")
    runner.chmod(0o700)

    result = run_mutation_profile(
        repository_root=repository,
        profile=profile,
        artifact_root=repository / "artifacts",
        python_runtime=Path(sys.executable),
        offline_runner=runner,
    )

    assert result["passed"] is True
    assert result["summary"] == {
        "KILLED": 5,
        "SURVIVED": 0,
        "INVALID": 0,
        "TIMED_OUT": 0,
    }
    assert source.read_text(encoding="utf-8").startswith("def state(value): return value ==")
    assert not (repository / "artifacts/work").exists()


def test_mutation_runner_requires_isolation_and_does_not_reuse_stale_receipt(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    source = repository / "rules.py"
    source.write_text(
        "def state(value): return value == 'READY'\n"
        "def identity(value): return value == 'execution-1'\n"
        "def budget(remaining, amount): return remaining >= amount\n"
        "def fence(current, expected): return current == expected\n"
        "def reconcile(total, emitted, retained): return total == emitted + retained\n",
        encoding="utf-8",
    )
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    mutations = []
    replacements = {
        "state": ("value == 'READY'", "value != 'READY'"),
        "identity_evidence": ("value == 'execution-1'", "value != 'execution-1'"),
        "budget": ("remaining >= amount", "remaining > amount"),
        "fencing": ("current == expected", "current != expected"),
        "reconciliation": (
            "total == emitted + retained",
            "total != emitted + retained",
        ),
    }
    for category, (before, after) in replacements.items():
        mutations.append(
            {
                "id": category.replace("_", "-"),
                "category": category,
                "language": "python",
                "sourcePath": "rules.py",
                "preimageSha256": digest,
                "before": before,
                "after": after,
                "workingDirectory": ".",
                "argv": [
                    "{python}",
                    "-m",
                    "pytest",
                    "test_rules.py::test_guard",
                    "--junitxml={receipt}",
                ],
                "expectedTest": "test_guard",
                "timeoutSeconds": 30,
                "snapshotPaths": ["rules.py"],
            }
        )
    profile = {"schemaVersion": 1, "mutations": mutations}
    with pytest.raises(GateInputError, match="offline isolation runner"):
        run_mutation_profile(
            repository_root=repository,
            profile=profile,
            artifact_root=repository / "artifacts",
            python_runtime=Path(sys.executable),
            offline_runner=None,
        )

    stale = repository / "artifacts/results/state-junit.xml"
    stale.parent.mkdir(parents=True)
    _receipt(stale, failures=1, errors=0, failed_test="test_guard")
    runner = repository / "offline-runner"
    runner.write_text(
        "#!/bin/sh\n"
        "for argument in \"$@\"; do\n"
        "  case \"$argument\" in\n"
        "    --junitxml=*control*)\n"
        "      receipt=${argument#--junitxml=}\n"
        "      printf '%s\\n' '<testsuite tests=\"1\" failures=\"0\" errors=\"0\" skipped=\"0\"><testcase name=\"test_guard\"/></testsuite>' > \"$receipt\"\n"
        "      exit 0\n"
        "      ;;\n"
        "  esac\n"
        "done\n"
        "exit 1\n",
        encoding="utf-8",
    )
    runner.chmod(0o700)
    result = run_mutation_profile(
        repository_root=repository,
        profile=profile,
        artifact_root=repository / "artifacts",
        python_runtime=Path(sys.executable),
        offline_runner=runner,
    )
    assert result["summary"]["KILLED"] == 0
    assert result["summary"]["INVALID"] == 5
    assert not stale.exists()
