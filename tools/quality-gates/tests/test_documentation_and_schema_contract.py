from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


QUALITY_ROOT = Path(__file__).resolve().parents[1]
POLICY = QUALITY_ROOT / "policy"
SCHEMA = QUALITY_ROOT / "schema"


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_versioned_schemas_are_strict_draft_2020_12_documents() -> None:
    names = {
        "normalized-coverage-v1.schema.json",
        "coverage-exclusions-v1.schema.json",
        "coverage-baseline-v1.schema.json",
        "mutation-profile-v1.schema.json",
        "source-snapshot-v1.schema.json",
        "source-inventory-v1.schema.json",
        "compensating-receipt-v1.schema.json",
        "gate-result-v1.schema.json",
        "trust-bundle-v1.schema.json",
    }
    assert {path.name for path in SCHEMA.glob("*.schema.json")} == names
    for name in names:
        schema = _json(SCHEMA / name)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"].endswith(name)
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert schema["required"]
        assert schema["properties"]["schemaVersion"] == {"const": 1}

        for definition in schema.get("$defs", {}).values():
            if definition.get("type") == "object":
                assert definition.get("additionalProperties") is False
        for pattern in _patterns(schema):
            re.compile(pattern)


def test_coverage_baseline_schema_requires_the_source_inventory_contract() -> None:
    schema = _json(SCHEMA / "coverage-baseline-v1.schema.json")
    assert set(schema["required"]) == {
        "schemaVersion",
        "comparisonBase",
        "sourceSnapshotSha256",
        "sourceInventoryPolicyPath",
        "sourceInventoryPolicySha256",
        "files",
        "domains",
    }


def test_source_snapshot_schema_requires_the_source_inventory_contract() -> None:
    schema = _json(SCHEMA / "source-snapshot-v1.schema.json")
    assert set(schema["required"]) == {
        "schemaVersion",
        "sourceInventorySha256",
        "sourceInventoryPolicyPath",
        "sourceInventoryPolicySha256",
        "files",
    }


def _patterns(value: object) -> list[str]:
    if isinstance(value, dict):
        return [
            item
            for key, nested in value.items()
            for item in ([nested] if key == "pattern" else _patterns(nested))
        ]
    if isinstance(value, list):
        return [item for nested in value for item in _patterns(nested)]
    return []


def test_frozen_baseline_matches_domain_policy_and_source_snapshot() -> None:
    baseline = _json(POLICY / "coverage-baseline-v1.json")
    domains = _json(POLICY / "coverage-domains-v1.json")["domains"]
    snapshot_path = POLICY / "source-snapshot-v1.json"
    snapshot = _json(snapshot_path)

    assert baseline["schemaVersion"] == 1
    assert baseline["comparisonBase"] == "89287e1fce55dc9bffeca2b92ce660d8791ae6ac"
    assert set(baseline) == {
        "schemaVersion",
        "comparisonBase",
        "sourceSnapshotSha256",
        "sourceInventoryPolicyPath",
        "sourceInventoryPolicySha256",
        "files",
        "domains",
    }
    assert baseline["sourceInventoryPolicyPath"] == (
        "tools/quality-gates/policy/source-inventory-policy-v1.json"
    )
    assert baseline["sourceInventoryPolicySha256"] == hashlib.sha256(
        (POLICY / "source-inventory-policy-v1.json").read_bytes()
    ).hexdigest()
    assert baseline["files"]
    assert list(baseline["domains"]) == sorted(domains)
    assert set(baseline["domains"]) == set(domains)
    assert "python:tools/quality-gates" in baseline["domains"]
    assert any(
        path.startswith("tools/quality-gates/quality_gates/")
        for path in baseline["files"]
    )
    assert baseline["sourceSnapshotSha256"] == hashlib.sha256(
        snapshot_path.read_bytes()
    ).hexdigest()

    assert set(snapshot) == {
        "schemaVersion",
        "sourceInventorySha256",
        "sourceInventoryPolicyPath",
        "sourceInventoryPolicySha256",
        "files",
    }
    assert snapshot["sourceInventoryPolicyPath"] == baseline[
        "sourceInventoryPolicyPath"
    ]
    assert snapshot["sourceInventoryPolicySha256"] == baseline[
        "sourceInventoryPolicySha256"
    ]

    entries = snapshot["files"]
    assert entries
    paths = [entry["path"] for entry in entries]
    assert paths == sorted(set(paths))
    assert all(re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) for entry in entries)
    assert any(path.startswith("java-ecosystem/") for path in paths)
    assert any(path.startswith("python-ecosystem/inference-orchestrator/") for path in paths)
    assert any(path.startswith("python-ecosystem/rag-pipeline/") for path in paths)
    assert any(path.startswith("tools/quality-gates/quality_gates/") for path in paths)

    for language in ("java", "python"):
        modules = [
            counters
            for domain, counters in baseline["domains"].items()
            if domain.startswith(language + ":") and domain != language + ":@repository"
        ]
        expected = {
            kind: {
                field: sum(module[kind][field] for module in modules)
                for field in ("covered", "total")
            }
            for kind in ("lines", "branches")
        }
        assert baseline["domains"][language + ":@repository"] == expected


def test_checked_in_quality_policies_have_exact_owned_inventories() -> None:
    java_modules = _json(POLICY / "java-modules-v1.json")["modules"]
    assert len(java_modules) == 18
    assert len({entry["module"] for entry in java_modules}) == 18
    assert len({entry["reportGroup"] for entry in java_modules}) == 18
    assert len({entry["sourceRoot"] for entry in java_modules}) == 18
    exclusion_policy = _json(POLICY / "exclusions-v1.json")
    assert exclusion_policy["schemaVersion"] == 1
    assert len(exclusion_policy["entries"]) == 41
    assert len({entry["id"] for entry in exclusion_policy["entries"]}) == 41
    assert len({entry["fileGlob"] for entry in exclusion_policy["entries"]}) == 41
    assert all(
        set(entry["compensatingIntegrationTest"]["receipt"]) == {"artifact"}
        for entry in exclusion_policy["entries"]
    )

    mutations = _json(POLICY / "mutation-profile-v1.json")["mutations"]
    assert {entry["category"] for entry in mutations} == {
        "state",
        "identity_evidence",
        "budget",
        "fencing",
        "reconciliation",
    }


def test_operator_readme_covers_required_workflows_and_recovery() -> None:
    readme = (QUALITY_ROOT / "README.md").read_text(encoding="utf-8").lower()
    required = (
        "deterministic local checks",
        "normalize-jacoco-aggregate",
        "normalize-coveragepy",
        "resolve-changes",
        "evaluate",
        "exclusion approval and receipts",
        "independent reviewer",
        "deliberate mutation gate",
        "run-mutations",
        "baseline reproduction and updates",
        "verify-source-snapshot",
        "fail-closed behavior and recovery",
        "rollback",
        "never run more than one maven reactor",
    )
    assert all(term in readme for term in required)
    assert "head^" in readme and "there is no" in readme
    assert "--cov-branch" in readme and "--cov-fail-under=100" in readme
