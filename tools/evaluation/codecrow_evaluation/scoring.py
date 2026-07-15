from __future__ import annotations

import copy
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from ._util import (
    canonical_bytes,
    require_mapping,
    require_sha256,
    require_string,
    sha256_bytes,
)


class EvaluationInputError(ValueError):
    """The evaluation input cannot be scored honestly."""


_SEVERITIES = ("low", "medium", "high", "critical")
_SEVERITY_RANK = {value: index for index, value in enumerate(_SEVERITIES)}
_STATES = ("complete", "partial", "abstained")
_PURPOSES = (
    "development",
    "calibration",
    "primary_heldout",
    "confirmation_reserve",
    "diagnostic",
)
_DEFAULT_POLICY = Path(__file__).resolve().parents[1] / "policy" / "scoring-policy-v1.json"
_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
_INDEX_RE = re.compile(r"^(?:rag-disabled|rag-commit-[0-9a-f]{40,64})$")


def _integer(value: object, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise EvaluationInputError(f"{field} must be an integer >= {minimum}")
    return value


def _optional_integer(value: object, field: str) -> int | None:
    if value is None:
        return None
    return _integer(value, field)


def _severity(value: object, field: str) -> str:
    if value not in _SEVERITIES:
        raise EvaluationInputError(f"{field} must be one of {', '.join(_SEVERITIES)}")
    return str(value)


def _sequence(value: object, field: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise EvaluationInputError(f"{field} must be an array")
    return value


def _ratio(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {
        "denominator": denominator,
        "numerator": numerator,
        "value": None if denominator == 0 else numerator / denominator,
    }


def _wilson(
    numerator: int,
    denominator: int,
    *,
    z: float,
) -> dict[str, int | float | None]:
    metric = _ratio(numerator, denominator)
    if denominator == 0:
        return {
            "denominator": 0,
            "lower95": None,
            "numerator": numerator,
            "upper95": None,
            "value": None,
        }
    proportion = numerator / denominator
    denominator_adjustment = 1 + (z * z / denominator)
    center = (proportion + (z * z / (2 * denominator))) / denominator_adjustment
    margin = (
        z
        * math.sqrt(
            (proportion * (1 - proportion) / denominator)
            + (z * z / (4 * denominator * denominator))
        )
        / denominator_adjustment
    )
    return {
        "denominator": denominator,
        "lower95": max(0.0, center - margin),
        "numerator": numerator,
        "upper95": min(1.0, center + margin),
        "value": metric["value"],
    }


def _percentile(values: list[int], percentile: float) -> float | int:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = percentile * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + ((ordered[upper] - ordered[lower]) * (position - lower))


def _normalize_input(bundle: Mapping[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(dict(bundle))
    cases = normalized.get("cases")
    if isinstance(cases, list):
        for case in cases:
            if isinstance(case, dict):
                if isinstance(case.get("labels"), list):
                    case["labels"].sort(key=lambda item: str(item.get("labelId", "")))
                if isinstance(case.get("predictions"), list):
                    case["predictions"].sort(
                        key=lambda item: str(item.get("findingId", ""))
                    )
        cases.sort(
            key=lambda item: (
                str(item.get("caseId", "")) if isinstance(item, Mapping) else ""
            )
        )
    return normalized


def _score_case(case: Mapping[str, Any]) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    case_id = require_string(case.get("caseId"), "caseId", EvaluationInputError)
    label_values = _sequence(case.get("labels"), f"{case_id}.labels")
    prediction_values = _sequence(case.get("predictions"), f"{case_id}.predictions")

    labels: dict[str, str] = {}
    for raw in label_values:
        label = require_mapping(raw, f"{case_id}.labels[]", EvaluationInputError)
        label_id = require_string(label.get("labelId"), "labelId", EvaluationInputError)
        if label_id in labels:
            raise EvaluationInputError(f"{case_id} has duplicate labelId {label_id}")
        labels[label_id] = _severity(label.get("severity"), f"{label_id}.severity")
        require_string(label.get("labelVersion"), f"{label_id}.labelVersion", EvaluationInputError)
        require_string(label.get("oracleId"), f"{label_id}.oracleId", EvaluationInputError)

    prediction_by_id: dict[str, Mapping[str, Any]] = {}
    for raw in prediction_values:
        prediction = require_mapping(raw, f"{case_id}.predictions[]", EvaluationInputError)
        finding_id = require_string(
            prediction.get("findingId"), "findingId", EvaluationInputError
        )
        if finding_id in prediction_by_id:
            raise EvaluationInputError(f"{case_id} has duplicate findingId {finding_id}")
        prediction_by_id[finding_id] = prediction

    duplicates = 0
    unsupported = 0
    true_positives = 0
    false_positives = 0
    matched_labels: set[str] = set()
    severity_pairs: list[tuple[str, str]] = []
    for finding_id in sorted(prediction_by_id):
        prediction = prediction_by_id[finding_id]
        matched = prediction.get("matchedLabelId")
        if matched is not None and (not isinstance(matched, str) or matched not in labels):
            raise EvaluationInputError(
                f"{case_id}.{finding_id}.matchedLabelId must reference an existing label or be null"
            )
        predicted_severity = _severity(
            prediction.get("severity"), f"{case_id}.{finding_id}.severity"
        )
        supported = prediction.get("supported")
        if not isinstance(supported, bool):
            raise EvaluationInputError(f"{case_id}.{finding_id}.supported must be boolean")
        if not supported:
            unsupported += 1

        duplicate_of = prediction.get("duplicateOf")
        if duplicate_of is not None:
            if not isinstance(duplicate_of, str) or duplicate_of == finding_id:
                raise EvaluationInputError(
                    f"{case_id}.{finding_id}.duplicateOf must reference another finding"
                )
            original = prediction_by_id.get(duplicate_of)
            if original is None:
                raise EvaluationInputError(
                    f"{case_id}.{finding_id}.duplicateOf references missing finding {duplicate_of}"
                )
            if original.get("duplicateOf") is not None:
                raise EvaluationInputError(
                    f"{case_id}.{finding_id}.duplicateOf cannot form a duplicate chain"
                )
            if original.get("matchedLabelId") != matched:
                raise EvaluationInputError(
                    f"{case_id}.{finding_id}.duplicateOf must have the same matchedLabelId"
                )
            duplicates += 1
            continue

        if supported and matched is not None and matched not in matched_labels:
            true_positives += 1
            matched_labels.add(matched)
            severity_pairs.append((labels[matched], predicted_severity))
        else:
            false_positives += 1

    false_negatives = len(set(labels) - matched_labels)

    analysis = require_mapping(case.get("analysis"), f"{case_id}.analysis", EvaluationInputError)
    state = analysis.get("state")
    if state not in _STATES:
        raise EvaluationInputError(
            f"{case_id}.analysis.state must be one of {', '.join(_STATES)}"
        )
    partial_reason = analysis.get("partialReason")
    if state in ("partial", "abstained"):
        require_string(
            partial_reason,
            f"{case_id}.analysis.partialReason",
            EvaluationInputError,
        )
    elif partial_reason is not None:
        raise EvaluationInputError(
            f"{case_id}.analysis.partialReason must be null for a complete result"
        )

    coverage = require_mapping(case.get("coverage"), f"{case_id}.coverage", EvaluationInputError)
    represented = _integer(coverage.get("represented"), f"{case_id}.coverage.represented")
    coverage_total = _integer(coverage.get("total"), f"{case_id}.coverage.total")
    if represented > coverage_total:
        raise EvaluationInputError(
            f"{case_id}.coverage represented cannot exceed total"
        )

    resource = require_mapping(case.get("resource"), f"{case_id}.resource", EvaluationInputError)
    estimated_cost = _integer(
        resource.get("estimatedCostMicrousd"),
        f"{case_id}.resource.estimatedCostMicrousd",
    )
    reported_cost = _optional_integer(
        resource.get("providerReportedCostMicrousd"),
        f"{case_id}.resource.providerReportedCostMicrousd",
    )
    latency_ms = _integer(resource.get("latencyMs"), f"{case_id}.resource.latencyMs")

    counts = {
        "falseNegatives": false_negatives,
        "falsePositives": false_positives,
        "publishedFindings": len(prediction_by_id),
        "truePositives": true_positives,
        "duplicates": duplicates,
        "unsupported": unsupported,
    }
    per_pr = {
        "analysisState": state,
        "caseId": case_id,
        "cleanControl": len(labels) == 0,
        "cleanControlPassed": len(labels) == 0 and false_positives == 0,
        "counts": counts,
        "coverageHonesty": {
            "ratio": None if coverage_total == 0 else represented / coverage_total,
            "represented": represented,
            "total": coverage_total,
        },
        "costMicrousd": {
            "estimated": estimated_cost,
            "providerReported": reported_cost,
        },
        "duplicateRate": _ratio(duplicates, len(prediction_by_id)),
        "latencyMs": latency_ms,
        "partialReason": partial_reason,
        "precision": _ratio(true_positives, true_positives + false_positives),
        "recall": _ratio(true_positives, true_positives + false_negatives),
        "unsupportedRate": _ratio(unsupported, len(prediction_by_id)),
    }
    return per_pr, severity_pairs


def _load_policy(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        policy = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvaluationInputError(f"cannot load scoring policy {path}") from exc
    if not isinstance(policy, dict) or policy.get("schemaVersion") != 1:
        raise EvaluationInputError("scoring policy schemaVersion must be 1")
    if policy.get("policyId") != "p0-05-v1":
        raise EvaluationInputError("unsupported scoring policy identity")
    confidence = policy.get("confidenceInterval")
    if (
        not isinstance(confidence, dict)
        or confidence.get("kind") != "wilson_score"
        or confidence.get("level") != 0.95
        or confidence.get("z") != 1.959963984540054
    ):
        raise EvaluationInputError("scoring policy confidenceInterval is unsupported")
    if policy.get("severityOrder") != list(_SEVERITIES):
        raise EvaluationInputError("scoring policy severityOrder is unsupported")
    return policy, sha256_bytes(raw)


def _validate_provenance(value: object, *, purpose: str) -> dict[str, Any]:
    provenance = dict(require_mapping(value, "provenance", EvaluationInputError))
    expected_fields = {
        "accessGrantId",
        "accessLedgerHeadSha256",
        "baselineManifestSha256",
        "codecrowPublicRevision",
        "codecrowStaticRevision",
        "command",
        "corpusManifestSha256",
        "dirtyStateSha256",
        "environmentSha256",
        "executionTelemetrySha256",
        "indexVersion",
        "modelVersion",
        "oracleCatalogSha256",
        "promptVersion",
        "ruleVersion",
        "seed",
        "splitRegistrySha256",
    }
    missing = sorted(expected_fields - set(provenance))
    extra = sorted(set(provenance) - expected_fields)
    if missing:
        raise EvaluationInputError(f"provenance is missing {missing[0]}")
    if extra:
        raise EvaluationInputError(f"provenance has unsupported field {extra[0]}")
    for field in (
        "baselineManifestSha256",
        "corpusManifestSha256",
        "dirtyStateSha256",
        "environmentSha256",
        "executionTelemetrySha256",
        "oracleCatalogSha256",
        "splitRegistrySha256",
    ):
        require_sha256(provenance[field], f"provenance.{field}", EvaluationInputError)
    for field in ("codecrowPublicRevision", "codecrowStaticRevision"):
        revision = provenance[field]
        if not isinstance(revision, str) or _REVISION_RE.fullmatch(revision) is None:
            raise EvaluationInputError(f"provenance.{field} must be a full lowercase Git commit")
    for field in ("modelVersion", "promptVersion", "ruleVersion"):
        require_string(provenance[field], f"provenance.{field}", EvaluationInputError)
    index_version = provenance["indexVersion"]
    if not isinstance(index_version, str) or _INDEX_RE.fullmatch(index_version) is None:
        raise EvaluationInputError(
            "provenance.indexVersion must be rag-disabled or an exact rag-commit digest"
        )
    seed = provenance["seed"]
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise EvaluationInputError("provenance.seed must be an integer >= 0")
    command = provenance["command"]
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(argument, str) or not argument for argument in command)
    ):
        raise EvaluationInputError("provenance.command must be a non-empty argv array")
    protected = purpose in ("primary_heldout", "confirmation_reserve")
    if protected:
        require_sha256(
            provenance["accessGrantId"],
            "provenance.accessGrantId",
            EvaluationInputError,
        )
        require_sha256(
            provenance["accessLedgerHeadSha256"],
            "provenance.accessLedgerHeadSha256",
            EvaluationInputError,
        )
    elif (
        provenance["accessGrantId"] is not None
        or provenance["accessLedgerHeadSha256"] is not None
    ):
        raise EvaluationInputError(
            "public/diagnostic provenance must not claim a protected access grant"
        )
    return provenance


def score_evaluation(
    bundle: Mapping[str, Any],
    *,
    policy_path: Path | None = None,
) -> dict[str, Any]:
    """Score a versioned evaluation bundle deterministically at PR granularity."""

    data = require_mapping(bundle, "evaluation bundle", EvaluationInputError)
    if data.get("schemaVersion") != 1:
        raise EvaluationInputError("schemaVersion must be 1")
    evaluation_id = require_string(
        data.get("evaluationId"), "evaluationId", EvaluationInputError
    )
    purpose = data.get("splitPurpose")
    if purpose not in _PURPOSES:
        raise EvaluationInputError(f"splitPurpose must be one of {', '.join(_PURPOSES)}")
    scoring_policy_version = require_string(
        data.get("scoringPolicyVersion"),
        "scoringPolicyVersion",
        EvaluationInputError,
    )
    policy, policy_sha256 = _load_policy(policy_path or _DEFAULT_POLICY)
    if scoring_policy_version != policy["policyId"]:
        raise EvaluationInputError(
            "scoringPolicyVersion must match the loaded scoring policy"
        )
    provenance = _validate_provenance(data.get("provenance"), purpose=str(purpose))
    raw_cases = _sequence(data.get("cases"), "cases")
    if not raw_cases:
        raise EvaluationInputError("cases must contain at least one PR")

    normalized = _normalize_input(data)
    case_ids: set[str] = set()
    per_pr: list[dict[str, Any]] = []
    severity_pairs: list[tuple[str, str]] = []
    for raw_case in normalized["cases"]:
        case = require_mapping(raw_case, "cases[]", EvaluationInputError)
        scored, pairs = _score_case(case)
        if scored["caseId"] in case_ids:
            raise EvaluationInputError(f"duplicate caseId {scored['caseId']}")
        case_ids.add(scored["caseId"])
        per_pr.append(scored)
        severity_pairs.extend(pairs)

    count_names = (
        "falseNegatives",
        "falsePositives",
        "publishedFindings",
        "truePositives",
        "duplicates",
        "unsupported",
    )
    totals = {
        name: sum(int(case["counts"][name]) for case in per_pr) for name in count_names
    }
    aggregate_counts = {"caseCount": len(per_pr), **totals}
    clean_controls = [case for case in per_pr if case["cleanControl"]]
    clean_passed = sum(1 for case in clean_controls if case["cleanControlPassed"])
    confusion = Counter(f"{expected}->{predicted}" for expected, predicted in severity_pairs)
    severity_errors = [
        abs(_SEVERITY_RANK[expected] - _SEVERITY_RANK[predicted])
        for expected, predicted in severity_pairs
    ]
    exact = sum(1 for error in severity_errors if error == 0)
    coverage_represented = sum(case["coverageHonesty"]["represented"] for case in per_pr)
    coverage_total = sum(case["coverageHonesty"]["total"] for case in per_pr)
    reported_costs = [
        case["costMicrousd"]["providerReported"]
        for case in per_pr
        if case["costMicrousd"]["providerReported"] is not None
    ]
    latencies = [int(case["latencyMs"]) for case in per_pr]
    state_counts = Counter(str(case["analysisState"]) for case in per_pr)

    aggregate = {
        "cleanControls": {
            "failed": len(clean_controls) - clean_passed,
            "passed": clean_passed,
            "total": len(clean_controls),
        },
        "costMicrousd": {
            "estimated": sum(case["costMicrousd"]["estimated"] for case in per_pr),
            "providerReported": sum(reported_costs),
            "providerReportedCases": len(reported_costs),
            "totalCases": len(per_pr),
        },
        "counts": aggregate_counts,
        "coverageHonesty": {
            "ratio": None if coverage_total == 0 else coverage_represented / coverage_total,
            "represented": coverage_represented,
            "total": coverage_total,
        },
        "duplicateRate": _ratio(totals["duplicates"], totals["publishedFindings"]),
        "latencyMs": {
            "max": max(latencies),
            "p50": _percentile(latencies, 0.5),
            "p95": _percentile(latencies, 0.95),
        },
        "precision": _wilson(
            totals["truePositives"],
            totals["truePositives"] + totals["falsePositives"],
            z=policy["confidenceInterval"]["z"],
        ),
        "recall": _wilson(
            totals["truePositives"],
            totals["truePositives"] + totals["falseNegatives"],
            z=policy["confidenceInterval"]["z"],
        ),
        "severityCalibration": {
            "confusion": dict(sorted(confusion.items())),
            "exactRate": _ratio(exact, len(severity_pairs)),
            "meanAbsoluteError": (
                None if not severity_errors else sum(severity_errors) / len(severity_errors)
            ),
        },
        "stateCounts": {state: state_counts[state] for state in _STATES},
        "unsupportedRate": _ratio(totals["unsupported"], totals["publishedFindings"]),
    }
    return {
        "aggregate": aggregate,
        "evaluationId": evaluation_id,
        "inputSha256": sha256_bytes(canonical_bytes(normalized)),
        "perPr": per_pr,
        "provenance": provenance,
        "provenanceSha256": sha256_bytes(canonical_bytes(provenance)),
        "schemaVersion": 1,
        "scoringPolicySha256": policy_sha256,
        "scoringPolicyVersion": scoring_policy_version,
        "splitPurpose": purpose,
    }
