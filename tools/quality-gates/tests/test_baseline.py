from __future__ import annotations

import sys
from pathlib import Path

import pytest


QUALITY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(QUALITY_ROOT))

from quality_gates import GateInputError  # noqa: E402
from quality_gates.baseline import (  # noqa: E402
    _capture_unbound_coverage_baseline,
    _capture_unbound_source_snapshot,
    _verify_unbound_source_snapshot,
    aggregate_normalized_reports,
    capture_coverage_baseline,
    capture_source_snapshot,
    verify_source_snapshot,
)
from quality_gates import baseline as baseline_module  # noqa: E402


INVENTORY_SHA = "f" * 64


def _report(module: str, path: str, covered: int, total: int) -> dict:
    missed = total - covered
    return {
        "schemaVersion": 1,
        "adapter": "coveragepy-json",
        "language": "python",
        "module": module,
        "toolVersion": "7.15.1",
        "sourceInventorySha256": INVENTORY_SHA,
        "branchInstrumentation": True,
        "files": {
            path: {
                "executableLines": list(range(1, total + 1)),
                "coveredLines": list(range(1, covered + 1)),
                "branches": {
                    "1": {"covered": covered, "missed": missed}
                } if total else {},
            }
        },
        "totals": {
            "lines": {"covered": covered, "total": total},
            "branches": {"covered": covered, "total": total},
        },
    }


def test_aggregate_and_baseline_keep_exact_per_module_and_repository_counters() -> None:
    first = _report("python-ecosystem/one", "python-ecosystem/one/src/a.py", 8, 10)
    second = _report("python-ecosystem/two", "python-ecosystem/two/src/b.py", 9, 10)

    aggregate = aggregate_normalized_reports([first, second], language="python")
    baseline = _capture_unbound_coverage_baseline(
        [first, second, aggregate],
        comparison_base="8" * 40,
        source_snapshot_sha256="a" * 64,
    )

    assert aggregate["module"] == "@repository"
    assert aggregate["totals"] == {
        "lines": {"covered": 17, "total": 20},
        "branches": {"covered": 17, "total": 20},
    }
    assert baseline["domains"] == {
        "python:@repository": aggregate["totals"],
        "python:python-ecosystem/one": first["totals"],
        "python:python-ecosystem/two": second["totals"],
    }


def test_aggregate_rejects_duplicate_paths_and_mixed_languages() -> None:
    first = _report("one", "same.py", 1, 1)
    second = _report("two", "same.py", 1, 1)
    with pytest.raises(GateInputError, match="duplicate normalized source path"):
        aggregate_normalized_reports([first, second], language="python")

    second["files"] = {"other.py": second["files"].pop("same.py")}
    second["language"] = "java"
    with pytest.raises(GateInputError, match="cannot mix coverage languages"):
        aggregate_normalized_reports([first, second], language="python")


def test_aggregate_rejects_missing_and_mixed_source_inventory_epochs() -> None:
    first = _report("one", "one.py", 1, 1)
    second = _report("two", "two.py", 1, 1)
    missing = dict(second)
    del missing["sourceInventorySha256"]
    with pytest.raises(GateInputError, match="identity is malformed"):
        aggregate_normalized_reports([first, missing], language="python")

    second["sourceInventorySha256"] = "e" * 64
    with pytest.raises(GateInputError, match="span source inventory epochs"):
        aggregate_normalized_reports([first, second], language="python")


def test_release_capture_apis_reject_a_missing_source_inventory(tmp_path: Path) -> None:
    report = _report("one", "one.py", 1, 1)
    aggregate = aggregate_normalized_reports([report], language="python")
    with pytest.raises(GateInputError, match="requires a source inventory"):
        capture_coverage_baseline(
            [report, aggregate],
            comparison_base="8" * 40,
            source_snapshot_sha256="a" * 64,
            source_inventory=None,  # type: ignore[arg-type]
        )
    with pytest.raises(GateInputError, match="requires a source inventory"):
        capture_source_snapshot(
            [report, aggregate],
            repository_root=tmp_path,
            source_inventory=None,  # type: ignore[arg-type]
        )
    with pytest.raises(GateInputError, match="requires a source inventory"):
        verify_source_snapshot(
            {"schemaVersion": 1, "files": []},
            repository_root=tmp_path,
            source_inventory=None,  # type: ignore[arg-type]
        )


def test_capture_baseline_rejects_duplicate_domains_and_bad_provenance() -> None:
    report = _report("one", "one.py", 1, 1)
    with pytest.raises(GateInputError, match="duplicate coverage baseline domain"):
        _capture_unbound_coverage_baseline(
            [report, report],
            comparison_base="8" * 40,
            source_snapshot_sha256="a" * 64,
        )
    with pytest.raises(GateInputError, match="coverage baseline provenance is malformed"):
        _capture_unbound_coverage_baseline(
            [report],
            comparison_base="bad",
            source_snapshot_sha256="bad",
        )


def test_aggregate_and_baseline_reject_forged_totals_and_incomplete_domains() -> None:
    first = _report("one", "one.py", 1, 1)
    forged = _report("two", "two.py", 1, 1)
    forged["totals"]["branches"]["covered"] = 0
    with pytest.raises(GateInputError, match="totals do not match normalized files"):
        aggregate_normalized_reports([first, forged], language="python")

    duplicate_module = _report("one", "other.py", 1, 1)
    with pytest.raises(GateInputError, match="duplicate coverage module"):
        aggregate_normalized_reports([first, duplicate_module], language="python")

    with pytest.raises(GateInputError, match="baseline report set is empty"):
        _capture_unbound_coverage_baseline(
            [],
            comparison_base="8" * 40,
            source_snapshot_sha256="a" * 64,
        )

    with pytest.raises(GateInputError, match="authoritative repository aggregate"):
        _capture_unbound_coverage_baseline(
            [first],
            comparison_base="8" * 40,
            source_snapshot_sha256="a" * 64,
        )


def test_baseline_requires_exact_declared_domain_inventory() -> None:
    first = _report("one", "one.py", 1, 1)
    aggregate = aggregate_normalized_reports([first], language="python")
    with pytest.raises(GateInputError, match="coverage baseline domains do not match policy"):
        _capture_unbound_coverage_baseline(
            [first, aggregate],
            comparison_base="8" * 40,
            source_snapshot_sha256="a" * 64,
            required_domains={"python:one", "python:two", "python:@repository"},
        )


def test_source_snapshot_hashes_exact_normalized_sources(tmp_path: Path) -> None:
    source = tmp_path / "python-ecosystem/one/src/a.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    report = _report("python-ecosystem/one", source.relative_to(tmp_path).as_posix(), 1, 1)

    snapshot = _capture_unbound_source_snapshot([report], repository_root=tmp_path)
    _verify_unbound_source_snapshot(snapshot, repository_root=tmp_path)
    assert snapshot["files"][0]["path"] == "python-ecosystem/one/src/a.py"

    source.write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(GateInputError, match="source snapshot mismatch"):
        _verify_unbound_source_snapshot(snapshot, repository_root=tmp_path)


def test_source_bound_baseline_rejects_stale_reports_and_empty_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _report("one", "one.py", 1, 1)
    aggregate = aggregate_normalized_reports([report], language="python")
    inventory = {
        "schemaVersion": 1,
        "policyPath": "policy.json",
        "policySha256": "a" * 64,
        "inventorySha256": "e" * 64,
        "sources": [
            {
                "path": "one.py",
                "language": "python",
                "module": "one",
                "coverageDisposition": "required",
                "sha256": "b" * 64,
            }
        ],
    }
    monkeypatch.setattr(baseline_module, "source_inventory_digest", lambda value: "e" * 64)
    with pytest.raises(GateInputError, match="source inventory is stale"):
        _capture_unbound_coverage_baseline(
            [report, aggregate],
            comparison_base="8" * 40,
            source_snapshot_sha256="a" * 64,
            source_inventory=inventory,
        )

    monkeypatch.setattr(
        baseline_module,
        "_capture_unbound_coverage_baseline",
        lambda *args, **kwargs: {"files": {}},
    )
    with pytest.raises(GateInputError, match="contains no required source files"):
        capture_coverage_baseline(
            [report, aggregate],
            comparison_base="8" * 40,
            source_snapshot_sha256="a" * 64,
            source_inventory=inventory,
        )
