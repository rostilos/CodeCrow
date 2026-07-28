"""Fail-closed acceptance gate for paired review-quality captures.

The capture evaluator proves that each pair is the same immutable review input.
This module adds the finite corpus, quality, coverage, plugin, and provider-cost
requirements used by the general review-quality milestone. It never calls a
model or a connected repository.
"""

from __future__ import annotations

import argparse
import json
import re
from decimal import Decimal, InvalidOperation
from math import ceil
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence

from .capture_pair_evaluation import (
    _digest,
    _provider_response_digests,
    _sha256_file,
    evaluate_capture_manifest,
)


DEFAULT_REQUIRED_STANDALONE_LANGUAGES = ("java", "python", "typescript")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_VERIFIED_COST_SOURCES = frozenset({"provider-billing", "provider-response"})


def _check(
    passed: bool,
    *,
    actual: Any,
    required: Any,
) -> dict[str, Any]:
    return {
        "passed": bool(passed),
        "actual": actual,
        "required": required,
    }


def _mode_by_name(items: Any, field: str) -> dict[str, Mapping[str, Any]]:
    if not isinstance(items, list):
        raise ValueError(f"{field} must be a list")
    result: dict[str, Mapping[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"{field} entries must be objects")
        mode = item.get("mode")
        if not isinstance(mode, str) or not mode:
            raise ValueError(f"{field} entries require a mode")
        if mode in result:
            raise ValueError(f"{field} contains duplicate mode {mode!r}")
        result[mode] = item
    return result


def _case_metrics_by_mode(
    items: Any,
) -> dict[str, dict[str, Mapping[str, Any]]]:
    if not isinstance(items, list) or not items:
        raise ValueError("paired evaluation caseMetrics must be a non-empty list")
    result: dict[str, dict[str, Mapping[str, Any]]] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("caseMetrics entries must be objects")
        case_id = item.get("caseId")
        mode = item.get("mode")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("caseMetrics entries require a caseId")
        if not isinstance(mode, str) or not mode:
            raise ValueError("caseMetrics entries require a mode")
        by_case = result.setdefault(mode, {})
        if case_id in by_case:
            raise ValueError(
                f"caseMetrics contains duplicate {case_id}/{mode}"
            )
        for field in (
            "truePositives",
            "falsePositives",
            "falseNegatives",
            "reviewableHunks",
            "terminalHunks",
            "changedLines",
            "modelCalls",
            "inputTokens",
            "outputTokens",
        ):
            value = item.get(field)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
            ):
                raise ValueError(
                    f"caseMetrics {case_id}/{mode} has invalid {field}"
                )
        if item["terminalHunks"] > item["reviewableHunks"]:
            raise ValueError(
                f"caseMetrics {case_id}/{mode} has invalid hunk coverage"
            )
        for field in ("precision", "recall"):
            value = item.get(field)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not 0 <= value <= 1
            ):
                raise ValueError(
                    f"caseMetrics {case_id}/{mode} has invalid {field}"
                )
        if _decimal(item.get("cost")) is None:
            raise ValueError(
                f"caseMetrics {case_id}/{mode} has invalid cost"
            )
        if _decimal(item.get("costPerChangedKloc")) is None:
            raise ValueError(
                f"caseMetrics {case_id}/{mode} has invalid normalized cost"
            )
        for field in ("repositoryPlugins", "languages", "frameworks"):
            values = item.get(field)
            if (
                not isinstance(values, list)
                or any(not isinstance(value, str) or not value for value in values)
                or len(values) != len(set(values))
            ):
                raise ValueError(
                    f"caseMetrics {case_id}/{mode} has invalid {field}"
                )
        by_case[case_id] = item
    return result


def _within_multiplier(
    candidate: int | float,
    baseline: int | float,
    multiplier: float,
) -> bool:
    if baseline == 0:
        return candidate == 0
    return candidate <= baseline * multiplier


def _p95(values: Sequence[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[max(0, ceil(len(ordered) * 0.95) - 1)]


def _metric_matches(actual: Any, expected: int | float) -> bool:
    return (
        isinstance(actual, (int, float))
        and not isinstance(actual, bool)
        and round(float(actual), 8) == round(float(expected), 8)
    )


def _quality_summary(
    items: Sequence[Mapping[str, Any]],
) -> dict[str, int | float]:
    true_positives = sum(int(item["truePositives"]) for item in items)
    false_positives = sum(int(item["falsePositives"]) for item in items)
    false_negatives = sum(int(item["falseNegatives"]) for item in items)
    return {
        "cases": len(items),
        "expectedDefects": true_positives + false_negatives,
        "truePositives": true_positives,
        "falsePositives": false_positives,
        "falseNegatives": false_negatives,
        "precision": (
            true_positives / (true_positives + false_positives)
            if true_positives + false_positives
            else 0.0
        ),
        "recall": (
            true_positives / (true_positives + false_negatives)
            if true_positives + false_negatives
            else 0.0
        ),
    }


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    if not result.is_finite() or result < 0:
        return None
    return result


def _json_pointer(value: Any, pointer: str) -> Any:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("cost JSON pointer must start with '/'")
    current = value
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                raise ValueError("cost JSON pointer does not exist")
            current = current[token]
        elif isinstance(current, list):
            if not token.isdigit():
                raise ValueError("cost JSON pointer list token is invalid")
            index = int(token)
            if index >= len(current):
                raise ValueError("cost JSON pointer list index is out of range")
            current = current[index]
        else:
            raise ValueError("cost JSON pointer crosses a scalar value")
    return current


def _capture_provider_responses(
    capture: Mapping[str, Any],
) -> list[tuple[str, Any]]:
    digests = _provider_response_digests(capture)
    responses: list[Any] = []
    calls = capture.get("calls")
    if not isinstance(calls, list):
        return []
    for call in calls:
        if not isinstance(call, dict):
            continue
        events = call.get("providerEvents")
        if not isinstance(events, list):
            continue
        for event in events:
            if (
                isinstance(event, dict)
                and event.get("status") == "completed"
                and "response" in event
            ):
                responses.append(event.get("response"))
    return list(zip(digests, responses, strict=True))


def _resolve_path(value: str, base_dir: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base_dir / path


def _validate_billing_evidence(
    evidence: Mapping[str, Any],
    *,
    mode_cost: Decimal,
    base_dir: Path,
) -> tuple[bool, str]:
    artifact = evidence.get("artifact")
    if not isinstance(artifact, str) or not artifact.strip():
        return False, "billing-artifact-missing"
    artifact_path = _resolve_path(artifact, base_dir)
    if not artifact_path.is_file():
        return False, "billing-artifact-missing"
    try:
        artifact_digest = _sha256_file(artifact_path)
    except ValueError:
        return False, "billing-artifact-unreadable"
    if artifact_digest != evidence.get("sourceDigest"):
        return False, "billing-artifact-digest-mismatch"
    if _decimal(evidence.get("costUsd")) != mode_cost:
        return False, "billing-cost-mismatch"
    return True, "valid"


def _validate_provider_response_evidence(
    evidence: Mapping[str, Any],
    *,
    mode_cost: Decimal,
    capture: Mapping[str, Any],
) -> tuple[bool, str, int]:
    captured = _capture_provider_responses(capture)
    declared = evidence.get("responseCosts")
    if not captured or not isinstance(declared, list) or len(declared) != len(captured):
        return False, "provider-response-coverage-mismatch", 0

    total = Decimal("0")
    for index, (entry, (actual_digest, response)) in enumerate(
        zip(declared, captured, strict=True),
        start=1,
    ):
        if not isinstance(entry, dict):
            return False, f"provider-response-{index}-invalid", 0
        if entry.get("responseDigest") != actual_digest:
            return False, f"provider-response-{index}-digest-mismatch", 0
        pointer = entry.get("jsonPointer")
        try:
            reported_cost = _decimal(_json_pointer(response, pointer))
        except ValueError:
            return False, f"provider-response-{index}-cost-pointer-invalid", 0
        declared_cost = _decimal(entry.get("costUsd"))
        if reported_cost is None or declared_cost != reported_cost:
            return False, f"provider-response-{index}-cost-mismatch", 0
        total += reported_cost

    if total != mode_cost:
        return False, "provider-response-total-cost-mismatch", 0
    if evidence.get("sourceDigest") != _digest(declared):
        return False, "provider-response-source-digest-mismatch", 0
    if _decimal(evidence.get("costUsd")) != mode_cost:
        return False, "provider-response-declared-cost-mismatch", 0
    return True, "valid", len(captured)


def _cost_evidence_summary(
    manifest: Mapping[str, Any],
    *,
    base_dir: Path,
) -> dict[str, Any]:
    raw_cases = manifest.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError("manifest cases must be a list")

    checked = 0
    invalid: list[str] = []
    currencies: set[str] = set()
    sources: set[str] = set()
    bound_billing_artifacts = 0
    bound_provider_responses = 0
    declared_costs: dict[str, float] = {}
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            raise ValueError("manifest cases must contain objects")
        case_id = raw_case.get("caseId")
        modes = raw_case.get("modes")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("manifest caseId must be a non-empty string")
        if not isinstance(modes, list):
            raise ValueError(f"case {case_id}: modes must be a list")
        for raw_mode in modes:
            if not isinstance(raw_mode, dict):
                raise ValueError(f"case {case_id}: modes must contain objects")
            mode = raw_mode.get("mode")
            identity = f"{case_id}/{mode}"
            evidence = raw_mode.get("costEvidence")
            mode_cost = _decimal(raw_mode.get("cost"))
            if mode_cost is not None:
                declared_costs[identity] = float(mode_cost)
            checked += 1
            if not isinstance(evidence, dict):
                invalid.append(f"{identity}:missing")
                continue
            currency = evidence.get("currency")
            source = evidence.get("source")
            currencies.add(str(currency))
            sources.add(str(source))
            metadata_valid = (
                evidence.get("status") == "verified"
                and currency == "USD"
                and source in _VERIFIED_COST_SOURCES
                and mode_cost is not None
                and all(
                    isinstance(evidence.get(field), str)
                    and bool(evidence[field].strip())
                    for field in ("verifiedBy", "verifiedAt")
                )
                and isinstance(evidence.get("sourceDigest"), str)
                and _SHA256.fullmatch(evidence["sourceDigest"]) is not None
            )
            if not metadata_valid:
                invalid.append(f"{identity}:invalid")
                continue
            if source == "provider-billing":
                valid, reason = _validate_billing_evidence(
                    evidence,
                    mode_cost=mode_cost,
                    base_dir=base_dir,
                )
                if valid:
                    bound_billing_artifacts += 1
                else:
                    invalid.append(f"{identity}:{reason}")
                continue

            capture_value = raw_mode.get("capture")
            if not isinstance(capture_value, str) or not capture_value:
                invalid.append(f"{identity}:capture-missing")
                continue
            capture_path = _resolve_path(capture_value, base_dir)
            try:
                capture = json.loads(capture_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                invalid.append(f"{identity}:capture-unreadable")
                continue
            if not isinstance(capture, dict):
                invalid.append(f"{identity}:capture-invalid")
                continue
            valid, reason, response_count = _validate_provider_response_evidence(
                evidence,
                mode_cost=mode_cost,
                capture=capture,
            )
            if valid:
                bound_provider_responses += response_count
            else:
                invalid.append(f"{identity}:{reason}")
    return {
        "checked": checked,
        "invalid": sorted(invalid),
        "currencies": sorted(currencies),
        "sources": sorted(sources),
        "boundBillingArtifacts": bound_billing_artifacts,
        "boundProviderResponses": bound_provider_responses,
        "declaredCosts": declared_costs,
    }


def evaluate_acceptance_gate(
    *,
    paired_evaluation: Mapping[str, Any],
    manifest: Mapping[str, Any],
    candidate: str,
    minimum_cases: int = 4,
    required_standalone_languages: Sequence[str] = (
        DEFAULT_REQUIRED_STANDALONE_LANGUAGES
    ),
    minimum_precision: float = 0.60,
    minimum_recall: float = 0.60,
    minimum_precision_delta: float = 0.05,
    minimum_standalone_precision_delta: float = -0.05,
    minimum_recall_delta: float = -0.05,
    maximum_input_token_multiplier: float = 1.10,
    maximum_per_case_cost_multiplier: float = 1.10,
    maximum_p95_cost_multiplier: float = 1.10,
    evidence_base_dir: Path,
) -> dict[str, Any]:
    """Apply the milestone acceptance policy to an integrity-checked pair."""

    if paired_evaluation.get("kind") != "review-quality-paired-capture-report":
        raise ValueError("unsupported paired evaluation kind")
    report = paired_evaluation.get("report")
    if not isinstance(report, dict):
        raise ValueError("paired evaluation report is missing")
    baseline = report.get("baseline")
    if not isinstance(baseline, str) or not baseline:
        raise ValueError("paired evaluation baseline is missing")
    if not isinstance(candidate, str) or not candidate:
        raise ValueError("candidate must be a non-empty string")
    if minimum_cases < 1:
        raise ValueError("minimum_cases must be positive")
    if not 0 <= minimum_precision <= 1:
        raise ValueError("minimum_precision must be between zero and one")
    if not 0 <= minimum_recall <= 1:
        raise ValueError("minimum_recall must be between zero and one")
    if not -1 <= minimum_precision_delta <= 1:
        raise ValueError("minimum_precision_delta must be between -1 and one")
    if not -1 <= minimum_standalone_precision_delta <= 1:
        raise ValueError(
            "minimum_standalone_precision_delta must be between -1 and one"
        )
    if not -1 <= minimum_recall_delta <= 1:
        raise ValueError("minimum_recall_delta must be between -1 and one")
    if maximum_input_token_multiplier < 0:
        raise ValueError("maximum_input_token_multiplier must be non-negative")
    if maximum_per_case_cost_multiplier < 0:
        raise ValueError("maximum_per_case_cost_multiplier must be non-negative")
    if maximum_p95_cost_multiplier < 0:
        raise ValueError("maximum_p95_cost_multiplier must be non-negative")
    required_languages = tuple(sorted(set(required_standalone_languages)))
    if not required_languages or any(not item for item in required_languages):
        raise ValueError("required standalone languages must be non-empty")

    modes = _mode_by_name(report.get("modes"), "report modes")
    deltas = _mode_by_name(report.get("pairedDeltas"), "paired deltas")
    case_metrics = _case_metrics_by_mode(
        paired_evaluation.get("caseMetrics")
    )
    baseline_metrics = modes.get(baseline)
    candidate_metrics = modes.get(candidate)
    candidate_delta = deltas.get(candidate)

    coverage = report.get("corpusCoverage")
    if not isinstance(coverage, dict):
        raise ValueError("corpus coverage is missing")
    language_strata = report.get("languageStrata")
    if not isinstance(language_strata, dict):
        raise ValueError("language strata are missing")

    provenance = paired_evaluation.get("provenance")
    if not isinstance(provenance, list):
        raise ValueError("paired evaluation provenance is missing")
    plugin_selections: dict[str, list[list[str]]] = {}
    provenance_plugins: dict[tuple[str, str], list[str]] = {}
    for item in provenance:
        if not isinstance(item, dict):
            raise ValueError("paired evaluation provenance entries must be objects")
        mode = item.get("mode")
        case_id = item.get("caseId")
        plugins = item.get("repositoryPlugins")
        if (
            not isinstance(case_id, str)
            or not case_id
            or not isinstance(mode, str)
            or not isinstance(plugins, list)
        ):
            raise ValueError(
                "paired evaluation provenance lacks repository plugin identity"
            )
        if any(not isinstance(plugin, str) or not plugin for plugin in plugins):
            raise ValueError("repository plugin identities must be non-empty strings")
        identity = (case_id, mode)
        if identity in provenance_plugins:
            raise ValueError("paired evaluation provenance identity is duplicated")
        provenance_plugins[identity] = list(plugins)
        plugin_selections.setdefault(mode, []).append(list(plugins))

    cost_evidence = _cost_evidence_summary(
        manifest,
        base_dir=evidence_base_dir,
    )
    candidate_exists = candidate_metrics is not None and candidate_delta is not None
    baseline_exists = baseline_metrics is not None

    baseline_coverage = (
        baseline_metrics.get("coverage") if baseline_metrics is not None else None
    )
    candidate_coverage = (
        candidate_metrics.get("coverage") if candidate_metrics is not None else None
    )
    candidate_precision = (
        candidate_metrics.get("precision") if candidate_metrics is not None else None
    )
    candidate_recall = (
        candidate_metrics.get("recall") if candidate_metrics is not None else None
    )
    precision_delta = (
        candidate_precision - baseline_metrics.get("precision")
        if (
            isinstance(candidate_precision, (int, float))
            and baseline_metrics is not None
            and isinstance(baseline_metrics.get("precision"), (int, float))
        )
        else None
    )
    recall_delta = (
        candidate_recall - baseline_metrics.get("recall")
        if (
            isinstance(candidate_recall, (int, float))
            and baseline_metrics is not None
            and isinstance(baseline_metrics.get("recall"), (int, float))
        )
        else None
    )
    model_call_delta = (
        candidate_metrics.get("model_calls") - baseline_metrics.get("model_calls")
        if (
            candidate_metrics is not None
            and baseline_metrics is not None
            and isinstance(candidate_metrics.get("model_calls"), int)
            and isinstance(baseline_metrics.get("model_calls"), int)
        )
        else None
    )
    input_token_delta = (
        candidate_metrics.get("input_tokens") - baseline_metrics.get("input_tokens")
        if (
            candidate_metrics is not None
            and baseline_metrics is not None
            and isinstance(candidate_metrics.get("input_tokens"), int)
            and isinstance(baseline_metrics.get("input_tokens"), int)
        )
        else None
    )
    median_cost_delta = (
        candidate_metrics.get("median_cost_per_changed_kloc")
        - baseline_metrics.get("median_cost_per_changed_kloc")
        if (
            candidate_metrics is not None
            and baseline_metrics is not None
            and isinstance(
                candidate_metrics.get("median_cost_per_changed_kloc"),
                (int, float),
            )
            and isinstance(
                baseline_metrics.get("median_cost_per_changed_kloc"),
                (int, float),
            )
        )
        else None
    )
    baseline_p95 = (
        baseline_metrics.get("p95_cost_per_changed_kloc")
        if baseline_metrics is not None
        else None
    )
    candidate_p95 = (
        candidate_metrics.get("p95_cost_per_changed_kloc")
        if candidate_metrics is not None
        else None
    )
    maximum_candidate_p95 = (
        float(baseline_p95) * maximum_p95_cost_multiplier
        if isinstance(baseline_p95, (int, float))
        else None
    )

    baseline_plugins = plugin_selections.get(baseline, [])
    candidate_plugins = plugin_selections.get(candidate, [])
    baseline_case_metrics = case_metrics.get(baseline, {})
    candidate_case_metrics = case_metrics.get(candidate, {})
    paired_case_ids = (
        set(baseline_case_metrics) == set(candidate_case_metrics)
        and bool(baseline_case_metrics)
    )
    common_case_ids = sorted(
        set(baseline_case_metrics) & set(candidate_case_metrics)
    )
    per_case_call_growth = {
        case_id: (
            candidate_case_metrics[case_id]["modelCalls"]
            - baseline_case_metrics[case_id]["modelCalls"]
        )
        for case_id in common_case_ids
    }
    per_case_input_growth = {
        case_id: {
            "baseline": baseline_case_metrics[case_id]["inputTokens"],
            "candidate": candidate_case_metrics[case_id]["inputTokens"],
        }
        for case_id in common_case_ids
    }
    per_case_cost_growth = {
        case_id: {
            "baseline": baseline_case_metrics[case_id][
                "costPerChangedKloc"
            ],
            "candidate": candidate_case_metrics[case_id][
                "costPerChangedKloc"
            ],
        }
        for case_id in common_case_ids
    }
    missing_language_plugins = {
        case_id: sorted(
            (
                set(candidate_case_metrics[case_id]["languages"])
                & set(required_languages)
            )
            - set(candidate_case_metrics[case_id]["repositoryPlugins"])
        )
        for case_id in common_case_ids
    }
    missing_language_plugins = {
        case_id: missing
        for case_id, missing in missing_language_plugins.items()
        if missing
    }
    provenance_identity_matches = (
        set(provenance_plugins)
        == {
            (case_id, mode)
            for mode, by_case in case_metrics.items()
            for case_id in by_case
        }
        and all(
            provenance_plugins[(case_id, mode)]
            == list(item["repositoryPlugins"])
            for mode, by_case in case_metrics.items()
            for case_id, item in by_case.items()
        )
    )
    case_metric_costs_match_manifest = all(
        round(float(item["cost"]), 8)
        == round(
            cost_evidence["declaredCosts"].get(
                f"{case_id}/{mode}",
                -1,
            ),
            8,
        )
        for mode, by_case in case_metrics.items()
        for case_id, item in by_case.items()
    )
    case_metric_normalization_valid = all(
        round(float(item["costPerChangedKloc"]), 8)
        == (
            round(
                (float(item["cost"]) * 1000) / item["changedLines"],
                8,
            )
            if item["changedLines"]
            else 0.0
        )
        for by_case in case_metrics.values()
        for item in by_case.values()
    )
    case_metric_quality_valid = all(
        _metric_matches(
            item["precision"],
            (
                item["truePositives"]
                / (item["truePositives"] + item["falsePositives"])
                if item["truePositives"] + item["falsePositives"]
                else 0.0
            ),
        )
        for by_case in case_metrics.values()
        for item in by_case.values()
    ) and all(
        _metric_matches(
            item["recall"],
            (
                item["truePositives"]
                / (item["truePositives"] + item["falseNegatives"])
                if item["truePositives"] + item["falseNegatives"]
                else 0.0
            ),
        )
        for by_case in case_metrics.values()
        for item in by_case.values()
    )

    aggregate_integrity: dict[str, bool] = {}
    expected_aggregates: dict[str, dict[str, int | float]] = {}
    for mode in (baseline, candidate):
        aggregate = modes.get(mode)
        by_case = case_metrics.get(mode, {})
        true_positives = sum(
            item["truePositives"] for item in by_case.values()
        )
        false_positives = sum(
            item["falsePositives"] for item in by_case.values()
        )
        false_negatives = sum(
            item["falseNegatives"] for item in by_case.values()
        )
        reviewable_hunks = sum(
            item["reviewableHunks"] for item in by_case.values()
        )
        terminal_hunks = sum(
            item["terminalHunks"] for item in by_case.values()
        )
        changed_lines = sum(
            item["changedLines"] for item in by_case.values()
        )
        cost = round(
            sum(float(item["cost"]) for item in by_case.values()),
            8,
        )
        case_costs = [
            float(item["costPerChangedKloc"])
            for item in by_case.values()
            if item["changedLines"]
        ]
        expected = {
            "cases": len(by_case),
            "true_positives": true_positives,
            "false_positives": false_positives,
            "false_negatives": false_negatives,
            "precision": (
                true_positives / (true_positives + false_positives)
                if true_positives + false_positives
                else 0.0
            ),
            "recall": (
                true_positives / (true_positives + false_negatives)
                if true_positives + false_negatives
                else 0.0
            ),
            "coverage": (
                terminal_hunks / reviewable_hunks
                if reviewable_hunks
                else 0.0
            ),
            "changed_lines": changed_lines,
            "model_calls": sum(
                item["modelCalls"] for item in by_case.values()
            ),
            "input_tokens": sum(
                item["inputTokens"] for item in by_case.values()
            ),
            "output_tokens": sum(
                item["outputTokens"] for item in by_case.values()
            ),
            "cost": cost,
            "cost_per_changed_kloc": (
                round((cost * 1000) / changed_lines, 8)
                if changed_lines
                else 0.0
            ),
            "median_cost_per_changed_kloc": round(
                median(case_costs) if case_costs else 0.0,
                8,
            ),
            "p95_cost_per_changed_kloc": round(_p95(case_costs), 8),
        }
        expected_aggregates[mode] = expected
        aggregate_integrity[mode] = bool(aggregate) and all(
            _metric_matches(aggregate.get(field), value)
            for field, value in expected.items()
        )

    expected_delta = {
        field: round(
            float(expected_aggregates[candidate][field])
            - float(expected_aggregates[baseline][field]),
            8,
        )
        for field in (
            "precision",
            "recall",
            "model_calls",
            "input_tokens",
            "output_tokens",
            "cost",
            "cost_per_changed_kloc",
            "median_cost_per_changed_kloc",
            "p95_cost_per_changed_kloc",
        )
    }
    delta_integrity = (
        candidate_delta is not None
        and candidate_delta.get("baseline") == baseline
        and all(
            _metric_matches(candidate_delta.get(field), value)
            for field, value in expected_delta.items()
        )
    )
    paired_case_identity_valid = paired_case_ids and all(
        all(
            baseline_case_metrics[case_id][field]
            == candidate_case_metrics[case_id][field]
            for field in (
                "languages",
                "frameworks",
                "changedLines",
                "reviewableHunks",
            )
        )
        and (
            baseline_case_metrics[case_id]["truePositives"]
            + baseline_case_metrics[case_id]["falseNegatives"]
        )
        == (
            candidate_case_metrics[case_id]["truePositives"]
            + candidate_case_metrics[case_id]["falseNegatives"]
        )
        for case_id in common_case_ids
    )

    derived_language_strata: dict[
        str,
        dict[str, dict[str, int | float]],
    ] = {}
    for mode, by_case in case_metrics.items():
        grouped: dict[str, list[Mapping[str, Any]]] = {}
        for item in by_case.values():
            profile = "+".join(sorted(item["languages"]))
            grouped.setdefault(profile, []).append(item)
        for profile, items in grouped.items():
            derived_language_strata.setdefault(profile, {})[mode] = (
                _quality_summary(items)
            )

    language_strata_integrity = (
        set(language_strata) == set(derived_language_strata)
    )
    if language_strata_integrity:
        for profile, expected_modes in derived_language_strata.items():
            try:
                reported_modes = _mode_by_name(
                    language_strata[profile],
                    f"language stratum {profile}",
                )
            except ValueError:
                language_strata_integrity = False
                break
            if set(reported_modes) != set(expected_modes):
                language_strata_integrity = False
                break
            for mode, expected in expected_modes.items():
                reported = reported_modes[mode]
                report_field_map = {
                    "cases": "cases",
                    "truePositives": "true_positives",
                    "falsePositives": "false_positives",
                    "falseNegatives": "false_negatives",
                    "precision": "precision",
                    "recall": "recall",
                }
                if not all(
                    _metric_matches(reported.get(report_field), expected[field])
                    for field, report_field in report_field_map.items()
                ):
                    language_strata_integrity = False
                    break
            if not language_strata_integrity:
                break

    expected_corpus_coverage = {
        "cases": len(baseline_case_metrics),
        "languages": sorted({
            language
            for item in baseline_case_metrics.values()
            for language in item["languages"]
        }),
        "frameworks": sorted({
            framework
            for item in baseline_case_metrics.values()
            for framework in item["frameworks"]
        }),
        "polyglotCases": sum(
            len(item["languages"]) > 1
            for item in baseline_case_metrics.values()
        ),
        "changedLines": sum(
            item["changedLines"]
            for item in baseline_case_metrics.values()
        ),
    }
    corpus_coverage_integrity = all(
        coverage.get(field) == value
        for field, value in expected_corpus_coverage.items()
    )

    standalone_quality: dict[str, dict[str, Any]] = {}
    for language in required_languages:
        baseline_stratum = derived_language_strata.get(language, {}).get(
            baseline
        )
        candidate_stratum = derived_language_strata.get(language, {}).get(
            candidate
        )
        if baseline_stratum is None or candidate_stratum is None:
            standalone_quality[language] = {"present": False}
            continue
        precision_change = (
            float(candidate_stratum["precision"])
            - float(baseline_stratum["precision"])
        )
        recall_change = (
            float(candidate_stratum["recall"])
            - float(baseline_stratum["recall"])
        )
        standalone_quality[language] = {
            "present": True,
            "expectedDefects": candidate_stratum["expectedDefects"],
            "precision": candidate_stratum["precision"],
            "recall": candidate_stratum["recall"],
            "precisionDelta": precision_change,
            "recallDelta": recall_change,
        }
    standalone_quality_valid = all(
        values.get("present") is True
        and values["expectedDefects"] >= 1
        and values["precision"] >= minimum_precision
        and values["recall"] >= minimum_recall
        and values["precisionDelta"] >= minimum_standalone_precision_delta
        and values["recallDelta"] >= minimum_recall_delta
        for values in standalone_quality.values()
    )
    checks = {
        "baseline-and-candidate-present": _check(
            baseline_exists and candidate_exists,
            actual=sorted(modes),
            required={"baseline": baseline, "candidate": candidate},
        ),
        "minimum-case-count": _check(
            len(baseline_case_metrics) >= minimum_cases,
            actual=len(baseline_case_metrics),
            required=f">={minimum_cases}",
        ),
        "standalone-language-profiles": _check(
            all(
                language in derived_language_strata
                for language in required_languages
            ),
            actual=sorted(derived_language_strata),
            required=list(required_languages),
        ),
        "polyglot-profile": _check(
            any("+" in profile for profile in derived_language_strata)
            and expected_corpus_coverage["polyglotCases"] >= 1,
            actual={
                "profiles": sorted(derived_language_strata),
                "polyglotCases": expected_corpus_coverage["polyglotCases"],
            },
            required="at least one exact multi-language profile",
        ),
        "fallback-plugin-selection-empty": _check(
            bool(baseline_plugins)
            and all(not plugins for plugins in baseline_plugins),
            actual=baseline_plugins,
            required="empty repository plugin selection for every baseline case",
        ),
        "candidate-plugin-selection-non-empty": _check(
            bool(candidate_plugins)
            and all(bool(plugins) for plugins in candidate_plugins),
            actual=candidate_plugins,
            required="non-empty repository plugin selection for every candidate case",
        ),
        "declared-language-plugin-selection": _check(
            paired_case_ids and not missing_language_plugins,
            actual=missing_language_plugins,
            required=(
                "every required declared language selects its matching "
                "candidate plugin"
            ),
        ),
        "case-metric-integrity": _check(
            paired_case_ids
            and provenance_identity_matches
            and case_metric_costs_match_manifest
            and case_metric_normalization_valid
            and case_metric_quality_valid
            and all(aggregate_integrity.values())
            and delta_integrity
            and paired_case_identity_valid
            and language_strata_integrity
            and corpus_coverage_integrity,
            actual={
                "pairedCaseIds": paired_case_ids,
                "provenanceIdentityMatches": provenance_identity_matches,
                "costsMatchManifest": case_metric_costs_match_manifest,
                "normalizedCostsValid": case_metric_normalization_valid,
                "qualityMetricsValid": case_metric_quality_valid,
                "aggregateIntegrity": aggregate_integrity,
                "deltaIntegrity": delta_integrity,
                "pairedCaseIdentityValid": paired_case_identity_valid,
                "languageStrataIntegrity": language_strata_integrity,
                "corpusCoverageIntegrity": corpus_coverage_integrity,
            },
            required="paired case metrics match provenance and mode aggregates",
        ),
        "complete-hunk-coverage": _check(
            baseline_coverage == 1.0 and candidate_coverage == 1.0,
            actual={
                baseline: baseline_coverage,
                candidate: candidate_coverage,
            },
            required=1.0,
        ),
        "general-precision": _check(
            isinstance(candidate_precision, (int, float))
            and candidate_precision >= minimum_precision,
            actual=candidate_precision,
            required=f">={minimum_precision}",
        ),
        "general-recall": _check(
            isinstance(candidate_recall, (int, float))
            and candidate_recall >= minimum_recall,
            actual=candidate_recall,
            required=f">={minimum_recall}",
        ),
        "standalone-language-quality": _check(
            standalone_quality_valid,
            actual=standalone_quality,
            required={
                "expectedDefects": ">=1 per standalone language",
                "precision": f">={minimum_precision}",
                "recall": f">={minimum_recall}",
                "precisionDelta": (
                    f">={minimum_standalone_precision_delta}"
                ),
                "recallDelta": f">={minimum_recall_delta}",
            },
        ),
        "paired-precision-improvement": _check(
            isinstance(precision_delta, (int, float))
            and precision_delta >= minimum_precision_delta,
            actual=precision_delta,
            required=f">={minimum_precision_delta}",
        ),
        "paired-recall": _check(
            isinstance(recall_delta, (int, float))
            and recall_delta >= minimum_recall_delta,
            actual=recall_delta,
            required=f">={minimum_recall_delta}",
        ),
        "model-call-growth": _check(
            isinstance(model_call_delta, int)
            and model_call_delta <= 0
            and paired_case_ids
            and all(delta <= 0 for delta in per_case_call_growth.values()),
            actual={
                "aggregate": model_call_delta,
                "perCase": per_case_call_growth,
            },
            required="<=0 for the aggregate and every paired case",
        ),
        "input-token-growth": _check(
            isinstance(input_token_delta, int)
            and paired_case_ids
            and all(
                _within_multiplier(
                    values["candidate"],
                    values["baseline"],
                    maximum_input_token_multiplier,
                )
                for values in per_case_input_growth.values()
            )
            and _within_multiplier(
                candidate_metrics.get("input_tokens", -1)
                if candidate_metrics is not None
                else -1,
                baseline_metrics.get("input_tokens", -1)
                if baseline_metrics is not None
                else -1,
                maximum_input_token_multiplier,
            ),
            actual={
                "aggregateDelta": input_token_delta,
                "perCase": per_case_input_growth,
            },
            required=f"<={maximum_input_token_multiplier}x baseline",
        ),
        "per-case-cost-growth": _check(
            paired_case_ids
            and all(
                _within_multiplier(
                    values["candidate"],
                    values["baseline"],
                    maximum_per_case_cost_multiplier,
                )
                for values in per_case_cost_growth.values()
            ),
            actual=per_case_cost_growth,
            required=f"<={maximum_per_case_cost_multiplier}x baseline per case",
        ),
        "median-cost-growth": _check(
            isinstance(median_cost_delta, (int, float))
            and median_cost_delta <= 0,
            actual=median_cost_delta,
            required="<=0 cost per changed KLOC",
        ),
        "p95-cost-growth": _check(
            isinstance(candidate_p95, (int, float))
            and maximum_candidate_p95 is not None
            and candidate_p95 <= maximum_candidate_p95,
            actual=candidate_p95,
            required=(
                f"<={maximum_p95_cost_multiplier}x baseline "
                f"({maximum_candidate_p95})"
            ),
        ),
        "verified-provider-cost-evidence": _check(
            cost_evidence["checked"] > 0 and not cost_evidence["invalid"],
            actual=cost_evidence,
            required={
                "status": "verified",
                "currency": "USD",
                "source": sorted(_VERIFIED_COST_SOURCES),
                "sourceDigest": (
                    "existing billing artifact SHA-256 or exact captured "
                    "provider-response cost projection SHA-256"
                ),
            },
        ),
    }
    failed = [name for name, value in checks.items() if not value["passed"]]
    return {
        "kind": "review-quality-paired-acceptance-gate",
        "status": "passed" if not failed else "failed",
        "baseline": baseline,
        "candidate": candidate,
        "policy": {
            "minimumCases": minimum_cases,
            "requiredStandaloneLanguages": list(required_languages),
            "minimumPrecision": minimum_precision,
            "minimumRecall": minimum_recall,
            "minimumPrecisionDelta": minimum_precision_delta,
            "minimumStandalonePrecisionDelta": (
                minimum_standalone_precision_delta
            ),
            "minimumRecallDelta": minimum_recall_delta,
            "maximumInputTokenMultiplier": maximum_input_token_multiplier,
            "maximumPerCaseCostMultiplier": maximum_per_case_cost_multiplier,
            "maximumP95CostMultiplier": maximum_p95_cost_multiplier,
        },
        "checks": checks,
        "failedChecks": failed,
        "pairedEvaluation": paired_evaluation,
    }


def _load_json(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exception:
        raise ValueError(f"cannot read JSON from {path}: {exception}") from exception
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate integrity-bound paired captures and enforce the finite "
            "general review-quality/cost milestone."
        )
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--minimum-cases", type=int, default=4)
    parser.add_argument(
        "--required-standalone-language",
        action="append",
        dest="required_languages",
        default=None,
    )
    args = parser.parse_args()

    manifest = _load_json(args.manifest)
    paired = evaluate_capture_manifest(
        manifest,
        base_dir=args.manifest.resolve().parent,
    )
    result = evaluate_acceptance_gate(
        paired_evaluation=paired,
        manifest=manifest,
        candidate=args.candidate,
        minimum_cases=args.minimum_cases,
        required_standalone_languages=(
            args.required_languages or DEFAULT_REQUIRED_STANDALONE_LANGUAGES
        ),
        evidence_base_dir=args.manifest.resolve().parent,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.write_text(rendered, encoding="utf-8")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
