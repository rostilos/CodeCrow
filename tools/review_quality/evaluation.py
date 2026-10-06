from __future__ import annotations

from dataclasses import asdict, dataclass
from math import ceil, isfinite
import hashlib
import json
from statistics import median
from typing import Any, Iterable, Mapping


def _string_set(value: Any, field: str) -> frozenset[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{field} must be a list of non-empty strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{field} must not contain duplicates")
    return frozenset(value)


def _non_negative_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _non_negative_number(value: Any, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(value) or value < 0:
        raise ValueError(f"{field} must be a non-negative number")
    return float(value)


def _optional_number(payload: Mapping[str, Any], field: str, *, integer: bool = False):
    value = payload.get(field)
    if value is None:
        return None
    return (_non_negative_int if integer else _non_negative_number)(value, field)


def _complete_sum(values: Iterable[int | float | None]):
    items = tuple(values)
    return None if any(item is None for item in items) else sum(items)


def _difference(value, baseline):
    return None if value is None or baseline is None else round(value - baseline, 8)


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    mode: str
    plugins: tuple[str, ...]
    languages: tuple[str, ...]
    frameworks: tuple[str, ...]
    expected: frozenset[str]
    published: frozenset[str]
    abstained: frozenset[str]
    reviewable_hunks: int
    terminal_hunks: int
    changed_lines: int
    model_calls: int | None
    input_tokens: int | None
    output_tokens: int | None
    cost: float | None
    completion_status: str
    wall_time_seconds: float | None
    provenance: Mapping[str, str]

    @classmethod
    def parse(cls, payload: Mapping[str, Any]) -> "CaseResult":
        if not isinstance(payload, Mapping):
            raise ValueError("each case must be an object")
        case_id = payload.get("caseId")
        mode = payload.get("mode")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("caseId must be a non-empty string")
        if not isinstance(mode, str) or not mode:
            raise ValueError("mode must be a non-empty string")
        plugins = tuple(sorted(_string_set(payload.get("plugins", []), "plugins")))
        languages = tuple(sorted(_string_set(payload.get("languages"), "languages")))
        if not languages:
            raise ValueError("languages must not be empty")
        frameworks = tuple(sorted(_string_set(payload.get("frameworks", []), "frameworks")))
        expected = _string_set(payload.get("expected", []), "expected")
        published = _string_set(payload.get("published", []), "published")
        abstained = _string_set(payload.get("abstained", []), "abstained")
        if published & abstained:
            raise ValueError(f"case {case_id}/{mode} publishes and abstains on the same finding")
        reviewable = _non_negative_int(payload.get("reviewableHunks", 0), "reviewableHunks")
        terminal = _non_negative_int(payload.get("terminalHunks", 0), "terminalHunks")
        if terminal > reviewable:
            raise ValueError(f"case {case_id}/{mode} has more terminal than reviewable hunks")
        changed_lines = _non_negative_int(
            payload.get("changedLines"),
            "changedLines",
        )
        model_calls = _optional_number(payload, "modelCalls", integer=True)
        input_tokens = _optional_number(payload, "inputTokens", integer=True)
        output_tokens = _optional_number(payload, "outputTokens", integer=True)
        cost = _optional_number(payload, "cost")
        completion_status = payload.get("completionStatus", "unknown")
        if completion_status not in {"complete", "partial", "failed", "missing", "unknown"}:
            raise ValueError("completionStatus must be complete, partial, failed, missing or unknown")
        provenance = payload.get("provenance", {})
        if not isinstance(provenance, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) or not v
            for k, v in provenance.items()
        ):
            raise ValueError("provenance must contain non-empty string values")
        if changed_lines == 0 and any(
            (model_calls, input_tokens, output_tokens, cost)
        ):
            raise ValueError(
                f"case {case_id}/{mode} has provider usage but no changed "
                "lines for KLOC normalization"
            )
        return cls(
            case_id=case_id,
            mode=mode,
            plugins=plugins,
            languages=languages,
            frameworks=frameworks,
            expected=expected,
            published=published,
            abstained=abstained,
            reviewable_hunks=reviewable,
            terminal_hunks=terminal,
            changed_lines=changed_lines,
            model_calls=model_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
            completion_status=completion_status,
            wall_time_seconds=_optional_number(payload, "wallTimeSeconds"),
            provenance=provenance,
        )


@dataclass(frozen=True)
class ModeMetrics:
    mode: str
    cases: int
    true_positives: int
    false_positives: int
    false_negatives: int
    abstentions: int
    precision: float
    recall: float
    f1: float
    f2: float
    coverage: float
    changed_lines: int
    model_calls: int | None
    input_tokens: int | None
    output_tokens: int | None
    cost: float | None
    cost_per_changed_kloc: float | None
    median_cost_per_changed_kloc: float | None
    p95_cost_per_changed_kloc: float | None
    completion_counts: Mapping[str, int]
    usage_known_cases: Mapping[str, int]
    wall_time_seconds: float | None
    median_wall_time_seconds: float | None
    p95_wall_time_seconds: float | None


@dataclass(frozen=True)
class PairedDelta:
    mode: str
    baseline: str
    precision: float
    recall: float
    f1: float
    f2: float
    model_calls: int | None
    input_tokens: int | None
    output_tokens: int | None
    cost: float | None
    cost_per_changed_kloc: float | None
    median_cost_per_changed_kloc: float | None
    p95_cost_per_changed_kloc: float | None
    wall_time_seconds: float | None


@dataclass(frozen=True)
class EvaluationReport:
    baseline: str
    modes: tuple[ModeMetrics, ...]
    paired_deltas: tuple[PairedDelta, ...]
    corpus_coverage: Mapping[str, Any]
    language_strata: Mapping[str, tuple[ModeMetrics, ...]]
    framework_strata: Mapping[str, tuple[ModeMetrics, ...]]
    evidence: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "baseline": self.baseline,
            "evidence": dict(self.evidence),
            "modes": [asdict(metrics) for metrics in self.modes],
            "pairedDeltas": [asdict(delta) for delta in self.paired_deltas],
            "corpusCoverage": dict(self.corpus_coverage),
            "languageStrata": {
                name: [asdict(metrics) for metrics in values]
                for name, values in self.language_strata.items()
            },
            "frameworkStrata": {
                name: [asdict(metrics) for metrics in values]
                for name, values in self.framework_strata.items()
            },
        }


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _cost_per_changed_kloc(cost: float | None, changed_lines: int) -> float | None:
    if cost is None:
        return None
    return round((cost * 1000) / changed_lines, 8) if changed_lines else 0.0


def _percentile_95(values: Iterable[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[max(0, ceil(len(ordered) * 0.95) - 1)]


def _aggregate(mode: str, cases: Iterable[CaseResult]) -> ModeMetrics:
    values = tuple(cases)
    true_positives = sum(len(case.expected & case.published) for case in values)
    false_positives = sum(len(case.published - case.expected) for case in values)
    false_negatives = sum(len(case.expected - case.published) for case in values)
    terminal = sum(case.terminal_hunks for case in values)
    reviewable = sum(case.reviewable_hunks for case in values)
    changed_lines = sum(case.changed_lines for case in values)
    cost = _complete_sum(case.cost for case in values)
    cost = round(cost, 8) if cost is not None else None
    case_costs_per_kloc = [
        _cost_per_changed_kloc(case.cost, case.changed_lines)
        for case in values
        if case.changed_lines and case.cost is not None
    ]
    times = [case.wall_time_seconds for case in values if case.wall_time_seconds is not None]
    cost_complete = all(case.cost is not None for case in values)
    time_complete = len(times) == len(values)
    return ModeMetrics(
        mode=mode,
        cases=len(values),
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        abstentions=sum(len(case.abstained) for case in values),
        precision=_ratio(true_positives, true_positives + false_positives),
        recall=_ratio(true_positives, true_positives + false_negatives),
        f1=_ratio(2 * true_positives, 2 * true_positives + false_positives + false_negatives),
        f2=_ratio(5 * true_positives, 5 * true_positives + false_positives + 4 * false_negatives),
        coverage=_ratio(terminal, reviewable),
        changed_lines=changed_lines,
        model_calls=_complete_sum(case.model_calls for case in values),
        input_tokens=_complete_sum(case.input_tokens for case in values),
        output_tokens=_complete_sum(case.output_tokens for case in values),
        cost=cost,
        cost_per_changed_kloc=_cost_per_changed_kloc(cost, changed_lines),
        median_cost_per_changed_kloc=round(
            median(case_costs_per_kloc) if case_costs_per_kloc else 0.0,
            8,
        ) if cost_complete else None,
        p95_cost_per_changed_kloc=round(
            _percentile_95(case_costs_per_kloc),
            8,
        ) if cost_complete else None,
        completion_counts={status: sum(case.completion_status == status for case in values)
                           for status in ("complete", "partial", "failed", "missing", "unknown")},
        usage_known_cases={name: sum(getattr(case, name) is not None for case in values)
                           for name in ("model_calls", "input_tokens", "output_tokens", "cost", "wall_time_seconds")},
        wall_time_seconds=sum(times) if time_complete else None,
        median_wall_time_seconds=median(times) if time_complete and times else None,
        p95_wall_time_seconds=_percentile_95(times) if time_complete and times else None,
    )


def _strata(
    cases: Iterable[CaseResult],
    *,
    attribute: str,
    empty_profile: str,
) -> dict[str, tuple[ModeMetrics, ...]]:
    all_cases = tuple(cases)
    profiles = {
        "+".join(getattr(case, attribute)) or empty_profile
        for case in all_cases
    }
    result: dict[str, tuple[ModeMetrics, ...]] = {}
    for profile in sorted(profiles):
        matching = [
            case
            for case in all_cases
            if ("+".join(getattr(case, attribute)) or empty_profile) == profile
        ]
        modes = sorted({case.mode for case in matching})
        result[profile] = tuple(
            _aggregate(
                mode,
                (case for case in matching if case.mode == mode),
            )
            for mode in modes
        )
    return result


def evaluate_dataset(payload: Mapping[str, Any]) -> EvaluationReport:
    baseline = payload.get("baseline", "fallback")
    if not isinstance(baseline, str) or not baseline:
        raise ValueError("baseline must be a non-empty string")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("cases must be a non-empty list")
    cases = tuple(CaseResult.parse(item) for item in raw_cases)
    identities = [(case.case_id, case.mode) for case in cases]
    if len(identities) != len(set(identities)):
        raise ValueError("each caseId/mode pair must be unique")

    grouped: dict[str, list[CaseResult]] = {}
    for case in cases:
        grouped.setdefault(case.mode, []).append(case)
    if baseline not in grouped:
        raise ValueError(f"baseline mode {baseline!r} is absent")

    baseline_by_case = {case.case_id: case for case in grouped[baseline]}
    for mode, mode_cases in grouped.items():
        if mode == baseline:
            continue
        mode_by_case = {case.case_id: case for case in mode_cases}
        if mode_by_case.keys() != baseline_by_case.keys():
            raise ValueError(f"mode {mode!r} is not paired with the baseline case set")
        for case_id, case in mode_by_case.items():
            reference = baseline_by_case[case_id]
            if case.expected != reference.expected:
                raise ValueError(f"mode {mode!r} changes expected labels for case {case_id!r}")
            if case.reviewable_hunks != reference.reviewable_hunks:
                raise ValueError(f"mode {mode!r} changes reviewable hunk count for case {case_id!r}")
            if case.changed_lines != reference.changed_lines:
                raise ValueError(f"mode {mode!r} changes changed line count for case {case_id!r}")
            if case.languages != reference.languages:
                raise ValueError(f"mode {mode!r} changes languages for case {case_id!r}")
            if case.frameworks != reference.frameworks:
                raise ValueError(f"mode {mode!r} changes frameworks for case {case_id!r}")
            if case.provenance != reference.provenance:
                raise ValueError(f"mode {mode!r} changes provenance for case {case_id!r}")

    metrics = tuple(_aggregate(mode, grouped[mode]) for mode in sorted(grouped))
    by_mode = {item.mode: item for item in metrics}
    base = by_mode[baseline]
    deltas = tuple(
        PairedDelta(
            mode=mode,
            baseline=baseline,
            precision=by_mode[mode].precision - base.precision,
            recall=by_mode[mode].recall - base.recall,
            f1=by_mode[mode].f1 - base.f1,
            f2=by_mode[mode].f2 - base.f2,
            model_calls=_difference(by_mode[mode].model_calls, base.model_calls),
            input_tokens=_difference(by_mode[mode].input_tokens, base.input_tokens),
            output_tokens=_difference(by_mode[mode].output_tokens, base.output_tokens),
            cost=_difference(by_mode[mode].cost, base.cost),
            cost_per_changed_kloc=_difference(by_mode[mode].cost_per_changed_kloc, base.cost_per_changed_kloc),
            median_cost_per_changed_kloc=_difference(by_mode[mode].median_cost_per_changed_kloc, base.median_cost_per_changed_kloc),
            p95_cost_per_changed_kloc=_difference(by_mode[mode].p95_cost_per_changed_kloc, base.p95_cost_per_changed_kloc),
            wall_time_seconds=_difference(by_mode[mode].wall_time_seconds, base.wall_time_seconds),
        )
        for mode in sorted(grouped)
        if mode != baseline
    )
    baseline_cases = tuple(grouped[baseline])
    languages = sorted({
        language
        for case in baseline_cases
        for language in case.languages
    })
    frameworks = sorted({
        framework
        for case in baseline_cases
        for framework in case.frameworks
    })
    return EvaluationReport(
        baseline=baseline,
        evidence={
            "datasetSha256": hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
            "allReviewsComplete": all(case.completion_status == "complete" for case in cases),
            "completionPolicy": "All supplied cases contribute to TP/FP/FN; missing predictions count as misses. Failed, partial, missing and unknown jobs are reported separately and are not clean negatives.",
            "zeroDenominatorPolicy": "Precision, recall, F1 and F2 are 0 when their denominator is 0.",
            "provenanceCases": sum(bool(case.provenance) for case in baseline_cases),
            "caseProvenance": {case.case_id: dict(case.provenance) for case in baseline_cases},
            "scope": payload.get("scope", "adjudicated-input-reduction"),
            "limitations": "Offline reduction of supplied labels, not independent adjudication. Declared provenance is paired but external artifacts are not verified. Missing usage stays null; no prices are estimated. No confidence intervals are computed.",
        },
        modes=metrics,
        paired_deltas=deltas,
        corpus_coverage={
            "cases": len(baseline_cases),
            "languages": languages,
            "frameworks": frameworks,
            "polyglotCases": sum(
                len(case.languages) > 1 for case in baseline_cases
            ),
            "changedLines": sum(
                case.changed_lines for case in baseline_cases
            ),
        },
        language_strata=_strata(
            cases,
            attribute="languages",
            empty_profile="unknown",
        ),
        framework_strata=_strata(
            cases,
            attribute="frameworks",
            empty_profile="none",
        ),
    )
