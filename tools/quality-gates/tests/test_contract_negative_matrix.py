from __future__ import annotations

import copy
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


QUALITY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(QUALITY_ROOT))

from quality_gates import baseline as baseline_module  # noqa: E402
from quality_gates import changed_coverage as changed_module  # noqa: E402
from quality_gates import normalized_reports as normalized_module  # noqa: E402
from quality_gates.baseline import (  # noqa: E402
    _verify_unbound_source_snapshot,
    _capture_unbound_coverage_baseline,
    _capture_unbound_source_snapshot,
    aggregate_normalized_reports,
)
from quality_gates.changed_coverage import (  # noqa: E402
    _evaluate_unbound_gate,
    GateInputError,
    validate_normalized_report,
)


PATH = "python-ecosystem/demo/src/rules.py"
BASE = "8" * 40
HEAD = "1" * 40
INVENTORY_SHA = "f" * 64


def _normalize_jacoco_element(*args: object, **kwargs: object) -> dict:
    kwargs.setdefault("source_inventory_sha256", INVENTORY_SHA)
    return normalized_module._normalize_jacoco_element(*args, **kwargs)  # type: ignore[arg-type]


def _normalize_jacoco_aggregate(*args: object, **kwargs: object):
    kwargs.setdefault("source_inventory_sha256", INVENTORY_SHA)
    return normalized_module.normalize_jacoco_aggregate_xml(*args, **kwargs)  # type: ignore[arg-type]


def _report(
    *,
    module: str = "python-ecosystem/demo",
    path: str = PATH,
    language: str = "python",
    executable: list[int] | None = None,
    covered: list[int] | None = None,
    branches: dict[str, dict[str, int]] | None = None,
    files: dict | None = None,
    adapter: str | None = None,
    version: str = "7.15.1",
    instrumented: bool = True,
) -> dict:
    executable = [1] if executable is None else executable
    covered = [1] if covered is None else covered
    branches = {"1": {"covered": 2, "missed": 0}} if branches is None else branches
    if files is None:
        files = {
            path: {
                "executableLines": executable,
                "coveredLines": covered,
                "branches": branches,
            }
        }
    line_covered = sum(len(value["coveredLines"]) for value in files.values())
    line_total = sum(len(value["executableLines"]) for value in files.values())
    branch_covered = sum(
        branch["covered"]
        for value in files.values()
        for branch in value["branches"].values()
    )
    branch_total = sum(
        branch["covered"] + branch["missed"]
        for value in files.values()
        for branch in value["branches"].values()
    )
    return {
        "schemaVersion": 1,
        "adapter": adapter or ("jacoco-xml" if language == "java" else "coveragepy-json"),
        "language": language,
        "module": module,
        "toolVersion": version,
        "sourceInventorySha256": INVENTORY_SHA,
        "branchInstrumentation": instrumented,
        "files": files,
        "totals": {
            "lines": {"covered": line_covered, "total": line_total},
            "branches": {"covered": branch_covered, "total": branch_total},
        },
    }


def _changes(*, lines: list[int] | None = None, critical: bool = True, status: str = "modified") -> dict:
    return {
        "schemaVersion": 1,
        "baseCommit": BASE,
        "headCommit": HEAD,
        "files": [
            {
                "path": PATH,
                "status": status,
                "correctnessCritical": critical,
                "language": "python" if critical else None,
                "changedLines": (
                    [] if status == "deleted" else ([1] if lines is None else lines)
                ),
            }
        ],
    }


def _baseline(report: dict | None = None) -> dict:
    report = _report() if report is None else report
    return {
        "schemaVersion": 1,
        "comparisonBase": BASE,
        "sourceSnapshotSha256": "a" * 64,
        "domains": {f"{report['language']}:{report['module']}": copy.deepcopy(report["totals"])},
    }


def _exclusions(entries: list | None = None) -> dict:
    return {"schemaVersion": 1, "entries": [] if entries is None else entries}


def _exclusion(**overrides: object) -> dict:
    value = {
        "id": "generated-rules",
        "fileGlob": PATH,
        "reason": "Generated from a reviewed contract.",
        "owner": "owner",
        "reviewer": "reviewer",
        "expiresOn": "2026-08-01",
        "compensatingIntegrationTest": {
            "selector": "tests/test_contract.py::test_contract",
            "executionPolicy": {
                "runner": {"artifact": "tools/offline-runner", "sha256": "a" * 64},
                "runtime": {"artifact": "tools/runtime", "sha256": "b" * 64},
                "argvTemplate": [
                    "tools/offline-runner",
                    "{runtime}",
                    "{selector}",
                ],
            },
            "receipt": {
                "artifact": ".llm-handoff-artifacts/p0-07/results/contract.json"
            },
        },
    }
    value.update(overrides)
    return value


def _evaluate(
    *,
    changes: dict | None = None,
    reports: list[dict] | None = None,
    baseline: dict | None = None,
    exclusions: dict | None = None,
    as_of: str = "2026-07-14",
    repository_root: Path | None = None,
):
    report = _report()
    return _evaluate_unbound_gate(
        changes=_changes() if changes is None else changes,
        reports=[report] if reports is None else reports,
        baseline=_baseline(report) if baseline is None else baseline,
        exclusions=_exclusions() if exclusions is None else exclusions,
        as_of=as_of,
        repository_root=repository_root,
    )


@pytest.mark.parametrize(
    "counter",
    [None, {"covered": None, "total": 1}, {"covered": True, "total": 1},
     {"covered": 0, "total": False}, {"covered": -1, "total": 1},
     {"covered": 0, "total": -1}, {"covered": 2, "total": 1}],
)
def test_exact_counter_negative_matrix(counter: object) -> None:
    with pytest.raises(GateInputError):
        changed_module._counter(counter, "counter")


def test_ratio_zero_denominator_matrix() -> None:
    assert changed_module._ratio_regressed(
        {"covered": 0, "total": 0}, {"covered": 0, "total": 0}
    ) is False
    assert changed_module._ratio_regressed(
        {"covered": 0, "total": 0}, {"covered": 1, "total": 2}
    ) is True


@pytest.mark.parametrize("value", [None, "", "a\\b", "/absolute", "a/../b", "."])
def test_safe_path_negative_matrix(value: object) -> None:
    with pytest.raises(GateInputError):
        changed_module._safe_path(value, "path")


@pytest.mark.parametrize("value", [None, [0], [True], [2, 1], [1, 1]])
def test_line_list_negative_matrix(value: object) -> None:
    with pytest.raises(GateInputError):
        changed_module._line_list(value, "lines")


def _mutate_identity(report: dict, field: str, value: object) -> None:
    report[field] = value


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schemaVersion", 2), ("language", None), ("language", " "),
        ("module", None), ("module", " "), ("adapter", None), ("adapter", " "),
        ("toolVersion", None), ("toolVersion", " "), ("files", []),
        ("sourceInventorySha256", None), ("sourceInventorySha256", "F" * 64),
        ("sourceInventorySha256", "bad"),
        ("branchInstrumentation", None),
    ],
)
def test_normalized_report_identity_negative_matrix(field: str, value: object) -> None:
    report = _report()
    _mutate_identity(report, field, value)
    with pytest.raises(GateInputError, match="identity is malformed"):
        validate_normalized_report(report)


def test_normalized_report_rejects_missing_or_extra_identity_fields() -> None:
    missing = _report()
    del missing["sourceInventorySha256"]
    with pytest.raises(GateInputError, match="identity is malformed"):
        validate_normalized_report(missing)
    extra = _report()
    extra["untrusted"] = True
    with pytest.raises(GateInputError, match="identity is malformed"):
        validate_normalized_report(extra)


def test_normalized_file_and_branch_negative_matrix() -> None:
    report = _report()
    report["files"] = {PATH: []}
    with pytest.raises(GateInputError, match="file report is malformed"):
        validate_normalized_report(report)

    report = _report()
    report["files"][PATH]["branches"] = []
    with pytest.raises(GateInputError, match="branches must be an object"):
        validate_normalized_report(report)

    for origin, branch in [(1, {"covered": 1, "missed": 0}), ("x", {}),
                           ("01", {}), ("2", {}), ("1", [])]:
        report = _report()
        report["files"][PATH]["branches"] = {origin: branch}
        with pytest.raises(GateInputError, match="malformed branch origin"):
            validate_normalized_report(report)

    report = _report(branches={"1": {"covered": 0, "missed": 0}})
    with pytest.raises(GateInputError, match="empty branch counter"):
        validate_normalized_report(report)

    report = _report()
    report["totals"] = []
    with pytest.raises(GateInputError, match="totals are malformed"):
        validate_normalized_report(report)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda entry: entry.update(id=""),
        lambda entry: entry.update(fileGlob=""),
        lambda entry: entry.update(fileGlob="../escape/file.py"),
        lambda entry: entry.update(fileGlob="a/b/c/*"),
        lambda entry: entry.update(reason=" "),
        lambda entry: entry.update(owner=" "),
        lambda entry: entry.update(reviewer=" "),
        lambda entry: entry.update(expiresOn="bad"),
        lambda entry: entry.update(compensatingIntegrationTest=[]),
        lambda entry: entry["compensatingIntegrationTest"]["receipt"].update(artifact="../x"),
        lambda entry: entry["compensatingIntegrationTest"]["receipt"].update(artifact="results/x"),
    ],
)
def test_exclusion_contract_negative_matrix(mutate) -> None:
    entry = _exclusion()
    mutate(entry)
    with pytest.raises(GateInputError):
        _evaluate(reports=[], baseline={"schemaVersion": 1, "comparisonBase": BASE, "domains": {}},
                  exclusions=_exclusions([entry]))


def test_exclusion_inventory_and_overlap_negative_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(changed_module, "_verify_compensating_receipt", lambda **kwargs: None)
    with pytest.raises(GateInputError, match="exclusions must use schema"):
        _evaluate(exclusions={})
    with pytest.raises(GateInputError, match="coverage exclusion must be an object"):
        _evaluate(exclusions=_exclusions([None]))
    duplicate = [_exclusion(), _exclusion()]
    with pytest.raises(GateInputError, match="id must be unique"):
        _evaluate(exclusions=_exclusions(duplicate), repository_root=Path("."))
    first = _exclusion(id="one", fileGlob="python-ecosystem/demo/src/*.py")
    second = _exclusion(id="two", fileGlob=PATH)
    with pytest.raises(GateInputError, match="multiple coverage exclusions"):
        _evaluate(exclusions=_exclusions([first, second]), reports=[],
                  baseline={"schemaVersion": 1, "comparisonBase": BASE, "domains": {}},
                  repository_root=Path("."))


def test_evaluate_top_level_negative_matrix() -> None:
    with pytest.raises(GateInputError, match="changes must use schema"):
        _evaluate(changes={})
    changes = _changes()
    changes["baseCommit"] = "bad"
    with pytest.raises(GateInputError, match="commits are malformed"):
        _evaluate(changes=changes)
    with pytest.raises(GateInputError, match="baseline must use schema"):
        _evaluate(baseline={})
    with pytest.raises(GateInputError, match="as_of"):
        _evaluate(as_of="bad")


def test_evaluate_duplicate_and_malformed_inventory_matrix(monkeypatch: pytest.MonkeyPatch) -> None:
    report = _report()
    with pytest.raises(GateInputError, match="duplicate report domain"):
        _evaluate(reports=[report, copy.deepcopy(report)])

    first = _report(module="one")
    second = _report(module="two")
    with pytest.raises(GateInputError, match="ambiguous report path"):
        _evaluate(reports=[first, second])

    changes = _changes()
    changes["files"] = [None]
    with pytest.raises(GateInputError, match="change entry is malformed"):
        _evaluate(changes=changes)

    changes = _changes()
    changes["files"].append(copy.deepcopy(changes["files"][0]))
    with pytest.raises(GateInputError, match="duplicate changed path"):
        _evaluate(changes=changes)

    malformed = _report()
    malformed["files"] = {PATH: []}
    monkeypatch.setattr(changed_module, "validate_normalized_report", lambda report: report["totals"])
    with pytest.raises(GateInputError, match="malformed file report"):
        _evaluate(reports=[malformed])


def test_evaluate_revalidates_the_selected_file_and_new_domain_totals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NonMappingFile:
        def get(self, key):
            return None

    class SplitViewFiles(dict):
        def items(self):
            return super().items()

        def __getitem__(self, key):
            return NonMappingFile()

    report = _report()
    report["files"] = SplitViewFiles(report["files"])
    with pytest.raises(GateInputError, match="file report is malformed"):
        _evaluate(reports=[report])

    malformed = _report(module="new")
    malformed["totals"] = []
    monkeypatch.setattr(changed_module, "validate_normalized_report", lambda value: value.get("totals"))
    with pytest.raises(GateInputError, match="totals are malformed"):
        _evaluate(
            changes=_changes(lines=[]),
            reports=[malformed],
            baseline={"schemaVersion": 1, "comparisonBase": BASE, "domains": {}},
        )


def test_evaluate_all_change_and_baseline_outcomes(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _evaluate(changes=_changes(critical=False)).passed
    assert _evaluate(changes=_changes(status="deleted")).passed
    assert _evaluate(changes=_changes(lines=[2])).changed_lines == {"covered": 0, "total": 0}

    uncovered = _report(covered=[], branches={})
    result = _evaluate(reports=[uncovered], baseline=_baseline(uncovered))
    assert f"{PATH}:1 is an uncovered changed line" in result.failures

    no_branch = _report(branches={})
    result = _evaluate(reports=[no_branch], baseline=_baseline(no_branch))
    assert result.changed_branches == {"covered": 0, "total": 0}

    malformed_change_report = _report()
    malformed_change_report["files"][PATH] = []
    monkeypatch.setattr(changed_module, "validate_normalized_report", lambda report: report["totals"])
    with pytest.raises(GateInputError, match="malformed file report"):
        _evaluate(reports=[malformed_change_report])


def test_evaluate_baseline_domain_and_new_domain_matrix(monkeypatch: pytest.MonkeyPatch) -> None:
    report = _report()
    malformed_domain = _baseline(report)
    malformed_domain["domains"] = {1: {}}
    with pytest.raises(GateInputError, match="baseline domain is malformed"):
        _evaluate(reports=[report], baseline=malformed_domain)

    missing = _baseline(report)
    missing["domains"]["python:missing"] = copy.deepcopy(report["totals"])
    result = _evaluate(reports=[report], baseline=missing)
    assert "python:missing has no current aggregate report" in result.failures

    monkeypatch.setattr(changed_module, "validate_normalized_report", lambda value: value.get("totals"))
    malformed = _report()
    malformed["totals"] = []
    with pytest.raises(GateInputError, match="totals are malformed"):
        _evaluate(reports=[malformed], baseline=_baseline(report))

    full_new = _report(module="python-ecosystem/new")
    result = _evaluate(changes=_changes(lines=[]), reports=[full_new],
                       baseline={"schemaVersion": 1, "comparisonBase": BASE, "domains": {}})
    assert result.passed


def test_baseline_totals_and_aggregate_negative_matrix(monkeypatch: pytest.MonkeyPatch) -> None:
    for totals in [None, {"lines": None, "branches": {"covered": 0, "total": 0}}]:
        with pytest.raises(GateInputError):
            baseline_module._totals({"totals": totals}, "domain")

    with pytest.raises(GateInputError, match="language or report set"):
        aggregate_normalized_reports([], language="python")
    with pytest.raises(GateInputError, match="requires module reports"):
        aggregate_normalized_reports([_report(module="@repository")], language="python")
    with pytest.raises(GateInputError, match="not branch-instrumented"):
        aggregate_normalized_reports([_report(instrumented=False)], language="python")

    first = _report(module="one", path="one.py")
    second = _report(module="two", path="two.py", adapter="other-adapter")
    with pytest.raises(GateInputError, match="one exact version"):
        aggregate_normalized_reports([first, second], language="python")

    monkeypatch.setattr(baseline_module, "validate_normalized_report", lambda report: report["totals"])
    missing_adapter_identity = _report()
    missing_adapter_identity["adapter"] = None
    with pytest.raises(GateInputError, match="lacks adapter identity"):
        aggregate_normalized_reports([missing_adapter_identity], language="python")

    invalid_identity = _report()
    invalid_identity["module"] = ""
    with pytest.raises(GateInputError, match="report identity is malformed"):
        _capture_unbound_coverage_baseline(
            [invalid_identity],
            comparison_base=BASE,
            source_snapshot_sha256="a" * 64,
        )


def test_baseline_authoritative_aggregate_negative_matrix() -> None:
    only_aggregate = _report(module="@repository", files={})
    with pytest.raises(GateInputError, match="no module reports"):
        _capture_unbound_coverage_baseline(
            [only_aggregate],
            comparison_base=BASE,
            source_snapshot_sha256="a" * 64,
        )

    module = _report(module="one", path="one.py")
    empty_aggregate = _report(module="@repository", files={})
    with pytest.raises(GateInputError, match="does not match module reports"):
        _capture_unbound_coverage_baseline(
            [module, empty_aggregate],
            comparison_base=BASE,
            source_snapshot_sha256="a" * 64,
        )


def test_source_snapshot_negative_matrix(tmp_path: Path) -> None:
    with pytest.raises(GateInputError, match="report set is empty"):
        _capture_unbound_source_snapshot([], repository_root=tmp_path)

    source = tmp_path / PATH
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    module = _report()
    aggregate = _report(module="@repository")
    snapshot = _capture_unbound_source_snapshot(
        [aggregate, module], repository_root=tmp_path
    )
    assert snapshot["files"][0]["path"] == PATH

    with pytest.raises(GateInputError, match="duplicate source snapshot path"):
        _capture_unbound_source_snapshot(
            [module, copy.deepcopy(module)], repository_root=tmp_path
        )

    missing = _report(path="python-ecosystem/demo/src/missing.py")
    with pytest.raises(GateInputError, match="not a regular file"):
        _capture_unbound_source_snapshot([missing], repository_root=tmp_path)

    empty = _report(files={})
    with pytest.raises(GateInputError, match="contains no source files"):
        _capture_unbound_source_snapshot([empty], repository_root=tmp_path)

    with pytest.raises(GateInputError, match="contract is malformed"):
        _verify_unbound_source_snapshot({}, repository_root=tmp_path)
    with pytest.raises(GateInputError, match="entry is malformed"):
        _verify_unbound_source_snapshot(
            {"schemaVersion": 1, "files": [None]}, repository_root=tmp_path
        )
    with pytest.raises(GateInputError, match="malformed or unsorted"):
        _verify_unbound_source_snapshot(
            {"schemaVersion": 1, "files": [{"path": "", "sha256": "bad"}]},
            repository_root=tmp_path,
        )


def test_source_snapshot_rejects_symlink_ancestor_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "rules.py").write_text("VALUE = 1\n", encoding="utf-8")
    os.symlink(outside, tmp_path / "linked")
    report = _report(path="linked/rules.py")
    with pytest.raises(GateInputError, match="escapes repository"):
        _capture_unbound_source_snapshot([report], repository_root=tmp_path)


@pytest.mark.parametrize("value", [None, True, -1])
def test_normalizer_exact_integer_negative_matrix(value: object) -> None:
    with pytest.raises(GateInputError):
        normalized_module._exact_nonnegative_int(value, "value")


def test_xml_helpers_negative_matrix(tmp_path: Path) -> None:
    for attributes in [{}, {"missed": "x", "covered": "1"}, {"missed": "-1", "covered": "0"}]:
        with pytest.raises(GateInputError, match="counter is malformed"):
            normalized_module._xml_counter(ET.Element("counter", attributes), "counter")

    outside = tmp_path.parent / f"{tmp_path.name}-source.py"
    outside.write_text("VALUE = 1\n", encoding="utf-8")
    with pytest.raises(GateInputError, match="stay inside"):
        normalized_module._repo_relative(outside, tmp_path, "source")
    with pytest.raises(GateInputError, match="does not identify"):
        normalized_module._repo_relative(tmp_path / "missing.py", tmp_path, "source")

    with pytest.raises(GateInputError, match="cannot read"):
        normalized_module._parse_jacoco_xml(tmp_path / "missing.xml")
    malformed = tmp_path / "malformed.xml"
    malformed.write_text("<report>", encoding="utf-8")
    with pytest.raises(GateInputError, match="malformed JaCoCo XML"):
        normalized_module._parse_jacoco_xml(malformed)

    with pytest.raises(GateInputError, match="lacks exact"):
        normalized_module._report_counters(ET.fromstring("<report/>"))


def _java_tree(tmp_path: Path) -> tuple[Path, Path, Path]:
    repository = tmp_path / "repo"
    source_root = repository / "java/src/main/java"
    source = source_root / "example/Rules.java"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("package example; class Rules {}\n", encoding="utf-8")
    return repository, source_root, source


def _jacoco_report(package: str = "example", source: str = "Rules.java", line: str = "") -> str:
    return (
        f'<report name="demo"><package name="{package}"><sourcefile name="{source}">'
        f'{line}</sourcefile></package>'
        '<counter type="LINE" missed="0" covered="0"/>'
        '<counter type="BRANCH" missed="0" covered="0"/></report>'
    )


def _normalize_xml(tmp_path: Path, xml: str, *, module: str = "java", source_roots=None,
                   tool_version: str = "0.8.11"):
    repository, source_root, _ = _java_tree(tmp_path)
    report = repository / "report.xml"
    report.write_text(xml, encoding="utf-8")
    return normalized_module.normalize_jacoco_xml(
        report, module=module, source_root=source_root if source_roots is None else source_roots,
        repository_root=repository, tool_version=tool_version,
        source_inventory_sha256=INVENTORY_SHA,
    )


def test_jacoco_identity_root_and_source_negative_matrix(tmp_path: Path) -> None:
    repository, source_root, _ = _java_tree(tmp_path)
    root = ET.fromstring(_jacoco_report())
    for element, module, version in [(ET.Element("bad"), "java", "v"), (root, "", "v"), (root, "java", "")]:
        with pytest.raises(GateInputError, match="identity is malformed"):
            _normalize_jacoco_element(
                element, module=module, source_root=source_root,
                repository_root=repository, tool_version=version,
            )
    with pytest.raises(GateInputError, match="at least one source root"):
        _normalize_jacoco_element(
            root, module="java", source_root=[], repository_root=repository, tool_version="v"
        )
    with pytest.raises(GateInputError, match="escapes repository"):
        _normalize_jacoco_element(
            root, module="java", source_root=[tmp_path], repository_root=repository, tool_version="v"
        )
    with pytest.raises(GateInputError, match="real directory"):
        _normalize_jacoco_element(
            root, module="java", source_root=[repository / "missing"],
            repository_root=repository, tool_version="v",
        )
    with pytest.raises(GateInputError, match="must be unique"):
        _normalize_jacoco_element(
            root, module="java", source_root=[source_root, source_root],
            repository_root=repository, tool_version="v",
        )


@pytest.mark.parametrize(
    ("xml", "message"),
    [
        ('<report><package><counter type="LINE" missed="0" covered="0"/></package>'
         '<counter type="LINE" missed="0" covered="0"/><counter type="BRANCH" missed="0" covered="0"/></report>',
         "package is missing"),
        (_jacoco_report(source=""), "source file is missing"),
        (_jacoco_report(package="../example"), "name is unsafe"),
        (_jacoco_report(source="../Rules.java"), "name is unsafe"),
        (_jacoco_report(source="Missing.java"), "0 repository matches"),
        (_jacoco_report(line='<line nr="x" mi="0" ci="0" mb="0" cb="0"/>'), "malformed JaCoCo line"),
        (_jacoco_report(line='<line nr="0" mi="0" ci="0" mb="0" cb="0"/>'), "malformed JaCoCo line"),
        (_jacoco_report(line='<line nr="1" mi="0" ci="0" mb="x" cb="0"/>'), "malformed JaCoCo branch"),
        (_jacoco_report(line='<line nr="1" mi="0" ci="0" mb="-1" cb="0"/>'), "malformed JaCoCo branch"),
    ],
)
def test_jacoco_source_and_line_negative_matrix(tmp_path: Path, xml: str, message: str) -> None:
    with pytest.raises(GateInputError, match=message):
        _normalize_xml(tmp_path, xml)


def test_jacoco_nonbranch_line_takes_zero_branch_path(tmp_path: Path) -> None:
    xml = (
        '<report><package name="example"><sourcefile name="Rules.java">'
        '<line nr="1" mi="0" ci="1" mb="0" cb="0"/></sourcefile></package>'
        '<counter type="LINE" missed="0" covered="1"/>'
        '<counter type="BRANCH" missed="0" covered="0"/></report>'
    )
    report = _normalize_xml(tmp_path, xml)
    assert report["totals"]["branches"] == {"covered": 0, "total": 0}

    zero_instruction_xml = (
        '<report><package name="example"><sourcefile name="Rules.java">'
        '<line nr="1" mi="0" ci="0" mb="0" cb="0"/></sourcefile></package>'
        '<counter type="LINE" missed="0" covered="0"/>'
        '<counter type="BRANCH" missed="0" covered="0"/></report>'
    )
    zero_instruction = _normalize_xml(tmp_path, zero_instruction_xml)
    assert zero_instruction["files"]["java/src/main/java/example/Rules.java"]["executableLines"] == []


def test_jacoco_aggregate_policy_and_group_negative_matrix(tmp_path: Path) -> None:
    repository, source_root, _ = _java_tree(tmp_path)
    report = repository / "aggregate.xml"
    report.write_text('<report><counter type="LINE" missed="0" covered="0"/>'
                      '<counter type="BRANCH" missed="0" covered="0"/></report>', encoding="utf-8")
    with pytest.raises(GateInputError, match="policy is empty"):
        _normalize_jacoco_aggregate(
            report, module_groups={}, repository_root=repository, tool_version="v"
        )
    for policy in [{"": ("g", source_root)}, {"m": []}, {"m": ("", source_root)},
                   {"m": ("g", "not-a-path")}]:
        with pytest.raises(GateInputError, match="policy is malformed"):
            _normalize_jacoco_aggregate(
                report, module_groups=policy, repository_root=repository, tool_version="v"
            )
    with pytest.raises(GateInputError, match="duplicate JaCoCo aggregate group policy"):
        _normalize_jacoco_aggregate(
            report, module_groups={"one": ("g", source_root), "two": ("g", source_root)},
            repository_root=repository, tool_version="v",
        )
    with pytest.raises(GateInputError, match="aggregate identity"):
        _normalize_jacoco_aggregate(
            report, module_groups={"one": ("g", source_root)},
            repository_root=repository, tool_version="",
        )

    report.write_text('<report><group/><counter type="LINE" missed="0" covered="0"/>'
                      '<counter type="BRANCH" missed="0" covered="0"/></report>', encoding="utf-8")
    with pytest.raises(GateInputError, match="malformed/duplicate group"):
        _normalize_jacoco_aggregate(
            report, module_groups={"one": ("g", source_root)},
            repository_root=repository, tool_version="v",
        )


def test_jacoco_aggregate_duplicate_path_and_counter_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, source_root, _ = _java_tree(tmp_path)
    report_path = repository / "aggregate.xml"
    report_path.write_text(
        '<report><group name="one"/><group name="two"/>'
        '<counter type="LINE" missed="0" covered="0"/>'
        '<counter type="BRANCH" missed="0" covered="0"/></report>', encoding="utf-8"
    )
    emitted = _report(language="java", module="one", path="java/src/main/java/example/Rules.java",
                      files={})
    calls = iter([emitted, copy.deepcopy(emitted)])
    monkeypatch.setattr(normalized_module, "_normalize_jacoco_element", lambda *a, **k: next(calls))
    # Empty reports exercise the successful group loop; a shared file exercises duplicate detection.
    aggregate, modules = _normalize_jacoco_aggregate(
        report_path, module_groups={"one": ("one", source_root), "two": ("two", source_root)},
        repository_root=repository, tool_version="v",
    )
    assert len(modules) == 2 and aggregate["totals"]["lines"]["total"] == 0

    shared = _report(language="java", module="one", path="java/src/main/java/example/Rules.java")
    calls = iter([shared, copy.deepcopy(shared)])
    monkeypatch.setattr(normalized_module, "_normalize_jacoco_element", lambda *a, **k: next(calls))
    with pytest.raises(GateInputError, match="duplicate aggregate source path"):
        _normalize_jacoco_aggregate(
            report_path, module_groups={"one": ("one", source_root), "two": ("two", source_root)},
            repository_root=repository, tool_version="v",
        )

    report_path.write_text(
        '<report><group name="one"/><counter type="LINE" missed="0" covered="1"/>'
        '<counter type="BRANCH" missed="0" covered="0"/></report>', encoding="utf-8"
    )
    monkeypatch.setattr(normalized_module, "_normalize_jacoco_element", lambda *a, **k: emitted)
    with pytest.raises(GateInputError, match="root counters do not match"):
        _normalize_jacoco_aggregate(
            report_path, module_groups={"one": ("one", source_root)},
            repository_root=repository, tool_version="v",
        )


def test_partition_contract_negative_matrix(tmp_path: Path) -> None:
    aggregate = _report(language="java", module="@repository", files={})
    malformed = copy.deepcopy(aggregate)
    malformed["module"] = "not-repository"
    with pytest.raises(GateInputError, match="aggregate contract is malformed"):
        normalized_module.partition_jacoco_aggregate(
            malformed, module_source_roots={}, repository_root=tmp_path
        )
    source_root = tmp_path / "java/src"
    source_root.mkdir(parents=True)
    with pytest.raises(GateInputError, match="identity is malformed"):
        normalized_module.partition_jacoco_aggregate(
            aggregate, module_source_roots={"": source_root}, repository_root=tmp_path
        )
    outside = tmp_path.parent / f"{tmp_path.name}-java-src"
    outside.mkdir()
    with pytest.raises(GateInputError, match="escapes repository"):
        normalized_module.partition_jacoco_aggregate(
            aggregate, module_source_roots={"module": outside}, repository_root=tmp_path
        )

    source = source_root / "Rules.java"
    source.write_text("class Rules {}\n", encoding="utf-8")
    unmatched = _report(language="java", module="@repository", path="other/Rules.java")
    with pytest.raises(GateInputError, match="0 module matches"):
        normalized_module.partition_jacoco_aggregate(
            unmatched, module_source_roots={"module": source_root}, repository_root=tmp_path
        )

    monkey = copy.deepcopy(aggregate)
    monkey["totals"]["lines"] = {"covered": 1, "total": 1}
    with pytest.raises(GateInputError):
        normalized_module.partition_jacoco_aggregate(
            monkey, module_source_roots={"module": source_root}, repository_root=tmp_path
        )


def test_partition_defensive_sum_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "java/src"
    source_root.mkdir(parents=True)
    aggregate = _report(language="java", module="@repository", files={})
    aggregate["totals"]["lines"] = {"covered": 1, "total": 1}
    monkeypatch.setattr(normalized_module, "validate_normalized_report", lambda report: report.get("totals"))
    with pytest.raises(GateInputError, match="partitioned JaCoCo counters"):
        normalized_module.partition_jacoco_aggregate(
            aggregate, module_source_roots={"module": source_root}, repository_root=tmp_path
        )


def test_strict_json_and_array_helpers_negative_matrix(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"files": {}, "files": {}}', encoding="utf-8")
    with pytest.raises(GateInputError, match="duplicate JSON key"):
        normalized_module._load_strict_json(duplicate)
    malformed = tmp_path / "malformed.json"
    malformed.write_bytes(b"\xff")
    with pytest.raises(GateInputError, match="malformed coverage.py JSON"):
        normalized_module._load_strict_json(malformed)
    array = tmp_path / "array.json"
    array.write_text("[]", encoding="utf-8")
    with pytest.raises(GateInputError, match="must be an object"):
        normalized_module._load_strict_json(array)

    for value in [None, [0], [1, 1]]:
        with pytest.raises(GateInputError):
            normalized_module._int_list(value, "lines")
    for value in [None, [1], [[0, 1]], [[True, 1]], [[1, True]], [[1, 2], [1, 2]]]:
        with pytest.raises(GateInputError):
            normalized_module._branch_arcs(value, "branches")


def _coverage_raw() -> dict:
    return {
        "meta": {"format": 3, "version": "7.15.1", "branch_coverage": True},
        "files": {
            "src/rules.py": {
                "executed_lines": [1], "missing_lines": [],
                "executed_branches": [], "missing_branches": [],
                "summary": {"covered_lines": 1, "num_statements": 1,
                            "covered_branches": 0, "num_branches": 0},
            }
        },
        "totals": {"covered_lines": 1, "num_statements": 1,
                   "covered_branches": 0, "num_branches": 0},
    }


def _run_coverage_raw(tmp_path: Path, raw: object, *, prefix: str = "python-ecosystem/demo",
                      module: str = "python-ecosystem/demo"):
    source = tmp_path / PATH
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    report = tmp_path / "coverage.json"
    report.write_text(json.dumps(raw), encoding="utf-8")
    return normalized_module.normalize_coveragepy_json(
        report, module=module, source_prefix=prefix, repository_root=tmp_path,
        source_inventory_sha256=INVENTORY_SHA,
    )


def test_coveragepy_top_level_negative_matrix(tmp_path: Path) -> None:
    cases = []
    raw = _coverage_raw(); raw["meta"] = {}; cases.append(raw)
    raw = _coverage_raw(); raw["meta"]["version"] = ""; cases.append(raw)
    raw = _coverage_raw(); raw["files"] = []; cases.append(raw)
    raw = _coverage_raw(); raw["files"] = {"src/rules.py": []}; cases.append(raw)
    for raw in cases:
        with pytest.raises(GateInputError):
            _run_coverage_raw(tmp_path, raw)


def test_coveragepy_path_and_file_negative_matrix(tmp_path: Path) -> None:
    raw = _coverage_raw()
    raw["files"] = {"../rules.py": next(iter(raw["files"].values()))}
    with pytest.raises(GateInputError, match="path must be repository-relative"):
        _run_coverage_raw(tmp_path, raw)

    raw = _coverage_raw()
    raw["files"] = {
        "src/rules.py": next(iter(raw["files"].values())),
        PATH: copy.deepcopy(next(iter(raw["files"].values()))),
    }
    with pytest.raises(GateInputError, match="duplicate coverage.py source path"):
        _run_coverage_raw(tmp_path, raw)

    raw = _coverage_raw()
    file_data = next(iter(raw["files"].values()))
    file_data["missing_lines"] = [1]
    with pytest.raises(GateInputError, match="both executed and missing"):
        _run_coverage_raw(tmp_path, raw)

    raw = _coverage_raw()
    file_data = next(iter(raw["files"].values()))
    file_data.update(executed_branches=[[1, -1]], missing_branches=[[1, -1]])
    with pytest.raises(GateInputError, match="branch is both"):
        _run_coverage_raw(tmp_path, raw)

    raw = _coverage_raw()
    file_data = next(iter(raw["files"].values()))
    file_data.update(executed_branches=[[2, -1]])
    with pytest.raises(GateInputError, match="origin is not executable"):
        _run_coverage_raw(tmp_path, raw)


def test_coveragepy_already_prefixed_path(tmp_path: Path) -> None:
    raw = _coverage_raw()
    raw["files"] = {PATH: next(iter(raw["files"].values()))}
    report = _run_coverage_raw(tmp_path, raw)
    assert list(report["files"]) == [PATH]


def _correctness_policy() -> dict:
    return {
        "schemaVersion": 1,
        "scopeRoots": ["python-ecosystem"],
        "languageSuffixes": {".java": "java", ".py": "python"},
        "nonCriticalPaths": [],
    }


def _content_bound_changes(entry: dict, *, dirty_digest: object | None = None) -> dict:
    result = {
        "schemaVersion": 1,
        "baseCommit": BASE,
        "headCommit": HEAD,
        "mergeBase": BASE,
        "dirty": True,
        "files": [entry],
        "correctnessPolicy": {"path": "policy.json", "sha256": "a" * 64},
    }
    if dirty_digest is not None:
        result["comparisonBaseDirtyStateSha256"] = dirty_digest
    return result


def test_evaluator_residual_top_level_and_change_contract_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = _correctness_policy()
    bound = _content_bound_changes(
        {
            "path": PATH,
            "status": "modified",
            "correctnessCritical": True,
            "language": "python",
            "changedLines": [1],
            "contentSha256": "b" * 64,
            "previousContentSha256": "c" * 64,
        }
    )
    with pytest.raises(GateInputError, match="policy identity is invalid"):
        _evaluate_unbound_gate(
            changes=bound, reports=[],
            baseline={"schemaVersion": 1, "comparisonBase": BASE, "domains": {}},
            exclusions=_exclusions(), as_of="2026-07-14",
        )

    malformed_dirty = _content_bound_changes(bound["files"][0], dirty_digest="bad")
    with pytest.raises(GateInputError, match="change inventory contract is malformed"):
        _evaluate_unbound_gate(
            changes=malformed_dirty, reports=[],
            baseline={"schemaVersion": 1, "comparisonBase": BASE, "domains": {}},
            exclusions=_exclusions(), as_of="2026-07-14",
            correctness_policy=policy, correctness_policy_path="policy.json",
            correctness_policy_sha256="a" * 64,
        )
    with pytest.raises(GateInputError, match="requires a file-level baseline"):
        _evaluate_unbound_gate(
            changes=_changes(), reports=[],
            baseline={"schemaVersion": 1, "comparisonBase": BASE, "domains": {}},
            exclusions=_exclusions(), as_of="2026-07-14", source_inventory={},
        )
    with pytest.raises(GateInputError, match="baseline source inventory contract"):
        _evaluate_unbound_gate(
            changes=_changes(), reports=[],
            baseline={
                "schemaVersion": 1, "comparisonBase": BASE, "domains": {}, "files": {}
            },
            exclusions=_exclusions(), as_of="2026-07-14",
        )

    unhashable = _changes()
    unhashable["files"] = [object()]
    with pytest.raises(GateInputError, match="cannot be canonically hashed"):
        _evaluate(
            changes=unhashable, reports=[],
            baseline={"schemaVersion": 1, "comparisonBase": BASE, "domains": {}},
        )

    renamed = _changes()
    renamed["files"][0]["status"] = "renamed"
    with pytest.raises(GateInputError, match="entry contract is malformed"):
        _evaluate(changes=renamed)
    renamed["files"][0]["oldPath"] = PATH
    with pytest.raises(GateInputError, match="old path must differ"):
        _evaluate(changes=renamed)
    deleted = _changes(status="deleted")
    deleted["files"][0]["changedLines"] = [1]
    with pytest.raises(GateInputError, match="deleted change cannot"):
        _evaluate(changes=deleted)

    with pytest.raises(GateInputError, match="require a repository root"):
        _evaluate_unbound_gate(
            changes=bound, reports=[],
            baseline={"schemaVersion": 1, "comparisonBase": BASE, "domains": {}},
            exclusions=_exclusions(), as_of="2026-07-14",
            correctness_policy=policy, correctness_policy_path="policy.json",
            correctness_policy_sha256="a" * 64,
        )

    source = tmp_path / PATH
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    current_digest = changed_module.hashlib.sha256(source.read_bytes()).hexdigest()
    bound["files"][0]["contentSha256"] = current_digest
    monkeypatch.setattr(
        "quality_gates.git_changes._git_blob_sha256", lambda *args, **kwargs: "d" * 64
    )
    with pytest.raises(GateInputError, match="previous source identity"):
        _evaluate_unbound_gate(
            changes=bound, reports=[],
            baseline={"schemaVersion": 1, "comparisonBase": BASE, "domains": {}},
            exclusions=_exclusions(), as_of="2026-07-14", repository_root=tmp_path,
            correctness_policy=policy, correctness_policy_path="policy.json",
            correctness_policy_sha256="a" * 64,
        )

    target = tmp_path / "README.md"
    target.write_text("readme\n", encoding="utf-8")
    target_digest = changed_module.hashlib.sha256(target.read_bytes()).hexdigest()
    old_critical = _content_bound_changes(
        {
            "path": "README.md",
            "oldPath": PATH,
            "status": "renamed",
            "correctnessCritical": True,
            "language": "python",
            "changedLines": [],
            "contentSha256": target_digest,
            "previousContentSha256": "c" * 64,
        }
    )
    monkeypatch.setattr(
        "quality_gates.git_changes._git_blob_sha256", lambda *args, **kwargs: "c" * 64
    )
    result = _evaluate_unbound_gate(
        changes=old_critical, reports=[],
        baseline={"schemaVersion": 1, "comparisonBase": BASE, "domains": {}},
        exclusions=_exclusions(), as_of="2026-07-14", repository_root=tmp_path,
        correctness_policy=policy, correctness_policy_path="policy.json",
        correctness_policy_sha256="a" * 64,
    )
    assert "README.md has no coverage report" in result.failures


def test_evaluator_exclusion_must_match_once_and_requires_repository_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(changed_module, "_verify_compensating_receipt", lambda **kwargs: None)
    unmatched = _exclusion(fileGlob="python-ecosystem/other/src/value.py")
    with pytest.raises(GateInputError, match="must match exactly one"):
        _evaluate(exclusions=_exclusions([unmatched]))
    with pytest.raises(GateInputError, match="require a repository root"):
        _evaluate(exclusions=_exclusions([_exclusion()]))


def test_content_bound_deleted_and_noncritical_rename_source_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = _correctness_policy()
    monkeypatch.setattr(
        "quality_gates.git_changes._git_blob_sha256", lambda *args, **kwargs: "c" * 64
    )
    deleted = _content_bound_changes(
        {
            "path": PATH,
            "status": "deleted",
            "correctnessCritical": True,
            "language": "python",
            "changedLines": [],
            "previousContentSha256": "c" * 64,
        }
    )
    assert _evaluate_unbound_gate(
        changes=deleted, reports=[],
        baseline={"schemaVersion": 1, "comparisonBase": BASE, "domains": {}},
        exclusions=_exclusions(), as_of="2026-07-14", repository_root=tmp_path,
        correctness_policy=policy, correctness_policy_path="policy.json",
        correctness_policy_sha256="a" * 64,
    ).passed

    source = tmp_path / PATH
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    renamed = _content_bound_changes(
        {
            "path": PATH,
            "oldPath": "README.md",
            "status": "renamed",
            "correctnessCritical": True,
            "language": "python",
            "changedLines": [],
            "contentSha256": changed_module.hashlib.sha256(source.read_bytes()).hexdigest(),
            "previousContentSha256": "c" * 64,
        }
    )
    result = _evaluate_unbound_gate(
        changes=renamed, reports=[],
        baseline={"schemaVersion": 1, "comparisonBase": BASE, "domains": {}},
        exclusions=_exclusions(), as_of="2026-07-14", repository_root=tmp_path,
        correctness_policy=policy, correctness_policy_path="policy.json",
        correctness_policy_sha256="a" * 64,
    )
    assert f"{PATH} has no coverage report" in result.failures
