from __future__ import annotations

from dataclasses import asdict, dataclass
from math import ceil
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
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field} must be a non-negative number")
    return float(value)


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
    model_calls: int
    input_tokens: int
    output_tokens: int
    cost: float

    @classmethod
    def parse(cls, payload: Mapping[str, Any]) -> "CaseResult":
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
        model_calls = _non_negative_int(payload.get("modelCalls", 0), "modelCalls")
        input_tokens = _non_negative_int(payload.get("inputTokens", 0), "inputTokens")
        output_tokens = _non_negative_int(payload.get("outputTokens", 0), "outputTokens")
        cost = _non_negative_number(payload.get("cost", 0), "cost")
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
    coverage: float
    changed_lines: int
    model_calls: int
    input_tokens: int
    output_tokens: int
    cost: float
    cost_per_changed_kloc: float
    median_cost_per_changed_kloc: float
    p95_cost_per_changed_kloc: float


@dataclass(frozen=True)
class PairedDelta:
    mode: str
    baseline: str
    precision: float
    recall: float
    model_calls: int
    input_tokens: int
    output_tokens: int
    cost: float
    cost_per_changed_kloc: float
    median_cost_per_changed_kloc: float
    p95_cost_per_changed_kloc: float


@dataclass(frozen=True)
class EvaluationReport:
    baseline: str
    modes: tuple[ModeMetrics, ...]
    paired_deltas: tuple[PairedDelta, ...]
    corpus_coverage: Mapping[str, Any]
    language_strata: Mapping[str, tuple[ModeMetrics, ...]]
    framework_strata: Mapping[str, tuple[ModeMetrics, ...]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "baseline": self.baseline,
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


def _cost_per_changed_kloc(cost: float, changed_lines: int) -> float:
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
    cost = round(sum(case.cost for case in values), 8)
    case_costs_per_kloc = [
        _cost_per_changed_kloc(case.cost, case.changed_lines)
        for case in values
        if case.changed_lines
    ]
    return ModeMetrics(
        mode=mode,
        cases=len(values),
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
        abstentions=sum(len(case.abstained) for case in values),
        precision=_ratio(true_positives, true_positives + false_positives),
        recall=_ratio(true_positives, true_positives + false_negatives),
        coverage=_ratio(terminal, reviewable),
        changed_lines=changed_lines,
        model_calls=sum(case.model_calls for case in values),
        input_tokens=sum(case.input_tokens for case in values),
        output_tokens=sum(case.output_tokens for case in values),
        cost=cost,
        cost_per_changed_kloc=_cost_per_changed_kloc(cost, changed_lines),
        median_cost_per_changed_kloc=round(
            median(case_costs_per_kloc) if case_costs_per_kloc else 0.0,
            8,
        ),
        p95_cost_per_changed_kloc=round(
            _percentile_95(case_costs_per_kloc),
            8,
        ),
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

    metrics = tuple(_aggregate(mode, grouped[mode]) for mode in sorted(grouped))
    by_mode = {item.mode: item for item in metrics}
    base = by_mode[baseline]
    deltas = tuple(
        PairedDelta(
            mode=mode,
            baseline=baseline,
            precision=by_mode[mode].precision - base.precision,
            recall=by_mode[mode].recall - base.recall,
            model_calls=by_mode[mode].model_calls - base.model_calls,
            input_tokens=by_mode[mode].input_tokens - base.input_tokens,
            output_tokens=by_mode[mode].output_tokens - base.output_tokens,
            cost=round(by_mode[mode].cost - base.cost, 8),
            cost_per_changed_kloc=round(
                by_mode[mode].cost_per_changed_kloc
                - base.cost_per_changed_kloc,
                8,
            ),
            median_cost_per_changed_kloc=round(
                by_mode[mode].median_cost_per_changed_kloc
                - base.median_cost_per_changed_kloc,
                8,
            ),
            p95_cost_per_changed_kloc=round(
                by_mode[mode].p95_cost_per_changed_kloc
                - base.p95_cost_per_changed_kloc,
                8,
            ),
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
