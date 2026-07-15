from __future__ import annotations

import json

import pytest

import service.review.telemetry as telemetry

from service.review.telemetry import (
    CandidateCounts,
    CandidateLineage,
    CoverageCounts,
    ExecutionIdentity,
    ExecutionTelemetryRecorder,
    MemoryTelemetrySink,
    ModelCallTelemetry,
    StageOutcome,
    TerminalOutcome,
    ToolCallTelemetry,
    UsageCounts,
    VersionAttribution,
    bind_telemetry,
    observed_ainvoke,
    reset_telemetry,
    trace_document,
)


def _identity() -> ExecutionIdentity:
    return ExecutionIdentity(
        execution_id="execution-0001",
        base_revision="a" * 40,
        head_revision="b" * 40,
    )


def _versions() -> VersionAttribution:
    return VersionAttribution(
        provider="scripted",
        model="fixture-v1",
        prompt_version="review-prompts-v1",
        rules_version="project-rules-v1",
        policy_version="legacy-v1",
        index_version="rag-commit-" + "c" * 40,
    )


def test_terminal_execution_requires_complete_low_cardinality_summary() -> None:
    sink = MemoryTelemetrySink()
    recorder = ExecutionTelemetryRecorder(
        identity=_identity(),
        versions=_versions(),
        sink=sink,
    )

    recorder.record_stage(
        name="generation",
        producer="stage_1",
        outcome=StageOutcome.COMPLETE,
        duration_ms=125,
        usage=UsageCounts(
            requested_input_tokens=100,
            requested_output_tokens=25,
            provider_input_tokens=90,
            provider_output_tokens=20,
            provider_cache_read_tokens=10,
            calls=1,
            retries=0,
            estimated_cost_microunits=75,
            provider_usage_missing_calls=0,
            cost_estimate_missing_calls=0,
        ),
        candidates=CandidateCounts(input=0, produced=3, retained=2),
        coverage=CoverageCounts(inventory=4, represented=4, unrepresented=0),
    )
    for stage_name in (
        "acquisition",
        "retrieval",
        "pre_dedup",
        "post_dedup",
        "verification",
        "reconciliation",
        "persistence",
        "delivery",
    ):
        recorder.record_stage(
            name=stage_name,
            producer="fixture",
            outcome=StageOutcome.COMPLETE,
            duration_ms=1,
            coverage=CoverageCounts(inventory=4, represented=4, unrepresented=0),
        )

    recorder.finish(
        outcome=TerminalOutcome.COMPLETE,
        duration_ms=250,
        usage=UsageCounts(
            requested_input_tokens=100,
            requested_output_tokens=25,
            provider_input_tokens=90,
            provider_output_tokens=20,
            provider_cache_read_tokens=10,
            calls=1,
            retries=0,
            estimated_cost_microunits=75,
            provider_usage_missing_calls=0,
            cost_estimate_missing_calls=0,
        ),
        candidates=CandidateCounts(input=0, produced=3, retained=2),
        coverage=CoverageCounts(inventory=4, represented=4, unrepresented=0),
    )

    assert len(sink.metrics) == 1
    terminal_metric = sink.metrics[0]
    assert terminal_metric.name == "codecrow.review.execution.terminal"
    assert terminal_metric.labels == {
        "outcome": "complete",
        "policy_version": "legacy-v1",
        "provider": "scripted",
    }
    assert set(terminal_metric.values) == {
        "duration_ms",
        "requested_input_tokens",
        "requested_output_tokens",
        "provider_input_tokens",
        "provider_output_tokens",
        "provider_cache_read_tokens",
        "calls",
        "retries",
        "estimated_cost_microunits",
        "provider_usage_missing_calls",
        "cost_estimate_missing_calls",
        "candidate_input",
        "candidate_produced",
        "candidate_retained",
        "coverage_inventory",
        "coverage_represented",
        "coverage_unrepresented",
    }

    terminal_trace = sink.traces[-1]
    assert terminal_trace.execution_id == "execution-0001"
    assert terminal_trace.base_revision == "a" * 40
    assert terminal_trace.head_revision == "b" * 40
    assert terminal_trace.versions == _versions()
    assert terminal_trace.stages[0].producer == "stage_1"


@pytest.mark.parametrize(
    ("identity", "versions"),
    [
        (
            {"execution_id": "execution-0001", "base_revision": "short", "head_revision": "b" * 40},
            {},
        ),
        (
            {"execution_id": "execution-0001", "base_revision": "a" * 40, "head_revision": "b" * 40},
            {"policy_version": "contains customer data"},
        ),
    ],
)
def test_exact_identity_and_configuration_versions_are_mandatory(
    identity: dict[str, str], versions: dict[str, str]
) -> None:
    with pytest.raises(ValueError):
        resolved_identity = ExecutionIdentity(**identity)
        version_values = {
            "provider": "scripted",
            "model": "fixture-v1",
            "prompt_version": "review-prompts-v1",
            "rules_version": "project-rules-v1",
            "policy_version": "legacy-v1",
            "index_version": "rag-commit-" + "c" * 40,
            **versions,
        }
        ExecutionTelemetryRecorder(
            identity=resolved_identity,
            versions=VersionAttribution(**version_values),
            sink=MemoryTelemetrySink(),
        )


def test_failed_boundary_cannot_be_reported_as_zero_finding_success() -> None:
    sink = MemoryTelemetrySink()
    recorder = ExecutionTelemetryRecorder(
        identity=_identity(), versions=_versions(), sink=sink
    )
    recorder.record_stage(
        name="generation",
        producer="stage_1",
        outcome=StageOutcome.FAILED,
        duration_ms=25,
        candidates=CandidateCounts(input=0, produced=0, retained=0),
        coverage=CoverageCounts(inventory=2, represented=0, unrepresented=2),
        reason="provider_timeout",
    )

    with pytest.raises(ValueError, match="partial or failed stage"):
        recorder.finish(
            outcome=TerminalOutcome.COMPLETE,
            duration_ms=30,
            usage=UsageCounts(),
            candidates=CandidateCounts(),
            coverage=CoverageCounts(inventory=2, represented=2, unrepresented=0),
        )

    trace = recorder.finish(
        outcome=TerminalOutcome.FAILED,
        duration_ms=30,
        usage=UsageCounts(),
        candidates=CandidateCounts(),
        coverage=CoverageCounts(inventory=2, represented=0, unrepresented=2),
        reason="analysis_failed",
    )
    assert trace.outcome is TerminalOutcome.FAILED
    assert trace.stages[-1].name == "generation"
    assert sink.metrics[-1].labels["outcome"] == "failed"


@pytest.mark.asyncio
async def test_model_call_records_deadline_retry_usage_without_prompt_leak() -> None:
    class Response:
        usage_metadata = {
            "input_tokens": 11,
            "output_tokens": 7,
            "input_token_details": {"cache_read": 3},
        }

    class Model:
        max_tokens = 64

        async def ainvoke(self, value: object) -> Response:
            assert value == "private prompt and source secret-123"
            return Response()

    sink = MemoryTelemetrySink()
    recorder = ExecutionTelemetryRecorder(
        identity=_identity(),
        versions=_versions(),
        sink=sink,
        default_deadline_ms=12_000,
    )
    token = bind_telemetry(recorder)
    try:
        response = await observed_ainvoke(
            Model(),
            "private prompt and source secret-123",
            stage="generation",
            producer="stage_1",
            retry=True,
        )
    finally:
        reset_telemetry(token)

    assert isinstance(response, Response)
    call = recorder.model_calls[-1]
    assert call.deadline_ms == 12_000
    assert call.usage.provider_input_tokens == 11
    assert call.usage.provider_output_tokens == 7
    assert call.usage.provider_cache_read_tokens == 3
    assert call.usage.requested_output_tokens == 64
    assert call.usage.retries == 1

    trace = recorder.finish(
        outcome=TerminalOutcome.PARTIAL,
        duration_ms=20,
        usage=recorder.model_usage,
        candidates=CandidateCounts(),
        coverage=CoverageCounts(),
        reason="cost_estimate_unavailable",
    )
    serialized = json.dumps(trace_document(trace), sort_keys=True)
    assert "secret-123" not in serialized
    assert "private prompt" not in serialized
    assert "execution-0001" in serialized
    assert set(sink.metrics[-1].labels) == {"outcome", "policy_version", "provider"}


@pytest.mark.asyncio
async def test_telemetry_validation_failure_does_not_change_model_result() -> None:
    class HostileValue:
        def __str__(self) -> str:
            raise RuntimeError("source cannot be stringified")

    sentinel = object()

    class Model:
        @property
        def max_tokens(self) -> int:
            raise RuntimeError("model metadata unavailable")

        async def ainvoke(self, value: object) -> object:
            assert isinstance(value, HostileValue)
            return sentinel

    recorder = ExecutionTelemetryRecorder(
        identity=_identity(), versions=_versions(), sink=MemoryTelemetrySink()
    )
    token = bind_telemetry(recorder)
    try:
        result = await observed_ainvoke(
            Model(),
            HostileValue(),
            stage="invalid stage name",
            producer="stage_1",
        )
    finally:
        reset_telemetry(token)

    assert result is sentinel
    assert recorder.model_calls == ()


@pytest.mark.asyncio
async def test_provider_fault_is_traced_to_named_boundary_without_error_payload() -> None:
    class FailingModel:
        async def ainvoke(self, value: object) -> object:
            raise TimeoutError("credential=secret-provider-token")

    sink = MemoryTelemetrySink()
    recorder = ExecutionTelemetryRecorder(
        identity=_identity(),
        versions=_versions(),
        sink=sink,
        default_deadline_ms=5_000,
    )
    token = bind_telemetry(recorder)
    try:
        with pytest.raises(TimeoutError, match="secret-provider-token"):
            await observed_ainvoke(
                FailingModel(),
                "customer source payload",
                stage="generation",
                producer="stage_1",
            )
    finally:
        reset_telemetry(token)

    call = recorder.model_calls[-1]
    assert call.stage == "generation"
    assert call.producer == "stage_1"
    assert call.outcome is StageOutcome.FAILED
    assert call.reason == "model_call_failed"
    trace = recorder.finish(
        outcome=TerminalOutcome.FAILED,
        duration_ms=5,
        usage=recorder.model_usage,
        candidates=CandidateCounts(),
        coverage=CoverageCounts(),
        reason="analysis_failed",
    )
    serialized = json.dumps(trace_document(trace), sort_keys=True)
    assert "secret-provider-token" not in serialized
    assert "customer source payload" not in serialized
    assert sink.metrics[-1].labels["outcome"] == "failed"


def test_tool_outcome_and_candidate_lineage_remain_in_trace_artifact() -> None:
    sink = MemoryTelemetrySink()
    recorder = ExecutionTelemetryRecorder(
        identity=_identity(), versions=_versions(), sink=sink
    )
    recorder.record_tool_call(
        ToolCallTelemetry(
            stage="verification",
            tool="get_file",
            outcome=StageOutcome.FAILED,
            duration_ms=9,
            retries=1,
            reason="tool_call_failed",
        )
    )
    recorder.record_lineage(
        CandidateLineage(
            producer="verification_agent",
            input_artifact_ids=("candidate:" + "a" * 64,),
            output_artifact_ids=(),
        )
    )
    trace = recorder.finish(
        outcome=TerminalOutcome.FAILED,
        duration_ms=10,
        usage=UsageCounts(),
        candidates=CandidateCounts(input=1, produced=0, retained=0),
        coverage=CoverageCounts(inventory=1, represented=0, unrepresented=1),
        reason="verification_failed",
    )

    assert trace.tool_calls[-1].tool == "get_file"
    assert trace.lineage[-1].input_artifact_ids == ("candidate:" + "a" * 64,)
    assert "candidate:" not in json.dumps(sink.metrics[-1].labels)


def test_sink_failure_is_observational_and_does_not_replace_terminal_state() -> None:
    class BrokenSink:
        def emit_terminal(self, trace: object, metric: object) -> None:
            raise RuntimeError("secret sink payload")

    recorder = ExecutionTelemetryRecorder(
        identity=_identity(), versions=_versions(), sink=BrokenSink()
    )
    trace = recorder.finish(
        outcome=TerminalOutcome.PARTIAL,
        duration_ms=1,
        usage=UsageCounts(),
        candidates=CandidateCounts(),
        coverage=CoverageCounts(),
        reason="index_version_unavailable",
    )

    assert trace.outcome is TerminalOutcome.PARTIAL
    assert recorder.sink_errors == ("RuntimeError",)
    assert "secret sink payload" not in repr(recorder.sink_errors)


def test_complete_terminal_rejects_missing_provider_usage_or_call_deadline() -> None:
    recorder = ExecutionTelemetryRecorder(
        identity=_identity(), versions=_versions(), sink=MemoryTelemetrySink()
    )
    recorder.record_model_call(
        ModelCallTelemetry(
            stage="generation",
            producer="stage_1",
            outcome=StageOutcome.COMPLETE,
            duration_ms=1,
            deadline_ms=0,
            usage=UsageCounts(
                calls=1,
                provider_usage_missing_calls=1,
                cost_estimate_missing_calls=1,
            ),
        )
    )

    with pytest.raises(ValueError, match="provider usage"):
        recorder.finish(
            outcome=TerminalOutcome.COMPLETE,
            duration_ms=1,
            usage=recorder.model_usage,
            candidates=CandidateCounts(),
            coverage=CoverageCounts(),
        )


@pytest.mark.parametrize(
    "factory",
    [
        lambda: UsageCounts(calls=True),
        lambda: CandidateCounts(input=0, produced=0, retained=1),
        lambda: CoverageCounts(inventory=2, represented=1, unrepresented=0),
    ],
)
def test_counter_contracts_reject_invalid_or_inconsistent_values(factory) -> None:
    with pytest.raises(ValueError):
        factory()


@pytest.mark.parametrize(
    "value",
    [
        lambda: ModelCallTelemetry(
            stage="generation",
            producer="stage_1",
            outcome=StageOutcome.FAILED,
            duration_ms=1,
            deadline_ms=1,
            usage=UsageCounts(),
        ),
        lambda: ModelCallTelemetry(
            stage="generation",
            producer="stage_1",
            outcome=StageOutcome.COMPLETE,
            duration_ms=1,
            deadline_ms=1,
            usage=UsageCounts(),
            reason="unexpected_reason",
        ),
    ],
)
def test_reason_contract_matches_operation_outcome(value) -> None:
    with pytest.raises(ValueError):
        value()


def test_complete_terminal_checks_each_honesty_boundary() -> None:
    unrepresented = ExecutionTelemetryRecorder(
        identity=_identity(), versions=_versions(), sink=MemoryTelemetrySink()
    )
    with pytest.raises(ValueError, match="unrepresented coverage"):
        unrepresented.finish(
            outcome=TerminalOutcome.COMPLETE,
            duration_ms=1,
            usage=UsageCounts(),
            candidates=CandidateCounts(),
            coverage=CoverageCounts(inventory=1, represented=0, unrepresented=1),
        )

    missing_cost = ExecutionTelemetryRecorder(
        identity=_identity(), versions=_versions(), sink=MemoryTelemetrySink()
    )
    with pytest.raises(ValueError, match="cost estimate"):
        missing_cost.finish(
            outcome=TerminalOutcome.COMPLETE,
            duration_ms=1,
            usage=UsageCounts(cost_estimate_missing_calls=1),
            candidates=CandidateCounts(),
            coverage=CoverageCounts(),
        )

    missing_deadline = ExecutionTelemetryRecorder(
        identity=_identity(), versions=_versions(), sink=MemoryTelemetrySink()
    )
    missing_deadline.record_model_call(
        ModelCallTelemetry(
            stage="generation",
            producer="stage_1",
            outcome=StageOutcome.COMPLETE,
            duration_ms=1,
            deadline_ms=0,
            usage=UsageCounts(),
        )
    )
    with pytest.raises(ValueError, match="model-call deadlines"):
        missing_deadline.finish(
            outcome=TerminalOutcome.COMPLETE,
            duration_ms=1,
            usage=UsageCounts(),
            candidates=CandidateCounts(),
            coverage=CoverageCounts(),
        )
    trace = missing_deadline.finish(
        outcome=TerminalOutcome.PARTIAL,
        duration_ms=1,
        usage=UsageCounts(),
        candidates=CandidateCounts(),
        coverage=CoverageCounts(),
        reason="deadline_unavailable",
    )
    assert trace.reason == "deadline_unavailable"

    finished = ExecutionTelemetryRecorder(
        identity=_identity(), versions=_versions(), sink=MemoryTelemetrySink()
    )
    finished.finish(
        outcome=TerminalOutcome.PARTIAL,
        duration_ms=1,
        usage=UsageCounts(),
        candidates=CandidateCounts(),
        coverage=CoverageCounts(),
        reason="usage_unavailable",
    )
    with pytest.raises(RuntimeError, match="already terminal"):
        finished.record_lineage(CandidateLineage(producer="stage_1"))


def test_model_metadata_and_provider_metadata_fallbacks() -> None:
    class Model:
        model_kwargs = {"max_output_tokens": 37}

    class Response:
        response_metadata = {
            "token_usage": {
                "prompt_tokens": 8,
                "completion_tokens": 5,
                "prompt_tokens_details": {"cached_tokens": 2},
            }
        }

    assert telemetry._requested_output_tokens(Model()) == 37
    assert telemetry._provider_usage(Response()) == (8, 5, 2, True)
    assert telemetry._provider_usage(object()) == (0, 0, 0, False)
    assert telemetry._estimate_input_tokens(None) == 0


@pytest.mark.asyncio
async def test_failed_provider_result_survives_broken_telemetry_recorder(monkeypatch) -> None:
    class FailingModel:
        async def ainvoke(self, value: object) -> object:
            raise LookupError("authoritative provider failure")

    recorder = ExecutionTelemetryRecorder(
        identity=_identity(), versions=_versions(), sink=MemoryTelemetrySink()
    )
    token = bind_telemetry(recorder)
    monkeypatch.setattr(
        telemetry,
        "_safe_record_model_call",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("telemetry failed")),
    )
    try:
        with pytest.raises(LookupError, match="authoritative provider failure"):
            await observed_ainvoke(
                FailingModel(),
                "prompt",
                stage="generation",
                producer="stage_1",
            )
    finally:
        reset_telemetry(token)


def test_safe_model_recording_absorbs_closed_recorder() -> None:
    recorder = ExecutionTelemetryRecorder(
        identity=_identity(), versions=_versions(), sink=MemoryTelemetrySink()
    )
    recorder.finish(
        outcome=TerminalOutcome.PARTIAL,
        duration_ms=1,
        usage=UsageCounts(),
        candidates=CandidateCounts(),
        coverage=CoverageCounts(),
        reason="usage_unavailable",
    )
    telemetry._safe_record_model_call(
        recorder,
        ModelCallTelemetry(
            stage="generation",
            producer="stage_1",
            outcome=StageOutcome.COMPLETE,
            duration_ms=1,
            deadline_ms=1,
            usage=UsageCounts(),
        ),
    )
