"""Strict adapters for JaCoCo XML and coverage.py branch JSON."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

from .changed_coverage import GateInputError, validate_normalized_report


def _exact_nonnegative_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise GateInputError(f"{field} must be a non-negative integer")
    return value


def _xml_counter(element: ET.Element, field: str) -> dict[str, int]:
    try:
        missed = int(element.attrib["missed"])
        covered = int(element.attrib["covered"])
    except (KeyError, ValueError) as error:
        raise GateInputError(f"{field} counter is malformed") from error
    if missed < 0 or covered < 0:
        raise GateInputError(f"{field} counter is malformed")
    return {"covered": covered, "total": covered + missed}


def _repo_relative(path: Path, repository_root: Path, field: str) -> str:
    root = repository_root.resolve()
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as error:
        raise GateInputError(f"{field} must stay inside the repository") from error
    if not resolved.is_file():
        raise GateInputError(f"{field} does not identify a source file")
    return relative.as_posix()


def _parse_jacoco_xml(report_path: Path) -> ET.Element:
    try:
        raw = report_path.read_bytes()
    except OSError as error:
        raise GateInputError("cannot read JaCoCo XML") from error
    if b"<!ENTITY" in raw or b"<!DOCTYPE report [" in raw:
        raise GateInputError("unsafe JaCoCo XML declaration")
    try:
        return ET.fromstring(raw)
    except ET.ParseError as error:
        raise GateInputError("malformed JaCoCo XML") from error


def _report_counters(root: ET.Element) -> dict[str, dict[str, int]]:
    report_counters: dict[str, dict[str, int]] = {}
    for child in root:
        counter_type = child.attrib.get("type")
        if child.tag == "counter" and counter_type in {"LINE", "BRANCH"}:
            if counter_type in report_counters:
                raise GateInputError(f"duplicate JaCoCo report counter: {counter_type}")
            report_counters[counter_type] = _xml_counter(
                child, f"JaCoCo report {counter_type}"
            )
    if set(report_counters) == {"LINE"}:
        # JaCoCo legitimately omits a BRANCH counter for a project with no
        # branch instructions.  Accept that one representation only when the
        # already-required per-line counters independently prove an exact 0/0.
        try:
            branch_values = [
                (int(line.attrib["mb"]), int(line.attrib["cb"]))
                for line in root.iter("line")
            ]
        except (KeyError, ValueError) as error:
            raise GateInputError("JaCoCo report lacks exact line or branch totals") from error
        if any(missed < 0 or covered < 0 or missed + covered for missed, covered in branch_values):
            raise GateInputError("JaCoCo report lacks exact line or branch totals")
        nested_branch_counters = [
            counter
            for counter in root.iter("counter")
            if counter.attrib.get("type") == "BRANCH"
        ]
        if any(
            counter["covered"] != 0 or counter["total"] != 0
            for counter in (
                _xml_counter(element, "nested JaCoCo BRANCH")
                for element in nested_branch_counters
            )
        ):
            raise GateInputError("JaCoCo report lacks exact line or branch totals")
        report_counters["BRANCH"] = {"covered": 0, "total": 0}
    elif set(report_counters) != {"LINE", "BRANCH"}:
        raise GateInputError("JaCoCo report lacks exact line or branch totals")
    return report_counters


def normalize_jacoco_xml(
    report_path: Path,
    *,
    module: str,
    source_root: Path | Sequence[Path],
    repository_root: Path,
    tool_version: str,
    source_inventory_sha256: str,
) -> dict[str, Any]:
    """Normalize one module report without rounding or double-counting XML levels."""

    root = _parse_jacoco_xml(report_path)
    return _normalize_jacoco_element(
        root,
        module=module,
        source_root=source_root,
        repository_root=repository_root,
        tool_version=tool_version,
        source_inventory_sha256=source_inventory_sha256,
    )


def _normalize_jacoco_element(
    root: ET.Element,
    *,
    module: str,
    source_root: Path | Sequence[Path],
    repository_root: Path,
    tool_version: str,
    source_inventory_sha256: str,
) -> dict[str, Any]:
    if (
        root.tag not in {"report", "group"}
        or not module
        or not tool_version
        or not isinstance(source_inventory_sha256, str)
        or len(source_inventory_sha256) != 64
        or any(character not in "0123456789abcdef" for character in source_inventory_sha256)
    ):
        raise GateInputError("JaCoCo report identity is malformed")

    source_roots = [source_root] if isinstance(source_root, Path) else list(source_root)
    if not source_roots:
        raise GateInputError("JaCoCo report needs at least one source root")
    repository = repository_root.resolve()
    resolved_roots: list[Path] = []
    for root_path in source_roots:
        resolved = root_path.resolve()
        try:
            resolved.relative_to(repository)
        except ValueError as error:
            raise GateInputError("JaCoCo source root escapes repository") from error
        if not resolved.is_dir() or root_path.is_symlink():
            raise GateInputError("JaCoCo source root must be a real directory")
        resolved_roots.append(resolved)
    if len(resolved_roots) != len(set(resolved_roots)):
        raise GateInputError("JaCoCo source roots must be unique")
    files: dict[str, dict[str, Any]] = {}
    for package in root.iter("package"):
        package_name = package.attrib.get("name")
        if package_name is None:
            raise GateInputError("JaCoCo package is missing its name")
        package_path = PurePosixPath(package_name)
        if "\\" in package_name or package_path.is_absolute() or ".." in package_path.parts:
            raise GateInputError("JaCoCo package or source name is unsafe")
        for sourcefile in package.findall("sourcefile"):
            source_name = sourcefile.attrib.get("name")
            if not source_name:
                raise GateInputError("JaCoCo source file is missing its name")
            source_path = PurePosixPath(source_name)
            if (
                "\\" in source_name
                or source_path.is_absolute()
                or ".." in source_path.parts
                or len(source_path.parts) != 1
            ):
                raise GateInputError("JaCoCo package or source name is unsafe")
            candidates = [
                candidate
                for root_path in resolved_roots
                if (candidate := root_path / package_path / source_name).is_file()
            ]
            if len(candidates) != 1:
                raise GateInputError(
                    f"JaCoCo source path has {len(candidates)} repository matches: "
                    f"{package_name}/{source_name}"
                )
            relative_path = _repo_relative(candidates[0], repository_root, "JaCoCo source path")
            if relative_path in files:
                raise GateInputError(f"duplicate JaCoCo source path: {relative_path}")

            executable: list[int] = []
            covered_lines: list[int] = []
            branches: dict[str, dict[str, int]] = {}
            seen_lines: set[int] = set()
            for line in sourcefile.findall("line"):
                try:
                    number = int(line.attrib["nr"])
                    missed_instructions = int(line.attrib["mi"])
                    covered_instructions = int(line.attrib["ci"])
                except (KeyError, ValueError) as error:
                    raise GateInputError(f"malformed JaCoCo line in {relative_path}") from error
                if number <= 0 or number in seen_lines or missed_instructions < 0 or covered_instructions < 0:
                    raise GateInputError(f"malformed JaCoCo line in {relative_path}")
                seen_lines.add(number)
                if "mb" not in line.attrib or "cb" not in line.attrib:
                    raise GateInputError(f"missing JaCoCo branch counters in {relative_path}:{number}")
                try:
                    missed_branches = int(line.attrib["mb"])
                    covered_branches = int(line.attrib["cb"])
                except ValueError as error:
                    raise GateInputError(f"malformed JaCoCo branch counters in {relative_path}:{number}") from error
                if missed_branches < 0 or covered_branches < 0:
                    raise GateInputError(f"malformed JaCoCo branch counters in {relative_path}:{number}")
                if missed_instructions + covered_instructions:
                    executable.append(number)
                    if covered_instructions:
                        covered_lines.append(number)
                if missed_branches + covered_branches:
                    branches[str(number)] = {
                        "covered": covered_branches,
                        "missed": missed_branches,
                    }
            files[relative_path] = {
                "executableLines": sorted(executable),
                "coveredLines": sorted(covered_lines),
                "branches": branches,
            }

    report_counters = _report_counters(root)

    report = {
        "schemaVersion": 1,
        "adapter": "jacoco-xml",
        "language": "java",
        "module": module,
        "toolVersion": tool_version,
        "sourceInventorySha256": source_inventory_sha256,
        "branchInstrumentation": True,
        "files": dict(sorted(files.items())),
        "totals": {
            "lines": report_counters["LINE"],
            "branches": report_counters["BRANCH"],
        },
    }
    validate_normalized_report(report)
    return report


def normalize_jacoco_aggregate_xml(
    report_path: Path,
    *,
    module_groups: Mapping[str, tuple[str, Path]],
    repository_root: Path,
    tool_version: str,
    source_inventory_sha256: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Normalize an authoritative Maven aggregate by its exact project groups."""

    if not module_groups:
        raise GateInputError("JaCoCo aggregate module policy is empty")
    group_to_module: dict[str, tuple[str, Path]] = {}
    for module, value in module_groups.items():
        if (
            not isinstance(module, str)
            or not module
            or not isinstance(value, tuple)
            or len(value) != 2
            or not isinstance(value[0], str)
            or not value[0]
            or not isinstance(value[1], Path)
        ):
            raise GateInputError("JaCoCo aggregate module policy is malformed")
        group_name, source_root = value
        if group_name in group_to_module:
            raise GateInputError(f"duplicate JaCoCo aggregate group policy: {group_name}")
        group_to_module[group_name] = (module, source_root)

    root = _parse_jacoco_xml(report_path)
    if root.tag != "report" or not tool_version:
        raise GateInputError("JaCoCo aggregate identity is malformed")
    groups: dict[str, ET.Element] = {}
    for group in root.findall("group"):
        group_name = group.attrib.get("name")
        if not group_name or group_name in groups:
            raise GateInputError("JaCoCo aggregate contains a malformed/duplicate group")
        groups[group_name] = group
    if set(groups) != set(group_to_module):
        raise GateInputError("JaCoCo aggregate groups do not match module policy")

    modules: list[dict[str, Any]] = []
    files: dict[str, Any] = {}
    totals = {
        "lines": {"covered": 0, "total": 0},
        "branches": {"covered": 0, "total": 0},
    }
    for group_name in sorted(groups):
        module, source_root = group_to_module[group_name]
        report = _normalize_jacoco_element(
            groups[group_name],
            module=module,
            source_root=source_root,
            repository_root=repository_root,
            tool_version=tool_version,
            source_inventory_sha256=source_inventory_sha256,
        )
        for path, file_report in report["files"].items():
            if path in files:
                raise GateInputError(f"duplicate aggregate source path: {path}")
            files[path] = file_report
        for kind in ("lines", "branches"):
            totals[kind]["covered"] += report["totals"][kind]["covered"]
            totals[kind]["total"] += report["totals"][kind]["total"]
        modules.append(report)

    root_counters = _report_counters(root)
    declared = {
        "lines": root_counters["LINE"],
        "branches": root_counters["BRANCH"],
    }
    if declared != totals:
        raise GateInputError("JaCoCo aggregate root counters do not match project groups")
    aggregate = {
        "schemaVersion": 1,
        "adapter": "jacoco-xml",
        "language": "java",
        "module": "@repository",
        "toolVersion": tool_version,
        "sourceInventorySha256": source_inventory_sha256,
        "branchInstrumentation": True,
        "files": dict(sorted(files.items())),
        "totals": totals,
    }
    validate_normalized_report(aggregate)
    return aggregate, sorted(modules, key=lambda report: report["module"])


def partition_jacoco_aggregate(
    aggregate: Mapping[str, Any],
    *,
    module_source_roots: Mapping[str, Path],
    repository_root: Path,
) -> list[dict[str, Any]]:
    """Partition one authoritative aggregate into exact per-module domains."""

    if (
        aggregate.get("schemaVersion") != 1
        or aggregate.get("language") != "java"
        or aggregate.get("module") != "@repository"
        or aggregate.get("branchInstrumentation") is not True
        or not isinstance(aggregate.get("files"), Mapping)
    ):
        raise GateInputError("JaCoCo aggregate contract is malformed")
    validate_normalized_report(aggregate)
    root = repository_root.resolve()
    prefixes: dict[str, str] = {}
    resolved_roots: set[Path] = set()
    for module, source_root in module_source_roots.items():
        if not isinstance(module, str) or not module:
            raise GateInputError("JaCoCo module source-root identity is malformed")
        resolved = source_root.resolve()
        if not resolved.is_dir() or source_root.is_symlink():
            raise GateInputError(
                f"JaCoCo module source root does not identify a source directory: {module}"
            )
        if resolved in resolved_roots:
            raise GateInputError("JaCoCo module source roots must be unique")
        resolved_roots.add(resolved)
        try:
            prefixes[module] = resolved.relative_to(root).as_posix()
        except ValueError as error:
            raise GateInputError(f"JaCoCo module source root escapes repository: {module}") from error

    module_files: dict[str, dict[str, Any]] = {module: {} for module in prefixes}
    for path, file_report in aggregate["files"].items():
        matches = [
            module
            for module, prefix in prefixes.items()
            if path == prefix or path.startswith(prefix + "/")
        ]
        if len(matches) != 1:
            raise GateInputError(f"aggregate source path has {len(matches)} module matches: {path}")
        module_files[matches[0]][path] = file_report

    reports: list[dict[str, Any]] = []
    summed = {
        "lines": {"covered": 0, "total": 0},
        "branches": {"covered": 0, "total": 0},
    }
    for module in sorted(module_files):
        files = module_files[module]
        line_covered = sum(len(file_report["coveredLines"]) for file_report in files.values())
        line_total = sum(len(file_report["executableLines"]) for file_report in files.values())
        branch_covered = sum(
            branch["covered"]
            for file_report in files.values()
            for branch in file_report["branches"].values()
        )
        branch_total = sum(
            branch["covered"] + branch["missed"]
            for file_report in files.values()
            for branch in file_report["branches"].values()
        )
        totals = {
            "lines": {"covered": line_covered, "total": line_total},
            "branches": {"covered": branch_covered, "total": branch_total},
        }
        for kind in ("lines", "branches"):
            summed[kind]["covered"] += totals[kind]["covered"]
            summed[kind]["total"] += totals[kind]["total"]
        report = {
                "schemaVersion": 1,
                "adapter": aggregate.get("adapter"),
                "language": "java",
                "module": module,
                "toolVersion": aggregate.get("toolVersion"),
                "sourceInventorySha256": aggregate.get("sourceInventorySha256"),
                "branchInstrumentation": True,
                "files": dict(sorted(files.items())),
                "totals": totals,
            }
        validate_normalized_report(report)
        reports.append(report)
    if summed != aggregate.get("totals"):
        raise GateInputError("partitioned JaCoCo counters do not match aggregate totals")
    return reports


def _reject_duplicate_keys(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GateInputError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_strict_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                GateInputError(f"invalid JSON constant: {constant}")
            ),
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise GateInputError("malformed coverage.py JSON") from error
    if not isinstance(value, Mapping):
        raise GateInputError("coverage.py report must be an object")
    return value


def _int_list(value: Any, field: str, *, positive: bool = True) -> list[int]:
    if not isinstance(value, list):
        raise GateInputError(f"{field} must be an integer array")
    result = []
    for item in value:
        number = _exact_nonnegative_int(item, field)
        if positive and number == 0:
            raise GateInputError(f"{field} must contain positive source lines")
        result.append(number)
    if len(result) != len(set(result)):
        raise GateInputError(f"{field} contains duplicates")
    return result


def _branch_arcs(value: Any, field: str) -> list[tuple[int, int]]:
    if not isinstance(value, list):
        raise GateInputError(f"{field} must be a branch-arc array")
    arcs: list[tuple[int, int]] = []
    for item in value:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not isinstance(item[0], int)
            or isinstance(item[0], bool)
            or item[0] <= 0
            or not isinstance(item[1], int)
            or isinstance(item[1], bool)
        ):
            raise GateInputError(f"{field} contains a malformed branch arc")
        arcs.append((item[0], item[1]))
    if len(arcs) != len(set(arcs)):
        raise GateInputError(f"{field} contains duplicate branch arcs")
    return arcs


def _coverage_source_path(
    raw_path: str, *, source_prefix: str, repository_root: Path
) -> str:
    prefix = PurePosixPath(source_prefix)
    if (
        not source_prefix
        or "\\" in source_prefix
        or prefix.is_absolute()
        or ".." in prefix.parts
        or source_prefix == "."
    ):
        raise GateInputError("coverage.py source prefix must be repository-relative")
    if not raw_path or "\\" in raw_path or PurePosixPath(raw_path).is_absolute():
        raise GateInputError("coverage.py path must be repository-relative")
    parts = PurePosixPath(raw_path).parts
    if ".." in parts:
        raise GateInputError("coverage.py path must be repository-relative")
    candidate_relative = PurePosixPath(raw_path)
    if candidate_relative.parts[: len(prefix.parts)] != prefix.parts:
        candidate_relative = prefix / candidate_relative
    return _repo_relative(
        repository_root / candidate_relative,
        repository_root,
        "coverage.py source path",
    )


def normalize_coveragepy_json(
    report_path: Path,
    *,
    module: str,
    source_prefix: str,
    repository_root: Path,
    source_inventory_sha256: str,
) -> dict[str, Any]:
    """Normalize coverage.py JSON format 3 with mandatory branch instrumentation."""

    raw = _load_strict_json(report_path)
    meta = raw.get("meta")
    raw_files = raw.get("files")
    totals = raw.get("totals")
    if not isinstance(meta, Mapping) or meta.get("format") != 3:
        raise GateInputError("unsupported coverage.py JSON format")
    if meta.get("branch_coverage") is not True:
        raise GateInputError("coverage.py branch coverage is disabled")
    version = meta.get("version")
    if (
        not isinstance(version, str)
        or not version
        or not module
        or not isinstance(source_inventory_sha256, str)
        or len(source_inventory_sha256) != 64
        or any(character not in "0123456789abcdef" for character in source_inventory_sha256)
    ):
        raise GateInputError("coverage.py report identity is malformed")
    if not isinstance(raw_files, Mapping) or not isinstance(totals, Mapping):
        raise GateInputError("coverage.py report lacks files or totals")

    files: dict[str, dict[str, Any]] = {}
    computed_lines_covered = 0
    computed_lines_total = 0
    computed_branches_covered = 0
    computed_branches_total = 0
    for raw_path, raw_file in raw_files.items():
        if not isinstance(raw_path, str) or not isinstance(raw_file, Mapping):
            raise GateInputError("coverage.py file entry is malformed")
        relative_path = _coverage_source_path(
            raw_path, source_prefix=source_prefix, repository_root=repository_root
        )
        if relative_path in files:
            raise GateInputError(f"duplicate coverage.py source path: {relative_path}")
        executed = _int_list(raw_file.get("executed_lines"), f"{relative_path} executed_lines")
        missing = _int_list(raw_file.get("missing_lines"), f"{relative_path} missing_lines")
        if set(executed) & set(missing):
            raise GateInputError(f"{relative_path} line is both executed and missing")
        executed_arcs = _branch_arcs(
            raw_file.get("executed_branches"), f"{relative_path} executed_branches"
        )
        missing_arcs = _branch_arcs(
            raw_file.get("missing_branches"), f"{relative_path} missing_branches"
        )
        if set(executed_arcs) & set(missing_arcs):
            raise GateInputError(f"{relative_path} branch is both executed and missing")
        if any(origin not in set(executed + missing) for origin, _ in executed_arcs + missing_arcs):
            raise GateInputError(f"{relative_path} branch origin is not executable")
        branch_counts: dict[str, dict[str, int]] = {}
        for origin, _ in executed_arcs:
            branch_counts.setdefault(str(origin), {"covered": 0, "missed": 0})["covered"] += 1
        for origin, _ in missing_arcs:
            branch_counts.setdefault(str(origin), {"covered": 0, "missed": 0})["missed"] += 1

        summary = raw_file.get("summary")
        expected_summary = {
            "covered_lines": len(executed),
            "num_statements": len(executed) + len(missing),
            "covered_branches": len(executed_arcs),
            "num_branches": len(executed_arcs) + len(missing_arcs),
        }
        if not isinstance(summary, Mapping) or any(
            _exact_nonnegative_int(summary.get(name), f"{relative_path} {name}") != value
            for name, value in expected_summary.items()
        ):
            raise GateInputError(f"{relative_path} file summary does not match coverage data")

        # Coverage.py reports package markers even when they have no executable
        # statements. The source inventory owns those explicitly as
        # nonExecutable, so normalized executable reports must omit them.
        if not executed and not missing and not executed_arcs and not missing_arcs:
            continue

        computed_lines_covered += len(executed)
        computed_lines_total += len(executed) + len(missing)
        computed_branches_covered += len(executed_arcs)
        computed_branches_total += len(executed_arcs) + len(missing_arcs)
        files[relative_path] = {
            "executableLines": sorted(executed + missing),
            "coveredLines": sorted(executed),
            "branches": dict(sorted(branch_counts.items(), key=lambda item: int(item[0]))),
        }

    raw_counters = {
        "lines": {
            "covered": _exact_nonnegative_int(totals.get("covered_lines"), "covered_lines"),
            "total": _exact_nonnegative_int(totals.get("num_statements"), "num_statements"),
        },
        "branches": {
            "covered": _exact_nonnegative_int(totals.get("covered_branches"), "covered_branches"),
            "total": _exact_nonnegative_int(totals.get("num_branches"), "num_branches"),
        },
    }
    computed = {
        "lines": {"covered": computed_lines_covered, "total": computed_lines_total},
        "branches": {
            "covered": computed_branches_covered,
            "total": computed_branches_total,
        },
    }
    if raw_counters != computed:
        raise GateInputError("coverage.py totals do not match files")

    report = {
        "schemaVersion": 1,
        "adapter": "coveragepy-json",
        "language": "python",
        "module": module,
        "toolVersion": version,
        "sourceInventorySha256": source_inventory_sha256,
        "branchInstrumentation": True,
        "files": dict(sorted(files.items())),
        "totals": computed,
    }
    validate_normalized_report(report)
    return report
