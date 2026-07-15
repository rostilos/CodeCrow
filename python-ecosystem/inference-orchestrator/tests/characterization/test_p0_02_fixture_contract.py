"""Contract for the versioned P0-02 current-behavior fixture bundle."""

import json
import hashlib
from pathlib import Path

import pytest


pytestmark = pytest.mark.legacy_defect

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "v1"
REQUIRED_BOUNDARIES = {
    "acquisition",
    "batching",
    "planner_exclusion",
    "rag_readiness",
    "stage_ordering",
    "java_structural_dedup",
    "python_text_dedup",
    "final_result_cache",
    "webhook_coalescing",
    "delivery_state",
}
REQUIRED_FINDINGS = {
    "J-01", "J-02", "J-03", "J-04", "J-05", "J-06",
    "P-01", "P-02", "P-03", "P-04", "P-05", "P-06", "P-07", "P-08",
    "R-01", "R-02", "R-03", "C-01", "D-01",
}
REQUIRED_GOLDEN_FIELDS = {
    "schemaVersion",
    "sourceRevision",
    "scenario",
    "observedResult",
    "defect",
    "pre",
    "post",
    "published",
    "digest",
}
SOURCE_REVISION = "89287e1fce55dc9bffeca2b92ce660d8791ae6ac"


def _load(name):
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def _canonical_digest(document):
    payload = {key: value for key, value in document.items() if key != "digest"}
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def test_v1_manifest_covers_every_required_loss_boundary():
    manifest = _load("manifest.json")

    assert manifest["schemaVersion"] == 1
    assert set(manifest["boundaries"]) == REQUIRED_BOUNDARIES


def test_v1_manifest_machine_checks_every_audited_finding_disposition():
    dispositions = _load("manifest.json")["findingDispositions"]

    assert set(dispositions) == REQUIRED_FINDINGS
    assert dispositions["C-01"] == {
        "status": "delegated",
        "ownerTask": "P0-04",
        "scenarios": ["telemetry_ledger_not_owned_by_p0_02"],
    }
    assert all(item["ownerTask"] for item in dispositions.values())
    assert all(item["scenarios"] for item in dispositions.values())


@pytest.mark.parametrize(
    "golden_name",
    [
        "manifest.json",
        "pre_stage_candidates.json",
        "post_stage_candidates.json",
        "published_outputs.json",
    ],
)
def test_v1_goldens_are_complete_source_bound_and_digest_verified(golden_name):
    golden = _load(golden_name)

    assert REQUIRED_GOLDEN_FIELDS <= set(golden)
    assert golden["schemaVersion"] == 1
    assert golden["sourceRevision"] == SOURCE_REVISION
    assert golden["scenario"]
    assert golden["observedResult"]
    assert golden["defect"]
    assert golden["digest"] == _canonical_digest(golden)


def test_published_golden_distinguishes_legitimate_from_failure_collapsed_empty():
    outputs = _load("published_outputs.json")["published"]
    classifications = {item["scenario"]: item.get("classification") for item in outputs}

    assert classifications["legitimate_empty_scope"] == "legitimate-empty"
    assert classifications["acquisition_failure_collapsed_empty"] == "failure-collapsed-empty"
    assert classifications["batch_failure_collapsed_empty"] == "failure-collapsed-empty"
