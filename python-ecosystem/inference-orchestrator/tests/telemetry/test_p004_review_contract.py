from __future__ import annotations

from decimal import Decimal

import pytest

from model.dtos import ReviewRequestDto
import service.review.review_service as review_module
from service.review.orchestrator.orchestrator import MultiStageReviewOrchestrator
from service.review.review_service import ReviewService
from service.review.telemetry import (
    CandidateCounts,
    CoverageCounts,
    ExecutionIdentity,
    ExecutionTelemetryRecorder,
    MemoryTelemetrySink,
    ModelPricing,
    StageOutcome,
    TerminalOutcome,
    UsageCounts,
    VersionAttribution,
    _provider_cost_microunits,
    bind_telemetry,
    observed_ainvoke,
    reset_telemetry,
)


def _recorder(*, pricing: ModelPricing | None = None) -> ExecutionTelemetryRecorder:
    return ExecutionTelemetryRecorder(
        identity=ExecutionIdentity(
            execution_id="execution-p004",
            base_revision="a" * 40,
            head_revision="b" * 40,
        ),
        versions=VersionAttribution(
            provider="scripted",
            model="fixture-v1",
            prompt_version="prompt-sha256-" + "1" * 64,
            rules_version="rules-sha256-" + "2" * 64,
            policy_version="legacy-review-v1",
            index_version="rag-commit-" + "c" * 40,
        ),
        sink=MemoryTelemetrySink(),
        model_pricing=pricing,
    )


@pytest.mark.parametrize(
    "index_version",
    ["stale-index-v1", "rag-commit-" + "A" * 40, "rag-commit-short"],
)
def test_index_attribution_rejects_non_exact_active_versions(
    index_version: str,
) -> None:
    with pytest.raises(ValueError, match="index_version"):
        VersionAttribution(
            provider="scripted",
            model="fixture-v1",
            prompt_version="prompt-sha256-" + "1" * 64,
            rules_version="rules-sha256-" + "2" * 64,
            policy_version="legacy-review-v1",
            index_version=index_version,
        )


def test_python_snapshot_is_provisional_and_cannot_emit_the_pipeline_terminal() -> None:
    recorder = _recorder()
    recorder.record_stage(
        name="generation",
        producer="stage_1",
        outcome=StageOutcome.COMPLETE,
        duration_ms=1,
        coverage=CoverageCounts(inventory=1, represented=1, unrepresented=0),
    )

    trace = recorder.provisional_snapshot(
        outcome=TerminalOutcome.COMPLETE,
        duration_ms=2,
        usage=UsageCounts(),
        candidates=CandidateCounts(),
        coverage=CoverageCounts(inventory=1, represented=1, unrepresented=0),
    )

    assert trace.outcome is TerminalOutcome.COMPLETE
    assert recorder.sink.traces == []
    assert recorder.sink.metrics == []


def test_complete_terminal_rejects_missing_java_persistence_and_delivery_stages() -> None:
    recorder = _recorder()
    recorder.record_stage(
        name="generation",
        producer="stage_1",
        outcome=StageOutcome.COMPLETE,
        duration_ms=1,
    )

    with pytest.raises(ValueError, match="required pipeline stages"):
        recorder.finish(
            outcome=TerminalOutcome.COMPLETE,
            duration_ms=2,
            usage=UsageCounts(),
            candidates=CandidateCounts(),
            coverage=CoverageCounts(),
        )


def test_active_prompt_and_rule_versions_are_derived_from_actual_configuration() -> None:
    base = dict(
        projectId=1,
        projectVcsWorkspace="vcs",
        projectVcsRepoSlug="repo",
        projectWorkspace="workspace",
        projectNamespace="namespace",
        aiProvider="scripted",
        aiModel="fixture-v1",
        aiApiKey="secret",
    )
    first = ReviewRequestDto(
        **base,
        projectRules='[{"key":"one","enabled":true}]',
        promptVersion="request-supplied-prompt-label",
        rulesVersion="request-supplied-rules-label",
    )
    second = ReviewRequestDto(**base, projectRules='[{"key":"two","enabled":true}]')

    first_prompt, first_rules = ReviewService._active_configuration_versions(first)
    second_prompt, second_rules = ReviewService._active_configuration_versions(second)

    assert first_prompt.startswith("prompt-sha256-")
    assert first_prompt == second_prompt
    assert first_rules.startswith("rules-sha256-")
    assert first_rules != second_rules
    assert first_prompt != first.promptVersion
    assert first_rules != first.rulesVersion


def test_invalid_rules_are_attributed_by_content_without_leaking_the_value() -> None:
    request = ReviewRequestDto(
        projectId=1,
        projectVcsWorkspace="vcs",
        projectVcsRepoSlug="repo",
        projectWorkspace="workspace",
        projectNamespace="namespace",
        aiProvider="scripted",
        aiModel="fixture-v1",
        aiApiKey="secret",
        projectRules="not-json-customer-rules",
    )

    _prompt, rules = ReviewService._active_configuration_versions(request)

    assert rules.startswith("rules-sha256-")
    assert "not-json-customer-rules" not in rules


def test_prompt_inspection_failure_disables_only_observational_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = ReviewRequestDto(
        projectId=1,
        projectVcsWorkspace="vcs",
        projectVcsRepoSlug="repo",
        projectWorkspace="workspace",
        projectNamespace="namespace",
        aiProvider="scripted",
        aiModel="fixture-v1",
        aiApiKey="secret",
        executionId="execution-p004",
        baseRevision="a" * 40,
        headRevision="b" * 40,
        indexVersion="rag-commit-" + "c" * 40,
    )

    def unavailable_source(_value):
        raise OSError("source unavailable")

    monkeypatch.setattr(review_module.inspect, "getsource", unavailable_source)

    recorder, sink = ReviewService._create_telemetry_recorder(request)

    assert recorder is None
    assert sink.traces == []
    assert sink.metrics == []


def test_model_pricing_computes_non_missing_microunit_estimate() -> None:
    pricing = ModelPricing(
        input_price_per_million=Decimal("2.5"),
        output_price_per_million=Decimal("10"),
    )
    assert pricing.estimate_microunits(input_tokens=100, output_tokens=20) == 450


def test_model_pricing_rejects_missing_invalid_and_negative_prices() -> None:
    assert ModelPricing.from_values(None, "1") is None
    assert ModelPricing.from_values("1", None) is None
    assert ModelPricing.from_values("not-a-price", "1") is None
    assert ModelPricing.from_values("1", "not-a-price") is None
    assert ModelPricing.from_values("-1", "1") is None
    assert ModelPricing.from_values("1", "Infinity") is None
    assert ModelPricing.from_values("0", "0") == ModelPricing(
        input_price_per_million=Decimal("0"),
        output_price_per_million=Decimal("0"),
    )


@pytest.mark.asyncio
async def test_real_model_observation_uses_active_pricing_with_provider_usage() -> None:
    class Response:
        usage_metadata = {"input_tokens": 100, "output_tokens": 20}

    class Model:
        async def ainvoke(self, _value):
            return Response()

    recorder = _recorder(
        pricing=ModelPricing(
            input_price_per_million=Decimal("2.5"),
            output_price_per_million=Decimal("10"),
        )
    )
    token = bind_telemetry(recorder)
    try:
        await observed_ainvoke(
            Model(), "private prompt", stage="generation", producer="stage_1"
        )
    finally:
        reset_telemetry(token)

    usage = recorder.model_calls[-1].usage
    assert usage.estimated_cost_microunits == 450
    assert usage.cost_estimate_missing_calls == 0


@pytest.mark.asyncio
async def test_provider_reported_cost_takes_precedence_over_configured_estimate() -> None:
    class Response:
        usage_metadata = {"input_tokens": 100, "output_tokens": 20}
        response_metadata = {"cost": "0.00125"}

    class Model:
        async def ainvoke(self, _value):
            return Response()

    recorder = _recorder(
        pricing=ModelPricing(
            input_price_per_million=Decimal("999"),
            output_price_per_million=Decimal("999"),
        )
    )
    token = bind_telemetry(recorder)
    try:
        await observed_ainvoke(
            Model(), "private prompt", stage="generation", producer="stage_1"
        )
    finally:
        reset_telemetry(token)

    usage = recorder.model_calls[-1].usage
    assert usage.estimated_cost_microunits == 1250
    assert usage.cost_estimate_missing_calls == 0


def test_provider_cost_reader_fails_closed_for_hostile_or_invalid_metadata() -> None:
    class ExplosiveMetadata:
        @property
        def response_metadata(self):
            raise RuntimeError("provider metadata unavailable")

    class InvalidMetadata:
        response_metadata = {
            "cost": None,
            "total_cost": True,
            "estimated_cost": "not-a-number",
            "token_usage": {
                "cost": "NaN",
                "total_cost": "-1",
                "estimated_cost": None,
            },
            "usage": "not-a-mapping",
        }

    assert _provider_cost_microunits(ExplosiveMetadata()) is None
    assert _provider_cost_microunits(InvalidMetadata()) is None


def test_latest_coverage_ignores_empty_stage_inventories() -> None:
    recorder = _recorder()
    assert recorder.latest_coverage is None

    recorder.record_stage(
        name="planning",
        producer="stage_0",
        outcome=StageOutcome.COMPLETE,
        duration_ms=1,
        coverage=CoverageCounts(),
    )

    assert recorder.latest_coverage is None


def test_planner_skips_are_not_counted_as_represented_hunks() -> None:
    plan = type(
        "Plan",
        (),
        {
            "file_groups": [
                type(
                    "Group",
                    (),
                    {"files": [type("Reviewed", (), {"path": "reviewed.py"})()]},
                )()
            ],
            "files_to_skip": [type("Skipped", (), {"path": "skipped.py"})()],
        },
    )()
    assert MultiStageReviewOrchestrator._planned_paths(plan) == {"reviewed.py"}
