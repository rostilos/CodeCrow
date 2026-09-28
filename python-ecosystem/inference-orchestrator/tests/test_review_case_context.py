"""Source-backed context/publication regressions with scripted model outcomes.

These check supplied evidence and host execution, not live-model defect recall.
The public fixture revisions are documented in plans/review-regression-investigation.md.
"""
import json
import re
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from service.review.review_service import _parts
from service.review.verification_cases import build_cases, related_parts
from service.review.verifier import ReviewVerifier


FIXTURES = json.loads((Path(__file__).parent / "fixtures/review_reconciliation/source_case_inputs.json").read_text())
DUPLICATES = json.loads((Path(__file__).parent / "fixtures/review_reconciliation/reported_duplicates.json").read_text())


def request():
    return SimpleNamespace(aiProvider="openai", pullRequestId="7232",
                           prTitle="Complete workflow reminder cancellation when cancelling or rescheduling a booking",
                           prDescription="Old scheduled reminders must be cancelled during the booking lifecycle.",
                           projectRules=None, taskContext=None)


def response(**value):
    return SimpleNamespace(content=json.dumps(value))


def payload(messages):
    return next(json.loads(message[1]) for message in messages
                if isinstance(message, tuple) and message[0] == "human")


def exact_source(packet, path, start, end):
    """Decode proposed source from the public packet shape, including diff references."""
    lines = {}
    records = {entry["id"]: entry for entry in packet["evidence"]}
    for entry in records.values():
        result = entry["result"]
        values = result.get("parts", []) if entry["kind"] == "getReviewDiff" else [result]
        for value in values:
            if value.get("path") != path or value.get("side", "proposed") != "proposed":
                continue
            for reference in value.get("sourceReferences", []):
                assert reference["evidenceId"] in records
            for segment in ([value] if "content" in value else value.get("sourceSegments", [])):
                lines.update(enumerate(segment["content"].splitlines(keepends=True), segment["startLine"]))
            line = None
            for raw in value.get("diff", "").splitlines(keepends=True):
                if raw.startswith("@@"):
                    line = int(re.search(r"\+(\d+)", raw).group(1))
                elif line is not None and raw[:1] in {" ", "+"}:
                    lines.setdefault(line, raw[1:])
                    line += 1
    return "".join(lines[number] for number in range(start, end + 1))


def step_response(*assessments, findings=()):
    return response(assessments=list(assessments), evidenceRequests=[], findings=list(findings))


def case_inputs():
    cal = FIXTURES["cal_missing_completion"]
    header = FIXTURES["discourse_header_layout"]
    cal_parts, missing = _parts(cal["diff"])
    assert not missing
    # Removed import types are a separate target-side change, not this case.
    cal_parts = [part for part in cal_parts if part.side == "proposed"]
    header_parts, missing = _parts(header["diff"])
    assert not missing
    caller = next(part for part in cal_parts if "handleCancelBooking" in part.path)
    helper = next(part for part in cal_parts if "emailReminderManager" in part.path)
    relation = {"kind": "CALLS", "source": {"unitId": "cancel-handler", "path": caller.path},
                "target": {"unitId": "cancel-email-helper", "path": helper.path}}
    graph = {
        caller.id: {"units": [{"unitId": "cancel-handler", "path": caller.path}], "relations": [relation]},
        helper.id: {"units": [{"unitId": "cancel-email-helper", "path": helper.path}], "relations": [relation]},
    }
    return cal, header, cal_parts, header_parts, caller, graph


def finding(part, line, **values):
    return {"partId": part.id, "file": part.path, "line": line,
            "title": "Cancellation returns before reminder cleanup completes",
            "reason": "The async helper is called without awaiting its completion.", **values}


@pytest.mark.asyncio
async def test_case_starts_with_complete_changed_contract_source_and_pr_intent():
    cal, _, parts, _, caller, graph = case_inputs()
    captured = []

    async def answer(messages, **kwargs):
        value = payload(messages)
        captured.append(value)
        return step_response({"workId": "work-1", "verdict": "confirmed",
            "reason": "The request can report success while the reminder cancellation promise is pending.",
            "evidenceIds": [f"diff:{caller.id}"]})

    llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=answer))
    result = await ReviewVerifier(None).verify(llm=llm, request=request(), findings=[finding(caller, 488)],
        summaries=[], parts=parts, binding={}, graph_context=graph, source_context=cal["sourceContext"])
    assert len(result.issues) == 1
    assert len(captured) == 1
    initial = captured[0]
    assert initial["changePurpose"] == {"title": request().prTitle, "description": request().prDescription}
    assert {record["result"]["diff"] for record in initial["evidence"] if record["kind"] == "diff"} == {part.diff for part in parts}
    supplied_source = [record["result"] for record in initial["evidence"] if record["kind"] == "readReviewFile"]
    assert [(item["path"], item["startLine"], item["endLine"]) for item in supplied_source] == [
        (item["path"], item["startLine"], item["endLine"]) for item in cal["sourceContext"]]
    for expected in cal["sourceContext"]:
        assert exact_source(initial, expected["path"], expected["startLine"], expected["endLine"]) == expected["content"]
    assert 'return { message: "Booking successfully cancelled." };' in json.dumps(initial).replace('\\"', '"')
    assert "catch (error)" in json.dumps(initial)
    # The source is usable immediately; no read merely to mint an evidence ID.
    assert llm.ainvoke.await_count == 1


@pytest.mark.asyncio
async def test_unrelated_cases_retire_source_bodies_before_next_case():
    cal, header, cal_parts, header_parts, caller, graph = case_inputs()
    panel = next(part for part in header_parts if 37 in part.anchors)
    captured = []

    async def answer(messages, **kwargs):
        value = payload(messages)
        captured.append(value)
        if "cases" in value:
            return response(groups=[{"caseIds": [item["caseId"]]} for item in value["cases"]])
        if "issues" in value:
            return response(groups=[{"memberIds": [item["issueId"]], "representativeId": item["issueId"]}
                                    for item in value["issues"]])
        candidate = value["workItems"][0]
        return step_response({"workId": candidate["id"], "verdict": "confirmed",
            "reason": candidate["reason"], "evidenceIds": [f"diff:{candidate['partId']}"]})

    llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=answer))
    result = await ReviewVerifier(None).verify(llm=llm, request=request(), findings=[finding(caller, 488),
        finding(panel, 37, title="Nested server header panel loses right alignment", reason="The panel is not a direct flex item.")],
        summaries=[], parts=[*cal_parts, *header_parts], binding={}, graph_context=graph,
        source_context=[*cal["sourceContext"], *header["sourceContext"]])
    assert len(result.issues) == 2
    planning, first, second, reconciliation = captured
    assert "cases" in planning and "evidence" not in planning
    assert first["caseId"] != second["caseId"]
    first_bodies = [exact_source(first, source["path"], source["startLine"], source["endLine"]) for source in cal["sourceContext"]]
    second_bodies = [exact_source(second, source["path"], source["startLine"], source["endLine"]) for source in header["sourceContext"]]
    assert {item["result"].get("path") for item in first["evidence"]}.isdisjoint({source["path"] for source in header["sourceContext"]})
    assert {item["result"].get("path") for item in second["evidence"]}.isdisjoint({source["path"] for source in cal["sourceContext"]})
    assert first_bodies == [source["content"] for source in cal["sourceContext"]]
    assert second_bodies == [source["content"] for source in header["sourceContext"]]
    assert all(json.dumps(body)[1:-1] not in json.dumps(second) for body in first_bodies)
    assert all(json.dumps(body)[1:-1] not in json.dumps(first) for body in second_bodies)
    assert set(reconciliation) == {"issues"}
    assert all(json.dumps(body)[1:-1] not in json.dumps(reconciliation) for body in [*first_bodies, *second_bodies])


@pytest.mark.asyncio
async def test_canonical_correction_preserves_missing_completion_without_rejected_subclaim():
    cal, _, parts, _, caller, graph = case_inputs()
    original = finding(caller, 488, reason="The call is unawaited and has an unhandled rejection.")
    corrected = {"title": "Cancellation response does not wait for reminder cleanup",
                 "reason": "The asynchronous helper catches errors internally but its caller does not await completion before returning success.",
                 "suggestedFixDescription": "Await all reminder cancellation promises before returning success."}
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=step_response({
        "workId": "work-1", "verdict": "confirmed", "reason": "The completion defect is independent of the incorrect rejection subtype.",
        "evidenceIds": [f"diff:{caller.id}"], "issue": corrected,
    })))
    result = await ReviewVerifier(None).verify(llm=llm, request=request(), findings=[original], summaries=[],
        parts=parts, binding={}, graph_context=graph, source_context=cal["sourceContext"])
    assert result.issues == [{**original, **corrected}]
    assert "unhandled rejection" not in result.issues[0]["reason"]


@pytest.mark.asyncio
async def test_final_reconciliation_includes_original_and_discovery_despite_unresolved_question():
    pair = DUPLICATES[0]["issues"]
    parts, missing = _parts(FIXTURES["cal_missing_completion"]["publicationRegressionDiff"])
    assert not missing
    part = next(part for part in parts if 57 in part.anchors)
    original = {**pair[0], "partId": part.id}
    discovery = {**pair[1], "partId": part.id, "evidenceIds": [f"diff:{part.id}"]}
    calls = []

    async def answer(messages, **kwargs):
        value = payload(messages)
        calls.append(value)
        if "cases" in value:
            return response(groups=[{"caseIds": [case["caseId"]]} for case in value["cases"]])
        if "issues" in value:
            return response(groups=[{"memberIds": ["issue-1", "issue-2"], "representativeId": "issue-2",
                                     "rationale": "Both describe the same exception exiting the shared loop and its deletion flush."}])
        assert len(value["workItems"]) == 1
        work = value["workItems"][0]
        if work["kind"] == "investigation":
            return step_response({"workId": work["id"], "verdict": "uncertain",
                                  "reason": "Provider retry behavior is not available in this snapshot.", "evidenceIds": []})
        return step_response({"workId": work["id"], "verdict": "confirmed", "reason": pair[0]["reason"],
                              "evidenceIds": [f"diff:{part.id}"]}, findings=[discovery])

    llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=answer))
    result = await ReviewVerifier(None).verify(llm=llm, request=request(), findings=[original], summaries=[],
        parts=[part], binding={}, investigations=[{"id": "external-retry-contract", "partIds": [part.id],
            "question": "Does the external queue retry a failed cancellation?", "paths": [part.path]}])
    assert len(calls) == 4  # Routing, independent candidate/question cases, reconciliation.
    assert [item["title"] for item in calls[-1]["issues"]] == [item["title"] for item in pair]
    assert len(result.issues) == 1
    assert result.issues[0]["title"] == pair[1]["title"]
    assert any("external-retry-contract" in message for message in result.diagnostics)
    assert "external-retry-contract" not in result.resolved_investigation_ids


def test_containment_is_not_an_exact_changed_contract_companion():
    first = SimpleNamespace(id="first", path="first.py", side="proposed", anchors={1: "first()"})
    second = SimpleNamespace(id="second", path="second.py", side="proposed", anchors={1: "second()"})
    relation = {"kind": "CONTAINS", "source": {"unitId": "first", "path": first.path},
                "target": {"unitId": "second", "path": second.path}}
    graph = {part.id: {"units": [{"unitId": part.id}], "relations": [relation]} for part in (first, second)}
    parts = {part.id: part for part in (first, second)}
    case = build_cases([finding(first, 1)], [], parts, graph)[0]
    assert related_parts(case, parts, graph) == [first]


def test_native_case_protocol_uses_real_message_classes():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-m", "pytest", "--noconftest", "-q", "-p", "no:cacheprovider",
                             str(root / "tests/review_case_protocol_checks.py")],
                            cwd=root, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.asyncio
async def test_shared_snapshot_cache_respects_case_specific_graph_focus():
    from service.review.verification_tools import VerificationTools

    async def graph(**kwargs):
        return {"status": "ready", "results": [{"path": kwargs["focus_paths"][0], "symbol": "render"}]}

    rag = SimpleNamespace(query_review_graph=AsyncMock(side_effect=graph))
    binding = {"workspace": "same-tenant", "project": "same-project", "review_collection_target": "same-snapshot"}
    first = VerificationTools(rag_client=rag, binding=binding, parts=[], focus_paths=["first_view.py"])
    second = VerificationTools(rag_client=rag, binding=binding, parts=[], focus_paths=["second_view.py"])
    second.cache = first.cache
    first_result = await first.call("queryCodeGraph", {"pattern": "callers_of", "target": "render"})
    second_result = await second.call("queryCodeGraph", {"pattern": "callers_of", "target": "render"})
    assert first_result["results"] == [{"path": "first_view.py", "symbol": "render"}]
    assert second_result["results"] == [{"path": "second_view.py", "symbol": "render"}]
    assert rag.query_review_graph.await_count == 2


@pytest.mark.asyncio
async def test_explicit_graph_scope_reuses_cache_across_other_case_focuses():
    from service.review.verification_tools import VerificationTools

    rag = SimpleNamespace(minimal_review_context=AsyncMock(return_value={"status": "ready", "nodes": []}))
    binding = {"workspace": "same-tenant", "project": "same-project", "review_collection_target": "same-snapshot"}
    first = VerificationTools(rag_client=rag, binding=binding, parts=[], focus_paths=["first_view.py"])
    second = VerificationTools(rag_client=rag, binding=binding, parts=[], focus_paths=["second_view.py"])
    second.cache = first.cache
    arguments = {"question": "How does this shared view render?", "paths": ["shared_view.py"]}
    assert await first.call("getMinimalReviewContext", arguments) == await second.call("getMinimalReviewContext", arguments)
    rag.minimal_review_context.assert_awaited_once()
    assert rag.minimal_review_context.call_args.kwargs["focus_paths"] == ["shared_view.py"]


@pytest.mark.asyncio
async def test_malformed_graph_paths_remain_tool_diagnostic_not_controller_exception():
    from service.review.verification_tools import VerificationTools

    tools = VerificationTools(rag_client=SimpleNamespace(), binding={"review_collection_target": "snapshot"}, parts=[])
    result = await tools.call("getMinimalReviewContext", {"question": "Where is the caller?", "paths": [{"not": "a-path"}]})
    assert result["status"] == "unavailable"
    assert "getMinimalReviewContext" in result["diagnostic"]


def test_partial_structural_ownership_shares_complete_file_evidence_without_merging_work():
    first = SimpleNamespace(id="first", path="module.py", side="proposed", anchors={1: "first()"})
    second = SimpleNamespace(id="second", path="module.py", side="proposed", anchors={9: "second()"})
    parts = {part.id: part for part in (first, second)}
    graph = {"first": {"units": [{"unitId": "only-partially-covers-first"}], "structuralOwnershipComplete": False},
             "second": {"units": [{"unitId": "fully-owned-second"}], "structuralOwnershipComplete": True}}
    cases = build_cases([finding(first, 1), finding(second, 9)], [], parts, graph)
    assert len(cases) == 2
    assert all(set(case.part_ids) == {"first", "second"} for case in cases)
    assert all(len(case.findings) == 1 for case in cases)


def test_finding_anchor_preserves_same_definition_source_without_joining_claims():
    first = SimpleNamespace(id="mixed", path="module.py", side="proposed", anchors={3: "a()", 12: "b()"})
    second = SimpleNamespace(id="a-again", path="module.py", side="proposed", anchors={6: "a2()"})
    third = SimpleNamespace(id="b-again", path="module.py", side="proposed", anchors={15: "b2()"})
    companion = SimpleNamespace(id="b-caller", path="caller.py", side="proposed", anchors={1: "call_b()"})
    a = {"unitId": "a", "path": "module.py", "startLine": 1, "endLine": 8}
    b = {"unitId": "b", "path": "module.py", "startLine": 10, "endLine": 18}
    relation = {"kind": "CALLS", "source": {"unitId": "caller", "path": "caller.py"}, "target": b}
    graph = {"mixed": {"units": [a, b], "relations": [relation]},
             "a-again": {"units": [a]}, "b-again": {"units": [b]},
             "b-caller": {"units": [{"unitId": "caller"}]}}
    parts = {part.id: part for part in (first, second, third, companion)}
    cases = build_cases([finding(first, 3), finding(second, 6), finding(third, 15)], [], parts, graph)
    assert len(cases) == 3
    assert cases[0].owner_ids == cases[1].owner_ids == ("a",)
    assert all(len(case.findings) == 1 for case in cases)
    assert set(cases[0].part_ids) == set(cases[1].part_ids) == {"mixed", "a-again"}
    assert related_parts(cases[0], parts, graph) == [first, second]
    assert related_parts(cases[1], parts, graph) == [first, second]
    assert related_parts(cases[2], parts, graph) == [first, third, companion]


def test_cross_definition_question_preserves_all_its_owners():
    parts = {name: SimpleNamespace(id=name, path=name + ".py", side="proposed", anchors={1: "changed()"})
             for name in ("a", "b", "unrelated")}
    graph = {name: {"units": [{"unitId": name, "startLine": 1, "endLine": 3}]} for name in parts}
    cases = build_cases([], [{"id": "contract", "partIds": ["a", "b"], "question": "Does b accept a's output?"}], parts, graph)
    assert len(cases) == 1
    assert cases[0].owner_ids == ("a", "b")
    assert related_parts(cases[0], parts, graph) == [parts["a"], parts["b"]]


def test_multi_definition_hunk_keeps_complete_diff_but_reuses_only_owned_extra_body():
    from service.review.verification_cases import source_for_case

    part = SimpleNamespace(id="mixed", path="module.py", side="proposed", anchors={2: "a()", 5: "b()"})
    graph = {"mixed": {"units": [
        {"unitId": "a", "path": part.path, "startLine": 1, "endLine": 3},
        {"unitId": "b", "path": part.path, "startLine": 4, "endLine": 6},
    ]}}
    sources = [{"status": "ready", "path": part.path, "side": "proposed", "startLine": 1,
                "endLine": 3, "content": "def a():\n    a()\n\n"},
               {"status": "ready", "path": part.path, "side": "proposed", "startLine": 4,
                "endLine": 6, "content": "def b():\n    b()\n\n"}]
    case = build_cases([finding(part, 2)], [], {part.id: part}, graph)[0]
    selected = related_parts(case, {part.id: part}, graph)
    assert selected == [part]
    assert set(selected[0].anchors) == {2, 5}  # Full hunk remains intact.
    assert source_for_case(selected, sources, owner_ids=case.owner_ids, graph_context=graph) == sources[:1]
