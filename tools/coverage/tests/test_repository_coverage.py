from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repository_coverage import (  # noqa: E402
    Counts,
    CoverageConfigurationError,
    CoverageResult,
    TargetResult,
    discover_java_modules,
    evaluate_thresholds,
    load_policy,
    parse_cobertura,
    parse_jacoco,
    validate_python_discovery,
)


class CoverageReportParsingTest(unittest.TestCase):
    def test_parses_jacoco_root_counters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "jacoco.xml"
            report.write_text(
                """<?xml version="1.0"?>
<report name="sample">
  <counter type="INSTRUCTION" missed="2" covered="8"/>
  <counter type="BRANCH" missed="3" covered="7"/>
  <counter type="LINE" missed="4" covered="16"/>
</report>
""",
                encoding="utf-8",
            )

            result = parse_jacoco(report)

        self.assertEqual(Counts(covered=16, missed=4), result.line)
        self.assertEqual(Counts(covered=7, missed=3), result.branch)
        self.assertEqual(80.0, result.line.percent)

    def test_parses_cobertura_counters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "coverage.xml"
            report.write_text(
                '<coverage lines-valid="100" lines-covered="81" '
                'branches-valid="40" branches-covered="27"/>',
                encoding="utf-8",
            )

            result = parse_cobertura(report)

        self.assertEqual(Counts(covered=81, missed=19), result.line)
        self.assertEqual(Counts(covered=27, missed=13), result.branch)

    def test_rejects_impossible_cobertura_counters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "coverage.xml"
            report.write_text(
                '<coverage lines-valid="3" lines-covered="4" '
                'branches-valid="0" branches-covered="0"/>',
                encoding="utf-8",
            )

            with self.assertRaises(CoverageConfigurationError):
                parse_cobertura(report)

    def test_rejects_jacoco_without_required_counters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "jacoco.xml"
            report.write_text(
                '<report name="sample"><counter type="BRANCH" missed="1" '
                'covered="2"/></report>',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(CoverageConfigurationError, "line"):
                parse_jacoco(report)


class CoveragePolicyTest(unittest.TestCase):
    def test_thresholds_fail_below_expected_value(self) -> None:
        target = TargetResult(
            name="sample",
            coverage=CoverageResult(
                line=Counts(covered=79, missed=21),
                branch=Counts(covered=60, missed=40),
            ),
            minimum={"line": 80.0, "branch": 60.0},
            reports=1,
            expected_reports=1,
        )

        _, failures = evaluate_thresholds(
            [target], {"line": 70.0, "branch": 50.0}
        )

        self.assertEqual(1, len(failures))
        self.assertIn("line coverage 79.00%", failures[0])

    def test_missing_report_fails_even_when_empty_metrics_meet_zero(self) -> None:
        target = TargetResult(
            name="sample",
            coverage=CoverageResult.empty(),
            minimum={"line": 0.0, "branch": 0.0},
            reports=0,
            expected_reports=1,
        )

        _, failures = evaluate_thresholds(
            [target], {"line": 0.0, "branch": 0.0}
        )

        self.assertEqual(
            ["sample: collected 0/1 required coverage reports"], failures
        )

    def test_discovers_every_java_source_module_except_explicit_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            included = root / "java-ecosystem/libs/included"
            excluded = root / "java-ecosystem/libs/test-support"
            for module in (included, excluded):
                (module / "src/main/java").mkdir(parents=True)
                (module / "src/main/java/App.java").write_text(
                    "class App {}", encoding="utf-8"
                )
                (module / "pom.xml").write_text("<project/>", encoding="utf-8")

            modules = discover_java_modules(
                root,
                {
                    "sourceRoots": ["java-ecosystem"],
                    "excludeModules": ["java-ecosystem/libs/test-support"],
                },
            )

        self.assertEqual([Path("java-ecosystem/libs/included")], modules)

    def test_new_python_service_requires_a_policy_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "python-ecosystem/new-service/src"
            source.mkdir(parents=True)
            (source / "main.py").write_text("value = 1", encoding="utf-8")
            policy = {
                "pythonDiscoveryRoots": ["python-ecosystem"],
                "python": {
                    "known": {"workingDirectory": "python-ecosystem/known"}
                },
            }

            with self.assertRaisesRegex(
                CoverageConfigurationError, "new-service"
            ):
                validate_python_discovery(root, policy)

    def test_checked_in_policy_is_valid(self) -> None:
        policy = Path(__file__).resolve().parents[1] / "coverage-policy.json"
        loaded = load_policy(policy)
        self.assertIn("java", loaded)
        self.assertIn("python", loaded)


if __name__ == "__main__":
    unittest.main()
