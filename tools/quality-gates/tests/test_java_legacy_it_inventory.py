from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
POLICY_PATH = (
    REPOSITORY_ROOT
    / "tools/quality-gates/policy/java-legacy-it-inventory-v1.json"
)
README_PATH = (
    REPOSITORY_ROOT
    / "tools/quality-gates/policy/JAVA_LEGACY_IT_INVENTORY.md"
)
EXPECTED_TOTALS = {
    "files": 37,
    "support": 4,
    "localDouble": 11,
    "containerBacked": 20,
    "abstractBase": 2,
    "concreteSelectors": 31,
    "testAnnotationTokens": 244,
    "testMethods": 228,
    "localDoubleTestMethods": 65,
    "containerBackedTestMethods": 163,
}
PACKAGE = re.compile(r"(?m)^package\s+([A-Za-z_][\w.]*)\s*;")
TYPE = re.compile(
    r"(?m)^(?:public\s+)?(?:(?:abstract|final)\s+)?"
    r"(?:class|interface|@interface|enum)\s+([A-Za-z_]\w*)"
)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        assert key not in result, f"duplicate JSON key: {key}"
        result[key] = value
    return result


def _read_json(path: Path) -> dict:
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
    )


def _legacy_sources() -> list[Path]:
    return sorted(
        path
        for path in (REPOSITORY_ROOT / "java-ecosystem").rglob("*.java")
        if "/src/it/java/" in path.as_posix()
    )


def test_versioned_inventory_reconciles_every_legacy_it_source_and_class() -> None:
    policy = _read_json(POLICY_PATH)
    assert policy["schemaVersion"] == 1
    assert policy["inventoryId"] == "java-legacy-it-inventory-v1"
    assert policy["sourcePattern"] == "java-ecosystem/**/src/it/java/**/*.java"
    assert policy["testAnnotationToken"] == "@Test"
    assert policy["expectedTotals"] == EXPECTED_TOTALS

    entries = policy["entries"]
    assert isinstance(entries, list)
    assert len(entries) == EXPECTED_TOTALS["files"]
    by_path = {entry["path"]: entry for entry in entries}
    assert len(by_path) == EXPECTED_TOTALS["files"]

    discovered = {
        path.relative_to(REPOSITORY_ROOT).as_posix(): path for path in _legacy_sources()
    }
    assert set(by_path) == set(discovered)

    for relative_path, source in discovered.items():
        assert source.is_file() and not source.is_symlink()
        text = source.read_text(encoding="utf-8")
        package_match = PACKAGE.search(text)
        type_match = TYPE.search(text)
        assert package_match, relative_path
        assert type_match, relative_path
        entry = by_path[relative_path]
        assert set(entry) == {
            "path",
            "className",
            "module",
            "category",
            "testMethods",
            "testAnnotationTokens",
        }
        assert entry["className"] == f"{package_match.group(1)}.{type_match.group(1)}"
        assert entry["module"] == relative_path.split("/src/it/java/", 1)[0].removeprefix(
            "java-ecosystem/"
        )
        assert entry["testMethods"] == len(re.findall(r"@Test\b", text))
        assert entry["testAnnotationTokens"] == text.count("@Test")


def test_inventory_categories_and_exact_selector_totals_are_exhaustive() -> None:
    policy = _read_json(POLICY_PATH)
    entries = policy["entries"]
    counts = Counter(entry["category"] for entry in entries)
    assert counts == {
        "support": 4,
        "localDouble": 11,
        "containerBacked": 20,
        "abstractBase": 2,
    }
    assert sum(entry["testAnnotationTokens"] for entry in entries) == 244
    assert sum(entry["testMethods"] for entry in entries) == 228
    assert sum(
        entry["testMethods"] for entry in entries if entry["category"] == "localDouble"
    ) == 65
    assert sum(
        entry["testMethods"]
        for entry in entries
        if entry["category"] == "containerBacked"
    ) == 163

    concrete = [
        entry
        for entry in entries
        if entry["category"] in {"localDouble", "containerBacked"}
    ]
    assert len(concrete) == 31
    assert len({entry["className"] for entry in concrete}) == 31
    assert all(entry["className"].endswith("IT") for entry in concrete)
    assert all(entry["testMethods"] > 0 for entry in concrete)

    for entry in entries:
        text = (REPOSITORY_ROOT / entry["path"]).read_text(encoding="utf-8")
        if entry["category"] == "abstractBase":
            assert re.search(r"\babstract\s+class\s+", text)
        elif entry["category"] == "localDouble":
            assert "SharedRedisContainer.getInstance()" not in text
            assert "extends BasePipelineAgentIT" not in text
            assert "extends BaseWebServerIT" not in text
        elif entry["category"] == "containerBacked":
            assert (
                "SharedRedisContainer.getInstance()" in text
                or "extends BasePipelineAgentIT" in text
                or "extends BaseWebServerIT" in text
            )


def test_safe_lane_design_selects_only_the_eleven_local_double_classes() -> None:
    policy = _read_json(POLICY_PATH)
    workflow_contract = policy["workflowContract"]
    safe_lane = workflow_contract["safeLane"]
    safe_selectors = {
        entry["className"]
        for entry in policy["entries"]
        if entry["category"] == "localDouble"
    }
    container_selectors = {
        entry["className"]
        for entry in policy["entries"]
        if entry["category"] == "containerBacked"
    }
    assert workflow_contract["blanketSkipITsAllowed"] is False
    assert safe_lane == {
        "wrapper": "tools/quality-gates/bin/run-java-coverage-offline.sh",
        "mavenGoal": "verify",
        "selectorProperty": "-Dit.test",
        "selectorCategory": "localDouble",
        "expectedSelectors": 11,
        "reportContract": {
            "format": "Failsafe JUnit XML",
            "expectedClasses": 11,
            "expectedTests": 65,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
            "rejectExtraDuplicateOrStaleReports": True,
        },
    }
    assert len(safe_selectors) == 11
    assert safe_selectors.isdisjoint(container_selectors)
    assert workflow_contract["status"] == "GUARDED_EXECUTION_REQUIRED"
    readme = README_PATH.read_text(encoding="utf-8")
    assert "tools/quality-gates/bin/run-java-coverage-offline.sh" in readme
    assert "org.rostilos.codecrow.analysisengine.AiClientIT" in readme
    assert '"-Dit.test=$SAFE_LEGACY_ITS"' in readme
    assert "-DskipITs" not in readme


def test_container_lane_is_guarded_only_with_reviewed_expiring_receipt() -> None:
    policy = _read_json(POLICY_PATH)
    lane = policy["workflowContract"]["containerLane"]
    assert lane["status"] == "GUARDED_ONLY"
    assert lane["selectorCategory"] == "containerBacked"
    assert lane["expectedSelectors"] == 20

    registry_path = REPOSITORY_ROOT / lane["registry"]["path"]
    assert registry_path.is_file() and not registry_path.is_symlink()
    assert hashlib.sha256(registry_path.read_bytes()).hexdigest() == lane["registry"]["sha256"]
    registry = _read_json(registry_path)
    assert registry["schemaVersion"] == 1
    assert registry["status"] == "GUARDED_ONLY"
    assert registry["owner"] != registry["reviewer"]
    assert date.fromisoformat(registry["issuedOn"]) == date(2026, 7, 14)
    assert date.fromisoformat(registry["expiresOn"]) > date.fromisoformat(
        registry["issuedOn"]
    )
    receipt = registry["receipt"]
    assert receipt["status"] == "guarded-only"
    assert receipt["selectorCount"] == 20
    assert receipt["testAnnotationTokens"] == 167
    assert receipt["testMethods"] == 163
    assert len(receipt["invariants"]) == 4

    expected = [
        entry["className"]
        for entry in policy["entries"]
        if entry["category"] == "containerBacked"
    ]
    assert registry["selectors"] == expected


def test_guarded_container_release_contract_pins_images_and_evidence() -> None:
    policy = _read_json(POLICY_PATH)
    release = policy["workflowContract"]["containerLane"]["guardedReleaseContract"]
    assert release["wrapper"] == (
        "tools/quality-gates/bin/run-java-legacy-it-guarded.sh"
    )
    manifest_path = REPOSITORY_ROOT / release["imageManifest"]
    assert hashlib.sha256(manifest_path.read_bytes()).hexdigest() == release[
        "imageManifestSha256"
    ]
    manifest = _read_json(manifest_path)
    expected_runtime_images = [
        image["runtime_reference"]
        for image in manifest["images"]
        if image["runtime_reference"].startswith(("postgres@", "redis@"))
    ]
    assert release["runtimeImages"] == expected_runtime_images
    assert not any(image.startswith("qdrant/") for image in release["runtimeImages"])
    assert release["freshTaskNamespace"] is True
    assert release["pullPolicy"] == "NEVER"
    assert release["requiredEvidence"] == [
        "task namespace receipt",
        "runtime image event log proving zero pulls",
        "external-call ledger",
        "owned container-id report",
        "exact teardown absence report",
    ]
    assert release["reportContract"] == {
        "format": "Failsafe JUnit XML",
        "expectedClasses": 20,
        "expectedTests": 163,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "rejectExtraDuplicateOrStaleReports": True,
    }
