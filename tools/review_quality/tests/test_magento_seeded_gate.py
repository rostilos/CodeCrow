from __future__ import annotations

from tools.review_quality.magento_seeded_gate import run_gate


def test_fixed_magento_candidate_gate_meets_quality_and_cost_targets():
    report = run_gate()
    by_mode = {
        metrics["mode"]: metrics
        for metrics in report["evaluation"]["modes"]
    }

    assert report["status"] == "passed"
    assert report["checks"] == {
        "precision": True,
        "recall": True,
        "modelCalls": True,
        "cost": True,
        "repositorySnapshots": True,
        "retrievalState": True,
    }
    assert report["architecture"]["snapshotPlugins"] == [
        "hyva",
        "magento",
        "php",
    ]
    assert report["architecture"]["retrievalState"] == "complete"
    assert by_mode["fallback"]["true_positives"] == 3
    assert by_mode["fallback"]["false_positives"] == 18
    assert by_mode["fallback"]["false_negatives"] == 0
    assert by_mode["php-magento"]["true_positives"] == 3
    assert by_mode["php-magento"]["false_positives"] == 0
    assert by_mode["php-magento"]["false_negatives"] == 0
    assert report["evaluation"]["pairedDeltas"] == [{
        "mode": "php-magento",
        "baseline": "fallback",
        "precision": 0.8571428571428572,
        "recall": 0.0,
        "model_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost": 0.0,
            "cost_per_changed_kloc": 0.0,
            "median_cost_per_changed_kloc": 0.0,
            "p95_cost_per_changed_kloc": 0.0,
        }]


def test_fixed_magento_candidate_gate_is_byte_stable():
    first = run_gate()
    second = run_gate()

    assert first == second
