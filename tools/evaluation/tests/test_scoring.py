from __future__ import annotations

import copy

import pytest

from codecrow_evaluation.scoring import EvaluationInputError, score_evaluation


def _label(label_id: str, severity: str = "medium") -> dict:
    return {
        "labelId": label_id,
        "severity": severity,
        "labelVersion": "labels-v1",
        "oracleId": "oracle-v1",
    }


def _prediction(
    finding_id: str,
    *,
    matched: str | None,
    severity: str = "medium",
    supported: bool = True,
    duplicate_of: str | None = None,
) -> dict:
    return {
        "findingId": finding_id,
        "matchedLabelId": matched,
        "severity": severity,
        "supported": supported,
        "duplicateOf": duplicate_of,
    }


def _case(
    case_id: str,
    *,
    labels: list[dict] | None = None,
    predictions: list[dict] | None = None,
    state: str = "complete",
    partial_reason: str | None = None,
    represented: int = 4,
    total: int = 4,
    estimated_cost: int = 100,
    reported_cost: int | None = 90,
    latency_ms: int = 10,
) -> dict:
    return {
        "caseId": case_id,
        "labels": labels or [],
        "predictions": predictions or [],
        "analysis": {"state": state, "partialReason": partial_reason},
        "coverage": {"represented": represented, "total": total},
        "resource": {
            "estimatedCostMicrousd": estimated_cost,
            "providerReportedCostMicrousd": reported_cost,
            "latencyMs": latency_ms,
        },
    }


def _bundle(cases: list[dict]) -> dict:
    return {
        "schemaVersion": 1,
        "evaluationId": "calibration-2026-07-15",
        "splitPurpose": "calibration",
        "scoringPolicyVersion": "p0-05-v1",
        "provenance": {
            "baselineManifestSha256": "a" * 64,
            "codecrowPublicRevision": "89287e1fce55dc9bffeca2b92ce660d8791ae6ac",
            "codecrowStaticRevision": "d661106ecafaabcb3349676b93684246de6bdc17",
            "dirtyStateSha256": "b" * 64,
            "splitRegistrySha256": "c" * 64,
            "corpusManifestSha256": "d" * 64,
            "oracleCatalogSha256": "e" * 64,
            "executionTelemetrySha256": "f" * 64,
            "environmentSha256": "1" * 64,
            "modelVersion": "offline-model-v1",
            "promptVersion": "prompt-v1",
            "ruleVersion": "rule-v1",
            "indexVersion": "rag-disabled",
            "seed": 0,
            "command": ["codecrow-evaluation", "score"],
            "accessGrantId": None,
            "accessLedgerHeadSha256": None,
        },
        "cases": cases,
    }


def test_scores_true_false_and_false_negative_findings_without_duplicate_inflation() -> None:
    result = score_evaluation(
        _bundle(
            [
                _case(
                    "pr-positive",
                    labels=[_label("bug-a", "high"), _label("bug-b", "medium")],
                    predictions=[
                        _prediction("finding-1", matched="bug-a", severity="medium"),
                        _prediction(
                            "finding-2",
                            matched="bug-a",
                            severity="medium",
                            duplicate_of="finding-1",
                        ),
                        _prediction("finding-3", matched=None, severity="low"),
                        _prediction(
                            "finding-4",
                            matched="bug-b",
                            severity="medium",
                            supported=False,
                        ),
                    ],
                )
            ]
        )
    )

    counts = result["aggregate"]["counts"]
    assert counts == {
        "caseCount": 1,
        "falseNegatives": 1,
        "falsePositives": 2,
        "publishedFindings": 4,
        "truePositives": 1,
        "duplicates": 1,
        "unsupported": 1,
    }
    assert result["aggregate"]["precision"]["value"] == pytest.approx(1 / 3)
    assert result["aggregate"]["recall"]["value"] == pytest.approx(1 / 2)
    assert result["aggregate"]["duplicateRate"]["value"] == pytest.approx(1 / 4)
    assert result["aggregate"]["unsupportedRate"]["value"] == pytest.approx(1 / 4)
    assert result["perPr"][0]["counts"]["duplicates"] == 1


def test_clean_negative_controls_report_pass_and_false_positive_failures() -> None:
    result = score_evaluation(
        _bundle(
            [
                _case("clean-pass"),
                _case(
                    "clean-fail",
                    predictions=[_prediction("finding-clean", matched=None)],
                ),
            ]
        )
    )

    assert result["aggregate"]["cleanControls"] == {
        "failed": 1,
        "passed": 1,
        "total": 2,
    }
    assert result["aggregate"]["counts"]["falsePositives"] == 1


def test_abstention_and_partial_results_remain_false_negative_visible() -> None:
    result = score_evaluation(
        _bundle(
            [
                _case(
                    "abstained",
                    labels=[_label("bug-a")],
                    state="abstained",
                    partial_reason="budget_exhausted",
                ),
                _case(
                    "partial",
                    labels=[_label("bug-b"), _label("bug-c")],
                    predictions=[_prediction("finding-b", matched="bug-b")],
                    state="partial",
                    partial_reason="deadline",
                ),
            ]
        )
    )

    assert result["aggregate"]["stateCounts"] == {
        "abstained": 1,
        "complete": 0,
        "partial": 1,
    }
    assert result["aggregate"]["counts"]["falseNegatives"] == 2
    assert [row["analysisState"] for row in result["perPr"]] == [
        "abstained",
        "partial",
    ]
    assert result["perPr"][0]["partialReason"] == "budget_exhausted"


def test_severity_calibration_confidence_intervals_cost_latency_and_coverage_are_reproducible() -> None:
    cases = [
        _case(
            "pr-z",
            labels=[_label("bug-z", "high")],
            predictions=[_prediction("finding-z", matched="bug-z", severity="medium")],
            represented=3,
            total=5,
            estimated_cost=300,
            reported_cost=250,
            latency_ms=30,
        ),
        _case(
            "pr-a",
            labels=[_label("bug-a", "low")],
            predictions=[_prediction("finding-a", matched="bug-a", severity="low")],
            represented=5,
            total=5,
            estimated_cost=100,
            reported_cost=None,
            latency_ms=10,
        ),
    ]
    result = score_evaluation(_bundle(cases))
    reordered = score_evaluation(_bundle(list(reversed(copy.deepcopy(cases)))))

    assert result == reordered
    assert result["aggregate"]["severityCalibration"] == {
        "confusion": {"high->medium": 1, "low->low": 1},
        "exactRate": {"denominator": 2, "numerator": 1, "value": 0.5},
        "meanAbsoluteError": 0.5,
    }
    precision = result["aggregate"]["precision"]
    assert precision["lower95"] <= precision["value"] <= precision["upper95"]
    assert precision["numerator"] == precision["denominator"] == 2
    assert result["aggregate"]["coverageHonesty"] == {
        "ratio": 0.8,
        "represented": 8,
        "total": 10,
    }
    assert result["aggregate"]["costMicrousd"] == {
        "estimated": 400,
        "providerReported": 250,
        "providerReportedCases": 1,
        "totalCases": 2,
    }
    assert result["aggregate"]["latencyMs"] == {"max": 30, "p50": 20.0, "p95": 29.0}
    assert [row["caseId"] for row in result["perPr"]] == ["pr-a", "pr-z"]
    assert len(result["inputSha256"]) == 64
    assert len(result["provenanceSha256"]) == 64
    assert result["provenance"]["codecrowPublicRevision"] == (
        "89287e1fce55dc9bffeca2b92ce660d8791ae6ac"
    )


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (
            lambda case: case["predictions"].append(
                _prediction("finding-dangling", matched="bug-a", duplicate_of="missing")
            ),
            "duplicateOf",
        ),
        (
            lambda case: case["predictions"].extend(
                [
                    _prediction("finding-1", matched="bug-a"),
                    _prediction("finding-2", matched="bug-b", duplicate_of="finding-1"),
                ]
            ),
            "same matchedLabelId",
        ),
        (lambda case: case["coverage"].update({"represented": 5, "total": 4}), "coverage"),
        (lambda case: case["analysis"].update({"state": "partial", "partialReason": None}), "partialReason"),
    ],
)
def test_invalid_or_dishonest_scoring_inputs_fail_closed(mutator, message: str) -> None:
    case = _case("invalid", labels=[_label("bug-a"), _label("bug-b")])
    mutator(case)

    with pytest.raises(EvaluationInputError, match=message):
        score_evaluation(_bundle([case]))


def test_zero_denominator_metrics_are_explicitly_undefined() -> None:
    result = score_evaluation(_bundle([_case("clean")]))

    assert result["aggregate"]["precision"] == {
        "denominator": 0,
        "lower95": None,
        "numerator": 0,
        "upper95": None,
        "value": None,
    }
    assert result["aggregate"]["recall"]["value"] is None


def test_missing_reproducibility_or_protected_access_provenance_fails_closed() -> None:
    missing = _bundle([_case("pr-a")])
    del missing["provenance"]["promptVersion"]
    with pytest.raises(EvaluationInputError, match="promptVersion"):
        score_evaluation(missing)

    protected = _bundle([_case("pr-a")])
    protected["splitPurpose"] = "primary_heldout"
    with pytest.raises(EvaluationInputError, match="accessGrantId"):
        score_evaluation(protected)
