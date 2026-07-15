from __future__ import annotations

import json
import sys
from pathlib import Path


QUALITY_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(QUALITY_ROOT))

from quality_gates.changed_coverage import _evaluate_unbound_gate  # noqa: E402


def _fixture(name: str) -> dict:
    with (FIXTURES / name).open(encoding="utf-8") as handle:
        return json.load(handle)


def test_high_aggregate_cannot_hide_uncovered_changed_correctness_branch() -> None:
    result = _evaluate_unbound_gate(
        changes=_fixture("changes-one-correctness-branch-v1.json"),
        reports=[_fixture("normalized-java-high-aggregate-missed-branch-v1.json")],
        baseline=_fixture("coverage-baseline-v1.json"),
        exclusions={"schemaVersion": 1, "entries": []},
        as_of="2026-07-14",
    )

    assert result.passed is False
    assert result.changed_lines == {"covered": 1, "total": 1}
    assert result.changed_branches == {"covered": 1, "total": 2}
    assert result.failures == (
        "java-ecosystem/libs/example/src/main/java/example/StateMachine.java:10 "
        "has 1 uncovered changed branch",
    )
