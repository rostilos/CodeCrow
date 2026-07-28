import copy
import hashlib
import json

from tools.review_quality.capture_pair_evaluation import _digest
from tools.review_quality.paired_quality_gate import evaluate_acceptance_gate


def _mode_metrics(
    mode,
    *,
    precision,
    recall,
    coverage=1.0,
    model_calls=8,
    median_cost=0.5,
    p95_cost=0.5,
):
    true_positives = 8
    false_positives = round((true_positives / precision) - true_positives)
    false_negatives = round((true_positives / recall) - true_positives)
    return {
        "mode": mode,
        "cases": 4,
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "abstentions": 0,
        "precision": precision,
        "recall": recall,
        "coverage": coverage,
        "changed_lines": 400,
        "model_calls": model_calls,
        "input_tokens": 1000,
        "output_tokens": 100,
        "cost": 0.2,
        "cost_per_changed_kloc": 0.5,
        "median_cost_per_changed_kloc": median_cost,
        "p95_cost_per_changed_kloc": p95_cost,
    }


def _paired_evaluation():
    baseline = _mode_metrics(
        "fallback",
        precision=0.5,
        recall=0.8,
    )
    candidate = _mode_metrics(
        "plugin-context",
        precision=0.8,
        recall=0.8,
    )
    profiles = (
        ("python-case", ["python"]),
        ("java-case", ["java"]),
        ("typescript-case", ["typescript"]),
        ("polyglot-case", ["java", "python", "typescript"]),
    )
    case_metrics = []
    for case_index, (case_id, languages) in enumerate(profiles):
        for mode in ("fallback", "plugin-context"):
            true_positives = 2
            false_positives = (
                2 if mode == "fallback"
                else (1 if case_index < 2 else 0)
            )
            false_negatives = 1 if case_index < 2 else 0
            case_metrics.append({
            "caseId": case_id,
            "mode": mode,
            "repositoryPlugins": (
                [] if mode == "fallback" else [*languages, "neutral-context"]
            ),
            "languages": languages,
            "frameworks": [],
            "truePositives": true_positives,
            "falsePositives": false_positives,
            "falseNegatives": false_negatives,
            "precision": (
                true_positives / (true_positives + false_positives)
            ),
            "recall": true_positives / (true_positives + false_negatives),
            "reviewableHunks": 10,
            "terminalHunks": 10,
            "changedLines": 100,
            "modelCalls": 2,
            "inputTokens": 250,
            "outputTokens": 25,
            "cost": 0.05,
            "costPerChangedKloc": 0.5,
            })
    language_strata = {}
    for case_id, languages in profiles:
        profile = "+".join(languages)
        language_strata[profile] = [
            {
                "mode": item["mode"],
                "cases": 1,
                "true_positives": item["truePositives"],
                "false_positives": item["falsePositives"],
                "false_negatives": item["falseNegatives"],
                "precision": item["precision"],
                "recall": item["recall"],
            }
            for item in case_metrics
            if item["caseId"] == case_id
        ]
    return {
        "kind": "review-quality-paired-capture-report",
        "provenance": [
            {
                "caseId": case_id,
                "mode": mode,
                "repositoryPlugins": (
                    [] if mode == "fallback" else [*languages, "neutral-context"]
                ),
            }
            for case_id, languages in profiles
            for mode in ("fallback", "plugin-context")
        ],
        "caseMetrics": case_metrics,
        "report": {
            "baseline": "fallback",
            "modes": [baseline, candidate],
            "pairedDeltas": [{
                "mode": "plugin-context",
                "baseline": "fallback",
                "precision": 0.3,
                "recall": 0.0,
                "model_calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost": 0.0,
                "cost_per_changed_kloc": 0.0,
                "median_cost_per_changed_kloc": 0.0,
                "p95_cost_per_changed_kloc": 0.0,
            }],
            "corpusCoverage": {
                "cases": 4,
                "languages": ["java", "python", "typescript"],
                "frameworks": [],
                "polyglotCases": 1,
                "changedLines": 400,
            },
            "languageStrata": language_strata,
            "frameworkStrata": {"none": []},
        },
    }


def _manifest(tmp_path):
    billing = tmp_path / "provider-billing.json"
    billing.write_text(
        json.dumps({"provider": "test", "costUsd": 0.05}),
        encoding="utf-8",
    )
    digest = hashlib.sha256(billing.read_bytes()).hexdigest()
    return {
        "baseline": "fallback",
        "cases": [
            {
                "caseId": case_id,
                "modes": [
                    {
                        "mode": mode,
                        "cost": 0.05,
                        "costEvidence": {
                            "status": "verified",
                            "currency": "USD",
                            "source": "provider-billing",
                            "costUsd": 0.05,
                            "verifiedBy": "independent-reviewer",
                            "verifiedAt": "2026-07-27T00:00:00Z",
                            "artifact": billing.name,
                            "sourceDigest": digest,
                        },
                    }
                    for mode in ("fallback", "plugin-context")
                ],
            }
            for case_id in (
                "python-case",
                "java-case",
                "typescript-case",
                "polyglot-case",
            )
        ],
    }


def _refresh_language_strata(paired):
    grouped = {}
    for item in paired["caseMetrics"]:
        profile = "+".join(sorted(item["languages"]))
        grouped.setdefault(profile, {}).setdefault(item["mode"], []).append(
            item
        )
    paired["report"]["languageStrata"] = {
        profile: [
            {
                "mode": mode,
                "cases": len(items),
                "true_positives": sum(
                    item["truePositives"] for item in items
                ),
                "false_positives": sum(
                    item["falsePositives"] for item in items
                ),
                "false_negatives": sum(
                    item["falseNegatives"] for item in items
                ),
                "precision": (
                    sum(item["truePositives"] for item in items)
                    / sum(
                        item["truePositives"] + item["falsePositives"]
                        for item in items
                    )
                    if sum(
                        item["truePositives"] + item["falsePositives"]
                        for item in items
                    )
                    else 0.0
                ),
                "recall": (
                    sum(item["truePositives"] for item in items)
                    / sum(
                        item["truePositives"] + item["falseNegatives"]
                        for item in items
                    )
                    if sum(
                        item["truePositives"] + item["falseNegatives"]
                        for item in items
                    )
                    else 0.0
                ),
            }
            for mode, items in sorted(by_mode.items())
        ]
        for profile, by_mode in sorted(grouped.items())
    }


def test_passes_complete_neutral_paired_quality_and_cost_gate(tmp_path):
    result = evaluate_acceptance_gate(
        paired_evaluation=_paired_evaluation(),
        manifest=_manifest(tmp_path),
        candidate="plugin-context",
        evidence_base_dir=tmp_path,
    )

    assert result["status"] == "passed"
    assert result["failedChecks"] == []
    assert all(check["passed"] for check in result["checks"].values())


def test_fails_closed_on_narrow_low_quality_or_costly_corpus(tmp_path):
    paired = _paired_evaluation()
    paired["report"]["corpusCoverage"]["cases"] = 1
    paired["report"]["corpusCoverage"]["polyglotCases"] = 0
    paired["report"]["languageStrata"] = {"python": []}
    paired["report"]["modes"][1].update({
        "coverage": 0.75,
        "precision": 0.5,
        "recall": 0.5,
        "false_positives": 8,
        "false_negatives": 8,
        "model_calls": 9,
        "input_tokens": 1250,
        "cost": 0.26,
        "cost_per_changed_kloc": 0.65,
        "median_cost_per_changed_kloc": 0.55,
        "p95_cost_per_changed_kloc": 1.0,
    })
    paired["report"]["pairedDeltas"][0].update({
        "precision": 0.0,
        "recall": -0.3,
        "model_calls": 1,
        "input_tokens": 250,
        "cost": 0.06,
        "cost_per_changed_kloc": 0.15,
        "median_cost_per_changed_kloc": 0.05,
        "p95_cost_per_changed_kloc": 0.5,
    })
    for item in paired["provenance"]:
        if item["mode"] == "plugin-context":
            item["repositoryPlugins"] = []
    for item in paired["caseMetrics"]:
        if item["mode"] == "plugin-context":
            item["repositoryPlugins"] = []
            item["falsePositives"] = 2
            item["precision"] = 0.5
            item["falseNegatives"] = 2
            item["recall"] = 0.5
            item["terminalHunks"] = 6 if item["caseId"] == "python-case" else 8
    candidate_cases = [
        item
        for item in paired["caseMetrics"]
        if item["mode"] == "plugin-context"
    ]
    candidate_cases[0]["inputTokens"] = 500
    candidate_cases[0]["modelCalls"] = 3
    candidate_cases[0]["cost"] = 0.1
    candidate_cases[0]["costPerChangedKloc"] = 1.0
    candidate_cases[1]["cost"] = 0.06
    candidate_cases[1]["costPerChangedKloc"] = 0.6
    manifest = _manifest(tmp_path)
    manifest["cases"][0]["modes"][1]["cost"] = 0.1
    manifest["cases"][0]["modes"][1]["costEvidence"]["costUsd"] = 0.1
    manifest["cases"][1]["modes"][1]["cost"] = 0.06
    manifest["cases"][1]["modes"][1]["costEvidence"]["costUsd"] = 0.06
    manifest["cases"][0]["modes"][0].pop("costEvidence")

    result = evaluate_acceptance_gate(
        paired_evaluation=paired,
        manifest=manifest,
        candidate="plugin-context",
        evidence_base_dir=tmp_path,
    )

    assert result["status"] == "failed"
    assert set(result["failedChecks"]) == {
        "candidate-plugin-selection-non-empty",
        "declared-language-plugin-selection",
        "case-metric-integrity",
        "complete-hunk-coverage",
        "general-precision",
        "general-recall",
        "standalone-language-quality",
        "paired-precision-improvement",
        "paired-recall",
        "model-call-growth",
        "input-token-growth",
        "per-case-cost-growth",
        "median-cost-growth",
        "p95-cost-growth",
        "verified-provider-cost-evidence",
    }


def test_rejects_unverifiable_cost_provenance_without_hiding_other_metrics(
    tmp_path,
):
    manifest = _manifest(tmp_path)
    evidence = manifest["cases"][2]["modes"][1]["costEvidence"]
    evidence["source"] = "operator-estimate"
    evidence["sourceDigest"] = "not-a-digest"

    result = evaluate_acceptance_gate(
        paired_evaluation=copy.deepcopy(_paired_evaluation()),
        manifest=manifest,
        candidate="plugin-context",
        evidence_base_dir=tmp_path,
    )

    assert result["status"] == "failed"
    assert result["failedChecks"] == ["verified-provider-cost-evidence"]
    assert result["checks"]["general-precision"]["passed"] is True
    assert result["checks"]["verified-provider-cost-evidence"]["actual"][
        "invalid"
    ] == ["typescript-case/plugin-context:invalid"]


def test_rejects_per_case_call_growth_hidden_by_aggregate(tmp_path):
    paired = _paired_evaluation()
    candidate = [
        item
        for item in paired["caseMetrics"]
        if item["mode"] == "plugin-context"
    ]
    candidate[0]["modelCalls"] = 3
    candidate[1]["modelCalls"] = 1

    result = evaluate_acceptance_gate(
        paired_evaluation=paired,
        manifest=_manifest(tmp_path),
        candidate="plugin-context",
        evidence_base_dir=tmp_path,
    )

    assert result["status"] == "failed"
    assert result["failedChecks"] == ["model-call-growth"]
    assert result["checks"]["model-call-growth"]["actual"]["aggregate"] == 0
    assert 1 in result["checks"]["model-call-growth"]["actual"][
        "perCase"
    ].values()


def test_rejects_case_metrics_that_drift_from_bound_manifest_cost(tmp_path):
    paired = _paired_evaluation()
    paired["caseMetrics"][1]["cost"] = 0.055
    paired["caseMetrics"][1]["costPerChangedKloc"] = 0.55
    paired["report"]["modes"][1]["cost"] = 0.205

    result = evaluate_acceptance_gate(
        paired_evaluation=paired,
        manifest=_manifest(tmp_path),
        candidate="plugin-context",
        evidence_base_dir=tmp_path,
    )

    assert result["status"] == "failed"
    assert result["failedChecks"] == ["case-metric-integrity"]
    assert result["checks"]["case-metric-integrity"]["actual"][
        "costsMatchManifest"
    ] is False


def test_rejects_low_absolute_recall_even_without_paired_regression(tmp_path):
    paired = _paired_evaluation()
    for aggregate in paired["report"]["modes"]:
        aggregate["false_negatives"] = 8
        aggregate["recall"] = 0.5
    for item in paired["caseMetrics"]:
        item["falseNegatives"] = 2
        item["recall"] = 0.5
    paired["report"]["pairedDeltas"][0]["recall"] = 0.0
    _refresh_language_strata(paired)

    result = evaluate_acceptance_gate(
        paired_evaluation=paired,
        manifest=_manifest(tmp_path),
        candidate="plugin-context",
        evidence_base_dir=tmp_path,
    )

    assert result["status"] == "failed"
    assert result["failedChecks"] == [
        "general-recall",
        "standalone-language-quality",
    ]


def test_rejects_language_regression_hidden_by_aggregate_improvement(tmp_path):
    paired = _paired_evaluation()
    candidate = {
        item["caseId"]: item
        for item in paired["caseMetrics"]
        if item["mode"] == "plugin-context"
    }
    candidate["python-case"]["falsePositives"] = 2
    candidate["python-case"]["precision"] = 0.5
    candidate["java-case"]["falsePositives"] = 0
    candidate["java-case"]["precision"] = 1.0
    _refresh_language_strata(paired)

    result = evaluate_acceptance_gate(
        paired_evaluation=paired,
        manifest=_manifest(tmp_path),
        candidate="plugin-context",
        evidence_base_dir=tmp_path,
    )

    assert result["status"] == "failed"
    assert result["failedChecks"] == ["standalone-language-quality"]
    python_quality = result["checks"]["standalone-language-quality"][
        "actual"
    ]["python"]
    assert python_quality["precision"] == 0.5
    assert python_quality["precisionDelta"] == 0.0


def test_rejects_tampered_paired_delta_even_when_aggregates_are_valid(tmp_path):
    paired = _paired_evaluation()
    paired["report"]["pairedDeltas"][0]["precision"] = 0.9

    result = evaluate_acceptance_gate(
        paired_evaluation=paired,
        manifest=_manifest(tmp_path),
        candidate="plugin-context",
        evidence_base_dir=tmp_path,
    )

    assert result["status"] == "failed"
    assert result["failedChecks"] == ["case-metric-integrity"]
    assert result["checks"]["case-metric-integrity"]["actual"][
        "deltaIntegrity"
    ] is False


def test_rejects_missing_or_tampered_billing_artifact(tmp_path):
    manifest = _manifest(tmp_path)
    billing = tmp_path / "provider-billing.json"
    billing.write_text('{"costUsd":999}', encoding="utf-8")

    result = evaluate_acceptance_gate(
        paired_evaluation=_paired_evaluation(),
        manifest=manifest,
        candidate="plugin-context",
        evidence_base_dir=tmp_path,
    )

    assert result["status"] == "failed"
    assert result["failedChecks"] == ["verified-provider-cost-evidence"]
    invalid = result["checks"]["verified-provider-cost-evidence"]["actual"][
        "invalid"
    ]
    assert len(invalid) == 8
    assert all(item.endswith("billing-artifact-digest-mismatch") for item in invalid)


def test_binds_provider_response_cost_pointer_and_exact_event_set(tmp_path):
    manifest = _manifest(tmp_path)
    response = {
        "llm_output": {
            "token_usage": {
                "input_tokens": 10,
                "output_tokens": 2,
                "cost": 0.05,
            },
        },
    }
    capture = {
        "calls": [{
            "providerEvents": [{
                "status": "completed",
                "response": response,
            }],
        }],
    }
    capture_path = tmp_path / "provider-response-capture.json"
    capture_path.write_text(json.dumps(capture), encoding="utf-8")
    response_costs = [{
        "responseDigest": _digest(response),
        "jsonPointer": "/llm_output/token_usage/cost",
        "costUsd": 0.05,
    }]
    mode = manifest["cases"][0]["modes"][0]
    mode["capture"] = capture_path.name
    mode["costEvidence"] = {
        "status": "verified",
        "currency": "USD",
        "source": "provider-response",
        "costUsd": 0.05,
        "verifiedBy": "independent-reviewer",
        "verifiedAt": "2026-07-27T00:00:00Z",
        "responseCosts": response_costs,
        "sourceDigest": _digest(response_costs),
    }

    result = evaluate_acceptance_gate(
        paired_evaluation=_paired_evaluation(),
        manifest=manifest,
        candidate="plugin-context",
        evidence_base_dir=tmp_path,
    )

    assert result["status"] == "passed"
    summary = result["checks"]["verified-provider-cost-evidence"]["actual"]
    assert summary["boundBillingArtifacts"] == 7
    assert summary["boundProviderResponses"] == 1

    mode["costEvidence"]["responseCosts"][0]["costUsd"] = 0.04
    failed = evaluate_acceptance_gate(
        paired_evaluation=_paired_evaluation(),
        manifest=manifest,
        candidate="plugin-context",
        evidence_base_dir=tmp_path,
    )
    assert failed["status"] == "failed"
    assert failed["checks"]["verified-provider-cost-evidence"]["actual"][
        "invalid"
    ] == ["python-case/fallback:provider-response-1-cost-mismatch"]
