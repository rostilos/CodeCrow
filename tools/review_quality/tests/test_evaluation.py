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
    assert fallback.f1 == 0.5
    assert fallback.f2 == 0.5
    assert fallback.coverage == 1.0
    assert fallback.changed_lines == 100
    assert fallback.cost_per_changed_kloc == 0.2
    assert report.corpus_coverage["languages"] == ["php"]
    assert report.corpus_coverage["polyglotCases"] == 0
    assert report.language_strata["php"][0].mode == "fallback"
    assert magento.precision == 1.0
    assert magento.recall == 0.5
    assert magento.f1 == pytest.approx(2 / 3)
    assert magento.f2 == pytest.approx(5 / 9)
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


def test_missing_usage_is_unknown_in_totals_and_paired_deltas():
    payload = _dataset()
    candidate = payload["cases"][1]
    candidate.pop("cost")
    candidate["inputTokens"] = None
    result = evaluate_dataset(payload)
    metrics = result.modes[1]
    assert metrics.cost is None
    assert metrics.cost_per_changed_kloc is None
    assert metrics.median_cost_per_changed_kloc is None
    assert metrics.p95_cost_per_changed_kloc is None
    assert metrics.input_tokens is None
    assert metrics.output_tokens == 100
    assert metrics.usage_known_cases["cost"] == 0
    assert result.paired_deltas[0].cost is None
    assert result.paired_deltas[0].input_tokens is None


def test_completion_counts_latency_and_missing_predictions_remain_in_score():
    payload = _dataset()
    for case in payload["cases"]:
        case["completionStatus"] = "complete"
        case["wallTimeSeconds"] = 20
    payload["cases"][1].update(completionStatus="failed", published=[], wallTimeSeconds=5)
    report = evaluate_dataset(payload)
    assert report.modes[1].false_negatives == 2
    assert report.modes[1].f1 == 0
    assert report.modes[1].completion_counts["failed"] == 1
    assert report.modes[1].median_wall_time_seconds == 5
    assert report.paired_deltas[0].wall_time_seconds == -15
    assert report.evidence["allReviewsComplete"] is False
    assert evaluate_dataset(_dataset()).modes[0].completion_counts["unknown"] == 1


def test_paired_provenance_and_dataset_digest_are_reproducible():
    payload = _dataset()
    identity = {"baseRevision": "abc", "sourceRevision": "def", "judge": "fixed-reviewer"}
    for case in payload["cases"]:
        case["provenance"] = dict(identity)
    report = evaluate_dataset(payload)
    assert report.evidence["provenanceCases"] == 1
    assert report.evidence["datasetSha256"] == evaluate_dataset(payload).evidence["datasetSha256"]
    payload["cases"][1]["provenance"]["sourceRevision"] = "different"
    with pytest.raises(ValueError, match="changes provenance"):
        evaluate_dataset(payload)


@pytest.mark.parametrize("field,value", [("cost", float("nan")), ("wallTimeSeconds", float("inf")), ("completionStatus", "success")])
def test_invalid_observations_cannot_look_like_complete_numeric_evidence(field, value):
    payload = _dataset()
    payload["cases"][0][field] = value
    with pytest.raises(ValueError):
        evaluate_dataset(payload)


def test_micro_f_scores_count_misses_across_cases_and_zero_denominators():
    payload = _dataset()
    for case in list(payload["cases"]):
        payload["cases"].append({**case, "caseId": "second", "expected": ["miss-1", "miss-2"], "published": []})
    report = evaluate_dataset(payload)
    assert report.modes[0].f1 == pytest.approx(2 / 6)
    assert report.modes[0].f2 == pytest.approx(5 / 18)
    for case in payload["cases"]:
        case.update(expected=[], published=[], abstained=[])
    empty = evaluate_dataset(payload).modes[0]
    assert empty.f1 == empty.f2 == empty.precision == empty.recall == 0
