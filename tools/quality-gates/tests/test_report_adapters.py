from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


QUALITY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(QUALITY_ROOT))

from quality_gates import GateInputError  # noqa: E402
from quality_gates.normalized_reports import (  # noqa: E402
    normalize_coveragepy_json as _normalize_coveragepy_json,
    normalize_jacoco_aggregate_xml as _normalize_jacoco_aggregate_xml,
    normalize_jacoco_xml as _normalize_jacoco_xml,
    partition_jacoco_aggregate,
)
from quality_gates import normalized_reports as normalized_reports_module  # noqa: E402


JACOCO_HEADER = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<!DOCTYPE report PUBLIC "-//JACOCO//DTD Report 1.1//EN" "report.dtd">'
)
INVENTORY_SHA = "f" * 64


def normalize_jacoco_xml(*args: object, **kwargs: object) -> dict:
    kwargs.setdefault("source_inventory_sha256", INVENTORY_SHA)
    return _normalize_jacoco_xml(*args, **kwargs)  # type: ignore[arg-type]


def normalize_jacoco_aggregate_xml(
    *args: object, **kwargs: object
) -> tuple[dict, list[dict]]:
    kwargs.setdefault("source_inventory_sha256", INVENTORY_SHA)
    return _normalize_jacoco_aggregate_xml(*args, **kwargs)  # type: ignore[arg-type]


def normalize_coveragepy_json(*args: object, **kwargs: object) -> dict:
    kwargs.setdefault("source_inventory_sha256", INVENTORY_SHA)
    return _normalize_coveragepy_json(*args, **kwargs)  # type: ignore[arg-type]


def _write_java_source(root: Path) -> Path:
    source = root / "java-ecosystem/libs/demo/src/main/java/example/StateMachine.java"
    source.parent.mkdir(parents=True)
    source.write_text("package example;\nfinal class StateMachine {}\n", encoding="utf-8")
    return source


def _jacoco_xml(*, line: str, totals: str | None = None) -> str:
    report_totals = totals or (
        '<counter type="LINE" missed="0" covered="1"/>'
        '<counter type="BRANCH" missed="1" covered="1"/>'
    )
    return (
        JACOCO_HEADER
        + '<report name="demo"><package name="example">'
        '<sourcefile name="StateMachine.java">'
        + line
        + '<counter type="LINE" missed="0" covered="1"/>'
        '<counter type="BRANCH" missed="1" covered="1"/>'
        '</sourcefile></package>'
        + report_totals
        + "</report>"
    )


def _write_source_epoch_adapter_inputs(
    root: Path,
) -> tuple[Path, Path, Path, Path]:
    java_source = _write_java_source(root)
    java_source_root = java_source.parents[1]
    jacoco = root / "jacoco-source-epoch.xml"
    jacoco.write_text(
        _jacoco_xml(line='<line nr="2" mi="0" ci="1" mb="1" cb="1"/>'),
        encoding="utf-8",
    )
    jacoco_aggregate = root / "jacoco-aggregate-source-epoch.xml"
    jacoco_aggregate.write_text(
        JACOCO_HEADER
        + '<report name="aggregate"><group name="demo"><package name="example">'
        '<sourcefile name="StateMachine.java">'
        '<line nr="2" mi="0" ci="1" mb="1" cb="1"/>'
        '<counter type="LINE" missed="0" covered="1"/>'
        '<counter type="BRANCH" missed="1" covered="1"/>'
        '</sourcefile></package><counter type="LINE" missed="0" covered="1"/>'
        '<counter type="BRANCH" missed="1" covered="1"/></group>'
        '<counter type="LINE" missed="0" covered="1"/>'
        '<counter type="BRANCH" missed="1" covered="1"/></report>',
        encoding="utf-8",
    )

    python_source = root / "python-ecosystem/demo/src/rules.py"
    python_source.parent.mkdir(parents=True)
    python_source.write_text("VALUE = 1\n", encoding="utf-8")
    coveragepy = root / "coverage-source-epoch.json"
    coveragepy.write_text(
        json.dumps(
            {
                "meta": {
                    "format": 3,
                    "version": "7.15.1",
                    "branch_coverage": True,
                },
                "files": {
                    "src/rules.py": {
                        "executed_lines": [1],
                        "missing_lines": [],
                        "excluded_lines": [],
                        "executed_branches": [],
                        "missing_branches": [],
                        "summary": {
                            "covered_lines": 1,
                            "num_statements": 1,
                            "covered_branches": 0,
                            "num_branches": 0,
                        },
                    }
                },
                "totals": {
                    "covered_lines": 1,
                    "num_statements": 1,
                    "covered_branches": 0,
                    "num_branches": 0,
                },
            }
        ),
        encoding="utf-8",
    )
    return jacoco, jacoco_aggregate, coveragepy, java_source_root


@pytest.mark.parametrize("digest", [None, "", "F" * 64, "0" * 63])
def test_every_report_adapter_rejects_a_malformed_source_inventory_digest(
    tmp_path: Path, digest: object
) -> None:
    jacoco, jacoco_aggregate, coveragepy, java_source_root = (
        _write_source_epoch_adapter_inputs(tmp_path)
    )

    with pytest.raises(GateInputError, match="JaCoCo report identity is malformed"):
        _normalize_jacoco_xml(
            jacoco,
            module="libs/demo",
            source_root=java_source_root,
            repository_root=tmp_path,
            tool_version="0.8.11",
            source_inventory_sha256=digest,  # type: ignore[arg-type]
        )
    with pytest.raises(GateInputError, match="JaCoCo report identity is malformed"):
        _normalize_jacoco_aggregate_xml(
            jacoco_aggregate,
            module_groups={"libs/demo": ("demo", java_source_root)},
            repository_root=tmp_path,
            tool_version="0.8.11",
            source_inventory_sha256=digest,  # type: ignore[arg-type]
        )
    with pytest.raises(GateInputError, match="coverage.py report identity is malformed"):
        _normalize_coveragepy_json(
            coveragepy,
            module="python-ecosystem/demo",
            source_prefix="python-ecosystem/demo",
            repository_root=tmp_path,
            source_inventory_sha256=digest,  # type: ignore[arg-type]
        )


def test_every_report_adapter_requires_an_explicit_source_inventory_digest(
    tmp_path: Path,
) -> None:
    jacoco, jacoco_aggregate, coveragepy, java_source_root = (
        _write_source_epoch_adapter_inputs(tmp_path)
    )

    with pytest.raises(TypeError, match="source_inventory_sha256"):
        _normalize_jacoco_xml(
            jacoco,
            module="libs/demo",
            source_root=java_source_root,
            repository_root=tmp_path,
            tool_version="0.8.11",
        )
    with pytest.raises(TypeError, match="source_inventory_sha256"):
        _normalize_jacoco_aggregate_xml(
            jacoco_aggregate,
            module_groups={"libs/demo": ("demo", java_source_root)},
            repository_root=tmp_path,
            tool_version="0.8.11",
        )
    with pytest.raises(TypeError, match="source_inventory_sha256"):
        _normalize_coveragepy_json(
            coveragepy,
            module="python-ecosystem/demo",
            source_prefix="python-ecosystem/demo",
            repository_root=tmp_path,
        )


def test_normalize_jacoco_preserves_exact_line_and_branch_counters(tmp_path: Path) -> None:
    _write_java_source(tmp_path)
    report_path = tmp_path / "jacoco.xml"
    report_path.write_text(
        _jacoco_xml(line='<line nr="2" mi="0" ci="4" mb="1" cb="1"/>'),
        encoding="utf-8",
    )

    report = normalize_jacoco_xml(
        report_path,
        module="libs/demo",
        source_root=tmp_path / "java-ecosystem/libs/demo/src/main/java",
        repository_root=tmp_path,
        tool_version="0.8.11",
    )

    path = "java-ecosystem/libs/demo/src/main/java/example/StateMachine.java"
    assert report["sourceInventorySha256"] == INVENTORY_SHA
    assert report["branchInstrumentation"] is True
    assert report["files"][path] == {
        "executableLines": [2],
        "coveredLines": [2],
        "branches": {"2": {"covered": 1, "missed": 1}},
    }
    assert report["totals"] == {
        "lines": {"covered": 1, "total": 1},
        "branches": {"covered": 1, "total": 2},
    }


def test_normalize_jacoco_synthesizes_only_proven_zero_branch_total(
    tmp_path: Path,
) -> None:
    _write_java_source(tmp_path)
    report_path = tmp_path / "jacoco.xml"
    prefix = (
        JACOCO_HEADER
        + '<report name="demo"><package name="example">'
        '<sourcefile name="StateMachine.java">'
    )
    suffix = (
        '<counter type="LINE" missed="0" covered="1"/>'
        '<counter type="BRANCH" missed="0" covered="0"/>'
        '</sourcefile></package><counter type="LINE" missed="0" covered="1"/>'
        '</report>'
    )
    report_path.write_text(
        prefix + '<line nr="2" mi="0" ci="1" mb="0" cb="0"/>' + suffix,
        encoding="utf-8",
    )
    report = normalize_jacoco_xml(
        report_path,
        module="libs/demo",
        source_root=tmp_path / "java-ecosystem/libs/demo/src/main/java",
        repository_root=tmp_path,
        tool_version="0.8.11",
    )
    assert report["totals"]["branches"] == {"covered": 0, "total": 0}

    for forged in (
        prefix
        + '<line nr="2" mi="0" ci="1" mb="1" cb="0"/>'
        + suffix,
        prefix
        + '<line nr="2" mi="0" ci="1" mb="0" cb="0"/>'
        '<counter type="LINE" missed="0" covered="1"/>'
        '<counter type="BRANCH" missed="0" covered="1"/>'
        '</sourcefile></package><counter type="LINE" missed="0" covered="1"/>'
        '</report>',
    ):
        report_path.write_text(forged, encoding="utf-8")
        with pytest.raises(GateInputError, match="lacks exact line or branch totals"):
            normalize_jacoco_xml(
                report_path,
                module="libs/demo",
                source_root=tmp_path / "java-ecosystem/libs/demo/src/main/java",
                repository_root=tmp_path,
                tool_version="0.8.11",
            )

    missing_line_counter = ET.fromstring(
        '<report><line cb="0"/><counter type="LINE" missed="0" covered="0"/></report>'
    )
    with pytest.raises(GateInputError, match="lacks exact line or branch totals"):
        normalized_reports_module._report_counters(missing_line_counter)


@pytest.mark.parametrize(
    ("xml", "message"),
    [
        (
            _jacoco_xml(line='<line nr="2" mi="0" ci="4"/>'),
            "missing JaCoCo branch counters",
        ),
        (
            JACOCO_HEADER
            + '<report name="demo"><package name="example">'
            '<sourcefile name="StateMachine.java"><line nr="2" mi="0" ci="1" mb="0" cb="0"/></sourcefile>'
            '<sourcefile name="StateMachine.java"><line nr="2" mi="0" ci="1" mb="0" cb="0"/></sourcefile>'
            '</package><counter type="LINE" missed="0" covered="1"/>'
            '<counter type="BRANCH" missed="0" covered="0"/></report>',
            "duplicate JaCoCo source path",
        ),
        (
            '<?xml version="1.0"?><!DOCTYPE report [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            '<report name="demo"/>',
            "unsafe JaCoCo XML declaration",
        ),
    ],
)
def test_normalize_jacoco_rejects_missing_branch_data_duplicates_and_entities(
    tmp_path: Path, xml: str, message: str
) -> None:
    _write_java_source(tmp_path)
    report_path = tmp_path / "jacoco.xml"
    report_path.write_text(xml, encoding="utf-8")

    with pytest.raises(GateInputError, match=message):
        normalize_jacoco_xml(
            report_path,
            module="libs/demo",
            source_root=tmp_path / "java-ecosystem/libs/demo/src/main/java",
            repository_root=tmp_path,
            tool_version="0.8.11",
        )


def test_normalize_jacoco_rejects_counter_forgery_and_source_root_escape(
    tmp_path: Path,
) -> None:
    source = _write_java_source(tmp_path)
    report_path = tmp_path / "jacoco.xml"
    report_path.write_text(
        _jacoco_xml(
            line='<line nr="2" mi="0" ci="1" mb="0" cb="0"/>',
            totals=(
                '<counter type="LINE" missed="0" covered="1"/>'
                '<counter type="LINE" missed="0" covered="1"/>'
                '<counter type="BRANCH" missed="0" covered="0"/>'
            ),
        ),
        encoding="utf-8",
    )
    with pytest.raises(GateInputError, match="duplicate JaCoCo report counter"):
        normalize_jacoco_xml(
            report_path,
            module="libs/demo",
            source_root=source.parents[1],
            repository_root=tmp_path,
            tool_version="0.8.11",
        )

    escaped = source.parents[1].parent / "StateMachine.java"
    escaped.write_text("final class StateMachine {}\n", encoding="utf-8")
    report_path.write_text(
        JACOCO_HEADER
        + '<report name="demo"><package name="..">'
        '<sourcefile name="StateMachine.java">'
        '<line nr="1" mi="0" ci="1" mb="0" cb="0"/>'
        '</sourcefile></package><counter type="LINE" missed="0" covered="1"/>'
        '<counter type="BRANCH" missed="0" covered="0"/></report>',
        encoding="utf-8",
    )
    with pytest.raises(GateInputError, match="package or source name is unsafe"):
        normalize_jacoco_xml(
            report_path,
            module="libs/demo",
            source_root=source.parents[1],
            repository_root=tmp_path,
            tool_version="0.8.11",
        )


def test_normalize_coveragepy_preserves_executable_lines_and_branch_arcs(tmp_path: Path) -> None:
    source = tmp_path / "python-ecosystem/demo/src/rules.py"
    source.parent.mkdir(parents=True)
    source.write_text("def allowed(value):\n    return value > 0\n", encoding="utf-8")
    raw = {
        "meta": {"format": 3, "version": "7.15.1", "branch_coverage": True},
        "files": {
            "src/rules.py": {
                "executed_lines": [1, 2],
                "missing_lines": [],
                "excluded_lines": [],
                "executed_branches": [[2, -1]],
                "missing_branches": [[2, -2]],
                "summary": {
                    "covered_lines": 2,
                    "num_statements": 2,
                    "covered_branches": 1,
                    "num_branches": 2,
                },
            }
        },
        "totals": {
            "covered_lines": 2,
            "num_statements": 2,
            "covered_branches": 1,
            "num_branches": 2,
        },
    }
    report_path = tmp_path / "coverage.json"
    report_path.write_text(json.dumps(raw), encoding="utf-8")

    report = normalize_coveragepy_json(
        report_path,
        module="python-ecosystem/demo",
        source_prefix="python-ecosystem/demo",
        repository_root=tmp_path,
    )

    path = "python-ecosystem/demo/src/rules.py"
    assert report["sourceInventorySha256"] == INVENTORY_SHA
    assert report["toolVersion"] == "7.15.1"
    assert report["files"][path] == {
        "executableLines": [1, 2],
        "coveredLines": [1, 2],
        "branches": {"2": {"covered": 1, "missed": 1}},
    }
    assert report["totals"]["branches"] == {"covered": 1, "total": 2}


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (("meta", "branch_coverage", False), "branch coverage is disabled"),
        (("totals", "num_branches", 3), "coverage.py totals do not match files"),
        (("files", "absolute", True), "coverage.py path must be repository-relative"),
    ],
)
def test_normalize_coveragepy_fails_closed(
    tmp_path: Path, mutation: tuple[str, str, object], message: str
) -> None:
    source = tmp_path / "python-ecosystem/demo/src/rules.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    raw = {
        "meta": {"format": 3, "version": "7.15.1", "branch_coverage": True},
        "files": {
            "src/rules.py": {
                "executed_lines": [1],
                "missing_lines": [],
                "excluded_lines": [],
                "executed_branches": [],
                "missing_branches": [],
                "summary": {
                    "covered_lines": 1,
                    "num_statements": 1,
                    "covered_branches": 0,
                    "num_branches": 0,
                },
            }
        },
        "totals": {
            "covered_lines": 1,
            "num_statements": 1,
            "covered_branches": 0,
            "num_branches": 0,
        },
    }
    section, field, value = mutation
    if section == "files":
        raw["files"] = {str(source.resolve()): next(iter(raw["files"].values()))}
    else:
        raw[section][field] = value
    report_path = tmp_path / "coverage.json"
    report_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(GateInputError, match=message):
        normalize_coveragepy_json(
            report_path,
            module="python-ecosystem/demo",
            source_prefix="python-ecosystem/demo",
            repository_root=tmp_path,
        )


def test_normalize_coveragepy_rejects_unsafe_prefix_and_forged_file_summary(
    tmp_path: Path,
) -> None:
    source = tmp_path / "python-ecosystem/demo/src/rules.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    raw = {
        "meta": {"format": 3, "version": "7.15.1", "branch_coverage": True},
        "files": {
            "src/rules.py": {
                "executed_lines": [1],
                "missing_lines": [],
                "excluded_lines": [],
                "executed_branches": [],
                "missing_branches": [],
                "summary": {
                    "covered_lines": 0,
                    "num_statements": 1,
                    "covered_branches": 0,
                    "num_branches": 0,
                },
            }
        },
        "totals": {
            "covered_lines": 1,
            "num_statements": 1,
            "covered_branches": 0,
            "num_branches": 0,
        },
    }
    report_path = tmp_path / "coverage.json"
    report_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(GateInputError, match="file summary does not match coverage data"):
        normalize_coveragepy_json(
            report_path,
            module="python-ecosystem/demo",
            source_prefix="python-ecosystem/demo",
            repository_root=tmp_path,
        )
    with pytest.raises(GateInputError, match="source prefix must be repository-relative"):
        normalize_coveragepy_json(
            report_path,
            module="python-ecosystem/demo",
            source_prefix="../demo",
            repository_root=tmp_path,
        )


def test_partition_jacoco_aggregate_keeps_zero_test_modules_in_baseline(tmp_path: Path) -> None:
    first = tmp_path / "java-ecosystem/libs/one/src/main/java/example/One.java"
    second = tmp_path / "java-ecosystem/libs/two/src/main/java/other/Two.java"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_text("package example; class One {}\n", encoding="utf-8")
    second.write_text("package other; class Two {}\n", encoding="utf-8")
    report_path = tmp_path / "aggregate.xml"
    report_path.write_text(
        JACOCO_HEADER
        + '<report name="all">'
        '<package name="example"><sourcefile name="One.java">'
        '<line nr="1" mi="0" ci="1" mb="0" cb="0"/>'
        '</sourcefile></package>'
        '<package name="other"><sourcefile name="Two.java">'
        '<line nr="1" mi="1" ci="0" mb="1" cb="0"/>'
        '</sourcefile></package>'
        '<counter type="LINE" missed="1" covered="1"/>'
        '<counter type="BRANCH" missed="1" covered="0"/>'
        '</report>',
        encoding="utf-8",
    )
    roots = {
        "libs/one": first.parents[1],
        "libs/two": second.parents[1],
    }

    aggregate = normalize_jacoco_xml(
        report_path,
        module="@repository",
        source_root=list(roots.values()),
        repository_root=tmp_path,
        tool_version="0.8.11",
    )
    modules = partition_jacoco_aggregate(aggregate, module_source_roots=roots, repository_root=tmp_path)

    by_module = {report["module"]: report for report in modules}
    assert {report["sourceInventorySha256"] for report in modules} == {INVENTORY_SHA}
    assert by_module["libs/one"]["totals"]["lines"] == {"covered": 1, "total": 1}
    assert by_module["libs/two"]["totals"] == {
        "lines": {"covered": 0, "total": 1},
        "branches": {"covered": 0, "total": 1},
    }


def test_partition_rejects_nonexistent_and_duplicate_module_roots(tmp_path: Path) -> None:
    aggregate = {
        "schemaVersion": 1,
        "adapter": "jacoco-xml",
        "language": "java",
        "module": "@repository",
        "toolVersion": "0.8.11",
        "sourceInventorySha256": INVENTORY_SHA,
        "branchInstrumentation": True,
        "files": {},
        "totals": {
            "lines": {"covered": 0, "total": 0},
            "branches": {"covered": 0, "total": 0},
        },
    }
    missing = tmp_path / "missing"
    with pytest.raises(GateInputError, match="does not identify a source directory"):
        partition_jacoco_aggregate(
            aggregate,
            module_source_roots={"libs/missing": missing},
            repository_root=tmp_path,
        )

    source = tmp_path / "java/src/main/java"
    source.mkdir(parents=True)
    with pytest.raises(GateInputError, match="module source roots must be unique"):
        partition_jacoco_aggregate(
            aggregate,
            module_source_roots={"one": source, "two": source},
            repository_root=tmp_path,
        )


def test_authoritative_jacoco_aggregate_uses_exact_project_groups(tmp_path: Path) -> None:
    one = tmp_path / "java-ecosystem/libs/one/src/main/java/shared/Rules.java"
    two = tmp_path / "java-ecosystem/libs/two/src/main/java/shared/Rules.java"
    one.parent.mkdir(parents=True)
    two.parent.mkdir(parents=True)
    one.write_text("package shared; class Rules {}\n", encoding="utf-8")
    two.write_text("package shared; class Rules {}\n", encoding="utf-8")
    report_path = tmp_path / "aggregate.xml"
    report_path.write_text(
        JACOCO_HEADER
        + '<report name="aggregate">'
        '<group name="one"><package name="shared"><sourcefile name="Rules.java">'
        '<line nr="1" mi="0" ci="1" mb="0" cb="0"/>'
        '</sourcefile></package><counter type="LINE" missed="0" covered="1"/>'
        '<counter type="BRANCH" missed="0" covered="0"/></group>'
        '<group name="two"><package name="shared"><sourcefile name="Rules.java">'
        '<line nr="1" mi="1" ci="0" mb="1" cb="0"/>'
        '</sourcefile></package><counter type="LINE" missed="1" covered="0"/>'
        '<counter type="BRANCH" missed="1" covered="0"/></group>'
        '<counter type="LINE" missed="1" covered="1"/>'
        '<counter type="BRANCH" missed="1" covered="0"/></report>',
        encoding="utf-8",
    )

    aggregate, modules = normalize_jacoco_aggregate_xml(
        report_path,
        module_groups={
            "java-ecosystem/libs/one": ("one", one.parents[1]),
            "java-ecosystem/libs/two": ("two", two.parents[1]),
        },
        repository_root=tmp_path,
        tool_version="0.8.11",
    )
    assert len(modules) == 2
    assert aggregate["sourceInventorySha256"] == INVENTORY_SHA
    assert {report["sourceInventorySha256"] for report in modules} == {INVENTORY_SHA}
    assert aggregate["totals"] == {
        "lines": {"covered": 1, "total": 2},
        "branches": {"covered": 0, "total": 1},
    }
    assert len(aggregate["files"]) == 2

    report_path.write_text(
        report_path.read_text(encoding="utf-8").replace('group name="two"', 'group name="extra"'),
        encoding="utf-8",
    )
    with pytest.raises(GateInputError, match="groups do not match module policy"):
        normalize_jacoco_aggregate_xml(
            report_path,
            module_groups={
                "java-ecosystem/libs/one": ("one", one.parents[1]),
                "java-ecosystem/libs/two": ("two", two.parents[1]),
            },
            repository_root=tmp_path,
            tool_version="0.8.11",
        )


def test_normalize_coveragepy_omits_proven_empty_non_executable_markers(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "python-ecosystem/demo/src/__init__.py"
    marker.parent.mkdir(parents=True)
    marker.write_text("", encoding="utf-8")
    raw = {
        "meta": {"format": 3, "version": "7.15.1", "branch_coverage": True},
        "files": {
            "src/__init__.py": {
                "executed_lines": [],
                "missing_lines": [],
                "executed_branches": [],
                "missing_branches": [],
                "summary": {
                    "covered_lines": 0,
                    "num_statements": 0,
                    "covered_branches": 0,
                    "num_branches": 0,
                },
            }
        },
        "totals": {
            "covered_lines": 0,
            "num_statements": 0,
            "covered_branches": 0,
            "num_branches": 0,
        },
    }
    report_path = tmp_path / "empty-coverage.json"
    report_path.write_text(json.dumps(raw), encoding="utf-8")
    report = normalize_coveragepy_json(
        report_path,
        module="python-ecosystem/demo",
        source_prefix="python-ecosystem/demo",
        repository_root=tmp_path,
    )
    assert report["files"] == {}
    assert report["totals"] == {
        "lines": {"covered": 0, "total": 0},
        "branches": {"covered": 0, "total": 0},
    }
