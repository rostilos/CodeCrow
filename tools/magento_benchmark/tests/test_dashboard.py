from __future__ import annotations

import json
from pathlib import Path

import pytest

from magento2_benchmark.dashboard import (
    DashboardError,
    build_dashboard,
    generate_dashboard,
)
from magento2_benchmark.util import sha256_json


def _summary() -> dict[str, object]:
    value = {
        "kind": "codecrow-magento2-benchmark-metrics",
        "generatedAt": "2026-07-29T00:00:00Z",
        "corpus": {"digest": "sha256:example", "caseCount": 1},
        "configurations": [
            {
                "configId": "analysis-a__judge-b",
                "analysisModel": "analysis-a",
                "judgeModel": "judge-b",
                "coverage": {"scoredCases": 1, "totalCases": 1, "rate": 1.0},
                "primary": {
                    "micro": {
                        "precision": 0.5,
                        "recall": 1.0,
                        "f1": 2 / 3,
                        "counts": {"tp": 1, "referenceSetFalsePositive": 1, "fn": 0},
                    }
                },
                "strata": {"sizeBand": {}},
                "cases": [],
            }
        ],
        "pairwiseComparisons": [],
    }
    value["metricsDigest"] = sha256_json(value)
    return value


def test_generate_dashboard_copies_metrics_and_static_assets(tmp_path: Path) -> None:
    summary_path = tmp_path / "summary.json"
    original = json.dumps(_summary(), indent=2) + "\n"
    summary_path.write_text(original, encoding="utf-8")
    output = tmp_path / "site"

    manifest = generate_dashboard(summary_path, output)

    assert (output / "data.json").read_text(encoding="utf-8") == original
    assert set(path.name for path in output.iterdir()) == {
        "app.js",
        "data.json",
        "index.html",
        "styles.css",
    }
    assert manifest["index"] == str(output / "index.html")
    assert "configuration-select" in (output / "index.html").read_text(encoding="utf-8")
    assert "reference-set false positive" in (
        output / "index.html"
    ).read_text(encoding="utf-8")
    script = (output / "app.js").read_text(encoding="utf-8")
    assert 'fetch("data.json"' in script
    assert "textContent" in script
    assert "pairwiseComparisons" in script
    assert "artifactIntegrityReady" in script
    assert "protocolControls" in script
    assert "This dashboard does not claim paper readiness" in script
    assert "microDeltaConfidenceInterval95" in script
    assert "@media" in (output / "styles.css").read_text(encoding="utf-8")


def test_generate_dashboard_rejects_invalid_summary_before_output(
    tmp_path: Path,
) -> None:
    summary_path = tmp_path / "summary.json"
    summary_path.write_text("[]\n", encoding="utf-8")
    output = tmp_path / "site"

    with pytest.raises(DashboardError, match="JSON object"):
        generate_dashboard(summary_path, output)

    assert not output.exists()


def test_generate_dashboard_requires_configurations_array(tmp_path: Path) -> None:
    summary_path = tmp_path / "summary.json"
    summary_path.write_text('{"kind": "metrics"}\n', encoding="utf-8")

    with pytest.raises(DashboardError, match="kind"):
        generate_dashboard(summary_path, tmp_path / "site")


def test_build_dashboard_keyword_wrapper(tmp_path: Path) -> None:
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps(_summary()), encoding="utf-8")

    manifest = build_dashboard(metrics_path=summary_path, output_dir=tmp_path / "site")

    assert Path(manifest["data"]).is_file()
