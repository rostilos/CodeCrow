from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


QUALITY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(QUALITY_ROOT))

from quality_gates import GateInputError  # noqa: E402
from quality_gates.changed_coverage import _evaluate_unbound_gate  # noqa: E402


PATH = "python-ecosystem/demo/src/rules.py"
BASE = "89287e1fce55dc9bffeca2b92ce660d8791ae6ac"
INVENTORY_SHA = "f" * 64


def _changes(*, path: str = PATH, lines: list[int] | None = None) -> dict:
    return {
        "schemaVersion": 1,
        "baseCommit": BASE,
        "headCommit": "1" * 40,
        "files": [
            {
                "path": path,
                "status": "modified",
                "correctnessCritical": True,
                "language": "python",
                "changedLines": [1] if lines is None else lines,
            }
        ],
    }


def _report(*, covered: bool = True, branch: bool = True) -> dict:
    return {
        "schemaVersion": 1,
        "adapter": "coveragepy-json",
        "language": "python",
        "module": "python-ecosystem/demo",
        "toolVersion": "7.15.1",
        "sourceInventorySha256": INVENTORY_SHA,
        "branchInstrumentation": branch,
        "files": {
            PATH: {
                "executableLines": [1],
                "coveredLines": [1] if covered else [],
                "branches": {"1": {"covered": 2 if covered else 1, "missed": 0 if covered else 1}},
            }
        },
        "totals": {
            "lines": {"covered": 1 if covered else 0, "total": 1},
            "branches": {"covered": 2 if covered else 1, "total": 2},
        },
    }


def _baseline() -> dict:
    return {
        "schemaVersion": 1,
        "comparisonBase": BASE,
        "domains": {
            "python:python-ecosystem/demo": {
                "lines": {"covered": 1, "total": 1},
                "branches": {"covered": 2, "total": 2},
            }
        },
    }


def _exclusions(entries: list[dict] | None = None) -> dict:
    return {"schemaVersion": 1, "entries": entries or []}


def _valid_exclusion(**overrides: object) -> dict:
    entry = {
        "id": "generated-demo",
        "fileGlob": PATH,
        "reason": "Generated from the reviewed protocol schema.",
        "owner": "quality-owner",
        "reviewer": "independent-reviewer",
        "expiresOn": "2026-08-01",
        "compensatingIntegrationTest": {
            "selector": "tests/test_generated_contract.py::test_generated_contract",
            "executionPolicy": {
                "runner": {"artifact": "tools/offline-runner", "sha256": "a" * 64},
                "runtime": {"artifact": "tools/runtime", "sha256": "a" * 64},
                "argvTemplate": [
                    "tools/offline-runner",
                    "{runtime}",
                    "{selector}",
                ],
            },
            "receipt": {
                "artifact": ".llm-handoff-artifacts/p0-07/test-results/generated-receipt.json"
            },
        },
    }
    integration_override = overrides.pop("compensatingIntegrationTest", None)
    entry.update(overrides)
    if isinstance(integration_override, dict):
        entry["compensatingIntegrationTest"].update(integration_override)
    return entry


def _write_receipt(repository: Path, entry: dict) -> None:
    source = repository / PATH
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = True\n", encoding="utf-8")
    evidence = repository / ".llm-handoff-artifacts/p0-07/test-results"
    evidence.mkdir(parents=True)
    selector = entry["compensatingIntegrationTest"]["selector"]
    expected_test = selector.rsplit("::", 1)[-1]
    junit = evidence / "generated.xml"
    junit.write_text(
        '<testsuite tests="1" failures="0" errors="0" skipped="0">'
        f'<testcase classname="tests.test_generated_contract" name="{expected_test}"/>'
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
    ledger = evidence / "generated-ledger.json"
    ledger.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "live_call_count": 0,
                "simulated_call_count": 0,
                "calls": [],
            }
        ),
        encoding="utf-8",
    )
    manifest = evidence / "generated-receipt.json"
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "selector": selector,
                "headCommit": "1" * 40,
                "changeInventorySha256": hashlib.sha256(
                    json.dumps(
                        _changes(),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest(),
                "source": {
                    "path": PATH,
                    "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                },
                "runner": {
                    "artifact": runner.relative_to(repository).as_posix(),
                    "sha256": hashlib.sha256(runner.read_bytes()).hexdigest(),
                },
                "runtime": {
                    "realPath": runtime.as_posix(),
                    "sha256": hashlib.sha256(runtime.read_bytes()).hexdigest(),
                },
                "argv": [
                    runner.relative_to(repository).as_posix(),
                    runtime.as_posix(),
                    selector,
                ],
                "junit": {
                    "artifact": junit.relative_to(repository).as_posix(),
                    "sha256": hashlib.sha256(junit.read_bytes()).hexdigest(),
                },
                "ledger": {
                    "artifact": ledger.relative_to(repository).as_posix(),
                    "sha256": hashlib.sha256(ledger.read_bytes()).hexdigest(),
                },
            }
        ),
        encoding="utf-8",
    )
    receipt = entry["compensatingIntegrationTest"]["receipt"]
    entry["compensatingIntegrationTest"]["executionPolicy"] = {
        "runner": {
            "artifact": runner.relative_to(repository).as_posix(),
            "sha256": hashlib.sha256(runner.read_bytes()).hexdigest(),
        },
        "runtime": {
            "artifact": runtime.relative_to(repository).as_posix(),
            "sha256": hashlib.sha256(runtime.read_bytes()).hexdigest(),
        },
        "argvTemplate": [
            runner.relative_to(repository).as_posix(),
            "{runtime}",
            "{selector}",
        ],
    }
    receipt["artifact"] = manifest.relative_to(repository).as_posix()


def test_approved_exclusion_is_reported_but_never_counted_as_covered(
    tmp_path: Path,
) -> None:
    exclusion = _valid_exclusion()
    _write_receipt(tmp_path, exclusion)
    result = _evaluate_unbound_gate(
        changes=_changes(),
        reports=[],
        baseline={"schemaVersion": 1, "comparisonBase": BASE, "domains": {}},
        exclusions=_exclusions([exclusion]),
        as_of="2026-07-14",
        repository_root=tmp_path,
    )

    assert result.passed is True
    assert result.excluded_files == (PATH,)
    assert result.changed_lines == {"covered": 0, "total": 0}


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"reviewer": "quality-owner"}, "owner and reviewer must differ"),
        ({"expiresOn": "2026-07-13"}, "coverage exclusion expired"),
        ({"fileGlob": "**"}, "coverage exclusion glob is too broad"),
        (
            {
                "compensatingIntegrationTest": {
                    "selector": "tests/test_generated_contract.py::test_generated_contract",
                    "receipt": {
                        "artifact": ".llm-handoff-artifacts/p0-07/test-results/generated.xml",
                        "exitCode": 1,
                        "status": "failed",
                    },
                }
            },
            "compensating integration test has no passing receipt",
        ),
    ],
)
def test_exclusion_metadata_fails_closed(
    tmp_path: Path, override: dict, message: str
) -> None:
    with pytest.raises(GateInputError, match=message):
        _evaluate_unbound_gate(
            changes=_changes(),
            reports=[],
            baseline={"schemaVersion": 1, "comparisonBase": BASE, "domains": {}},
            exclusions=_exclusions([_valid_exclusion(**override)]),
            as_of="2026-07-14",
            repository_root=tmp_path,
        )


def test_missing_report_and_branch_instrumentation_fail_closed() -> None:
    missing = _evaluate_unbound_gate(
        changes=_changes(),
        reports=[],
        baseline={"schemaVersion": 1, "comparisonBase": BASE, "domains": {}},
        exclusions=_exclusions(),
        as_of="2026-07-14",
    )
    assert missing.failures == (f"{PATH} has no coverage report",)

    no_branch = _evaluate_unbound_gate(
        changes=_changes(),
        reports=[_report(branch=False)],
        baseline=_baseline(),
        exclusions=_exclusions(),
        as_of="2026-07-14",
    )
    assert f"{PATH} lacks branch instrumentation" in no_branch.failures


def test_exact_aggregate_cross_multiplication_rejects_unrounded_regression() -> None:
    report = _report()
    report["files"]["python-ecosystem/demo/src/unchanged.py"] = {
        "executableLines": list(range(2, 1001)),
        "coveredLines": list(range(2, 900)),
        "branches": {"2": {"covered": 897, "missed": 101}},
    }
    report["totals"] = {
        "lines": {"covered": 899, "total": 1000},
        "branches": {"covered": 899, "total": 1000},
    }
    result = _evaluate_unbound_gate(
        changes=_changes(lines=[]),
        reports=[report],
        baseline=_baseline(),
        exclusions=_exclusions(),
        as_of="2026-07-14",
    )

    assert result.failures == (
        "python:python-ecosystem/demo aggregate lines coverage regressed",
        "python:python-ecosystem/demo aggregate branches coverage regressed",
    )


def test_modified_continuation_line_cannot_hide_branch_on_unchanged_origin() -> None:
    report = _report(covered=False)
    report["files"][PATH] = {
        "executableLines": [1, 2],
        "coveredLines": [1, 2],
        "branches": {"1": {"covered": 1, "missed": 1}},
    }
    report["totals"] = {
        "lines": {"covered": 2, "total": 2},
        "branches": {"covered": 1, "total": 2},
    }
    baseline = _baseline()
    baseline["domains"]["python:python-ecosystem/demo"] = report["totals"]

    result = _evaluate_unbound_gate(
        changes=_changes(lines=[2]),
        reports=[report],
        baseline=baseline,
        exclusions=_exclusions(),
        as_of="2026-07-14",
    )

    assert result.changed_lines == {"covered": 1, "total": 1}
    assert result.changed_branches == {"covered": 0, "total": 0}
    assert result.failures == (
        f"{PATH} modified correctness file has 1 uncovered branch(es)",
    )


def test_comparison_base_must_match_frozen_baseline() -> None:
    changes = _changes()
    changes["baseCommit"] = "2" * 40
    with pytest.raises(GateInputError, match="comparison base does not match coverage baseline"):
        _evaluate_unbound_gate(
            changes=changes,
            reports=[_report()],
            baseline=_baseline(),
            exclusions=_exclusions(),
            as_of="2026-07-14",
        )


def test_repository_aggregate_can_repeat_module_files_without_ambiguity() -> None:
    module = _report()
    aggregate = _report()
    aggregate["module"] = "@repository"
    baseline = _baseline()
    baseline["domains"]["python:@repository"] = aggregate["totals"]

    result = _evaluate_unbound_gate(
        changes=_changes(),
        reports=[module, aggregate],
        baseline=baseline,
        exclusions=_exclusions(),
        as_of="2026-07-14",
    )
    assert result.passed is True


def test_new_domain_without_baseline_must_be_fully_covered() -> None:
    report = _report(covered=False)
    report["module"] = "python-ecosystem/new"
    result = _evaluate_unbound_gate(
        changes=_changes(lines=[]),
        reports=[report],
        baseline={"schemaVersion": 1, "comparisonBase": BASE, "domains": {}},
        exclusions=_exclusions(),
        as_of="2026-07-14",
    )
    assert result.failures == (
        f"{PATH} modified correctness file has 1 uncovered branch(es)",
        "python:python-ecosystem/new new domain lines coverage is not 100%",
        "python:python-ecosystem/new new domain branches coverage is not 100%",
    )


def test_forged_normalized_report_cannot_strip_changed_branch_or_fake_totals() -> None:
    report = _report()
    report["files"][PATH]["branches"] = {}
    with pytest.raises(GateInputError, match="totals do not match normalized files"):
        _evaluate_unbound_gate(
            changes=_changes(),
            reports=[report],
            baseline=_baseline(),
            exclusions=_exclusions(),
            as_of="2026-07-14",
        )

    report = _report()
    report["files"][PATH]["coveredLines"] = [2]
    with pytest.raises(GateInputError, match="covered lines must be executable"):
        _evaluate_unbound_gate(
            changes=_changes(),
            reports=[report],
            baseline=_baseline(),
            exclusions=_exclusions(),
            as_of="2026-07-14",
        )


@pytest.mark.parametrize("changed_lines", [[0], [True], [1, 1], [2, 1]])
def test_changed_line_inventory_must_be_positive_unique_and_sorted(
    changed_lines: list[int],
) -> None:
    with pytest.raises(GateInputError, match="changedLines"):
        _evaluate_unbound_gate(
            changes=_changes(lines=changed_lines),
            reports=[_report()],
            baseline=_baseline(),
            exclusions=_exclusions(),
            as_of="2026-07-14",
        )


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"fileGlob": "python-ecosystem/*"}, "coverage exclusion glob is too broad"),
        (
            {"compensatingIntegrationTest": {"selector": "", "receipt": {}}},
            "compensating integration test selector",
        ),
        (
            {
                "compensatingIntegrationTest": {
                    "selector": "tests/test_generated_contract.py::test_generated_contract",
                    "receipt": {"artifact": "outside.json"},
                }
            },
            "receipt artifact is invalid",
        ),
    ],
)
def test_exclusion_cannot_be_a_broad_or_self_attested_bypass(
    tmp_path: Path, override: dict, message: str
) -> None:
    with pytest.raises(GateInputError, match=message):
        _evaluate_unbound_gate(
            changes=_changes(),
            reports=[],
            baseline={"schemaVersion": 1, "comparisonBase": BASE, "domains": {}},
            exclusions=_exclusions([_valid_exclusion(**override)]),
            as_of="2026-07-14",
            repository_root=tmp_path,
        )
