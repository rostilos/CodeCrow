from __future__ import annotations

from typing import Any, Mapping

from ._util import canonical_bytes, require_mapping, require_string, sha256_bytes
from .scoring import EvaluationInputError, score_evaluation


_PAIR_BOUND_PROVENANCE = (
    "baselineManifestSha256",
    "codecrowPublicRevision",
    "codecrowStaticRevision",
    "corpusManifestSha256",
    "dirtyStateSha256",
    "environmentSha256",
    "indexVersion",
    "modelVersion",
    "oracleCatalogSha256",
    "ruleVersion",
    "seed",
    "splitRegistrySha256",
)


def _approach(bundle: Mapping[str, Any], expected: str) -> None:
    provenance = require_mapping(
        bundle.get("provenance"), f"{expected} provenance", EvaluationInputError
    )
    if provenance.get("reviewApproach") != expected:
        raise EvaluationInputError(
            f"paired {expected} input must declare provenance.reviewApproach={expected}"
        )


def _case_truth(bundle: Mapping[str, Any]) -> dict[str, bytes]:
    values = bundle.get("cases")
    if not isinstance(values, list):
        raise EvaluationInputError("paired input cases must be an array")
    result: dict[str, bytes] = {}
    for value in values:
        case = require_mapping(value, "paired cases[]", EvaluationInputError)
        case_id = require_string(case.get("caseId"), "caseId", EvaluationInputError)
        if case_id in result:
            raise EvaluationInputError(f"duplicate paired caseId {case_id}")
        context = case.get("context")
        expected_context = (
            require_mapping(context, f"{case_id}.context", EvaluationInputError).get(
                "expectedItems"
            )
            if context is not None
            else None
        )
        labels = case.get("labels")
        if not isinstance(labels, list):
            raise EvaluationInputError(f"{case_id}.labels must be an array")
        normalized_labels = sorted(
            labels,
            key=lambda item: str(item.get("labelId", ""))
            if isinstance(item, Mapping)
            else "",
        )
        normalized_expected_context = (
            sorted(expected_context) if isinstance(expected_context, list) else expected_context
        )
        result[case_id] = canonical_bytes(
            {
                "labels": normalized_labels,
                "expectedContextItems": normalized_expected_context,
                "coverageTotal": require_mapping(
                    case.get("coverage"),
                    f"{case_id}.coverage",
                    EvaluationInputError,
                ).get("total"),
            }
        )
    return result


def _metric_value(result: Mapping[str, Any], name: str) -> float | None:
    metric = result["aggregate"][name]
    value = metric.get("value")
    return float(value) if value is not None else None


def _delta(agentic: float | int | None, classic: float | int | None):
    if agentic is None or classic is None:
        return None
    return agentic - classic


def _fully_reported_cost(result: Mapping[str, Any]) -> int | None:
    cost = result["aggregate"]["costMicrousd"]
    if cost["providerReportedCases"] != cost["totalCases"]:
        return None
    return int(cost["providerReported"])


def compare_approaches(
    classic_bundle: Mapping[str, Any],
    agentic_bundle: Mapping[str, Any],
) -> dict[str, Any]:
    """Score and compare two approaches over the same frozen case truth."""

    classic_input = require_mapping(
        classic_bundle, "CLASSIC evaluation bundle", EvaluationInputError
    )
    agentic_input = require_mapping(
        agentic_bundle, "AGENTIC evaluation bundle", EvaluationInputError
    )
    _approach(classic_input, "CLASSIC")
    _approach(agentic_input, "AGENTIC")

    for field in ("schemaVersion", "splitPurpose", "scoringPolicyVersion"):
        if classic_input.get(field) != agentic_input.get(field):
            raise EvaluationInputError(f"paired inputs disagree on {field}")
    classic_provenance = require_mapping(
        classic_input["provenance"], "CLASSIC provenance", EvaluationInputError
    )
    agentic_provenance = require_mapping(
        agentic_input["provenance"], "AGENTIC provenance", EvaluationInputError
    )
    for field in _PAIR_BOUND_PROVENANCE:
        if classic_provenance.get(field) != agentic_provenance.get(field):
            raise EvaluationInputError(
                f"paired inputs disagree on provenance.{field}"
            )

    classic_truth = _case_truth(classic_input)
    agentic_truth = _case_truth(agentic_input)
    if classic_truth.keys() != agentic_truth.keys():
        raise EvaluationInputError("paired inputs contain different case IDs")
    for case_id in classic_truth:
        if classic_truth[case_id] != agentic_truth[case_id]:
            raise EvaluationInputError(
                f"paired inputs contain different frozen truth for {case_id}"
            )

    classic = score_evaluation(classic_input)
    agentic = score_evaluation(agentic_input)
    classic_counts = classic["aggregate"]["counts"]
    agentic_counts = agentic["aggregate"]["counts"]
    classic_tp = int(classic_counts["truePositives"])
    agentic_tp = int(agentic_counts["truePositives"])
    per_classic = {item["caseId"]: item for item in classic["perPr"]}
    per_agentic = {item["caseId"]: item for item in agentic["perPr"]}

    return {
        "schemaVersion": 1,
        "comparisonId": sha256_bytes(
            canonical_bytes(
                {
                    "classicInput": classic["inputSha256"],
                    "agenticInput": agentic["inputSha256"],
                }
            )
        ),
        "caseCount": len(classic_truth),
        "classic": {
            "aggregate": classic["aggregate"],
            "inputSha256": classic["inputSha256"],
            "provenanceSha256": classic["provenanceSha256"],
            "reviewApproach": "CLASSIC",
        },
        "agentic": {
            "aggregate": agentic["aggregate"],
            "inputSha256": agentic["inputSha256"],
            "provenanceSha256": agentic["provenanceSha256"],
            "reviewApproach": "AGENTIC",
        },
        "delta": {
            "precision": _delta(
                _metric_value(agentic, "precision"),
                _metric_value(classic, "precision"),
            ),
            "recall": _delta(
                _metric_value(agentic, "recall"),
                _metric_value(classic, "recall"),
            ),
            "f1": _delta(
                _metric_value(agentic, "f1"),
                _metric_value(classic, "f1"),
            ),
            "highSeverityPrecision": _delta(
                _metric_value(agentic, "highSeverityPrecision"),
                _metric_value(classic, "highSeverityPrecision"),
            ),
            "truePositives": agentic_tp - classic_tp,
            "falsePositives": int(agentic_counts["falsePositives"])
            - int(classic_counts["falsePositives"]),
            "falseNegatives": int(agentic_counts["falseNegatives"])
            - int(classic_counts["falseNegatives"]),
            "estimatedCostMicrousd": int(
                agentic["aggregate"]["costMicrousd"]["estimated"]
            )
            - int(classic["aggregate"]["costMicrousd"]["estimated"]),
            "providerReportedCostMicrousd": _delta(
                _fully_reported_cost(agentic),
                _fully_reported_cost(classic),
            ),
            "coverageRepresented": int(
                agentic["aggregate"]["coverageHonesty"]["represented"]
            )
            - int(classic["aggregate"]["coverageHonesty"]["represented"]),
            "coverageRatio": _delta(
                agentic["aggregate"]["coverageHonesty"]["ratio"],
                classic["aggregate"]["coverageHonesty"]["ratio"],
            ),
            "latencyP50Ms": _delta(
                agentic["aggregate"]["latencyMs"]["p50"],
                classic["aggregate"]["latencyMs"]["p50"],
            ),
            "latencyP95Ms": _delta(
                agentic["aggregate"]["latencyMs"]["p95"],
                classic["aggregate"]["latencyMs"]["p95"],
            ),
            "latencyMaxMs": _delta(
                agentic["aggregate"]["latencyMs"]["max"],
                classic["aggregate"]["latencyMs"]["max"],
            ),
        },
        "truePositiveRetention": (
            None if classic_tp == 0 else agentic_tp / classic_tp
        ),
        "perPr": [
            {
                "caseId": case_id,
                "truePositivesDelta": (
                    int(per_agentic[case_id]["counts"]["truePositives"])
                    - int(per_classic[case_id]["counts"]["truePositives"])
                ),
                "falsePositivesDelta": (
                    int(per_agentic[case_id]["counts"]["falsePositives"])
                    - int(per_classic[case_id]["counts"]["falsePositives"])
                ),
                "falseNegativesDelta": (
                    int(per_agentic[case_id]["counts"]["falseNegatives"])
                    - int(per_classic[case_id]["counts"]["falseNegatives"])
                ),
            }
            for case_id in sorted(classic_truth)
        ],
    }
