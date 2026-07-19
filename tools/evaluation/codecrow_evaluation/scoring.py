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


def _f1(
    true_positives: int,
    false_positives: int,
    false_negatives: int,
) -> dict[str, int | float | None]:
    return _ratio(
        2 * true_positives,
        (2 * true_positives) + false_positives + false_negatives,
    )


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
                context = case.get("context")
                if isinstance(context, dict):
                    if isinstance(context.get("expectedItems"), list):
                        context["expectedItems"].sort()
                    if isinstance(context.get("retrievedItems"), list):
                        context["retrievedItems"].sort(
                            key=lambda item: str(item.get("itemId", ""))
                        )
                    if isinstance(context.get("gapCodes"), list):
                        context["gapCodes"].sort()
        cases.sort(
            key=lambda item: (
                str(item.get("caseId", "")) if isinstance(item, Mapping) else ""
            )
        )
    return normalized


def _score_context(case_id: str, value: object) -> dict[str, Any] | None:
    """Score RAG/context assembly independently from final issue generation."""
    if value is None:
        return None
    context = require_mapping(value, f"{case_id}.context", EvaluationInputError)
    expected_values = _sequence(
        context.get("expectedItems"), f"{case_id}.context.expectedItems"
    )
    expected: set[str] = set()
    for raw in expected_values:
        expected_id = require_string(
            raw, f"{case_id}.context.expectedItems[]", EvaluationInputError
        )
        if expected_id in expected:
            raise EvaluationInputError(
                f"{case_id}.context has duplicate expected item {expected_id}"
            )
        expected.add(expected_id)

    retrieved_values = _sequence(
        context.get("retrievedItems"), f"{case_id}.context.retrievedItems"
    )
    retrieved: dict[str, Mapping[str, Any]] = {}
    for raw in retrieved_values:
        item = require_mapping(
            raw, f"{case_id}.context.retrievedItems[]", EvaluationInputError
        )
        item_id = require_string(item.get("itemId"), "itemId", EvaluationInputError)
        if item_id in retrieved:
            raise EvaluationInputError(
                f"{case_id}.context has duplicate itemId {item_id}"
            )
        matched = item.get("matchedExpectedItemId")
        if matched is not None and (not isinstance(matched, str) or matched not in expected):
            raise EvaluationInputError(
                f"{case_id}.context.{item_id}.matchedExpectedItemId must reference an expected item or be null"
            )
        for field in ("snapshotVerified", "digestVerified"):
            if not isinstance(item.get(field), bool):
                raise EvaluationInputError(
                    f"{case_id}.context.{item_id}.{field} must be boolean"
                )
        require_string(
            item.get("relationshipType"),
            f"{case_id}.context.{item_id}.relationshipType",
            EvaluationInputError,
        )
        require_string(
            item.get("retrievalMethod"),
            f"{case_id}.context.{item_id}.retrievalMethod",
            EvaluationInputError,
        )
        retrieved[item_id] = item

    duplicate_count = 0
    matched_expected: set[str] = set()
    true_relevant = 0
    false_relevant = 0
    snapshot_unverified = 0
    digest_invalid = 0
    provenance_valid = 0
    for item_id in sorted(retrieved):
        item = retrieved[item_id]
        matched = item.get("matchedExpectedItemId")
        snapshot_verified = item["snapshotVerified"]
        digest_verified = item["digestVerified"]
        if not snapshot_verified:
            snapshot_unverified += 1
        if not digest_verified:
            digest_invalid += 1
        if snapshot_verified and digest_verified:
            provenance_valid += 1

        duplicate_of = item.get("duplicateOf")
        if duplicate_of is not None:
            if not isinstance(duplicate_of, str) or duplicate_of == item_id:
                raise EvaluationInputError(
                    f"{case_id}.context.{item_id}.duplicateOf must reference another retrieved item"
                )
            original = retrieved.get(duplicate_of)
            if original is None:
                raise EvaluationInputError(
                    f"{case_id}.context.{item_id}.duplicateOf references missing item {duplicate_of}"
                )
            if original.get("duplicateOf") is not None:
                raise EvaluationInputError(
                    f"{case_id}.context.{item_id}.duplicateOf cannot form a duplicate chain"
                )
            if original.get("matchedExpectedItemId") != matched:
                raise EvaluationInputError(
                    f"{case_id}.context.{item_id}.duplicateOf must have the same matchedExpectedItemId"
                )
            duplicate_count += 1
            continue

        if (
            matched is not None
            and matched not in matched_expected
            and snapshot_verified
            and digest_verified
        ):
            matched_expected.add(matched)
            true_relevant += 1
        else:
            false_relevant += 1

    gap_values = _sequence(context.get("gapCodes"), f"{case_id}.context.gapCodes")
    gap_codes = []
    for raw in gap_values:
        code = require_string(raw, f"{case_id}.context.gapCodes[]", EvaluationInputError)
        if re.fullmatch(r"[a-z0-9_]{1,64}", code) is None:
            raise EvaluationInputError(
                f"{case_id}.context gap code must be lowercase snake_case"
            )
        if code in gap_codes:
            raise EvaluationInputError(f"{case_id}.context has duplicate gap code {code}")
        gap_codes.append(code)

    base_index_available = context.get("exactBaseIndexAvailable")
    if not isinstance(base_index_available, bool):
        raise EvaluationInputError(
            f"{case_id}.context.exactBaseIndexAvailable must be boolean"
        )
    missed = len(expected - matched_expected)
    non_duplicate_retrieved = len(retrieved) - duplicate_count
    counts = {
        "digestInvalid": digest_invalid,
        "duplicates": duplicate_count,
        "expected": len(expected),
        "falseRelevant": false_relevant,
        "missed": missed,
        "retrieved": len(retrieved),
        "snapshotUnverified": snapshot_unverified,
        "trueRelevant": true_relevant,
    }
    return {
        "counts": counts,
        "exactBaseIndexAvailable": base_index_available,
        "gapCodes": sorted(gap_codes),
        "precision": _ratio(true_relevant, non_duplicate_retrieved),
        "provenanceIntegrity": _ratio(provenance_valid, len(retrieved)),
        "recall": _ratio(true_relevant, true_relevant + missed),
    }


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
    high_severity_true_positives = 0
    high_severity_false_positives = 0
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

        is_true_positive = (
            supported and matched is not None and matched not in matched_labels
        )
        if is_true_positive:
            true_positives += 1
            matched_labels.add(matched)
            severity_pairs.append((labels[matched], predicted_severity))
        else:
            false_positives += 1
        if predicted_severity in ("high", "critical"):
            if is_true_positive:
                high_severity_true_positives += 1
            else:
                high_severity_false_positives += 1

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
        "contextQuality": _score_context(case_id, case.get("context")),
        "duplicateRate": _ratio(duplicates, len(prediction_by_id)),
        "f1": _f1(true_positives, false_positives, false_negatives),
        "highSeverityPrecision": _ratio(
            high_severity_true_positives,
            high_severity_true_positives + high_severity_false_positives,
        ),
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
    optional_fields = {"reviewApproach"}
    missing = sorted(expected_fields - set(provenance))
    extra = sorted(set(provenance) - expected_fields - optional_fields)
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
    if (
        "reviewApproach" in provenance
        and provenance["reviewApproach"] not in ("CLASSIC", "AGENTIC")
    ):
        raise EvaluationInputError(
            "provenance.reviewApproach must be CLASSIC or AGENTIC"
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
    high_severity_true_positives = sum(
        int(case["highSeverityPrecision"]["numerator"]) for case in per_pr
    )
    high_severity_published = sum(
        int(case["highSeverityPrecision"]["denominator"]) for case in per_pr
    )
    context_cases = [case["contextQuality"] for case in per_pr if case["contextQuality"] is not None]
    context_count_names = (
        "digestInvalid",
        "duplicates",
        "expected",
        "falseRelevant",
        "missed",
        "retrieved",
        "snapshotUnverified",
        "trueRelevant",
    )
    context_totals = {
        name: sum(int(context["counts"][name]) for context in context_cases)
        for name in context_count_names
    }
    context_non_duplicates = (
        context_totals["retrieved"] - context_totals["duplicates"]
    )
    context_provenance_valid = sum(
        int(context["provenanceIntegrity"]["numerator"])
        for context in context_cases
    )
    context_gap_counts = Counter(
        code for context in context_cases for code in context["gapCodes"]
    )
    base_index_available = sum(
        1 for context in context_cases if context["exactBaseIndexAvailable"]
    )

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
        "contextQuality": {
            "baseIndexAvailability": _ratio(
                base_index_available,
                len(context_cases),
            ),
            "counts": context_totals,
            "gapCodes": dict(sorted(context_gap_counts.items())),
            "measuredCases": len(context_cases),
            "precision": _wilson(
                context_totals["trueRelevant"],
                context_non_duplicates,
                z=policy["confidenceInterval"]["z"],
            ),
            "provenanceIntegrity": _ratio(
                context_provenance_valid,
                context_totals["retrieved"],
            ),
            "recall": _wilson(
                context_totals["trueRelevant"],
                context_totals["trueRelevant"] + context_totals["missed"],
                z=policy["confidenceInterval"]["z"],
            ),
            "totalCases": len(per_pr),
        },
        "counts": aggregate_counts,
        "coverageHonesty": {
            "ratio": None if coverage_total == 0 else coverage_represented / coverage_total,
            "represented": coverage_represented,
            "total": coverage_total,
        },
        "duplicateRate": _ratio(totals["duplicates"], totals["publishedFindings"]),
        "f1": _f1(
            totals["truePositives"],
            totals["falsePositives"],
            totals["falseNegatives"],
        ),
        "highSeverityPrecision": _wilson(
            high_severity_true_positives,
            high_severity_published,
            z=policy["confidenceInterval"]["z"],
        ),
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
