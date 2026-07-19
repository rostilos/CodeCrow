from __future__ import annotations

import copy

import pytest

from codecrow_evaluation.comparison import compare_approaches
from codecrow_evaluation.scoring import EvaluationInputError


def _bundle(approach: str, *, false_positive: bool) -> dict:
    predictions = [
        {
            "findingId": "finding-tp",
            "matchedLabelId": "bug-a",
            "severity": "high",
            "supported": True,
            "duplicateOf": None,
        }
    ]
    if false_positive:
        predictions.append(
            {
                "findingId": "finding-fp",
                "matchedLabelId": None,
                "severity": "high",
                "supported": True,
                "duplicateOf": None,
            }
        )
    return {
        "schemaVersion": 1,
        "evaluationId": f"paired-{approach.lower()}",
        "splitPurpose": "calibration",
        "scoringPolicyVersion": "p0-05-v1",
        "provenance": {
            "baselineManifestSha256": "a" * 64,
            "codecrowPublicRevision": "8" * 40,
            "codecrowStaticRevision": "9" * 40,
            "dirtyStateSha256": "b" * 64,
            "splitRegistrySha256": "c" * 64,
            "corpusManifestSha256": "d" * 64,
            "oracleCatalogSha256": "e" * 64,
            "executionTelemetrySha256": ("f" if approach == "CLASSIC" else "2") * 64,
            "environmentSha256": "1" * 64,
            "modelVersion": "model-v1",
            "promptVersion": f"prompt-{approach.lower()}-v1",
            "ruleVersion": "rules-v1",
            "indexVersion": "rag-disabled",
            "reviewApproach": approach,
            "seed": 0,
            "command": ["codecrow-evaluation", approach.lower()],
            "accessGrantId": None,
            "accessLedgerHeadSha256": None,
        },
        "cases": [
            {
                "caseId": "pr-a",
                "labels": [
                    {
                        "labelId": "bug-a",
                        "severity": "high",
                        "labelVersion": "labels-v1",
                        "oracleId": "oracle-v1",
                    }
                ],
                "predictions": predictions,
                "analysis": {"state": "complete", "partialReason": None},
                "coverage": {
                    "represented": 0 if approach == "CLASSIC" else 1,
                    "total": 1,
                },
                "resource": {
                    "estimatedCostMicrousd": 10 if approach == "CLASSIC" else 20,
                    "providerReportedCostMicrousd": 9 if approach == "CLASSIC" else 18,
                    "latencyMs": 10 if approach == "CLASSIC" else 15,
                },
            }
        ],
    }


def test_compare_approaches_scores_same_cases_and_reports_deltas() -> None:
    result = compare_approaches(
        _bundle("CLASSIC", false_positive=True),
        _bundle("AGENTIC", false_positive=False),
    )

    assert result["caseCount"] == 1
    assert result["classic"]["reviewApproach"] == "CLASSIC"
    assert result["agentic"]["reviewApproach"] == "AGENTIC"
    assert result["classic"]["aggregate"]["precision"]["value"] == 0.5
    assert result["agentic"]["aggregate"]["precision"]["value"] == 1.0
    assert result["delta"]["precision"] == 0.5
    assert result["delta"]["f1"] == pytest.approx(1 / 3)
    assert result["delta"]["highSeverityPrecision"] == 0.5
    assert result["delta"]["falsePositives"] == -1
    assert result["delta"]["estimatedCostMicrousd"] == 10
    assert result["delta"]["providerReportedCostMicrousd"] == 9
    assert result["delta"]["coverageRatio"] == 1.0
    assert result["delta"]["coverageRepresented"] == 1
    assert result["delta"]["latencyP95Ms"] == 5
    assert result["delta"]["latencyMaxMs"] == 5
    assert result["truePositiveRetention"] == 1.0
    assert result["perPr"] == [
        {
            "caseId": "pr-a",
            "truePositivesDelta": 0,
            "falsePositivesDelta": -1,
            "falseNegativesDelta": 0,
        }
    ]


def test_compare_approaches_does_not_invent_partial_provider_cost_delta() -> None:
    classic = _bundle("CLASSIC", false_positive=True)
    agentic = _bundle("AGENTIC", false_positive=False)
    classic["cases"][0]["resource"]["providerReportedCostMicrousd"] = None

    result = compare_approaches(classic, agentic)

    assert result["delta"]["providerReportedCostMicrousd"] is None


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (
            lambda classic, agentic: agentic["provenance"].update(
                {"reviewApproach": "CLASSIC"}
            ),
            "AGENTIC",
        ),
        (
            lambda classic, agentic: agentic["cases"][0]["labels"][0].update(
                {"labelId": "other-bug"}
            ),
            "frozen truth",
        ),
        (
            lambda classic, agentic: agentic["provenance"].update(
                {"indexVersion": "rag-commit-" + "7" * 40}
            ),
            "indexVersion",
        ),
        (
            lambda classic, agentic: agentic["provenance"].update(
                {"environmentSha256": "3" * 64}
            ),
            "environmentSha256",
        ),
        (
            lambda classic, agentic: agentic["provenance"].update({"seed": 1}),
            "seed",
        ),
    ],
)
def test_compare_approaches_rejects_non_paired_inputs(mutator, message: str) -> None:
    classic = _bundle("CLASSIC", false_positive=True)
    agentic = copy.deepcopy(_bundle("AGENTIC", false_positive=False))
    mutator(classic, agentic)

    with pytest.raises(EvaluationInputError, match=message):
        compare_approaches(classic, agentic)
