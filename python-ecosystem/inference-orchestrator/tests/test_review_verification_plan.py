"""Offline work-partition checks; these do not measure live-model review quality."""
from collections import Counter
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from service.review.verification_cases import VerificationCase, build_cases, related_parts, source_for_case
from service.review.verification_plan import plan_verification_cases


def fixture_cases():
    """Independent defects share one owner; repeated wrapper claims cross callers."""
    parts = {
        key: SimpleNamespace(id=key, path=path, side="proposed", anchors={line: text},
                             diff="COMPLETE DIFF BODY " + key)
        for key, path, line, text in [
            ("parse", "credentials.ts", 3, "return schema.safeParse(payload);"),
            ("fallback", "credentials.ts", 7, 'refresh_token: payload.refresh_token || "refresh_token"'),
            ("caller", "webhook.ts", 10, "saveKey(parseCredentials(body));"),
        ]
    }
    owner = {"unitId": "opaque-parser-owner", "path": "credentials.ts", "name": "parseCredentials",
             "kind": "function", "startLine": 1, "endLine": 20, "source": "GRAPH SOURCE BODY"}
    caller = {"unitId": "opaque-caller-owner", "path": "webhook.ts", "name": "handleWebhook",
              "kind": "function", "startLine": 1, "endLine": 30}
    relation = {"kind": "CALLS", "source": caller, "target": owner}
    graph = {key: {"units": [caller if key == "caller" else owner], "relations": [relation],
                   "snapshotId": "private-snapshot-binding"} for key in parts}
    findings = [
        {"partId": "parse", "file": "credentials.ts", "line": 3,
         "title": "Parser result wrapper is persisted as credentials", "reason": "The caller saves the parse wrapper instead of its data.",
         "evidenceToCheck": ["Does saveKey receive the parser wrapper?"], "relatedPaths": ["webhook.ts"],
         "codeSnippet": "CANDIDATE SOURCE BODY", "batchIds": ["parser-batch"]},
        {"partId": "fallback", "file": "credentials.ts", "line": 7,
         "title": "Missing refreshed token overwrites a valid saved token", "reason": "An omitted refresh token becomes a literal placeholder.",
         "batchIds": ["parser-batch"]},
        {"partId": "caller", "file": "webhook.ts", "line": 10,
         "title": "Webhook stores safeParse wrapper instead of token fields", "reason": "saveKey receives the unwrapped parser result.",
         "batchIds": ["webhook-batch"]},
    ]
    questions = [
        {"id": "caller-wrapper", "partIds": ["caller"], "paths": ["webhook.ts"],
         "question": "Does saveKey unwrap the parser result before persistence?",
         "claim": "If not, persisting the wrapper loses token fields.", "origin": "webhook-batch"},
        {"id": "invalid-body", "partIds": ["caller"], "paths": ["webhook.ts"],
         "question": "Does invalid request data produce a controlled client error?",
         "claim": "A schema exception may escape the request handler.", "origin": "webhook-batch"},
    ]
    summaries = [{"batchId": "parser-batch", "paths": ["credentials.ts"],
                  "summary": {"contracts": ["BROAD GENERATED CONTRACT HINT"], "source": "SUMMARY SOURCE BODY"}}]
    return build_cases(findings, questions, parts, graph), parts, graph, summaries


def group(*ids, mechanism="The parse result wrapper is persisted instead of the token data",
          resolution="Unwrap the parsed token data before saveKey, or show that saveKey already unwraps it"):
    return {"caseIds": list(ids), "failureMechanism": mechanism, "sharedResolution": resolution}


def assert_work_preserved(original, planned):
    for attribute in ("findings", "investigations"):
        assert Counter(id(item) for case in original for item in getattr(case, attribute)) == Counter(
            id(item) for case in planned for item in getattr(case, attribute))
    for attribute in ("part_ids", "owner_ids", "batch_ids"):
        assert {item for case in original for item in getattr(case, attribute)} == {
            item for case in planned for item in getattr(case, attribute)}


def test_independent_failures_in_one_owner_start_as_separate_complete_work():
    cases, parts, graph, _ = fixture_cases()

    assert len(cases) == 5
    assert all(len(case.findings) + len(case.investigations) == 1 for case in cases)
    assert cases[0].owner_ids == cases[1].owner_ids == ("opaque-parser-owner",)
    assert cases[0].part_ids == cases[1].part_ids == ("fallback", "parse")
    # Shared source scope is retained; source ownership never fuses claims.
    assert related_parts(cases[0], parts, graph) == list(parts.values())
    assert related_parts(cases[1], parts, graph) == list(parts.values())
    source = {"status": "ready", "path": "credentials.ts", "side": "proposed", "startLine": 1,
              "endLine": 20, "content": "complete parser definition\n" * 20}
    for case in cases[:2]:
        assert source_for_case(related_parts(case, parts, graph), [source], owner_ids=case.owner_ids,
                               graph_context=graph) == [source]


@pytest.mark.asyncio
async def test_same_precise_failure_can_join_repeated_claims_and_checks_across_callers(monkeypatch):
    cases, parts, graph, summaries = fixture_cases()
    answer = {"groups": [group("case-1", "case-3", "case-4"), {"caseIds": ["case-2"]}, {"caseIds": ["case-5"]}]}
    model = AsyncMock(return_value=answer)
    monkeypatch.setattr("service.review.verification_plan.invoke_json", model)

    result = await plan_verification_cases(None, SimpleNamespace(), cases, parts, graph, summaries)

    assert [case.id for case in result.cases] == ["case-1", "case-2", "case-5"]
    assert [finding["title"] for finding in result.cases[0].findings] == [
        cases[0].findings[0]["title"], cases[2].findings[0]["title"]]
    assert result.cases[0].investigations == cases[3].investigations
    assert result.cases[1] is cases[1]  # Token preservation has a different correction.
    assert result.cases[2] is cases[4]  # Input validation has a different correction.
    assert_work_preserved(cases, result.cases)
    assert not result.diagnostics
    model.assert_awaited_once()
    assert model.call_args.kwargs["stage"] == "verification_planning"
    sent = model.call_args.kwargs["payload"]["cases"]
    assert all(len(case["findings"]) + len(case["investigations"]) == 1 for case in sent)
    assert sent[0]["findings"][0]["missingFacts"] == cases[0].findings[0]["evidenceToCheck"]
    assert "parseCredentials" in json.dumps(sent)
    assert "tools" not in model.call_args.kwargs


@pytest.mark.asyncio
async def test_planner_receives_claims_without_source_or_owner_wide_summary_authority(monkeypatch):
    cases, parts, graph, summaries = fixture_cases()
    model = AsyncMock(return_value={"groups": [{"caseIds": [case.id]} for case in cases]})
    monkeypatch.setattr("service.review.verification_plan.invoke_json", model)
    result = await plan_verification_cases(None, None, cases, parts, graph, summaries)

    serialized = json.dumps(model.call_args.kwargs["payload"])
    for excluded in ("SOURCE BODY", "COMPLETE DIFF BODY", "opaque-parser-owner", "private-snapshot-binding",
                     "BROAD GENERATED CONTRACT HINT", "contractHints"):
        assert excluded not in serialized
    assert_work_preserved(cases, result.cases)
    prompt = model.call_args.kwargs["system"]
    assert "not evidence" in prompt
    assert "Different defects remain separate" in prompt
    assert "host already caches source reads" in prompt


@pytest.mark.asyncio
async def test_overlap_and_malformed_groups_preserve_all_atomic_work_and_valid_siblings(monkeypatch):
    cases, parts, graph, summaries = fixture_cases()
    model = AsyncMock(return_value={"groups": [
        group("case-1", "case-3"), group("case-3", "not-a-case"),
        group("case-2", "case-5", mechanism=""),
        {"caseIds": [{"bad": "identity"}]}, {"caseIds": ["case-4"]},
    ]})
    monkeypatch.setattr("service.review.verification_plan.invoke_json", model)

    result = await plan_verification_cases(None, None, cases, parts, graph, summaries)

    assert result.cases == cases
    assert_work_preserved(cases, result.cases)
    assert any("overlaps" in message for message in result.diagnostics)
    assert any("invalid membership" in message for message in result.diagnostics)
    assert result.groups == [{"caseId": "case-4", "caseIds": ["case-4"], "failureMechanism": "", "sharedResolution": ""}]


@pytest.mark.asyncio
async def test_invalid_sibling_does_not_discard_a_valid_precise_failure_group(monkeypatch):
    cases, parts, graph, summaries = fixture_cases()
    model = AsyncMock(return_value={"groups": [group("case-1", "case-3", "case-4"),
                                              group("case-2", "case-5", resolution="")]})
    monkeypatch.setattr("service.review.verification_plan.invoke_json", model)
    result = await plan_verification_cases(None, None, cases, parts, graph, summaries)
    assert [case.id for case in result.cases] == ["case-1", "case-2", "case-5"]
    assert result.cases[1] is cases[1] and result.cases[2] is cases[4]
    assert_work_preserved(cases, result.cases)
    assert result.diagnostics


@pytest.mark.asyncio
async def test_broad_contract_only_group_is_not_the_current_merge_contract(monkeypatch):
    cases, parts, graph, summaries = fixture_cases()
    model = AsyncMock(return_value={"groups": [{"caseIds": [case.id for case in cases],
                                               "contract": "Credentials handling", "rationale": "Read the same files"}]})
    monkeypatch.setattr("service.review.verification_plan.invoke_json", model)
    result = await plan_verification_cases(None, None, cases, parts, graph, summaries)
    assert result.cases == cases
    assert_work_preserved(cases, result.cases)
    assert result.diagnostics


@pytest.mark.asyncio
async def test_group_and_member_order_do_not_change_case_ownership(monkeypatch):
    cases, parts, graph, summaries = fixture_cases()
    groups = [group("case-1", "case-3", "case-4"), group("case-2"), group("case-5")]
    model = AsyncMock(side_effect=[{"groups": groups}, {"groups": [
        {**value, "caseIds": list(reversed(value["caseIds"]))} for value in reversed(groups)]}])
    monkeypatch.setattr("service.review.verification_plan.invoke_json", model)
    first = await plan_verification_cases(None, None, cases, parts, graph, summaries)
    second = await plan_verification_cases(None, None, cases, parts, graph, summaries)
    assert first.cases == second.cases
    assert_work_preserved(cases, first.cases)


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [{}, {"groups": None}, {"groups": []}, {"groups": [None]}])
async def test_missing_plan_never_discards_original_cases(monkeypatch, response):
    cases, parts, graph, summaries = fixture_cases()
    model = AsyncMock(return_value=response)
    monkeypatch.setattr("service.review.verification_plan.invoke_json", model)
    result = await plan_verification_cases(None, None, cases, parts, graph, summaries)
    assert result.cases == cases
    assert result.diagnostics
    model.assert_awaited_once()


@pytest.mark.asyncio
async def test_unavailable_planning_falls_back_without_retrying_or_mutating_cases(monkeypatch):
    cases, parts, graph, summaries = fixture_cases()
    model = AsyncMock(side_effect=OSError("provider unavailable"))
    monkeypatch.setattr("service.review.verification_plan.invoke_json", model)
    result = await plan_verification_cases(None, None, cases, parts, graph, summaries)
    assert result.cases == cases
    assert "provider unavailable" in result.diagnostics[0]
    model.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1])
async def test_single_or_empty_work_needs_no_planning_call(monkeypatch, count):
    cases = [VerificationCase("case-1", ())][:count]
    model = AsyncMock()
    monkeypatch.setattr("service.review.verification_plan.invoke_json", model)
    result = await plan_verification_cases(None, None, cases, {}, {}, [])
    assert result.cases == cases
    assert not result.diagnostics
    model.assert_not_awaited()


@pytest.mark.asyncio
async def test_complete_claims_survive_without_length_clipping(monkeypatch):
    reason = "A relevant causal fact. " * 1000 + "The decisive final fact remains."
    cases = [VerificationCase("first", (), findings=[{"title": "Failure", "reason": reason}]),
             VerificationCase("second", (), investigations=[{"id": "question", "question": reason}])]
    model = AsyncMock(return_value={"groups": [group("first"), group("second")]})
    monkeypatch.setattr("service.review.verification_plan.invoke_json", model)
    result = await plan_verification_cases(None, None, cases, {}, {}, [])
    sent = model.call_args.kwargs["payload"]["cases"]
    assert sent[0]["findings"][0]["reason"] == reason
    assert sent[1]["investigations"][0]["question"] == reason
    assert_work_preserved(cases, result.cases)
