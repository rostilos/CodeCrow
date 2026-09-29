"""Offline architecture checks for graph-first review; no quality claims."""
import json
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from service.review import review_service, tool_conversation
from service.review.execution_mode import review_execution_mode
from service.review.mcp_review import change_inventory, plan_batches, TARGET_BATCH_DIFF_CHARACTERS
from service.review.review_service import ReviewService, _parts
from service.review.verification_tools import VerificationTools
from tests.test_review_pipeline import change, request, Model, observed_evidence


def test_mode_resolution_and_durable_request_roundtrip(monkeypatch):
    monkeypatch.delenv("REVIEW_EXECUTION_MODE", raising=False)
    assert review_execution_mode(request())[0] == "pipeline"
    monkeypatch.setenv("REVIEW_EXECUTION_MODE", "mcp_only")
    assert review_execution_mode(request())[0] == "mcp_only"
    assert review_execution_mode(request(reviewExecutionMode="pipeline"))[0] == "pipeline"
    req = request(reviewExecutionMode="mcp_only")
    restored = type(req).model_validate_json(req.model_dump_json())
    assert review_execution_mode(restored)[0] == "mcp_only"
    mode, diagnostics = review_execution_mode(request(reviewExecutionMode={"invalid": True}))
    assert mode == "mcp_only" and diagnostics
    monkeypatch.setenv("REVIEW_EXECUTION_MODE", "unknown")
    assert review_execution_mode(request())[0] == "pipeline"


def test_300_file_plan_preserves_every_hunk_without_full_diff_context():
    parts = [SimpleNamespace(id=f"p-{i}", path=f"package-{i % 10}/file-{i}.py", side="proposed",
                             anchors={1: "changed"}, diff="secret-source-marker" + "x" * 2000) for i in range(300)]
    inventory = change_inventory(parts)
    assert len(inventory) == 300
    assert "secret-source-marker" not in json.dumps(inventory)
    batches, diagnostics = plan_batches(parts, [{"groups": [{"paths": [part.path for part in parts], "focus": "cross-file behavior"}]}])
    assert not diagnostics
    assert Counter(part.id for batch in batches for part in batch.parts) == Counter(part.id for part in parts)
    assert len(batches) > 1
    assert all(sum(len(part.diff) for part in batch.parts) <= TARGET_BATCH_DIFF_CHARACTERS for batch in batches)
    assert all(part.diff.startswith("secret-source-marker") for batch in batches for part in batch.parts)


def test_malformed_plan_recovers_paths_and_never_splits_a_complete_hunk():
    parts = _parts(change("first.py") + change("second.py", after="x" * 50000))[0]
    batches, diagnostics = plan_batches(parts, [{"groups": [None, {"paths": ["first.py", "first.py", "invented.py"]}]}])
    assert diagnostics
    assert [part.id for batch in batches for part in batch.parts] == [part.id for part in parts]
    assert len(batches[-1].parts[0].diff) > TARGET_BATCH_DIFF_CHARACTERS


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, True])
async def test_explained_fenced_plan_keeps_cross_file_groups_without_fallback(native):
    parts = _parts(change("producer.py") + change("consumer.py"))[0]

    class Planner:
        calls = 0

        def bind_tools(self, schemas):
            return self

        async def ainvoke(self, messages, **kwargs):
            self.calls += 1
            return SimpleNamespace(content=(
                "The producer and consumer share one contract.\n\n```json\n"
                + json.dumps({"groups": [{"paths": [part.path for part in parts], "focus": "Shared contract"}]})
                + "\n```\nEvery changed path is assigned once."
            ), tool_calls=[])

    model = Planner()
    if not native:
        model.bind_tools = None
    run = await tool_conversation.converse(
        llm=model, request=request(), tools=VerificationTools(rag_client=None, binding={}, parts=parts),
        system="Plan", payload={"changedFiles": change_inventory(parts)}, stage="plan", batch_ids=[],
    )
    batches, diagnostics = plan_batches(parts, run.outputs)
    assert run.complete and model.calls == 1
    assert not diagnostics
    assert len(batches) == 1
    assert batches[0].focus == "Shared contract"
    assert list(batches[0].parts) == parts


@pytest.mark.asyncio
async def test_300_file_review_fetches_focused_diffs_and_completes_every_hunk(monkeypatch):
    marker = "source-body-only-in-tool-results-" + "x" * 1900
    raw = "".join(change(f"module/file-{index}.py", after=marker) for index in range(300))
    req = request(rawDiff=raw, reviewExecutionMode="mcp_only")
    requested = []

    def handler(payload):
        if "ownedParts" in payload:
            assert marker not in json.dumps(payload["ownedParts"])
            ids = [part["id"] for part in payload["ownedParts"]]
            read = observed_evidence(payload, "getReviewDiff")
            if not read:
                requested.append(ids)
                return {"toolCalls": [{"name": "getReviewDiff", "arguments": {"partIds": ids}}]}
            assert len(read["result"]["parts"]) < 300
            return {"reviewedHunkIds": ids, "findings": [],
                    "summary": {"behaviorChanges": ["Literal value changed"], "contracts": []}}
        if "batchSummaries" in payload:
            assert marker not in json.dumps(payload)
            return {"findings": [], "investigations": []}
        assert marker not in json.dumps(payload)
        return {"groups": [{"paths": [item["path"] for item in payload["changedFiles"]]}]}

    class JsonModel(Model):
        bind_tools = None
    model = JsonModel(handler)
    service = ReviewService(SimpleNamespace(enabled=False))
    monkeypatch.setattr(review_service.LLMFactory, "create_llm", lambda *args, **kwargs: model)
    result = (await service.process_review_request(req))["result"]
    assert result["status"] == "complete", result
    assert len(result["reviewedHunkIds"]) == 300
    assert Counter(part_id for ids in requested for part_id in ids) == Counter(result["reviewedHunkIds"])
    assert len(requested) > 1 and max(map(len, requested)) < 300


@pytest.mark.asyncio
async def test_change_inventory_pages_metadata_and_distinguishes_incremental_context():
    active = _parts(change("new.py"))[0]
    old = _parts(change("old.py"))[0]
    tools = VerificationTools(rag_client=None, binding={}, parts=active, context_parts=old)
    first = await tools.call("listReviewChanges", {"maxFiles": 1})
    following = await tools.call("listReviewChanges", {"maxFiles": 1, "cursor": first["nextCursor"]})
    files = first["files"] + following["files"]
    assert {file["path"] for file in files} == {"new.py", "old.py"}
    assert [part["active"] for file in files for part in file["parts"]] == [True, False]
    assert "diff" not in json.dumps(files) or "diffCharacters" in json.dumps(files)
    assert all("diff" not in part for file in files for part in file["parts"])
    assert following["nextCursor"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("graph_available", [False, True])
async def test_tool_only_review_discovers_then_independently_verifies_without_preloads(monkeypatch, native, graph_available):
    from service.review import agent_calls
    monkeypatch.setattr(agent_calls, "ToolMessage", lambda **value: SimpleNamespace(type="tool", **value))
    stages = []
    req = request(reviewExecutionMode="mcp_only", rawDiff=change(after="return 1 / 0"))
    part = _parts(req.rawDiff)[0][0]

    def handler(payload):
        if "ownedParts" in payload:
            stages.append("analysis")
            assert "diff" not in payload["ownedParts"][0]
            read = observed_evidence(payload, "getReviewDiff")
            if not read:
                return {"toolCalls": [{"name": "getReviewDiff", "arguments": {"partIds": [part.id]}}]}
            return {"reviewedHunkIds": [part.id], "findings": [{
                "partId": part.id, "file": part.path, "line": 1,
                "title": "Division by zero", "reason": "The changed expression raises instead of returning.",
                "evidenceIds": [read["id"]],
            }], "summary": {"contracts": ["always raises"], "unresolvedQuestions": []}}
        if "candidates" in payload:
            stages.append("verification")
            assert payload["evidence"] == []
            assert "ownerSource" not in payload
            read = observed_evidence(payload, "getReviewDiff")
            if not read:
                return {"toolCalls": [{"name": "getReviewDiff", "arguments": {"partIds": [part.id]}}]}
            return {"decisions": [{"candidateId": payload["candidates"][0]["candidateId"],
                                    "verdict": "keep", "reason": "Exact changed source divides by zero.",
                                    "evidenceIds": [read["id"]]}], "findings": [], "investigations": []}
        stages.append("planning")
        assert "return 1 / 0" not in json.dumps(payload)
        if not payload.get("toolObservations"):
            return {"toolCalls": [{"name": "queryCodeGraph", "arguments": {"pattern": "file_summary", "target": part.path}}]}
        return {"groups": [{"paths": [part.path], "focus": "return contract"}]}

    class JsonModel(Model):
        bind_tools = None
    model = Model(handler) if native else JsonModel(handler)
    rag = SimpleNamespace(enabled=graph_available, query_review_graph=AsyncMock(return_value={"status": "ready", "results": []}))
    service = ReviewService(rag)
    monkeypatch.setattr(service, "_prepare_context", AsyncMock(return_value=({"review_collection_target": "sealed"} if graph_available else {}, [])))
    monkeypatch.setattr(review_service.LLMFactory, "create_llm", lambda *args, **kwargs: model)
    monkeypatch.setattr(review_service, "ReviewPlanner", lambda *args: pytest.fail("automatic graph planning must not run"))
    monkeypatch.setattr(service, "_owner_source", AsyncMock(side_effect=AssertionError("owner source must not preload")))
    result = (await service.process_review_request(req))["result"]
    assert result["status"] == "complete", result
    assert result["reviewExecutionMode"] == "mcp_only"
    assert result["graphAvailable"] is graph_available
    assert len(result["issues"]) == 1
    assert result["reviewedHunkIds"] == [part.id]
    assert stages.index("planning") < stages.index("analysis") < stages.index("verification")
    if graph_available:
        assert rag.query_review_graph.call_count == 1
        assert rag.query_review_graph.call_args.kwargs["include_source"] is False


@pytest.mark.asyncio
async def test_planner_cannot_fetch_source_even_with_unoffered_tool_request():
    class JsonModel:
        def __init__(self):
            self.turn = 0
        async def ainvoke(self, messages, **kwargs):
            self.turn += 1
            return SimpleNamespace(content=json.dumps(
                {"toolCalls": [{"name": "readReviewFile", "arguments": {"path": "private.py"}}]}
                if self.turn == 1 else {"groups": []}))
    tools = VerificationTools(rag_client=None, binding={}, parts=[])
    tools.call = AsyncMock(side_effect=AssertionError("unoffered tool executed"))
    run = await tool_conversation.converse(llm=JsonModel(), request=request(), tools=tools,
        system="Plan", payload={}, stage="plan", batch_ids=[], allowed={"queryCodeGraph"})
    assert run.complete
    tools.call.assert_not_called()
    assert next(iter(run.evidence.values()))["result"]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_changing_prose_without_evidence_does_not_fund_an_endless_conversation():
    class JsonModel:
        calls = 0
        async def ainvoke(self, messages, **kwargs):
            self.calls += 1
            return SimpleNamespace(content=json.dumps({"comment": f"Still considering it {self.calls}"}))
    model = JsonModel()
    run = await tool_conversation.converse(llm=model, request=request(),
        tools=VerificationTools(rag_client=None, binding={}, parts=[]),
        system="Analyze", payload={}, stage="analysis", batch_ids=[],
        feedback=lambda result: {"remainingHunkIds": ["unread-hunk"]})
    assert not run.complete
    assert model.calls == 2
    assert "without new evidence" in run.diagnostics[-1]


@pytest.mark.asyncio
async def test_model_interruption_preserves_candidate_checkpoint():
    part = _parts(change())[0][0]
    finding = {"partId": part.id, "file": part.path, "line": 1,
               "title": "Changed behavior", "reason": "Supported failure", "evidenceIds": []}
    class JsonModel:
        calls = 0
        async def ainvoke(self, messages, **kwargs):
            self.calls += 1
            if self.calls > 1:
                raise TimeoutError("provider interrupted")
            return SimpleNamespace(content=json.dumps({"toolCalls": [{"name": "recordReviewDecisions",
                "arguments": {"decisions": [], "findings": [finding]}}]}))
    run = await tool_conversation.converse(llm=JsonModel(), request=request(),
        tools=VerificationTools(rag_client=None, binding={}, parts=[part]),
        system="Analyze", payload={}, stage="analysis", batch_ids=[], checkpoint=True)
    assert not run.complete
    assert any(value.get("findings") == [finding] for value in run.outputs)
    assert any("completed records retained" in message for message in run.diagnostics)
