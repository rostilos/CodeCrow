"""Typed, privacy-bounded telemetry for the legacy review pipeline.

The recorder intentionally keeps high-cardinality execution identity in a
trace artifact while terminal metric labels are restricted to a small,
auditable set.  Telemetry sinks are observational: a sink failure is retained
as local diagnostic state and is never allowed to change an analysis result.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from contextvars import ContextVar, Token
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import re
from time import monotonic_ns
from typing import Any, Mapping, Protocol, Sequence


_REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
_EXECUTION_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}")
_INDEX_VERSION = re.compile(
    r"(?:rag-disabled|rag-commit-(?:[0-9a-f]{40}|[0-9a-f]{64}))"
)
_REASON = re.compile(r"[a-z][a-z0-9_.-]{0,95}")


def _require_match(pattern: re.Pattern[str], value: str, field_name: str) -> None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{field_name} has an invalid telemetry identity")


def _require_non_negative(values: Mapping[str, int]) -> None:
    for name, value in values.items():
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")


class StageOutcome(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class TerminalOutcome(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReviewApproach(str, Enum):
    CLASSIC = "CLASSIC"
    AGENTIC = "AGENTIC"


@dataclass(frozen=True, slots=True)
class ExecutionIdentity:
    execution_id: str
    base_revision: str
    head_revision: str
    artifact_manifest_digest: str | None = None
    review_approach: ReviewApproach = ReviewApproach.CLASSIC

    def __post_init__(self) -> None:
        _require_match(_EXECUTION_IDENTIFIER, self.execution_id, "execution_id")
        _require_match(_REVISION, self.base_revision, "base_revision")
        _require_match(_REVISION, self.head_revision, "head_revision")
        if self.artifact_manifest_digest is not None:
            _require_match(
                _DIGEST,
                self.artifact_manifest_digest,
                "artifact_manifest_digest",
            )
        try:
            object.__setattr__(
                self,
                "review_approach",
                ReviewApproach(self.review_approach),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "review_approach must be CLASSIC or AGENTIC"
            ) from exc


@dataclass(frozen=True, slots=True)
class VersionAttribution:
    provider: str
    model: str
    prompt_version: str
    rules_version: str
    policy_version: str
    index_version: str

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if name == "index_version":
                continue
            _require_match(_VERSION, value, name)
        _require_match(_INDEX_VERSION, self.index_version, "index_version")


@dataclass(frozen=True, slots=True)
class UsageCounts:
    requested_input_tokens: int = 0
    requested_output_tokens: int = 0
    provider_input_tokens: int = 0
    provider_output_tokens: int = 0
    provider_cache_read_tokens: int = 0
    calls: int = 0
    retries: int = 0
    estimated_cost_microunits: int = 0
    provider_usage_missing_calls: int = 0
    cost_estimate_missing_calls: int = 0

    def __post_init__(self) -> None:
        _require_non_negative(asdict(self))

    def plus(self, other: "UsageCounts") -> "UsageCounts":
        return UsageCounts(
            **{
                name: getattr(self, name) + getattr(other, name)
                for name in asdict(self)
            }
        )


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """Active model prices expressed in currency units per million tokens.

    One currency unit is one million microunits, so multiplying this price by
    a token count directly yields microunits. Decimal arithmetic keeps the
    estimate reproducible across providers and runtimes.
    """

    input_price_per_million: Decimal
    output_price_per_million: Decimal

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be a finite non-negative decimal")

    @classmethod
    def from_values(cls, input_price: Any, output_price: Any) -> "ModelPricing | None":
        if input_price is None or output_price is None:
            return None
        try:
            return cls(Decimal(str(input_price)), Decimal(str(output_price)))
        except (InvalidOperation, TypeError, ValueError):
            return None

    def estimate_microunits(self, *, input_tokens: int, output_tokens: int) -> int:
        _require_non_negative(
            {"input_tokens": input_tokens, "output_tokens": output_tokens}
        )
        estimate = (
            Decimal(input_tokens) * self.input_price_per_million
            + Decimal(output_tokens) * self.output_price_per_million
        )
        return int(estimate.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True, slots=True)
class CandidateCounts:
    input: int = 0
    produced: int = 0
    retained: int = 0

    def __post_init__(self) -> None:
        _require_non_negative(asdict(self))
        if self.retained > self.input + self.produced:
            raise ValueError("retained candidates exceed observable candidates")


@dataclass(frozen=True, slots=True)
class CoverageCounts:
    inventory: int = 0
    represented: int = 0
    unrepresented: int = 0

    def __post_init__(self) -> None:
        _require_non_negative(asdict(self))
        if self.represented + self.unrepresented != self.inventory:
            raise ValueError("coverage counts do not reconcile")


@dataclass(frozen=True, slots=True)
class StageTelemetry:
    name: str
    producer: str
    outcome: StageOutcome
    duration_ms: int
    usage: UsageCounts
    candidates: CandidateCounts
    coverage: CoverageCounts
    reason: str | None = None

    def __post_init__(self) -> None:
        _require_match(_IDENTIFIER, self.name, "stage name")
        _require_match(_IDENTIFIER, self.producer, "stage producer")
        _require_non_negative({"duration_ms": self.duration_ms})
        _validate_reason(self.outcome is not StageOutcome.COMPLETE, self.reason)


@dataclass(frozen=True, slots=True)
class ModelCallTelemetry:
    stage: str
    producer: str
    outcome: StageOutcome
    duration_ms: int
    deadline_ms: int
    usage: UsageCounts
    reason: str | None = None

    def __post_init__(self) -> None:
        _require_match(_IDENTIFIER, self.stage, "call stage")
        _require_match(_IDENTIFIER, self.producer, "call producer")
        _require_non_negative(
            {"duration_ms": self.duration_ms, "deadline_ms": self.deadline_ms}
        )
        _validate_reason(self.outcome is not StageOutcome.COMPLETE, self.reason)


@dataclass(frozen=True, slots=True)
class ToolCallTelemetry:
    stage: str
    tool: str
    outcome: StageOutcome
    duration_ms: int
    retries: int = 0
    reason: str | None = None

    def __post_init__(self) -> None:
        _require_match(_IDENTIFIER, self.stage, "tool stage")
        _require_match(_IDENTIFIER, self.tool, "tool")
        _require_non_negative(
            {"duration_ms": self.duration_ms, "retries": self.retries}
        )
        _validate_reason(self.outcome is not StageOutcome.COMPLETE, self.reason)


@dataclass(frozen=True, slots=True)
class CandidateLineage:
    producer: str
    input_artifact_ids: tuple[str, ...] = ()
    output_artifact_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_match(_IDENTIFIER, self.producer, "lineage producer")
        for artifact_id in (*self.input_artifact_ids, *self.output_artifact_ids):
            _require_match(_IDENTIFIER, artifact_id, "lineage artifact_id")


def _validate_reason(required: bool, reason: str | None) -> None:
    if required:
        if reason is None:
            raise ValueError("non-complete telemetry requires a reason code")
        _require_match(_REASON, reason, "reason")
    elif reason is not None:
        raise ValueError("complete telemetry cannot carry an error reason")


@dataclass(frozen=True, slots=True)
class MetricPoint:
    name: str
    labels: Mapping[str, str]
    values: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class ExecutionTrace:
    execution_id: str
    base_revision: str
    head_revision: str
    artifact_manifest_digest: str | None
    review_approach: ReviewApproach
    versions: VersionAttribution
    outcome: TerminalOutcome
    duration_ms: int
    usage: UsageCounts
    candidates: CandidateCounts
    coverage: CoverageCounts
    reason: str | None
    stages: tuple[StageTelemetry, ...]
    model_calls: tuple[ModelCallTelemetry, ...]
    tool_calls: tuple[ToolCallTelemetry, ...]
    lineage: tuple[CandidateLineage, ...]


class TelemetrySink(Protocol):
    def emit_terminal(self, trace: ExecutionTrace, metric: MetricPoint) -> None: ...


@dataclass(slots=True)
class MemoryTelemetrySink:
    traces: list[ExecutionTrace] = field(default_factory=list)
    metrics: list[MetricPoint] = field(default_factory=list)

    def emit_terminal(self, trace: ExecutionTrace, metric: MetricPoint) -> None:
        self.traces.append(trace)
        self.metrics.append(metric)


class ExecutionTelemetryRecorder:
    """Accumulates one execution and atomically emits its terminal evidence."""

    def __init__(
        self,
        *,
        identity: ExecutionIdentity,
        versions: VersionAttribution,
        sink: TelemetrySink,
        default_deadline_ms: int = 0,
        model_pricing: ModelPricing | None = None,
    ) -> None:
        _require_non_negative({"default_deadline_ms": default_deadline_ms})
        self.identity = identity
        self.versions = versions
        self.sink = sink
        self.default_deadline_ms = default_deadline_ms
        self.model_pricing = model_pricing
        self._stages: list[StageTelemetry] = []
        self._model_calls: list[ModelCallTelemetry] = []
        self._tool_calls: list[ToolCallTelemetry] = []
        self._lineage: list[CandidateLineage] = []
        self._finished = False
        self._sink_errors: list[str] = []

    @property
    def sink_errors(self) -> tuple[str, ...]:
        return tuple(self._sink_errors)

    @property
    def model_usage(self) -> UsageCounts:
        return usage_total(self._model_calls)

    @property
    def model_calls(self) -> tuple[ModelCallTelemetry, ...]:
        return tuple(self._model_calls)

    @property
    def tool_calls(self) -> tuple[ToolCallTelemetry, ...]:
        return tuple(self._tool_calls)

    @property
    def stages(self) -> tuple[StageTelemetry, ...]:
        return tuple(self._stages)

    @property
    def latest_coverage(self) -> CoverageCounts | None:
        for stage in reversed(self._stages):
            if stage.coverage.inventory > 0:
                return stage.coverage
        return None

    @property
    def lineage(self) -> tuple[CandidateLineage, ...]:
        return tuple(self._lineage)

    def model_usage_for(self, *, producer: str) -> UsageCounts:
        return usage_total(
            [call for call in self._model_calls if call.producer == producer]
        )

    @property
    def has_incomplete_operations(self) -> bool:
        incomplete = {StageOutcome.PARTIAL, StageOutcome.FAILED}
        return (
            any(
                stage.outcome in incomplete or stage.coverage.unrepresented > 0
                for stage in self._stages
            )
            or any(
                call.outcome in incomplete
                for call in (*self._model_calls, *self._tool_calls)
            )
        )

    def record_stage(
        self,
        *,
        name: str,
        producer: str,
        outcome: StageOutcome,
        duration_ms: int,
        usage: UsageCounts | None = None,
        candidates: CandidateCounts | None = None,
        coverage: CoverageCounts | None = None,
        reason: str | None = None,
    ) -> None:
        self._require_open()
        self._stages.append(
            StageTelemetry(
                name=name,
                producer=producer,
                outcome=outcome,
                duration_ms=duration_ms,
                usage=usage or UsageCounts(),
                candidates=candidates or CandidateCounts(),
                coverage=coverage or CoverageCounts(),
                reason=reason,
            )
        )

    def record_model_call(self, call: ModelCallTelemetry) -> None:
        self._require_open()
        self._model_calls.append(call)

    def record_tool_call(self, call: ToolCallTelemetry) -> None:
        self._require_open()
        self._tool_calls.append(call)

    def record_lineage(self, lineage: CandidateLineage) -> None:
        self._require_open()
        self._lineage.append(lineage)

    def finish(
        self,
        *,
        outcome: TerminalOutcome,
        duration_ms: int,
        usage: UsageCounts,
        candidates: CandidateCounts,
        coverage: CoverageCounts,
        reason: str | None = None,
    ) -> ExecutionTrace:
        self._require_open()
        _require_non_negative({"duration_ms": duration_ms})
        _validate_reason(outcome is not TerminalOutcome.COMPLETE, reason)
        if outcome is TerminalOutcome.COMPLETE:
            if coverage.unrepresented:
                raise ValueError("complete telemetry cannot hide unrepresented coverage")
            if self.has_incomplete_operations:
                raise ValueError("complete telemetry cannot hide a partial or failed stage")
            if usage.provider_usage_missing_calls:
                raise ValueError("complete telemetry requires provider usage")
            if usage.cost_estimate_missing_calls:
                raise ValueError("complete telemetry requires a cost estimate")
            if any(call.deadline_ms == 0 for call in self._model_calls):
                raise ValueError("complete telemetry requires model-call deadlines")
            required_stages = {
                "acquisition",
                "retrieval",
                "generation",
                "pre_dedup",
                "post_dedup",
                "verification",
                "reconciliation",
                "persistence",
                "delivery",
            }
            observed_stages = {stage.name for stage in self._stages}
            missing_stages = sorted(required_stages - observed_stages)
            if missing_stages:
                raise ValueError(
                    "complete telemetry requires required pipeline stages: "
                    + ", ".join(missing_stages)
                )

        trace = self._build_trace(
            outcome=outcome,
            duration_ms=duration_ms,
            usage=usage,
            candidates=candidates,
            coverage=coverage,
            reason=reason,
        )
        metric = MetricPoint(
            name="codecrow.review.execution.terminal",
            labels={
                "outcome": outcome.value,
                "policy_version": self.versions.policy_version,
                "provider": self.versions.provider,
            },
            values={
                "duration_ms": duration_ms,
                **{name: value for name, value in asdict(usage).items()},
                "candidate_input": candidates.input,
                "candidate_produced": candidates.produced,
                "candidate_retained": candidates.retained,
                "coverage_inventory": coverage.inventory,
                "coverage_represented": coverage.represented,
                "coverage_unrepresented": coverage.unrepresented,
            },
        )
        self._finished = True
        try:
            self.sink.emit_terminal(trace, metric)
        except Exception as error:  # telemetry must never alter analysis state
            self._sink_errors.append(type(error).__name__)
        return trace

    def provisional_snapshot(
        self,
        *,
        outcome: TerminalOutcome,
        duration_ms: int,
        usage: UsageCounts,
        candidates: CandidateCounts,
        coverage: CoverageCounts,
        reason: str | None = None,
    ) -> ExecutionTrace:
        """Seal Python observations without emitting the pipeline terminal.

        Java still owns persistence and delivery.  It reconciles this snapshot
        with those downstream stages before constructing the only terminal
        metric for the end-to-end execution.
        """

        self._require_open()
        _require_non_negative({"duration_ms": duration_ms})
        _validate_reason(outcome is not TerminalOutcome.COMPLETE, reason)
        trace = self._build_trace(
            outcome=outcome,
            duration_ms=duration_ms,
            usage=usage,
            candidates=candidates,
            coverage=coverage,
            reason=reason,
        )
        self._finished = True
        return trace

    def _build_trace(
        self,
        *,
        outcome: TerminalOutcome,
        duration_ms: int,
        usage: UsageCounts,
        candidates: CandidateCounts,
        coverage: CoverageCounts,
        reason: str | None,
    ) -> ExecutionTrace:
        return ExecutionTrace(
            execution_id=self.identity.execution_id,
            base_revision=self.identity.base_revision,
            head_revision=self.identity.head_revision,
            artifact_manifest_digest=self.identity.artifact_manifest_digest,
            review_approach=self.identity.review_approach,
            versions=self.versions,
            outcome=outcome,
            duration_ms=duration_ms,
            usage=usage,
            candidates=candidates,
            coverage=coverage,
            reason=reason,
            stages=tuple(self._stages),
            model_calls=tuple(self._model_calls),
            tool_calls=tuple(self._tool_calls),
            lineage=tuple(self._lineage),
        )

    def _require_open(self) -> None:
        if self._finished:
            raise RuntimeError("execution telemetry is already terminal")


def usage_total(calls: Sequence[ModelCallTelemetry]) -> UsageCounts:
    total = UsageCounts()
    for call in calls:
        total = total.plus(call.usage)
    return total


_CURRENT_RECORDER: ContextVar[ExecutionTelemetryRecorder | None] = ContextVar(
    "codecrow_review_telemetry", default=None
)


def bind_telemetry(
    recorder: ExecutionTelemetryRecorder | None,
) -> Token[ExecutionTelemetryRecorder | None]:
    return _CURRENT_RECORDER.set(recorder)


def reset_telemetry(token: Token[ExecutionTelemetryRecorder | None]) -> None:
    _CURRENT_RECORDER.reset(token)


def current_telemetry() -> ExecutionTelemetryRecorder | None:
    return _CURRENT_RECORDER.get()


def _duration_ms(started_ns: int) -> int:
    return max(0, (monotonic_ns() - started_ns) // 1_000_000)


def _requested_output_tokens(model: Any) -> int:
    try:
        candidates = [
            getattr(model, "max_tokens", None),
            getattr(model, "max_output_tokens", None),
            getattr(model, "max_completion_tokens", None),
        ]
        model_kwargs = getattr(model, "model_kwargs", None)
    except Exception:
        return 0
    if isinstance(model_kwargs, Mapping):
        candidates.extend(
            model_kwargs.get(name)
            for name in ("max_tokens", "max_output_tokens", "max_completion_tokens")
        )
    for candidate in candidates:
        if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate > 0:
            return candidate
    return 0


def _provider_usage(response: Any) -> tuple[int, int, int, bool]:
    usage = getattr(response, "usage_metadata", None)
    if isinstance(usage, Mapping):
        details = usage.get("input_token_details")
        cached = details.get("cache_read", 0) if isinstance(details, Mapping) else 0
        return (
            int(usage.get("input_tokens", 0) or 0),
            int(usage.get("output_tokens", 0) or 0),
            int(cached or 0),
            True,
        )
    metadata = getattr(response, "response_metadata", None)
    token_usage = metadata.get("token_usage") if isinstance(metadata, Mapping) else None
    if isinstance(token_usage, Mapping):
        details = token_usage.get("prompt_tokens_details")
        cached = details.get("cached_tokens", 0) if isinstance(details, Mapping) else 0
        return (
            int(token_usage.get("prompt_tokens", 0) or 0),
            int(token_usage.get("completion_tokens", 0) or 0),
            int(cached or 0),
            True,
        )
    return 0, 0, 0, False


def _provider_cost_microunits(response: Any) -> int | None:
    """Read a provider-reported cost without retaining response content."""

    try:
        metadata = getattr(response, "response_metadata", None)
    except Exception:
        return None
    if not isinstance(metadata, Mapping):
        return None
    candidates: list[Any] = [
        metadata.get("cost"),
        metadata.get("total_cost"),
        metadata.get("estimated_cost"),
    ]
    for container_name in ("token_usage", "usage"):
        container = metadata.get(container_name)
        if isinstance(container, Mapping):
            candidates.extend(
                container.get(name)
                for name in ("cost", "total_cost", "estimated_cost")
            )
    for candidate in candidates:
        if candidate is None or isinstance(candidate, bool):
            continue
        try:
            currency_units = Decimal(str(candidate))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if currency_units.is_finite() and currency_units >= 0:
            return int(
                (currency_units * Decimal("1000000")).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                )
            )
    return None


def _estimate_input_tokens(value: Any) -> int:
    # Store only the count. Prompt/source content never enters telemetry.
    if value is None:
        return 0
    try:
        return max(1, len(str(value)) // 4)
    except Exception:
        return 0


async def observed_ainvoke(
    model: Any,
    value: Any,
    *,
    stage: str,
    producer: str,
    deadline_ms: int = 0,
    retry: bool = False,
) -> Any:
    """Invoke a model and record privacy-bounded request/provider usage."""

    recorder = current_telemetry()
    started_ns = monotonic_ns()
    requested_input = _estimate_input_tokens(value)
    requested_output = _requested_output_tokens(model)
    effective_deadline_ms = (
        deadline_ms
        if isinstance(deadline_ms, int)
        and not isinstance(deadline_ms, bool)
        and deadline_ms > 0
        else 0
    )
    if effective_deadline_ms == 0 and recorder is not None:
        effective_deadline_ms = recorder.default_deadline_ms
    retry_count = 1 if retry else 0
    try:
        response = await model.ainvoke(value)
    except Exception:
        if recorder is not None:
            try:
                _safe_record_model_call(
                    recorder,
                    ModelCallTelemetry(
                        stage=stage,
                        producer=producer,
                        outcome=StageOutcome.FAILED,
                        duration_ms=_duration_ms(started_ns),
                        deadline_ms=effective_deadline_ms,
                        usage=UsageCounts(
                            requested_input_tokens=requested_input,
                            requested_output_tokens=requested_output,
                            calls=1,
                            retries=retry_count,
                            provider_usage_missing_calls=1,
                            cost_estimate_missing_calls=1,
                        ),
                        reason="model_call_failed",
                    ),
                )
            except Exception:
                pass
        raise

    if recorder is not None:
        call: ModelCallTelemetry | None = None
        try:
            provider_input, provider_output, cache_read, provider_reported = (
                _provider_usage(response)
            )
            estimated_cost = _provider_cost_microunits(response)
            if (
                estimated_cost is None
                and provider_reported
                and recorder.model_pricing is not None
            ):
                estimated_cost = recorder.model_pricing.estimate_microunits(
                    input_tokens=provider_input,
                    output_tokens=provider_output,
                )
            call = ModelCallTelemetry(
                stage=stage,
                producer=producer,
                outcome=StageOutcome.COMPLETE,
                duration_ms=_duration_ms(started_ns),
                deadline_ms=effective_deadline_ms,
                usage=UsageCounts(
                    requested_input_tokens=requested_input,
                    requested_output_tokens=requested_output,
                    provider_input_tokens=provider_input,
                    provider_output_tokens=provider_output,
                    provider_cache_read_tokens=cache_read,
                    calls=1,
                    retries=retry_count,
                    estimated_cost_microunits=estimated_cost or 0,
                    provider_usage_missing_calls=0 if provider_reported else 1,
                    cost_estimate_missing_calls=0 if estimated_cost is not None else 1,
                ),
            )
        except Exception:
            try:
                call = ModelCallTelemetry(
                    stage=stage,
                    producer=producer,
                    outcome=StageOutcome.COMPLETE,
                    duration_ms=_duration_ms(started_ns),
                    deadline_ms=effective_deadline_ms,
                    usage=UsageCounts(
                        requested_input_tokens=requested_input,
                        requested_output_tokens=requested_output,
                        calls=1,
                        retries=retry_count,
                        provider_usage_missing_calls=1,
                        cost_estimate_missing_calls=1,
                    ),
                )
            except Exception:
                pass
        if call is not None:
            _safe_record_model_call(recorder, call)
    return response


def _safe_record_model_call(
    recorder: ExecutionTelemetryRecorder,
    call: ModelCallTelemetry,
) -> None:
    try:
        recorder.record_model_call(call)
    except Exception:
        # The observed provider result or failure remains authoritative; an
        # observational telemetry problem cannot replace it.
        return


def trace_document(trace: ExecutionTrace) -> dict[str, Any]:
    """Return the redaction-safe, JSON-compatible high-cardinality artifact."""

    def wire(value: Any) -> Any:
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, Mapping):
            return {str(key): wire(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [wire(item) for item in value]
        return value

    return wire(asdict(trace))
