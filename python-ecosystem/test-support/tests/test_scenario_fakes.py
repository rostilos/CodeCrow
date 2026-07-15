from __future__ import annotations

import asyncio
import math
import sys
from pathlib import Path

import pytest


TEST_SUPPORT_ROOT = Path(__file__).resolve().parents[1]
if str(TEST_SUPPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_SUPPORT_ROOT))

from codecrow_test_harness.fakes import (
    ContentAddressedEmbeddingFake,
    ScriptedBoundaryFake,
    ScriptedEmbeddingFake,
    ScriptedLlmFake,
)
from codecrow_test_harness.ledger import ExternalCallLedger
from codecrow_test_harness.scenario import (
    ScenarioContractError,
    ScenarioStep,
    ScriptedScenario,
    SimulatedRateLimit,
    SimulatedRetryableError,
)


def _scenario(*steps: ScenarioStep) -> ScriptedScenario:
    return ScriptedScenario("p0-03-test", steps)


def test_scenario_document_round_trip_replay_and_consumption() -> None:
    steps = (
        ScenarioStep("provider.call", 1, "response", payload={"ok": True}),
        ScenarioStep(
            "provider.call",
            2,
            "page",
            payload=[1, 2],
            usage={"input_tokens": 3},
            chunks=("a", "b"),
            retry_after_seconds=1.5,
            next_cursor="next",
            duplicate_count=2,
        ),
    )
    scenario = _scenario(*steps)
    document = scenario.to_document()
    assert document["schema_version"] == "1.0"
    restored = ScriptedScenario.from_document(document)
    assert restored.to_document() == document
    assert restored.take("provider.call") == steps[0]
    assert restored.remaining == (steps[1],)
    with pytest.raises(ScenarioContractError, match="1 unconsumed"):
        restored.assert_consumed()
    assert restored.take("provider.call") == steps[1]
    restored.assert_consumed()
    replay = restored.replay()
    assert replay.take("provider.call") == steps[0]
    with pytest.raises(ScenarioContractError, match="no step"):
        replay.take("different.operation")
    before = replay.remaining
    with pytest.raises(ScenarioContractError, match="ledger operation"):
        replay.take("Invalid Operation")
    assert replay.remaining == before


@pytest.mark.parametrize(
    "step,error",
    [
        (lambda: ScenarioStep("", 1, "response"), "operation"),
        (lambda: ScenarioStep("   ", 1, "response"), "operation"),
        (lambda: ScenarioStep(" op", 1, "response"), "whitespace"),
        (lambda: ScenarioStep("op ", 1, "response"), "whitespace"),
        (lambda: ScenarioStep("op\nnext", 1, "response"), "control"),
        (lambda: ScenarioStep("op\x7fnext", 1, "response"), "control"),
        (lambda: ScenarioStep("op\u00a0next", 1, "response"), "ledger operation"),
        (lambda: ScenarioStep("\ufeffop", 1, "response"), "ledger operation"),
        (lambda: ScenarioStep("Upper", 1, "response"), "ledger operation"),
        (lambda: ScenarioStep("ordinary space", 1, "response"), "ledger operation"),
        (lambda: ScenarioStep("op", 0, "response"), "ordinal"),
        (lambda: ScenarioStep("op", True, "response"), "ordinal"),
        (lambda: ScenarioStep("op", 1, "unknown"), "unsupported"),
        (lambda: ScenarioStep("op", 1, "response", retry_after_seconds=-1), "delay"),
        (lambda: ScenarioStep("op", 1, "response", retry_after_seconds=True), "delay"),
        (lambda: ScenarioStep("op", 1, "response", retry_after_seconds=math.inf), "delay"),
        (lambda: ScenarioStep("op", 1, "response", retry_after_seconds=math.nan), "delay"),
        (lambda: ScenarioStep("op", 1, "duplicate", duplicate_count=0), "duplicate"),
        (lambda: ScenarioStep("op", 1, "duplicate", duplicate_count=True), "duplicate"),
        (lambda: ScenarioStep("op", 1, "response", usage={"tokens": -1}), "usage"),
        (lambda: ScenarioStep("op", 1, "response", chunks=[]), "chunks"),
        (lambda: ScenarioStep("op", 1, "response", next_cursor=1), "cursor"),
    ],
)
def test_scenario_step_validation(step: object, error: str) -> None:
    with pytest.raises(ScenarioContractError, match=error):
        step()  # type: ignore[operator]


def test_scenario_validation_rejects_bad_envelopes_and_duplicate_slots() -> None:
    with pytest.raises(ScenarioContractError, match="schema version"):
        ScriptedScenario.from_document({"schema_version": "2", "steps": []})
    with pytest.raises(ScenarioContractError, match="steps must be a list"):
        ScriptedScenario.from_document({"schema_version": "1.0", "steps": {}})
    with pytest.raises(ScenarioContractError, match="scenario ID"):
        ScriptedScenario.from_document(
            {"schema_version": "1.0", "scenario_id": "", "steps": []}
        )
    with pytest.raises(ScenarioContractError, match="scenario ID"):
        ScriptedScenario("   ", ())
    with pytest.raises(ScenarioContractError, match="whitespace"):
        ScriptedScenario(" padded", ())
    with pytest.raises(ScenarioContractError, match="control"):
        ScriptedScenario("line\nbreak", ())
    duplicate = ScenarioStep("op", 1, "response")
    with pytest.raises(ScenarioContractError, match="duplicate operation"):
        ScriptedScenario("duplicate", (duplicate, duplicate))
    with pytest.raises(ScenarioContractError, match="contiguous"):
        ScriptedScenario("gap", (ScenarioStep("op", 2, "response"),))
    with pytest.raises(ScenarioContractError, match="tuple"):
        ScriptedScenario("wrong-steps", [])  # type: ignore[arg-type]
    with pytest.raises(ScenarioContractError, match="document"):
        ScriptedScenario.from_document([])  # type: ignore[arg-type]
    with pytest.raises(ScenarioContractError, match="step must be an object"):
        ScriptedScenario.from_document(
            {"schema_version": "1.0", "scenario_id": "bad-step", "steps": [1]}
        )
    with pytest.raises(ScenarioContractError, match="unknown fields"):
        ScriptedScenario.from_document(
            {
                "schema_version": "1.0",
                "scenario_id": "unknown-envelope",
                "steps": [],
                "unexpected": True,
            }
        )
    with pytest.raises(ScenarioContractError, match="unknown fields"):
        ScriptedScenario.from_document(
            {
                "schema_version": "1.0",
                "scenario_id": "unknown-step",
                "steps": [
                    {
                        "operation": "op",
                        "call": 1,
                        "kind": "response",
                        "unexpected": True,
                    }
                ],
            }
        )
    with pytest.raises(ScenarioContractError, match="usage must be an object"):
        ScriptedScenario.from_document(
            {
                "schema_version": "1.0",
                "scenario_id": "bad-usage",
                "steps": [
                    {"operation": "op", "call": 1, "kind": "response", "usage": []}
                ],
            }
        )
    with pytest.raises(ScenarioContractError, match="chunks must be a list"):
        ScriptedScenario.from_document(
            {
                "schema_version": "1.0",
                "scenario_id": "bad-chunks",
                "steps": [
                    {"operation": "op", "call": 1, "kind": "stream", "chunks": {}}
                ],
            }
        )


def test_every_fault_and_response_kind_resolves_deterministically() -> None:
    ordinary = ScenarioStep("op", 1, "response", payload="ok").resolve()
    structured = ScenarioStep("op", 1, "structured", payload={"ok": True}).resolve()
    stream = ScenarioStep("op", 1, "stream", chunks=("a", "b")).resolve()
    malformed = ScenarioStep("op", 1, "malformed", payload="not-json").resolve()
    overage = ScenarioStep("op", 1, "overage", usage={"output_tokens": 12}).resolve()
    page = ScenarioStep("op", 1, "page", payload=[1], next_cursor="p2").resolve()
    duplicate = ScenarioStep("op", 1, "duplicate", duplicate_count=2).resolve()
    assert ordinary.payload == "ok"
    assert structured.kind == "structured"
    assert stream.chunks == ("a", "b")
    assert malformed.payload == "not-json"
    assert overage.overage is True
    assert page.next_cursor == "p2"
    assert duplicate.duplicate_count == 2
    with pytest.raises(SimulatedRateLimit) as rate_limit:
        ScenarioStep("op", 1, "rate_limit", retry_after_seconds=2.5).resolve()
    assert rate_limit.value.retry_after_seconds == 2.5
    with pytest.raises(TimeoutError, match="simulated"):
        ScenarioStep("op", 1, "timeout").resolve()
    with pytest.raises(asyncio.CancelledError, match="simulated"):
        ScenarioStep("op", 1, "cancellation").resolve()
    with pytest.raises(SimulatedRetryableError, match="simulated"):
        ScenarioStep("op", 1, "retryable").resolve()


def test_scripted_boundary_records_success_async_and_failure_without_payloads() -> None:
    ledger = ExternalCallLedger()
    fake = ScriptedBoundaryFake(
        boundary="jira",
        target="fake-jira:12345",
        scenario=_scenario(
            ScenarioStep("jira.page", 1, "page", payload={"secret": "not-ledgered"}),
            ScenarioStep("jira.page", 2, "rate_limit", retry_after_seconds=1),
            ScenarioStep("jira.async", 1, "duplicate", duplicate_count=2),
        ),
        ledger=ledger,
    )
    assert fake.call("jira.page").kind == "page"
    with pytest.raises(SimulatedRateLimit):
        fake.call("jira.page")
    assert asyncio.run(fake.acall("jira.async")).duplicate_count == 2
    assert [entry.outcome for entry in ledger.entries] == ["page", "rate_limit", "duplicate"]
    assert "secret" not in str(ledger.to_document())
    with pytest.raises(ValueError, match="must not be empty"):
        ScriptedBoundaryFake(
            boundary="",
            target="fake:1",
            scenario=_scenario(),
            ledger=ledger,
        )


def test_llm_fake_matches_sync_async_structured_stream_bind_and_tool_ports() -> None:
    ledger = ExternalCallLedger()
    fake = ScriptedLlmFake(
        ledger=ledger,
        scenario=_scenario(
            ScenarioStep("llm.invoke", 1, "structured", payload={"plan": []}),
            ScenarioStep("llm.ainvoke", 1, "response", payload="async", usage={"output": 1}),
            ScenarioStep("llm.stream", 1, "stream", chunks=("a", "b")),
            ScenarioStep("llm.astream", 1, "stream", chunks=("c", "d")),
        ),
    )
    assert fake.bind(max_tokens=10) is fake
    assert fake.bound_options == {"max_tokens": 10}
    tools = [object()]
    assert fake.bind_tools(tools, tool_choice="auto") is fake
    assert fake.bound_tools == tuple(tools)
    assert fake.bound_options == {"tool_choice": "auto"}
    schema = {"type": "object"}
    assert fake.with_structured_output(schema, method="json") is fake
    assert fake.output_schema is schema
    assert fake.invoke("prompt") == {"plan": []}
    assert asyncio.run(fake.ainvoke("prompt")) == "async"
    assert fake.last_usage == {"output": 1}
    assert list(fake.stream("prompt")) == ["a", "b"]
    assert asyncio.run(_collect(fake.astream("prompt"))) == ["c", "d"]

    bad_sync = ScriptedLlmFake(
        ledger=ledger,
        scenario=_scenario(ScenarioStep("llm.stream", 1, "response", payload="not-stream")),
    )
    with pytest.raises(ScenarioContractError, match="requires a stream"):
        list(bad_sync.stream("prompt"))
    bad_async = ScriptedLlmFake(
        ledger=ledger,
        scenario=_scenario(ScenarioStep("llm.astream", 1, "response", payload="not-stream")),
    )
    with pytest.raises(ScenarioContractError, match="requires a stream"):
        asyncio.run(_collect(bad_async.astream("prompt")))


async def _collect(values: object) -> list[object]:
    return [value async for value in values]  # type: ignore[attr-defined]


def test_embedding_fakes_validate_dimension_batch_and_content_identity() -> None:
    ledger = ExternalCallLedger()
    vector = [0.1, 0.2]
    fake = ScriptedEmbeddingFake(
        ledger=ledger,
        dimension=2,
        scenario=_scenario(
            ScenarioStep("embedding.query", 1, "response", payload=vector),
            ScenarioStep("embedding.query", 2, "response", payload=vector),
            ScenarioStep("embedding.text", 1, "response", payload=vector),
            ScenarioStep("embedding.batch", 1, "response", payload=[vector, vector]),
            ScenarioStep("embedding.batch", 2, "response", payload=[vector]),
            ScenarioStep("embedding.batch", 3, "response", payload=[[0.1]]),
            ScenarioStep("embedding.query", 3, "response", payload=[0.1]),
        ),
    )
    assert fake.get_query_embedding("a") == vector
    assert fake.embed_query("a") == vector
    assert fake.get_text_embedding("a") == vector
    assert fake.get_text_embedding_batch(["a", "b"]) == [vector, vector]
    assert fake.embed_documents(["a"]) == [vector]
    with pytest.raises(ScenarioContractError, match="dimension"):
        fake.get_text_embedding_batch(["a"])
    with pytest.raises(ScenarioContractError, match="dimension"):
        fake.get_query_embedding("bad")
    size_mismatch = ScriptedEmbeddingFake(
        ledger=ledger,
        dimension=2,
        scenario=_scenario(
            ScenarioStep("embedding.batch", 1, "response", payload=[vector])
        ),
    )
    with pytest.raises(ScenarioContractError, match="batch size"):
        size_mismatch.get_text_embedding_batch(["a", "b"])
    with pytest.raises(ValueError, match="positive"):
        ScriptedEmbeddingFake(ledger=ledger, dimension=0, scenario=_scenario())

    content = ContentAddressedEmbeddingFake(ledger=ledger, dimension=40)
    first = content.embed_query("same")
    assert first == content.get_query_embedding("same")
    assert first != content.get_text_embedding("different")
    assert content.embed_documents(["a", "b"]) == content.get_text_embedding_batch(["a", "b"])
    assert len(first) == 40
    with pytest.raises(ValueError, match="positive"):
        ContentAddressedEmbeddingFake(ledger=ledger, dimension=0)


@pytest.mark.parametrize(
    ("method", "invalid"),
    (
        ("get_query_embedding", "   "),
        ("get_query_embedding", None),
        ("get_text_embedding", ""),
        ("get_text_embedding", 7),
    ),
)
def test_scripted_embedding_rejects_invalid_single_text_without_side_effects(
    method: str,
    invalid: object,
) -> None:
    ledger = ExternalCallLedger()
    operation = (
        "embedding.query" if method == "get_query_embedding" else "embedding.text"
    )
    scenario = _scenario(
        ScenarioStep(operation, 1, "response", payload=[0.1, 0.2]),
    )
    fake = ScriptedEmbeddingFake(ledger=ledger, dimension=2, scenario=scenario)

    expected_error = ValueError if isinstance(invalid, str) else TypeError
    with pytest.raises(expected_error, match="embedding text|empty text"):
        getattr(fake, method)(invalid)

    assert scenario.remaining == (
        ScenarioStep(operation, 1, "response", payload=[0.1, 0.2]),
    )
    assert ledger.entries == ()


@pytest.mark.parametrize("invalid", (["valid", "  "], ["valid", None]))
def test_scripted_embedding_rejects_invalid_batch_element_without_side_effects(
    invalid: list[object],
) -> None:
    ledger = ExternalCallLedger()
    step = ScenarioStep(
        "embedding.batch",
        1,
        "response",
        payload=[[0.1, 0.2], [0.3, 0.4]],
    )
    scenario = _scenario(step)
    fake = ScriptedEmbeddingFake(ledger=ledger, dimension=2, scenario=scenario)

    expected_error = ValueError if isinstance(invalid[1], str) else TypeError
    with pytest.raises(expected_error, match="embedding text|empty text"):
        fake.get_text_embedding_batch(invalid)  # type: ignore[arg-type]

    assert scenario.remaining == (step,)
    assert ledger.entries == ()


def test_embedding_empty_batch_returns_without_scenario_or_ledger_mutation() -> None:
    ledger = ExternalCallLedger()
    step = ScenarioStep("embedding.batch", 1, "response", payload=[])
    scenario = _scenario(step)
    fake = ScriptedEmbeddingFake(ledger=ledger, dimension=2, scenario=scenario)

    assert fake.get_text_embedding_batch([]) == []
    assert fake.embed_documents(()) == []
    assert scenario.remaining == (step,)
    assert ledger.entries == ()


def test_content_addressed_embedding_validates_before_ledger_mutation() -> None:
    ledger = ExternalCallLedger()
    fake = ContentAddressedEmbeddingFake(ledger=ledger, dimension=2)

    with pytest.raises(ValueError, match="empty text"):
        fake.embed_query(" ")
    with pytest.raises(TypeError, match="embedding text"):
        fake.get_text_embedding(None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="empty text"):
        fake.embed_documents(["valid", ""])
    assert fake.embed_documents([]) == []
    assert ledger.entries == ()
