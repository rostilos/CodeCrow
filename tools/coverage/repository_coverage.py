#!/usr/bin/env python3
"""Run and enforce CodeCrow's repository-wide backend coverage policy.

The runner deliberately uses only the Python standard library. Test and
coverage dependencies are installed into isolated, policy-owned virtual
environments so the independently deployed Python services do not have to
share incompatible runtime dependency versions.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = Path(__file__).with_name("coverage-policy.json")
DEFAULT_OUTPUT = REPOSITORY_ROOT / "build" / "coverage"
DEFAULT_VENV_ROOT = REPOSITORY_ROOT / ".coverage-venvs"
METRICS = ("line", "branch")


class CoverageConfigurationError(ValueError):
    """Raised when the checked-in policy cannot describe the repository."""


@dataclass(frozen=True)
class Counts:
    covered: int = 0
    missed: int = 0

    @property
    def total(self) -> int:
        return self.covered + self.missed

    @property
    def percent(self) -> float:
        return 100.0 if self.total == 0 else (100.0 * self.covered / self.total)

    def __add__(self, other: "Counts") -> "Counts":
        return Counts(self.covered + other.covered, self.missed + other.missed)

    def as_dict(self) -> dict[str, int | float]:
        return {
            "covered": self.covered,
            "missed": self.missed,
            "total": self.total,
            "percent": round(self.percent, 2),
        }


@dataclass(frozen=True)
class CoverageResult:
    line: Counts
    branch: Counts

    @classmethod
    def empty(cls) -> "CoverageResult":
        return cls(line=Counts(), branch=Counts())

    def __add__(self, other: "CoverageResult") -> "CoverageResult":
        return CoverageResult(
            line=self.line + other.line,
            branch=self.branch + other.branch,
        )

    def metric(self, name: str) -> Counts:
        if name not in METRICS:
            raise CoverageConfigurationError(f"Unsupported coverage metric: {name}")
        return getattr(self, name)

    def as_dict(self) -> dict[str, dict[str, int | float]]:
        return {metric: self.metric(metric).as_dict() for metric in METRICS}


@dataclass(frozen=True)
class TargetResult:
    name: str
    coverage: CoverageResult
    minimum: Mapping[str, float]
    reports: int
    expected_reports: int

    @property
    def complete(self) -> bool:
        return self.reports == self.expected_reports


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise CoverageConfigurationError(f"{label} must be a JSON object")
    return value


def _require_string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise CoverageConfigurationError(f"{label} must be a non-empty string array")
    return list(value)


def _minimums(value: Any, label: str) -> dict[str, float]:
    raw = _require_mapping(value, label)
    result: dict[str, float] = {}
    for metric in METRICS:
        threshold = raw.get(metric)
        if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
            raise CoverageConfigurationError(f"{label}.{metric} must be numeric")
        if not 0 <= float(threshold) <= 100:
            raise CoverageConfigurationError(
                f"{label}.{metric} must be between 0 and 100"
            )
        result[metric] = float(threshold)
    return result


def load_policy(path: Path) -> Mapping[str, Any]:
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CoverageConfigurationError(f"Cannot read coverage policy {path}: {exc}")

    root = _require_mapping(policy, "coverage policy")
    _minimums(root.get("overallMinimum"), "overallMinimum")

    java = _require_mapping(root.get("java"), "java")
    _require_string_list(java.get("sourceRoots"), "java.sourceRoots")
    _minimums(java.get("minimum"), "java.minimum")
    excludes = java.get("excludeModules", [])
    if not isinstance(excludes, list) or not all(
        isinstance(item, str) for item in excludes
    ):
        raise CoverageConfigurationError("java.excludeModules must be a string array")

    environments = _require_mapping(
        root.get("pythonEnvironments"), "pythonEnvironments"
    )
    if not environments:
        raise CoverageConfigurationError("pythonEnvironments must not be empty")
    for name, environment in environments.items():
        config = _require_mapping(environment, f"pythonEnvironments.{name}")
        if not isinstance(config.get("requirements"), str):
            raise CoverageConfigurationError(
                f"pythonEnvironments.{name}.requirements must be a path"
            )

    _require_string_list(root.get("pythonDiscoveryRoots"), "pythonDiscoveryRoots")
    python_targets = _require_mapping(root.get("python"), "python")
    if not python_targets:
        raise CoverageConfigurationError("python coverage targets must not be empty")
    for name, target in python_targets.items():
        config = _require_mapping(target, f"python.{name}")
        environment = config.get("environment")
        if environment not in environments:
            raise CoverageConfigurationError(
                f"python.{name}.environment references unknown environment {environment!r}"
            )
        if not isinstance(config.get("workingDirectory"), str):
            raise CoverageConfigurationError(
                f"python.{name}.workingDirectory must be a path"
            )
        _require_string_list(config.get("tests"), f"python.{name}.tests")
        _require_string_list(config.get("sources"), f"python.{name}.sources")
        _minimums(config.get("minimum"), f"python.{name}.minimum")

    return root


def parse_jacoco(path: Path) -> CoverageResult:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise CoverageConfigurationError(f"Invalid JaCoCo report {path}: {exc}")

    counters: dict[str, Counts] = {}
    for counter in root.findall("counter"):
        counter_type = counter.attrib.get("type", "").lower()
        if counter_type not in METRICS:
            continue
        try:
            covered = int(counter.attrib["covered"])
            missed = int(counter.attrib["missed"])
        except (KeyError, ValueError) as exc:
            raise CoverageConfigurationError(
                f"Invalid {counter_type} counter in {path}: {exc}"
            )
        if covered < 0 or missed < 0:
            raise CoverageConfigurationError(
                f"Negative {counter_type} counter in {path}"
            )
        counters[counter_type] = Counts(covered=covered, missed=missed)

    # JaCoCo legitimately omits BRANCH when the module has no branch
    # instructions, but every production-code report must contain LINE.
    if "line" not in counters:
        raise CoverageConfigurationError(
            f"Missing line counter in JaCoCo report {path}"
        )

    return CoverageResult(
        line=counters["line"],
        branch=counters.get("branch", Counts()),
    )


def parse_cobertura(path: Path) -> CoverageResult:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise CoverageConfigurationError(f"Invalid Cobertura report {path}: {exc}")

    def counts(covered_name: str, valid_name: str) -> Counts:
        try:
            covered = int(root.attrib[covered_name])
            valid = int(root.attrib[valid_name])
        except (KeyError, ValueError) as exc:
            raise CoverageConfigurationError(
                f"Missing or invalid {covered_name}/{valid_name} in {path}: {exc}"
            )
        if covered < 0 or valid < covered:
            raise CoverageConfigurationError(
                f"Impossible {covered_name}/{valid_name} counters in {path}"
            )
        return Counts(covered=covered, missed=valid - covered)

    return CoverageResult(
        line=counts("lines-covered", "lines-valid"),
        branch=counts("branches-covered", "branches-valid"),
    )


def discover_java_modules(
    repository_root: Path, java_policy: Mapping[str, Any]
) -> list[Path]:
    excluded = {Path(item).as_posix() for item in java_policy.get("excludeModules", [])}
    modules: set[Path] = set()
    for root_name in _require_string_list(
        java_policy.get("sourceRoots"), "java.sourceRoots"
    ):
        source_root = repository_root / root_name
        if not source_root.is_dir():
            raise CoverageConfigurationError(
                f"Java coverage source root does not exist: {root_name}"
            )
        for main_java in source_root.rglob("src/main/java"):
            module = main_java.parents[2]
            relative = module.relative_to(repository_root)
            if relative.as_posix() in excluded:
                continue
            if not (module / "pom.xml").is_file():
                raise CoverageConfigurationError(
                    f"Java source directory has no module pom.xml: {relative}"
                )
            if any(main_java.rglob("*.java")):
                modules.add(relative)
    if not modules:
        raise CoverageConfigurationError("No Java production modules were discovered")
    return sorted(modules, key=lambda item: item.as_posix())


def discover_python_services(
    repository_root: Path, discovery_roots: Sequence[str]
) -> set[str]:
    discovered: set[str] = set()
    for root_name in discovery_roots:
        root = repository_root / root_name
        if not root.is_dir():
            raise CoverageConfigurationError(
                f"Python coverage discovery root does not exist: {root_name}"
            )
        for child in root.iterdir():
            source = child / "src"
            if child.is_dir() and source.is_dir() and any(source.rglob("*.py")):
                discovered.add(child.relative_to(repository_root).as_posix())
    return discovered


def validate_python_discovery(
    repository_root: Path, policy: Mapping[str, Any]
) -> None:
    configured = {
        Path(target["workingDirectory"]).as_posix()
        for target in _require_mapping(policy.get("python"), "python").values()
        if Path(target["workingDirectory"]).as_posix().startswith("python-ecosystem/")
    }
    discovered = discover_python_services(
        repository_root,
        _require_string_list(
            policy.get("pythonDiscoveryRoots"), "pythonDiscoveryRoots"
        ),
    )
    missing = sorted(discovered - configured)
    if missing:
        raise CoverageConfigurationError(
            "Python production service(s) are missing from coverage-policy.json: "
            + ", ".join(missing)
        )


def run_command(
    command: Sequence[str], cwd: Path, environment: Mapping[str, str] | None = None
) -> int:
    rendered = " ".join(shlex.quote(item) for item in command)
    print(f"\n$ (cd {cwd} && {rendered})", flush=True)
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        env=dict(environment) if environment is not None else None,
        check=False,
    )
    return completed.returncode


def prepare_python_environments(
    repository_root: Path,
    policy: Mapping[str, Any],
    venv_root: Path,
    skip_install: bool,
) -> tuple[dict[str, Path], list[str]]:
    interpreters: dict[str, Path] = {}
    failures: list[str] = []
    environments = _require_mapping(
        policy.get("pythonEnvironments"), "pythonEnvironments"
    )

    for name, raw_config in environments.items():
        config = _require_mapping(raw_config, f"pythonEnvironments.{name}")
        interpreter = venv_root / name / "bin" / "python"
        if not interpreter.is_file():
            if skip_install:
                failures.append(
                    f"Python environment {name!r} is absent at {interpreter}; "
                    "rerun without --skip-install"
                )
                continue
            venv_root.mkdir(parents=True, exist_ok=True)
            status = run_command(
                [sys.executable, "-m", "venv", str(venv_root / name)],
                repository_root,
            )
            if status != 0 or not interpreter.is_file():
                failures.append(f"Could not create Python environment {name!r}")
                continue

        if not skip_install:
            requirements = repository_root / str(config["requirements"])
            if not requirements.is_file():
                failures.append(
                    f"Requirements for Python environment {name!r} do not exist: "
                    f"{requirements.relative_to(repository_root)}"
                )
                continue
            status = run_command(
                [
                    str(interpreter),
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "-r",
                    str(requirements),
                ],
                repository_root,
            )
            if status != 0:
                failures.append(
                    f"Dependency installation failed for Python environment {name!r}"
                )
                continue
            status = run_command(
                [str(interpreter), "-m", "pip", "check"], repository_root
            )
            if status != 0:
                failures.append(f"pip check failed for Python environment {name!r}")
                continue

        interpreters[name] = interpreter

    return interpreters, failures


def run_java_coverage(
    repository_root: Path,
    modules: Sequence[Path],
    maven_binary: str,
) -> list[str]:
    failures: list[str] = []
    java_root = repository_root / "java-ecosystem"
    maven_extra = shlex.split(os.environ.get("COVERAGE_MAVEN_ARGS", ""))
    status = run_command(
        [
            maven_binary,
            "-B",
            "--no-transfer-progress",
            *maven_extra,
            "clean",
            "verify",
        ],
        java_root,
    )
    if status != 0:
        failures.append(f"Maven clean verify failed with exit code {status}")
        return failures

    # JaCoCo skips report generation when a module has no execution-data file.
    # An empty file makes it emit an honest zero-coverage report for untested
    # production modules, so those modules remain visible in the gate.
    for relative in modules:
        target = repository_root / relative / "target"
        if target.is_dir():
            (target / "jacoco.exec").touch(exist_ok=True)

    status = run_command(
        [
            maven_binary,
            "-B",
            "--no-transfer-progress",
            *maven_extra,
            "jacoco:report",
        ],
        java_root,
    )
    if status != 0:
        failures.append(f"Maven JaCoCo report generation failed with exit code {status}")
    return failures


def run_python_coverage(
    repository_root: Path,
    output_dir: Path,
    policy: Mapping[str, Any],
    interpreters: Mapping[str, Path],
) -> list[str]:
    failures: list[str] = []
    python_targets = _require_mapping(policy.get("python"), "python")
    python_output = output_dir / "python"
    python_output.mkdir(parents=True, exist_ok=True)

    for name, raw_target in python_targets.items():
        target = _require_mapping(raw_target, f"python.{name}")
        report = python_output / f"{name}.xml"
        data_file = python_output / f".{name}.coverage"
        for stale in (report, data_file):
            stale.unlink(missing_ok=True)
        for stale_junit in python_output.glob(f"{name}-*-junit.xml"):
            stale_junit.unlink()

        environment_name = str(target["environment"])
        interpreter = interpreters.get(environment_name)
        if interpreter is None:
            failures.append(
                f"Coverage target {name!r} has no usable Python environment "
                f"{environment_name!r}"
            )
            continue

        working_directory = repository_root / str(target["workingDirectory"])
        if not working_directory.is_dir():
            failures.append(
                f"Coverage target {name!r} working directory does not exist: "
                f"{target['workingDirectory']}"
            )
            continue

        process_environment = os.environ.copy()
        process_environment["PYTHONDONTWRITEBYTECODE"] = "1"
        process_environment["COVERAGE_FILE"] = str(data_file)
        pytest_config = target.get("pytestConfig")

        # Unit and integration suites have independent conftest boundaries.
        # Run them in separate processes and append coverage data; combining
        # both trees in one pytest process can leak service fixtures across the
        # boundary and does not match their real CI execution model.
        for index, test_path in enumerate(target["tests"]):
            suite_name = Path(str(test_path)).name or f"suite-{index + 1}"
            junit = python_output / f"{name}-{suite_name}-junit.xml"
            command = [str(interpreter), "-m", "pytest"]
            if pytest_config:
                command.extend(["-c", str(repository_root / str(pytest_config))])
            command.append(str(test_path))
            for source in target["sources"]:
                command.append(f"--cov={source}")
            command.extend(["--cov-branch", "--cov-report="])
            if index > 0:
                command.append("--cov-append")
            command.extend([f"--junitxml={junit}", "--tb=short"])

            status = run_command(command, working_directory, process_environment)
            if status != 0:
                failures.append(
                    f"Python coverage target {name!r} suite {test_path!r} "
                    f"failed with exit code {status}"
                )

        if data_file.is_file():
            status = run_command(
                [str(interpreter), "-m", "coverage", "xml", "-o", str(report)],
                working_directory,
                process_environment,
            )
            if status != 0:
                failures.append(
                    f"Could not generate Cobertura report for Python target {name!r}"
                )
            run_command(
                [str(interpreter), "-m", "coverage", "report"],
                working_directory,
                process_environment,
            )

    return failures


def collect_results(
    repository_root: Path,
    output_dir: Path,
    policy: Mapping[str, Any],
    modules: Sequence[Path],
) -> tuple[list[TargetResult], dict[str, CoverageResult], list[str]]:
    failures: list[str] = []
    module_results: dict[str, CoverageResult] = {}
    java_total = CoverageResult.empty()
    java_reports = 0

    for relative in modules:
        report = repository_root / relative / "target/site/jacoco/jacoco.xml"
        if not report.is_file():
            failures.append(f"Missing JaCoCo report for production module {relative}")
            continue
        try:
            coverage = parse_jacoco(report)
        except CoverageConfigurationError as exc:
            failures.append(str(exc))
            continue
        module_results[relative.as_posix()] = coverage
        java_total += coverage
        java_reports += 1

    java_policy = _require_mapping(policy.get("java"), "java")
    targets = [
        TargetResult(
            name="java",
            coverage=java_total,
            minimum=_minimums(java_policy.get("minimum"), "java.minimum"),
            reports=java_reports,
            expected_reports=len(modules),
        )
    ]

    python_targets = _require_mapping(policy.get("python"), "python")
    for name, raw_target in python_targets.items():
        target = _require_mapping(raw_target, f"python.{name}")
        report = output_dir / "python" / f"{name}.xml"
        coverage = CoverageResult.empty()
        reports = 0
        if not report.is_file():
            failures.append(f"Missing Cobertura report for Python target {name}")
        else:
            try:
                coverage = parse_cobertura(report)
                reports = 1
            except CoverageConfigurationError as exc:
                failures.append(str(exc))
        targets.append(
            TargetResult(
                name=name,
                coverage=coverage,
                minimum=_minimums(target.get("minimum"), f"python.{name}.minimum"),
                reports=reports,
                expected_reports=1,
            )
        )

    return targets, module_results, failures


def evaluate_thresholds(
    targets: Sequence[TargetResult], overall_minimum: Mapping[str, float]
) -> tuple[CoverageResult, list[str]]:
    failures: list[str] = []
    overall = CoverageResult.empty()
    for target in targets:
        overall += target.coverage
        if not target.complete:
            failures.append(
                f"{target.name}: collected {target.reports}/{target.expected_reports} "
                "required coverage reports"
            )
        for metric in METRICS:
            actual = target.coverage.metric(metric).percent
            expected = float(target.minimum[metric])
            if actual + 1e-12 < expected:
                failures.append(
                    f"{target.name}: {metric} coverage {actual:.2f}% is below "
                    f"the expected {expected:.2f}%"
                )

    for metric in METRICS:
        actual = overall.metric(metric).percent
        expected = float(overall_minimum[metric])
        if actual + 1e-12 < expected:
            failures.append(
                f"overall: {metric} coverage {actual:.2f}% is below "
                f"the expected {expected:.2f}%"
            )
    return overall, failures


def _target_status(target: TargetResult) -> str:
    if not target.complete:
        return "FAIL"
    return (
        "PASS"
        if all(
            target.coverage.metric(metric).percent + 1e-12
            >= float(target.minimum[metric])
            for metric in METRICS
        )
        else "FAIL"
    )


def render_markdown(
    passed: bool,
    targets: Sequence[TargetResult],
    overall: CoverageResult,
    overall_minimum: Mapping[str, float],
    module_results: Mapping[str, CoverageResult],
    failures: Sequence[str],
) -> str:
    lines = [
        "# Repository coverage gate",
        "",
        f"**Result: {'PASS' if passed else 'FAIL'}**",
        "",
        "| Target | Reports | Line | Expected | Branch | Expected | Status |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for target in targets:
        lines.append(
            "| {name} | {reports}/{expected_reports} | {line:.2f}% | {line_min:.2f}% | "
            "{branch:.2f}% | {branch_min:.2f}% | {status} |".format(
                name=target.name,
                reports=target.reports,
                expected_reports=target.expected_reports,
                line=target.coverage.line.percent,
                line_min=float(target.minimum["line"]),
                branch=target.coverage.branch.percent,
                branch_min=float(target.minimum["branch"]),
                status=_target_status(target),
            )
        )
    lines.append(
        "| **overall** | - | **{line:.2f}%** | **{line_min:.2f}%** | "
        "**{branch:.2f}%** | **{branch_min:.2f}%** | **{status}** |".format(
            line=overall.line.percent,
            line_min=float(overall_minimum["line"]),
            branch=overall.branch.percent,
            branch_min=float(overall_minimum["branch"]),
            status="PASS"
            if all(
                overall.metric(metric).percent + 1e-12
                >= float(overall_minimum[metric])
                for metric in METRICS
            )
            else "FAIL",
        )
    )

    if failures:
        lines.extend(["", "## Failures", ""])
        lines.extend(f"- {failure}" for failure in failures)

    lines.extend(
        [
            "",
            "## Java modules",
            "",
            "| Module | Line | Branch |",
            "| --- | ---: | ---: |",
        ]
    )
    for name, coverage in sorted(module_results.items()):
        lines.append(
            f"| `{name}` | {coverage.line.percent:.2f}% | "
            f"{coverage.branch.percent:.2f}% |"
        )
    lines.append("")
    return "\n".join(lines)


def write_outputs(
    output_dir: Path,
    policy_path: Path,
    passed: bool,
    targets: Sequence[TargetResult],
    overall: CoverageResult,
    overall_minimum: Mapping[str, float],
    module_results: Mapping[str, CoverageResult],
    failures: Sequence[str],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = render_markdown(
        passed,
        targets,
        overall,
        overall_minimum,
        module_results,
        failures,
    )
    (output_dir / "summary.md").write_text(summary, encoding="utf-8")
    payload = {
        "passed": passed,
        "policy": policy_path.as_posix(),
        "overall": {
            "coverage": overall.as_dict(),
            "minimum": dict(overall_minimum),
        },
        "targets": {
            target.name: {
                "coverage": target.coverage.as_dict(),
                "minimum": dict(target.minimum),
                "reports": target.reports,
                "expectedReports": target.expected_reports,
                "passed": _target_status(target) == "PASS",
            }
            for target in targets
        },
        "javaModules": {
            name: coverage.as_dict() for name, coverage in sorted(module_results.items())
        },
        "failures": list(failures),
    }
    (output_dir / "summary.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print("\n" + summary, flush=True)
    print(f"Coverage artifacts: {output_dir}", flush=True)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run every backend test suite and enforce repository coverage"
    )
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--venv-root", type=Path, default=DEFAULT_VENV_ROOT)
    parser.add_argument(
        "--reports-only",
        action="store_true",
        help="evaluate existing reports without running tests",
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="reuse existing isolated Python coverage environments",
    )
    parser.add_argument("--maven-binary", default="mvn")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    repository_root = REPOSITORY_ROOT
    policy_path = args.policy.resolve()
    output_dir = args.output_dir.resolve()
    venv_root = args.venv_root.resolve()

    try:
        policy = load_policy(policy_path)
        validate_python_discovery(repository_root, policy)
        java_policy = _require_mapping(policy.get("java"), "java")
        modules = discover_java_modules(repository_root, java_policy)
        overall_minimum = _minimums(
            policy.get("overallMinimum"), "overallMinimum"
        )
    except CoverageConfigurationError as exc:
        print(f"Coverage configuration error: {exc}", file=sys.stderr)
        return 2

    operational_failures: list[str] = []
    if not args.reports_only:
        operational_failures.extend(
            run_java_coverage(
                repository_root,
                modules,
                args.maven_binary,
            )
        )
        interpreters, environment_failures = prepare_python_environments(
            repository_root,
            policy,
            venv_root,
            args.skip_install,
        )
        operational_failures.extend(environment_failures)
        operational_failures.extend(
            run_python_coverage(
                repository_root,
                output_dir,
                policy,
                interpreters,
            )
        )

    targets, module_results, collection_failures = collect_results(
        repository_root,
        output_dir,
        policy,
        modules,
    )
    overall, threshold_failures = evaluate_thresholds(targets, overall_minimum)
    failures = operational_failures + collection_failures + threshold_failures
    passed = not failures
    write_outputs(
        output_dir,
        policy_path,
        passed,
        targets,
        overall,
        overall_minimum,
        module_results,
        failures,
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
