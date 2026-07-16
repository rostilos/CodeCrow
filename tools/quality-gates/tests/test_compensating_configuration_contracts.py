from __future__ import annotations

import configparser
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import jsonschema
import yaml


QUALITY_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = QUALITY_ROOT.parents[1]


COMPENSATED_PATHS = (
    ".github/workflows/offline-tests.yml",
    "deployment/config/java-shared/application.properties.sample",
    "java-ecosystem/libs/analysis-engine/pom.xml",
    "java-ecosystem/libs/core/pom.xml",
    "java-ecosystem/libs/test-support/pom.xml",
    "java-ecosystem/libs/test-support/src/main/resources/META-INF/services/org.junit.platform.launcher.LauncherSessionListener",
    "java-ecosystem/pom.xml",
    "java-ecosystem/quality/coverage-aggregate/pom.xml",
    "java-ecosystem/quality/guarded-test-runtime/mockito-extensions/org.mockito.plugins.MemberAccessor",
    "java-ecosystem/quality/guarded-test-runtime/mockito-extensions/org.mockito.plugins.MockMaker",
    "java-ecosystem/services/pipeline-agent/src/main/resources/logback-spring.xml",
    "java-ecosystem/services/web-server/src/main/resources/logback-spring.xml",
    "tools/quality-gates/bin/java-legacy-it-a-supervisor.sh",
    "tools/quality-gates/bin/run-java-coverage-offline.sh",
    "tools/quality-gates/bin/run-java-legacy-it-guarded.sh",
    "tools/quality-gates/bin/run-locked-python.sh",
    "tools/quality-gates/bin/validate-p007-maven-cache.sh",
    "tools/quality-gates/config/inference.coveragerc",
    "tools/quality-gates/config/quality-gates.coveragerc",
    "tools/quality-gates/config/rag.coveragerc",
    "tools/quality-gates/policy/comparison-base-v1.json",
    "tools/quality-gates/policy/correctness-policy-v1.json",
    "tools/quality-gates/policy/coverage-baseline-v1.json",
    "tools/quality-gates/policy/coverage-domains-v1.json",
    "tools/quality-gates/policy/exclusions-v1.json",
    "tools/quality-gates/policy/java-legacy-it-container-quarantine-v1.json",
    "tools/quality-gates/policy/java-legacy-it-inventory-v1.json",
    "tools/quality-gates/policy/java-legacy-it-tools-v1.json",
    "tools/quality-gates/policy/java-modules-v1.json",
    "tools/quality-gates/policy/mutation-profile-v1.json",
    "tools/quality-gates/policy/source-inventory-policy-v1.json",
    "tools/quality-gates/policy/source-snapshot-v1.json",
    "tools/quality-gates/policy/trust-bundle-v1.json",
    "tools/quality-gates/schema/compensating-receipt-v1.schema.json",
    "tools/quality-gates/schema/coverage-baseline-v1.schema.json",
    "tools/quality-gates/schema/coverage-exclusions-v1.schema.json",
    "tools/quality-gates/schema/gate-result-v1.schema.json",
    "tools/quality-gates/schema/mutation-profile-v1.schema.json",
    "tools/quality-gates/schema/normalized-coverage-v1.schema.json",
    "tools/quality-gates/schema/source-inventory-v1.schema.json",
    "tools/quality-gates/schema/source-snapshot-v1.schema.json",
    "tools/quality-gates/schema/trust-bundle-v1.schema.json",
)


def _json_without_duplicates(path: Path) -> dict:
    def reject(pairs: list[tuple[str, object]]) -> dict:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise AssertionError(f"duplicate JSON key in {path}: {key}")
            value[key] = item
        return value

    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject)
    assert isinstance(value, dict)
    return value


def _sha256(relative: str) -> str:
    return hashlib.sha256((REPOSITORY_ROOT / relative).read_bytes()).hexdigest()


def test_critical_declarative_configuration_contracts() -> None:
    registry = _json_without_duplicates(
        REPOSITORY_ROOT / "tools/quality-gates/policy/exclusions-v1.json"
    )
    assert {entry["fileGlob"] for entry in registry["entries"]} == set(
        COMPENSATED_PATHS
    )
    for entry in registry["entries"]:
        execution = entry["compensatingIntegrationTest"]["executionPolicy"]
        assert execution["argvTemplate"][0] == execution["runner"]["artifact"]
        assert execution["argvTemplate"][1] == "{runtime}"
        assert execution["argvTemplate"][-1] == "{selector}"
        assert execution["runner"]["sha256"] == _sha256(
            execution["runner"]["artifact"]
        )
        assert execution["runtime"]["sha256"] == _sha256(
            execution["runtime"]["artifact"]
        )
        assert execution["runtime"]["artifact"] == (
            "tools/quality-gates/bin/run-locked-python.sh"
        )

    for relative in COMPENSATED_PATHS:
        path = REPOSITORY_ROOT / relative
        assert path.is_file() and not path.is_symlink(), relative
        if path.suffix == ".json":
            document = _json_without_duplicates(path)
            if "/schema/" in relative:
                jsonschema.Draft202012Validator.check_schema(document)
            else:
                assert document.get("schemaVersion") == 1, relative
        elif path.suffix in {".xml"} or path.name == "pom.xml":
            assert ET.parse(path).getroot() is not None
        elif path.suffix in {".yml", ".yaml"}:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            assert isinstance(document, dict) and "jobs" in document
        elif path.suffix == ".sh":
            subprocess.run(["/bin/bash", "-n", path], check=True)
        elif path.suffix == ".coveragerc":
            parser = configparser.ConfigParser()
            assert parser.read(path, encoding="utf-8") == [str(path)]
            assert parser.getboolean("run", "branch")
        else:
            assert path.read_text(encoding="utf-8").strip()

    for relative in (
        "java-ecosystem/services/pipeline-agent/src/main/resources/logback-spring.xml",
        "java-ecosystem/services/web-server/src/main/resources/logback-spring.xml",
    ):
        assert "${LOGGING_FILE_PATTERN:-" in (
            REPOSITORY_ROOT / relative
        ).read_text(encoding="utf-8")
    provider_relative = (
        "java-ecosystem/libs/test-support/src/main/resources/META-INF/services/"
        "org.junit.platform.launcher.LauncherSessionListener"
    )
    provider_bytes = (
        b"org.rostilos.codecrow.testsupport.legacy."
        b"LegacyContainerItLauncherSessionListener\n"
    )
    assert (REPOSITORY_ROOT / provider_relative).read_bytes() == provider_bytes
    source_provider_files = sorted(
        path.relative_to(REPOSITORY_ROOT).as_posix()
        for path in (REPOSITORY_ROOT / "java-ecosystem").rglob(
            "org.junit.platform.launcher.LauncherSessionListener"
        )
        if "target" not in path.parts
    )
    assert source_provider_files == [provider_relative]
    mockito_contracts = {
        "java-ecosystem/quality/guarded-test-runtime/mockito-extensions/"
        "org.mockito.plugins.MemberAccessor": b"member-accessor-reflection\n",
        "java-ecosystem/quality/guarded-test-runtime/mockito-extensions/"
        "org.mockito.plugins.MockMaker": b"mock-maker-subclass\n",
    }
    for relative, expected in mockito_contracts.items():
        assert (REPOSITORY_ROOT / relative).read_bytes() == expected
        source_matches = sorted(
            path.relative_to(REPOSITORY_ROOT).as_posix()
            for path in (REPOSITORY_ROOT / "java-ecosystem").rglob(Path(relative).name)
            if "target" not in path.parts
        )
        assert source_matches == [relative]

    workflow = (
        REPOSITORY_ROOT / ".github/workflows/offline-tests.yml"
    ).read_text(encoding="utf-8")
    assert "run-locked-python.sh \\\n            --prepare \"$GITHUB_WORKSPACE/$PYTHON_ENV\"" in workflow
    assert "for lane in queue pipeline web" in workflow
    assert workflow.count("--selector-evidence") == 1
    for required in (
        '"$CONFIGURATION_SELECTOR"',
        '"$P007/receipts/configuration-contracts.junit.xml"',
        '"$P007/receipts/configuration-contract-ledger.json"',
    ):
        assert required in workflow
    runtime_wrapper = (
        REPOSITORY_ROOT / "tools/quality-gates/bin/run-locked-python.sh"
    ).read_text(encoding="utf-8")
    for required in (
        "portable-python311",
        "ci-test.lock.sha256",
        "PYTHONHOME",
        "PYTHONNOUSERSITE",
        "--link-dest",
        "--prepare",
    ):
        assert required in runtime_wrapper

    execution_policy_sample = (
        REPOSITORY_ROOT / "deployment/config/java-shared/application.properties.sample"
    ).read_text(encoding="utf-8")
    for required in (
        "#codecrow.analysis.policy.mode=active",
        "#codecrow.analysis.policy.candidate-version=candidate-review-v1",
        "#codecrow.analysis.policy.known-versions=legacy-review-v1,candidate-review-v1",
        "#codecrow.analysis.policy.rollout-basis-points=10000",
        "#codecrow.analysis.policy.rollout-salt=codecrow-project-rollout-v1",
        "#codecrow.analysis.policy.config-revision=",
        "#codecrow.analysis.policy.stop-new-work=false",
        "#codecrow.analysis.policy.candidate-kill-switch=false",
    ):
        assert required in execution_policy_sample

    ledger = (
        REPOSITORY_ROOT
        / ".llm-handoff-artifacts/p0-07/receipts/configuration-contract-ledger.json"
    )
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "live_call_count": 0,
                "simulated_call_count": 0,
                "calls": [],
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
