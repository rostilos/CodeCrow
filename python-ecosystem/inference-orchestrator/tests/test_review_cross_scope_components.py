"""Independent synthesis scopes do not exchange unrelated summaries or failures."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from service.review.planner import CrossBatchScope, ReviewBatch, ReviewPlan
from service.review.review_stages import review_cross_batch


def fixture_plan(edges):
    names = sorted({name for edge in edges for name in edge})
    parts = {name: SimpleNamespace(id=f"part-{name}", path=f"{name}.py", side="proposed", anchors={7: "changed"})
             for name in names}
    batches = tuple(ReviewBatch(name, (parts[name],)) for name in names)
    scopes = tuple(CrossBatchScope(f"scope-{index}", tuple(edge), ({"contract": f"dependency-{index}"},), "dependency")
                   for index, edge in enumerate(edges))
    return ReviewPlan(batches, {}, scopes, ()), parts


@pytest.mark.asyncio
async def test_disconnected_scopes_receive_only_their_summaries_candidates_and_questions(monkeypatch):
    plan, parts = fixture_plan([("a", "b"), ("c", "d")])
    summaries = [{"batchId": name, "partIds": [part.id], "paths": [part.path],
                  "summary": {"behaviorChanges": [f"behavior-{name}"], "contracts": [f"contract-{name}"],
                              "unresolvedQuestions": ["not copied from summary"]}}
                 for name, part in parts.items()]
    candidates = [{"partId": part.id, "file": part.path, "line": 7, "title": f"defect-{name}", "reason": "failure"}
                  for name, part in parts.items()]
    pending = [{"id": f"pending-{name}", "origin": name, "partIds": [part.id], "paths": [part.path]}
               for name, part in parts.items()]

    async def response(*args, **kwargs):
        payload = kwargs["payload"]
        part_id = payload["changedAnchors"][0]["partId"]
        return {"findings": [], "investigations": [{"question": "Check specific caller", "partIds": [part_id]}]}

    model = AsyncMock(side_effect=response)
    monkeypatch.setattr("service.review.review_stages.invoke_json", model)
    result = await review_cross_batch(llm=None, request=SimpleNamespace(projectRules=None), plan=plan,
                                      summaries=summaries, candidates=candidates, investigations=pending, normalize=None)
    assert model.await_count == 2
    for call, expected in zip(model.await_args_list, ({"a", "b"}, {"c", "d"})):
        payload = call.kwargs["payload"]
        assert set(call.kwargs["batch_ids"]) == expected
        assert {summary["batchId"] for summary in payload["batchSummaries"]} == expected
        assert all("unresolvedQuestions" not in summary["summary"] for summary in payload["batchSummaries"])
        assert {part["partId"] for part in payload["changedAnchors"]} == {parts[name].id for name in expected}
        assert {issue["title"] for issue in payload["existingCandidates"]} == {f"defect-{name}" for name in expected}
        assert {question["id"] for question in payload["pendingInvestigations"]} == {f"pending-{name}" for name in expected}
    assert len({item["id"] for item in result.investigations}) == 2
    assert len({item["origin"] for item in result.investigations}) == 2
    assert not result.findings


@pytest.mark.asyncio
async def test_transitively_connected_scopes_keep_complete_context_and_stable_origin(monkeypatch):
    plan, parts = fixture_plan([("a", "b"), ("c", "d"), ("b", "c")])
    model = AsyncMock(return_value={"findings": [], "investigations": [{"question": "Cross-chain contract", "partIds": ["part-d"]}]})
    monkeypatch.setattr("service.review.review_stages.invoke_json", model)
    async def run(scopes):
        selected = ReviewPlan(plan.batches, {}, scopes, ())
        return await review_cross_batch(llm=None, request=SimpleNamespace(projectRules=None), plan=selected,
                                        summaries=[], candidates=[], normalize=None)
    first = await run(plan.cross_batch_scopes)
    second = await run(tuple(reversed(plan.cross_batch_scopes)))
    assert model.await_count == 2
    assert all(set(call.kwargs["batch_ids"]) == {"a", "b", "c", "d"} for call in model.await_args_list)
    assert all(len(call.kwargs["payload"]["changedAnchors"]) == 4 for call in model.await_args_list)
    assert first.investigations[0]["id"] == second.investigations[0]["id"]


@pytest.mark.asyncio
async def test_failed_or_malformed_component_keeps_valid_sibling_results(monkeypatch):
    plan, parts = fixture_plan([("a", "b"), ("c", "d"), ("e", "f")])
    model = AsyncMock(side_effect=[OSError("provider unavailable"),
                                  {"findings": "malformed", "investigations": [{"question": "Valid surviving contract", "partIds": ["part-c"]}]},
                                  {"findings": [], "investigations": [{"question": "Separate valid contract", "paths": ["f.py"]}]}])
    monkeypatch.setattr("service.review.review_stages.invoke_json", model)
    result = await review_cross_batch(llm=None, request=SimpleNamespace(projectRules=None), plan=plan,
                                      summaries=[{"batchId": "c", "summary": "malformed optional summary"}],
                                      candidates=[], normalize=None)
    assert model.await_count == 3
    assert {tuple(item["partIds"]) for item in result.investigations} == {("part-c",), ("part-f",)}
    assert any("provider unavailable" in message for message in result.diagnostics)
    assert any("did not return" in message for message in result.diagnostics)
    assert any("summary unavailable" in message for message in result.diagnostics)
