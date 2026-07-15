from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


QUALITY_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(QUALITY_ROOT))

from quality_gates import cli as cli_module  # noqa: E402
from quality_gates.changed_coverage import GateInputError, GateResult  # noqa: E402
from quality_gates.cli import main  # noqa: E402
from quality_gates.source_inventory import load_and_resolve_source_inventory  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_evaluate_bundle(
    tmp_path: Path,
) -> tuple[list[str], dict[str, Path], dict, dict]:
    paths = {
        "changes": tmp_path / "changes.json",
        "report": tmp_path / "report.json",
        "baseline": tmp_path / "baseline.json",
        "exclusions": tmp_path / "exclusions.json",
        "source_policy": tmp_path / "source-policy.json",
        "pinned_inventory": tmp_path / "pre-test-inventory.json",
        "correctness": tmp_path / "correctness.json",
        "attestation": tmp_path / "attestation.json",
        "output": tmp_path / "result.json",
    }
    fixture_names = {
        "changes": "changes-one-correctness-branch-v1.json",
        "report": "normalized-java-high-aggregate-missed-branch-v1.json",
        "baseline": "coverage-baseline-v1.json",
        "exclusions": "empty-exclusions-v1.json",
    }
    for key, fixture_name in fixture_names.items():
        paths[key].write_bytes((FIXTURES / fixture_name).read_bytes())
    provided = json.loads(paths["changes"].read_text(encoding="utf-8"))

    paths["source_policy"].write_text('{"schemaVersion": 1}\n', encoding="utf-8")
    paths["correctness"].write_text('{"schemaVersion": 1}\n', encoding="utf-8")
    paths["attestation"].write_text('{"schemaVersion": 1}\n', encoding="utf-8")
    inventory = {
        "epoch": "stable",
        "policySha256": _sha256(paths["source_policy"]),
    }
    paths["pinned_inventory"].write_text(
        json.dumps(inventory, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    arguments = [
        "evaluate",
        "--changes", str(paths["changes"]),
        "--report", str(paths["report"]),
        "--baseline", str(paths["baseline"]),
        "--exclusions", str(paths["exclusions"]),
        "--as-of", "2026-07-14",
        "--repository-root", str(tmp_path),
        "--source-inventory-policy", str(paths["source_policy"]),
        "--pinned-source-inventory", str(paths["pinned_inventory"]),
        "--pinned-source-inventory-artifact-sha256",
        _sha256(paths["pinned_inventory"]),
        "--correctness-policy", str(paths["correctness"]),
        "--base-attestation", str(paths["attestation"]),
        "--base-attestation-sha256", _sha256(paths["attestation"]),
        "--baseline-manifest-sha256", "d" * 64,
        "--output", str(paths["output"]),
    ]
    return arguments, paths, inventory, provided


def _stub_evaluate_prerequisites(
    monkeypatch: pytest.MonkeyPatch,
    *,
    paths: dict[str, Path],
    inventory: dict,
    provided: dict,
) -> None:
    monkeypatch.setattr(cli_module, "validate_source_inventory", lambda value: {})
    monkeypatch.setattr(cli_module, "source_inventory_digest", lambda value: "f" * 64)
    monkeypatch.setattr(
        cli_module, "_resolved_source_inventory", lambda *args, **kwargs: inventory
    )
    monkeypatch.setattr(
        cli_module,
        "load_correctness_policy",
        lambda *args, **kwargs: (
            {},
            "correctness.json",
            _sha256(paths["correctness"]),
        ),
    )
    monkeypatch.setattr(
        cli_module,
        "load_comparison_base_attestation",
        lambda *args, **kwargs: (
            provided["baseCommit"],
            ({"path": "prior", "status": "??", "contentSha256": "b" * 64},),
        ),
    )
    monkeypatch.setattr(
        cli_module, "resolve_git_changes", lambda *args, **kwargs: provided
    )
    monkeypatch.setattr(
        cli_module,
        "evaluate_gate",
        lambda **kwargs: GateResult(True, {}, {}, ()),
    )


def _set_argument(arguments: list[str], flag: str, value: str) -> None:
    arguments[arguments.index(flag) + 1] = value


def test_cli_evaluate_writes_machine_readable_failure_and_returns_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments, paths, inventory, provided = _write_evaluate_bundle(tmp_path)
    _stub_evaluate_prerequisites(
        monkeypatch, paths=paths, inventory=inventory, provided=provided
    )
    monkeypatch.setattr(
        cli_module,
        "evaluate_gate",
        lambda **kwargs: GateResult(
            passed=False,
            changed_lines={"covered": 1, "total": 1},
            changed_branches={"covered": 1, "total": 2},
            failures=("uncovered",),
        ),
    )
    exit_code = main(arguments)

    assert exit_code == 1
    result = json.loads(paths["output"].read_text(encoding="utf-8"))
    assert result["passed"] is False
    assert result["changedBranches"] == {"covered": 1, "total": 2}
    provenance = result["provenance"]
    assert set(provenance) == {
        "sourceInventorySha256",
        "sourceInventoryPolicySha256",
        "pinnedSourceInventoryArtifactSha256",
        "changeInventorySha256",
        "changesArtifactSha256",
        "baselineArtifactSha256",
        "exclusionsArtifactSha256",
        "compensatingReceipts",
        "reportArtifactSha256",
        "correctnessPolicySha256",
        "baseAttestationSha256",
        "baselineManifestSha256",
    }
    assert provenance["sourceInventorySha256"] == "f" * 64
    assert provenance["sourceInventoryPolicySha256"] == _sha256(paths["source_policy"])
    assert provenance["pinnedSourceInventoryArtifactSha256"] == _sha256(
        paths["pinned_inventory"]
    )
    assert provenance["changesArtifactSha256"] == _sha256(paths["changes"])
    assert provenance["baselineArtifactSha256"] == _sha256(paths["baseline"])
    assert provenance["exclusionsArtifactSha256"] == _sha256(paths["exclusions"])
    assert provenance["compensatingReceipts"] == []
    assert provenance["reportArtifactSha256"] == [_sha256(paths["report"])]
    assert provenance["correctnessPolicySha256"] == _sha256(paths["correctness"])
    assert provenance["baseAttestationSha256"] == _sha256(paths["attestation"])
    assert provenance["baselineManifestSha256"] == "d" * 64
    assert provenance["changeInventorySha256"] == hashlib.sha256(
        json.dumps(
            provided,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def test_cli_evaluate_requires_dirty_state_tuple(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments, paths, inventory, provided = _write_evaluate_bundle(tmp_path)
    _stub_evaluate_prerequisites(
        monkeypatch, paths=paths, inventory=inventory, provided=provided
    )
    monkeypatch.setattr(
        cli_module,
        "load_comparison_base_attestation",
        lambda *args, **kwargs: provided["baseCommit"],
    )
    assert main(arguments) == 2


def test_cli_provenance_preserves_report_argument_digest_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments, paths, inventory, provided = _write_evaluate_bundle(tmp_path)
    second_report = tmp_path / "report-second.json"
    second_report.write_bytes(paths["report"].read_bytes() + b" \n")
    report_value_index = arguments.index("--report") + 1
    arguments[report_value_index + 1:report_value_index + 1] = [
        "--report",
        str(second_report),
    ]
    _stub_evaluate_prerequisites(
        monkeypatch, paths=paths, inventory=inventory, provided=provided
    )

    assert main(arguments) == 0
    result = json.loads(paths["output"].read_text(encoding="utf-8"))
    assert result["provenance"]["reportArtifactSha256"] == [
        _sha256(paths["report"]),
        _sha256(second_report),
    ]


def test_cli_binds_qualified_compensating_receipts_into_final_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments, paths, inventory, provided = _write_evaluate_bundle(tmp_path)
    _stub_evaluate_prerequisites(
        monkeypatch, paths=paths, inventory=inventory, provided=provided
    )
    receipt = tmp_path / ".llm-handoff-artifacts/p0-07/receipts/config.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text('{"schemaVersion": 1}\n', encoding="utf-8")
    reference = {
        "artifact": ".llm-handoff-artifacts/p0-07/receipts/config.json",
        "sha256": _sha256(receipt),
    }
    monkeypatch.setattr(
        cli_module,
        "evaluate_gate",
        lambda **kwargs: GateResult(
            True, {}, {}, (), compensating_receipts=(reference,)
        ),
    )
    observed: list[tuple[Path, str, str]] = []

    def capture(bindings: list[tuple[Path, str, str]], **kwargs: object) -> None:
        observed.extend(bindings)

    monkeypatch.setattr(cli_module, "_revalidate_bound_inputs", capture)

    assert main(arguments) == 0
    result = json.loads(paths["output"].read_text(encoding="utf-8"))
    assert result["provenance"]["compensatingReceipts"] == [reference]
    assert (receipt, reference["sha256"], "compensating receipt 0") in observed


@pytest.mark.parametrize(
    "failure",
    [
        "malformed-provenance",
        "malformed-attestation-provenance",
        "malformed-manifest-provenance",
        "pinned-artifact",
        "pinned-semantic",
        "source-policy",
        "correctness-policy",
        "base-attestation",
    ],
)
def test_cli_evaluate_rejects_every_preflight_provenance_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    arguments, paths, inventory, provided = _write_evaluate_bundle(tmp_path)
    _stub_evaluate_prerequisites(
        monkeypatch, paths=paths, inventory=inventory, provided=provided
    )
    if failure == "malformed-provenance":
        _set_argument(
            arguments, "--pinned-source-inventory-artifact-sha256", "bad"
        )
    elif failure == "malformed-attestation-provenance":
        _set_argument(arguments, "--base-attestation-sha256", "bad")
    elif failure == "malformed-manifest-provenance":
        _set_argument(arguments, "--baseline-manifest-sha256", "bad")
    elif failure == "pinned-artifact":
        _set_argument(
            arguments, "--pinned-source-inventory-artifact-sha256", "0" * 64
        )
    elif failure == "pinned-semantic":
        monkeypatch.setattr(
            cli_module,
            "_resolved_source_inventory",
            lambda *args, **kwargs: {**inventory, "epoch": "post-test"},
        )
    elif failure == "source-policy":
        inventory["policySha256"] = "0" * 64
        paths["pinned_inventory"].write_text(
            json.dumps(inventory, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _set_argument(
            arguments,
            "--pinned-source-inventory-artifact-sha256",
            _sha256(paths["pinned_inventory"]),
        )
    elif failure == "correctness-policy":
        monkeypatch.setattr(
            cli_module,
            "load_correctness_policy",
            lambda *args, **kwargs: ({}, "correctness.json", "0" * 64),
        )
    else:
        _set_argument(arguments, "--base-attestation-sha256", "0" * 64)

    assert main(arguments) == 2
    assert not paths["output"].exists()


def test_cli_canonical_input_digest_rejects_unserializable_values() -> None:
    with pytest.raises(GateInputError, match="cannot be canonically hashed"):
        cli_module._canonical_json_sha256(
            {"unserializable": object()},
            field="test input",
        )


@pytest.mark.parametrize("drift", ["dirty", "critical-config"])
def test_cli_evaluate_rechecks_complete_change_inventory_after_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str
) -> None:
    arguments, paths, inventory, provided = _write_evaluate_bundle(tmp_path)
    _stub_evaluate_prerequisites(
        monkeypatch, paths=paths, inventory=inventory, provided=provided
    )
    calls = 0

    def resolve(*args: object, **kwargs: object) -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            return provided
        if drift == "dirty":
            raise GateInputError("comparison-base dirty entry drifted: protected")
        changed = json.loads(json.dumps(provided))
        changed["files"].append(
            {
                "path": "deployment/new-config.yml",
                "status": "added",
                "correctnessCritical": True,
                "language": None,
                "changedLines": [],
            }
        )
        return changed

    monkeypatch.setattr(cli_module, "resolve_git_changes", resolve)
    assert main(arguments) == 2
    assert calls == 2
    assert not paths["output"].exists()


def test_cli_evaluate_rejects_source_inventory_drift_after_gate_without_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments, paths, inventory, provided = _write_evaluate_bundle(tmp_path)
    _stub_evaluate_prerequisites(
        monkeypatch, paths=paths, inventory=inventory, provided=provided
    )
    calls = 0

    def resolve_inventory(*args: object, **kwargs: object) -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            return inventory
        return {**inventory, "epoch": "changed-after-tests"}

    monkeypatch.setattr(cli_module, "_resolved_source_inventory", resolve_inventory)
    assert main(arguments) == 2
    assert calls == 2
    assert not paths["output"].exists()


@pytest.mark.parametrize("drift", ["head", "file-content"])
def test_cli_evaluate_rejects_a_stale_full_git_inventory_before_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str
) -> None:
    arguments, paths, inventory, provided = _write_evaluate_bundle(tmp_path)
    _stub_evaluate_prerequisites(
        monkeypatch, paths=paths, inventory=inventory, provided=provided
    )
    current = json.loads(json.dumps(provided))
    if drift == "head":
        current["headCommit"] = "2" * 40
    else:
        current["files"][0]["changedLines"] = [11]
    called = False

    def must_not_evaluate(**kwargs: object) -> GateResult:
        nonlocal called
        called = True
        return GateResult(True, {}, {}, ())

    monkeypatch.setattr(
        cli_module, "resolve_git_changes", lambda *args, **kwargs: current
    )
    monkeypatch.setattr(cli_module, "evaluate_gate", must_not_evaluate)
    assert main(arguments) == 2
    assert called is False
    assert not paths["output"].exists()


@pytest.mark.parametrize(
    "artifact",
    [
        "pinned_inventory",
        "source_policy",
        "correctness",
        "attestation",
        "changes",
        "baseline",
        "exclusions",
        "report",
    ],
)
def test_cli_evaluate_rejects_every_bound_input_swap_after_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifact: str
) -> None:
    arguments, paths, inventory, provided = _write_evaluate_bundle(tmp_path)
    _stub_evaluate_prerequisites(
        monkeypatch, paths=paths, inventory=inventory, provided=provided
    )

    def swap_after_evaluation(**kwargs: object) -> GateResult:
        path = paths[artifact]
        path.write_bytes(path.read_bytes() + b" ")
        return GateResult(True, {}, {}, ())

    monkeypatch.setattr(cli_module, "evaluate_gate", swap_after_evaluation)
    assert main(arguments) == 2
    assert not paths["output"].exists()


def test_cli_evaluate_rejects_post_gate_report_source_inventory_epoch_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments, paths, inventory, provided = _write_evaluate_bundle(tmp_path)
    _stub_evaluate_prerequisites(
        monkeypatch, paths=paths, inventory=inventory, provided=provided
    )

    def rewrite_report_epoch(**kwargs: object) -> GateResult:
        report = json.loads(paths["report"].read_text(encoding="utf-8"))
        report["sourceInventorySha256"] = "e" * 64
        paths["report"].write_text(json.dumps(report) + "\n", encoding="utf-8")
        return GateResult(True, {}, {}, ())

    monkeypatch.setattr(cli_module, "evaluate_gate", rewrite_report_epoch)
    assert main(arguments) == 2
    assert not paths["output"].exists()


def test_cli_capture_baseline_writes_sorted_domains(tmp_path: Path) -> None:
    report = json.loads(
        (FIXTURES / "normalized-java-high-aggregate-missed-branch-v1.json").read_text(
            encoding="utf-8"
        )
    )
    report_path = tmp_path / "report.json"
    source_root = tmp_path / "java-ecosystem/libs/example/src/main/java"
    for relative_path in report["files"]:
        source = tmp_path / relative_path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("class Example {}\n", encoding="utf-8")
    inventory_policy = tmp_path / "source-inventory.json"
    inventory_policy.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "roots": [
                    {
                        "language": "java",
                        "module": "libs/example",
                        "root": source_root.relative_to(tmp_path).as_posix(),
                        "suffix": ".java",
                    }
                ],
                "files": [],
                "excludedSourceTrees": [],
                "nonExecutableSources": [],
            }
        ),
        encoding="utf-8",
    )
    inventory = load_and_resolve_source_inventory(
        inventory_policy, repository_root=tmp_path
    )
    report["sourceInventorySha256"] = inventory["inventorySha256"]
    report_path.write_text(json.dumps(report), encoding="utf-8")
    aggregate = dict(report)
    aggregate["module"] = "@repository"
    aggregate_path = tmp_path / "aggregate.json"
    aggregate_path.write_text(json.dumps(aggregate), encoding="utf-8")
    policy_path = tmp_path / "domains.json"
    policy_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "domains": ["java:@repository", "java:libs/example"],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "baseline.json"

    assert (
        main(
            [
                "capture-baseline",
                "--report",
                str(report_path),
                "--report",
                str(aggregate_path),
                "--comparison-base",
                "8" * 40,
                "--source-snapshot-sha256",
                "a" * 64,
                "--domain-policy",
                str(policy_path),
                "--repository-root",
                str(tmp_path),
                "--source-inventory-policy",
                str(inventory_policy),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    baseline = json.loads(output.read_text(encoding="utf-8"))
    assert list(baseline["domains"]) == ["java:@repository", "java:libs/example"]
