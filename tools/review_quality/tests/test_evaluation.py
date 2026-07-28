from __future__ import annotations

import pytest

from tools.review_quality import evaluate_dataset


def _dataset():
    base = {
        "caseId": "magento-di",
        "languages": ["php"],
        "frameworks": ["magento"],
        "expected": ["real-di-bug", "missing-observer"],
        "reviewableHunks": 4,
        "terminalHunks": 4,
        "changedLines": 100,
        "modelCalls": 2,
        "inputTokens": 1000,
        "outputTokens": 100,
        "cost": 0.02,
    }
    return {
        "baseline": "fallback",
        "cases": [
            {**base, "mode": "fallback", "plugins": [], "published": ["real-di-bug", "noise"], "abstained": []},
            {**base, "mode": "magento", "plugins": ["magento", "php"], "published": ["real-di-bug"], "abstained": ["noise"]},
        ],
    }


def test_reports_raw_quality_coverage_and_cost():
    report = evaluate_dataset(_dataset())
    fallback, magento = report.modes

    assert fallback.mode == "fallback"
    assert (fallback.true_positives, fallback.false_positives, fallback.false_negatives) == (1, 1, 1)
    assert fallback.precision == 0.5
    assert fallback.recall == 0.5
    assert fallback.coverage == 1.0
    assert fallback.changed_lines == 100
    assert fallback.cost_per_changed_kloc == 0.2
    assert report.corpus_coverage["languages"] == ["php"]
    assert report.corpus_coverage["polyglotCases"] == 0
    assert report.language_strata["php"][0].mode == "fallback"
    assert magento.precision == 1.0
    assert magento.recall == 0.5
    assert report.paired_deltas[0].model_calls == 0
    assert report.paired_deltas[0].cost == 0


def test_rejects_unpaired_modes():
    payload = _dataset()
    payload["cases"][1]["caseId"] = "different"

    with pytest.raises(ValueError, match="not paired"):
        evaluate_dataset(payload)


def test_rejects_changed_ground_truth_between_modes():
    payload = _dataset()
    payload["cases"][1]["expected"] = ["different"]

    with pytest.raises(ValueError, match="changes expected labels"):
        evaluate_dataset(payload)


def test_rejects_published_abstention_overlap():
    payload = _dataset()
    payload["cases"][1]["abstained"] = ["real-di-bug"]

    with pytest.raises(ValueError, match="publishes and abstains"):
        evaluate_dataset(payload)


def test_reports_polyglot_coverage_and_rejects_mode_metadata_drift():
    payload = _dataset()
    for case in payload["cases"]:
        case["languages"] = ["typescript", "python"]

    report = evaluate_dataset(payload)

    assert report.corpus_coverage["languages"] == ["python", "typescript"]
    assert report.corpus_coverage["polyglotCases"] == 1
    assert set(report.language_strata) == {"python+typescript"}

    payload["cases"][1]["languages"] = ["typescript"]
    with pytest.raises(ValueError, match="changes languages"):
        evaluate_dataset(payload)


def test_rejects_changed_line_drift_between_paired_modes():
    payload = _dataset()
    payload["cases"][1]["changedLines"] = 101

    with pytest.raises(ValueError, match="changes changed line count"):
        evaluate_dataset(payload)


def test_rejects_provider_usage_without_changed_lines():
    payload = _dataset()
    payload["cases"][0]["changedLines"] = 0

    with pytest.raises(ValueError, match="no changed lines"):
        evaluate_dataset(payload)
