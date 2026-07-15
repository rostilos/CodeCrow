from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path

import pytest


QUALITY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(QUALITY_ROOT))

from quality_gates import changed_coverage, normalized_reports  # noqa: E402


BASE = "a" * 40
HEAD = "b" * 40


def _valid_exclusion() -> dict:
    return {
        "id": "generated-rules",
        "fileGlob": "python-ecosystem/demo/src/rules.py",
        "reason": "Generated executable contract is verified by an integration selector.",
        "owner": "coverage-owner",
        "reviewer": "coverage-reviewer",
        "expiresOn": "2026-08-01",
        "compensatingIntegrationTest": {
            "selector": "tests/test_rules.py::test_generated_rules",
            "executionPolicy": {
                "runner": {
                    "artifact": "tools/offline-harness/bin/run-offline.sh",
                    "sha256": "d" * 64,
                },
                "runtime": {"artifact": "tools/locked-runtime", "sha256": "e" * 64},
                "argvTemplate": [
                    "tools/offline-harness/bin/run-offline.sh",
                    "{runtime}",
                    "{selector}",
                ],
            },
            "receipt": {
                "artifact": ".llm-handoff-artifacts/p0-07/results/generated.json"
            },
        },
    }


def _validate_one_exclusion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict, ...]:
    monkeypatch.setattr(
        changed_coverage,
        "_verify_compensating_receipt",
        lambda **_kwargs: None,
    )
    return changed_coverage._validate_exclusions(
        {"schemaVersion": 1, "entries": [_valid_exclusion()]},
        as_of=date(2026, 7, 14),
        expected_head=HEAD,
        repository_root=tmp_path,
    )


def test_real_receipt_state_accepts_only_one_stable_artifact_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert len(_validate_one_exclusion(tmp_path, monkeypatch)) == 1


def test_real_receipt_artifact_identity_accepts_only_evidence_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    validated = _validate_one_exclusion(tmp_path, monkeypatch)
    receipt = validated[0]["compensatingIntegrationTest"]["receipt"]
    assert receipt["artifact"].startswith(".llm-handoff-artifacts/")


def test_real_ratio_budget_boundary_does_not_regress_at_exact_equality() -> None:
    assert not changed_coverage._ratio_regressed(
        {"covered": 1, "total": 2},
        {"covered": 2, "total": 4},
    )


def test_real_comparison_base_fence_accepts_the_pinned_base() -> None:
    result = changed_coverage._evaluate_unbound_gate(
        changes={
            "schemaVersion": 1,
            "baseCommit": BASE,
            "headCommit": HEAD,
            "files": [],
        },
        reports=[],
        baseline={"schemaVersion": 1, "comparisonBase": BASE, "domains": {}},
        exclusions={"schemaVersion": 1, "entries": []},
        as_of="2026-07-14",
    )
    assert result.passed


def test_real_jacoco_aggregate_declared_totals_reconcile_with_project_groups(
    tmp_path: Path,
) -> None:
    source = tmp_path / "java-ecosystem/libs/demo/src/main/java/example/Rules.java"
    source.parent.mkdir(parents=True)
    source.write_text("package example; class Rules {}\n", encoding="utf-8")
    report_path = tmp_path / "aggregate.xml"
    report_path.write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<!DOCTYPE report PUBLIC "-//JACOCO//DTD Report 1.1//EN" "report.dtd">'
        '<report name="aggregate"><group name="demo"><package name="example">'
        '<sourcefile name="Rules.java"><line nr="1" mi="0" ci="1" mb="0" cb="0"/>'
        '<counter type="LINE" missed="0" covered="1"/>'
        '<counter type="BRANCH" missed="0" covered="0"/>'
        '</sourcefile></package><counter type="LINE" missed="0" covered="1"/>'
        '<counter type="BRANCH" missed="0" covered="0"/></group>'
        '<counter type="LINE" missed="0" covered="1"/>'
        '<counter type="BRANCH" missed="0" covered="0"/></report>',
        encoding="utf-8",
    )

    aggregate, modules = normalized_reports.normalize_jacoco_aggregate_xml(
        report_path,
        module_groups={
            "java-ecosystem/libs/demo": ("demo", source.parents[1]),
        },
        repository_root=tmp_path,
        tool_version="0.8.11",
        source_inventory_sha256="f" * 64,
    )
    assert aggregate["totals"] == modules[0]["totals"]


def test_real_mutation_profile_is_bound_to_exact_production_preimages() -> None:
    profile = json.loads(
        (QUALITY_ROOT / "policy/mutation-profile-v1.json").read_text(encoding="utf-8")
    )
    mutations = profile["mutations"]
    assert [entry["id"] for entry in mutations] == [
        "receipt-state-guard",
        "receipt-artifact-identity-guard",
        "coverage-ratio-boundary-guard",
        "comparison-base-fence",
        "jacoco-aggregate-reconciliation",
    ]
    assert {entry["category"] for entry in mutations} == {
        "state",
        "identity_evidence",
        "budget",
        "fencing",
        "reconciliation",
    }
    for entry in mutations:
        source = QUALITY_ROOT.parents[1] / entry["sourcePath"]
        raw = source.read_bytes()
        text = raw.decode("utf-8")
        assert hashlib.sha256(raw).hexdigest() == entry["preimageSha256"]
        assert text.count(entry["before"]) == 1
        assert entry["after"] not in text
        assert entry["argv"][:3] == ["{python}", "-m", "pytest"]
        selectors = [
            argument
            for argument in entry["argv"]
            if argument.endswith(f"::{entry['expectedTest']}")
        ]
        assert selectors == [
            f"tests/test_real_mutation_contracts.py::{entry['expectedTest']}"
        ]
