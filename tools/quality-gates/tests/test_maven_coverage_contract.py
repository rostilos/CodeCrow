from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


QUALITY_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = QUALITY_ROOT.parents[1]
JAVA_ROOT = REPOSITORY_ROOT / "java-ecosystem"
sys.path.insert(0, str(QUALITY_ROOT))


NS = {"m": "http://maven.apache.org/POM/4.0.0"}


def _text(element: ET.Element, path: str) -> str:
    value = element.findtext(path, namespaces=NS)
    assert value is not None
    return value.strip()


def test_quality_profile_is_additive_and_collects_unit_and_integration_coverage() -> None:
    root = ET.parse(JAVA_ROOT / "pom.xml").getroot()
    normal_modules = [element.text.strip() for element in root.findall("m:modules/m:module", NS)]
    assert len(normal_modules) == 18
    assert "quality/coverage-aggregate" not in normal_modules

    profiles = root.findall("m:profiles/m:profile", NS)
    profile = next(
        candidate
        for candidate in profiles
        if _text(candidate, "m:id") == "quality-coverage"
    )
    assert [element.text.strip() for element in profile.findall("m:modules/m:module", NS)] == [
        "quality/coverage-aggregate"
    ]
    plugin = next(
        candidate
        for candidate in root.findall("m:build/m:plugins/m:plugin", NS)
        if _text(candidate, "m:artifactId") == "jacoco-maven-plugin"
    )
    executions = {
        _text(execution, "m:id"): execution
        for execution in plugin.findall("m:executions/m:execution", NS)
    }
    assert set(executions) == {
        "prepare-unit-agent",
        "prepare-integration-agent",
        "merge-unit-and-integration-coverage",
        "report",
    }
    assert _text(
        executions["prepare-unit-agent"], "m:configuration/m:append"
    ) == "true"
    assert _text(
        executions["prepare-integration-agent"], "m:configuration/m:append"
    ) == "true"
    assert _text(
        executions["merge-unit-and-integration-coverage"], "m:phase"
    ) == "verify"
    assert _text(executions["report"], "m:phase") == "verify"
    assert _text(executions["report"], "m:configuration/m:dataFile") == (
        "${project.build.directory}/jacoco.exec"
    )


def test_aggregate_module_has_compile_dependency_on_every_normal_reactor_module() -> None:
    root = ET.parse(JAVA_ROOT / "pom.xml").getroot()
    module_paths = [element.text.strip() for element in root.findall("m:modules/m:module", NS)]
    expected_artifacts = {
        _text(ET.parse(JAVA_ROOT / module / "pom.xml").getroot(), "m:artifactId")
        for module in module_paths
    }

    aggregate = ET.parse(JAVA_ROOT / "quality/coverage-aggregate/pom.xml").getroot()
    dependencies = aggregate.findall("m:dependencies/m:dependency", NS)
    actual_artifacts = {_text(dependency, "m:artifactId") for dependency in dependencies}
    assert actual_artifacts == expected_artifacts
    assert all(_text(dependency, "m:scope") == "compile" for dependency in dependencies)

    execution = aggregate.find(
        "m:build/m:plugins/m:plugin[m:artifactId='jacoco-maven-plugin']/m:executions/m:execution",
        NS,
    )
    assert execution is not None
    assert _text(execution, "m:phase") == "verify"
    assert _text(execution, "m:goals/m:goal") == "report-aggregate"
    assert _text(
        execution, "m:configuration/m:dataFileIncludes/m:dataFileInclude"
    ) == "target/jacoco.exec"
