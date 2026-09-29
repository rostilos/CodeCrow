"""Scheduling preserves review inputs, source evidence, and publication order."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
from types import SimpleNamespace

import pytest

from service.review.planner import ReviewPlanner
from service.review.verification_tools import VerificationTools
from service.review.verifier import ReviewVerifier


def part(name):
    return SimpleNamespace(id=name, path=f"{name}.py", side="proposed", anchors={1: "changed()"},
                           diff="@@ -1 +1 @@\n-before()\n+changed()\n")


def finding(value):
    return {"partId": value.id, "file": value.path, "line": 1,
            "title": f"Failure in {value.path}", "reason": "A supported trigger reaches the changed operation."}


def snapshot(value):
    if isinstance(value, SimpleNamespace):
        return {key: snapshot(item) for key, item in vars(value).items()}
    if isinstance(value, dict):
        return {key: snapshot(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [snapshot(item) for item in value]
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, True])
async def test_parallel_cases_replay_identical_conversations_and_reconciliation(tmp_path, monkeypatch, native):
    from service.review import verifier as module

    target, overlay = tmp_path / "target", tmp_path / "overlay"
    target.mkdir()
    overlay.mkdir()
    (overlay / "manifest.json").write_text(json.dumps({"changedFiles": [], "deletedFiles": []}))
    (target / "shared.py").write_text("def caller():\n    changed()\n")
    parts = [part(name) for name in ("a", "b", "c")]
    binding = {"target_repo_path": str(target), "review_overlay_path": str(overlay)}
    request = SimpleNamespace(aiProvider="openai", pullRequestId="42", projectRules=None, taskContext=None)
    monkeypatch.setattr(module, "result_message", lambda call, result: {"call": call, "result": result})

    class ScriptedModel:
        def __init__(self):
            self.transcripts = {}
            self.schemas = []
            self.active = self.peak = 0
            if not native:
                self.bind_tools = None

        def bind_tools(self, definitions):
            self.schemas.append(definitions)
            return self

        async def ainvoke(self, messages, **options):
            # The compatibility adapter prepends its static schema instruction.
            supplied = messages if native else [item for item in messages if isinstance(item, (tuple, dict))]
            packet = next(json.loads(item[1]) for item in supplied
                          if isinstance(item, tuple) and item[0] == "human")
            case_id = packet.get("caseId", "reconcile")
            history = self.transcripts.setdefault(case_id, [])
            history.append({"messages": snapshot(supplied), "options": snapshot(options)})
            self.active += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0)
            self.active -= 1
            if case_id == "reconcile":
                return SimpleNamespace(content=json.dumps({"groups": []}), tool_calls=[], invalid_tool_calls=[])
            if len(history) == 1:
                name, arguments = "readReviewFile", {"path": "shared.py"}
            else:
                name = "recordReviewDecisions"
                arguments = {"decisions": [{"candidateId": "candidate-1", "verdict": "keep",
                    "reason": "The exact changed source and caller establish the failure.",
                    "evidenceIds": [f"diff:{packet['candidates'][0]['partId']}", "read-1"]}]}
            if native:
                return SimpleNamespace(content="", tool_calls=[{"id": f"call-{len(history)}",
                    "name": name, "args": arguments}], invalid_tool_calls=[])
            return SimpleNamespace(content=json.dumps({"toolCalls": [{"name": name, "arguments": arguments}]}))

    async def run(serial):
        model = ScriptedModel()
        verifier = ReviewVerifier(None)
        original = verifier._verify_case
        serial_slot = asyncio.Semaphore(1)

        async def one_at_a_time(*args, **kwargs):
            async with serial_slot:
                return await original(*args, **kwargs)

        if serial:
            verifier._verify_case = one_at_a_time
        result = await verifier.verify(llm=model, request=request, findings=[finding(value) for value in parts],
                                       summaries=[], parts=parts, binding=binding)
        return result, model

    serial_result, serial_model = await run(True)
    parallel_result, parallel_model = await run(False)
    assert asdict(parallel_result) == asdict(serial_result)
    assert parallel_model.transcripts == serial_model.transcripts
    assert parallel_model.schemas == serial_model.schemas
    assert len(parallel_result.issues) == 3
    assert [item["caseId"] for item in parallel_result.decisions] == ["case-1", "case-2", "case-3"]
    assert serial_model.peak == 1 and parallel_model.peak == 3
    assert len(parallel_model.transcripts["reconcile"]) == 1


@pytest.mark.asyncio
async def test_read_groups_overlap_but_preserve_result_order_and_decision_barriers():
    started, completed, consumed = [], [], []
    both_started = asyncio.Event()

    async def call(name, arguments):
        marker = arguments["marker"]
        started.append(marker)
        if marker == "first":
            await both_started.wait()
        elif marker == "second":
            both_started.set()
        elif name == "recordReviewDecisions":
            assert consumed == ["first", "second"]
            assert started == ["first", "second", "decision"]
        else:
            assert consumed == ["first", "second", "decision"]
        completed.append(marker)
        return {"status": "ready", "marker": marker}

    calls = [{"name": name, "arguments": {"marker": marker}}
             for name, marker in [("readReviewFile", "first"), ("grepReviewCode", "second"),
                                  ("recordReviewDecisions", "decision"), ("readReviewFile", "last")]]
    async with asyncio.timeout(2):
        async for item, result in ReviewVerifier._tool_results(SimpleNamespace(call=call), calls):
            consumed.append(result["marker"])
    assert completed == ["second", "first", "decision", "last"]
    assert consumed == ["first", "second", "decision", "last"]


@pytest.mark.asyncio
async def test_shared_source_requests_coalesce_without_cross_focus_or_request_reuse():
    calls = []
    release = asyncio.Event()

    async def execute(name, arguments):
        calls.append((name, arguments))
        await release.wait()
        return {"status": "ready", "results": [{"unitId": "same-source"}]}

    server = SimpleNamespace(call_tool=execute)
    first = VerificationTools(rag_client=None, binding={}, parts=[], focus_paths=["a.py"])
    second = VerificationTools(rag_client=None, binding={}, parts=[], focus_paths=["a.py"])
    other_focus = VerificationTools(rag_client=None, binding={}, parts=[], focus_paths=["b.py"])
    for instance in (first, second, other_focus):
        instance.server = server
        instance.cache = first.cache
        instance.in_flight = first.in_flight
    arguments = {"pattern": "callers_of", "target": "changed"}
    tasks = [asyncio.create_task(instance.call("queryCodeGraph", arguments))
             for instance in (first, second, other_focus)]
    for _ in range(3):
        await asyncio.sleep(0)
    assert len(calls) == 2
    release.set()
    results = await asyncio.gather(*tasks)
    assert results[0] == results[1] == results[2]
    assert not first.in_flight
    independent = VerificationTools(rag_client=None, binding={}, parts=[], focus_paths=["a.py"])
    independent.server = server
    assert await independent.call("queryCodeGraph", arguments) == results[0]
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_source_execution_capacity_and_transient_failure_remain_retryable():
    active = peak = attempts = 0

    async def execute(name, arguments):
        nonlocal active, peak, attempts
        attempts += 1
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return {"status": "unavailable" if attempts <= 2 else "ready"}

    tools = VerificationTools(rag_client=None, binding={}, parts=[])
    tools.server = SimpleNamespace(call_tool=execute)
    tools.source_slots = asyncio.Semaphore(2)
    await asyncio.gather(*(tools.call("readReviewFile", {"path": f"{index}.py"}) for index in range(6)))
    assert peak == 2
    assert not tools.in_flight
    assert (await tools.call("readReviewFile", {"path": "0.py"}))["status"] == "ready"
    assert attempts == 7


@pytest.mark.asyncio
async def test_planner_parallel_fetch_preserves_complete_plan_and_diagnostic_order(monkeypatch):
    parts = [part(name) for name in ("a", "b", "c", "d")]
    active = peak = 0

    async def graph_reader(*, pattern, target, focus_path):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        if target == "b.py" or target == "unit-c":
            raise RuntimeError(f"Unavailable {target}")
        if pattern == "file_summary":
            return [{"unitId": f"unit-{target[0]}", "path": target, "startLine": 1, "endLine": 10}]
        return []

    monkeypatch.setenv("MAX_CONCURRENT_REVIEWS", "1")
    serial = await ReviewPlanner(graph_reader).plan(parts)
    assert peak == 1
    peak = 0
    monkeypatch.setenv("MAX_CONCURRENT_REVIEWS", "3")
    parallel = await ReviewPlanner(graph_reader).plan(parts)
    assert parallel == serial
    assert peak == 3


@pytest.mark.asyncio
async def test_cancelled_source_work_does_not_leave_a_poisoned_shared_request():
    entered = asyncio.Event()
    release = asyncio.Event()
    active = 0

    async def execute(name, arguments):
        nonlocal active
        active += 1
        entered.set()
        try:
            await release.wait()
            return {"status": "ready"}
        finally:
            active -= 1

    tools = VerificationTools(rag_client=None, binding={}, parts=[])
    tools.server = SimpleNamespace(call_tool=execute)
    task = asyncio.create_task(tools.call("readReviewFile", {"path": "same.py"}))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not tools.in_flight and active == 0
    release.set()
    assert (await tools.call("readReviewFile", {"path": "same.py"}))["status"] == "ready"


@pytest.mark.asyncio
async def test_source_capacity_is_shared_across_reviews_without_sharing_source_results():
    from service.review.execution_scheduler import FairReviewScheduler, review_execution

    active = peak = 0
    source_capacity = asyncio.Semaphore(2)
    scheduler = FairReviewScheduler(4)

    def tool_for(marker):
        async def execute(name, arguments):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            active -= 1
            return {"status": "ready", "content": marker}

        instance = VerificationTools(rag_client=None, binding={}, parts=[])
        instance.server = SimpleNamespace(call_tool=execute)
        return instance

    async def review(marker):
        tools = tool_for(marker)
        with review_execution(scheduler, source_capacity):
            return await asyncio.gather(*(tools.call("readReviewFile", {"path": f"{index}.py"})
                                          for index in range(3)))

    first, second = await asyncio.gather(review("first-tenant-source"), review("second-tenant-source"))
    assert peak == 2
    assert {item["content"] for item in first} == {"first-tenant-source"}
    assert {item["content"] for item in second} == {"second-tenant-source"}


@pytest.mark.asyncio
async def test_failed_source_group_joins_reads_started_alongside_it():
    peer_started = asyncio.Event()
    peer_finished = asyncio.Event()

    async def call(name, arguments):
        if arguments["path"] == "failed.py":
            await peer_started.wait()
            raise RuntimeError("Tool execution interrupted")
        peer_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            peer_finished.set()

    calls = [{"name": "readReviewFile", "arguments": {"path": path}} for path in ("failed.py", "peer.py")]
    with pytest.raises(RuntimeError, match="Tool execution interrupted"):
        async for _ in ReviewVerifier._tool_results(SimpleNamespace(call=call), calls):
            pass
    assert peer_finished.is_set()


@pytest.mark.asyncio
async def test_cancelled_planner_fetch_joins_other_graph_reads(monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_REVIEWS", "2")
    peer_started = asyncio.Event()
    peer_finished = asyncio.Event()

    async def reader(*, pattern, target, focus_path):
        if target == "a.py":
            await peer_started.wait()
            raise asyncio.CancelledError()
        peer_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            peer_finished.set()

    with pytest.raises(asyncio.CancelledError):
        await ReviewPlanner(reader).plan([part("a"), part("b")])
    assert peer_finished.is_set()


@pytest.mark.asyncio
async def test_aborted_case_joins_other_case_conversations():
    peer_started = asyncio.Event()
    peer_finished = asyncio.Event()
    verifier = ReviewVerifier(None)

    async def verify_case(llm, request, state, tools, payload, batch_ids):
        if payload["caseId"] == "case-1":
            await peer_started.wait()
            raise RuntimeError("Case setup interrupted")
        peer_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            peer_finished.set()

    verifier._verify_case = verify_case
    parts = [part("a"), part("b")]
    with pytest.raises(RuntimeError, match="Case setup interrupted"):
        await verifier.verify(llm=None, request=SimpleNamespace(), findings=[finding(value) for value in parts],
                              summaries=[], parts=parts, binding={})
    assert peer_finished.is_set()


@pytest.mark.asyncio
async def test_case_progress_counts_completed_work_in_actual_completion_order(caplog):
    from service.review.verifier import VerificationResult

    parts = [part(name) for name in ("a", "b", "c")]
    releases = {f"case-{index}": asyncio.Event() for index in range(1, 4)}
    starts = {case_id: asyncio.Event() for case_id in releases}
    completions = {case_id: asyncio.Event() for case_id in releases}
    events = []

    def callback(event):
        events.append(event)
        if event["state"] == "verification_case_completed":
            completions[event["caseId"]].set()

    async def verify_case(llm, request, state, tools, payload, batch_ids):
        case_id = payload["caseId"]
        starts[case_id].set()
        await releases[case_id].wait()
        return VerificationResult(issues=[], diagnostics=["Source unavailable; discovery retained"]
                                  if case_id == "case-2" else [])

    verifier = ReviewVerifier(None)
    verifier._verify_case = verify_case
    with caplog.at_level("INFO", logger="service.review.verifier"):
        pending = asyncio.create_task(verifier.verify(llm=None, request=SimpleNamespace(pullRequestId="42"),
            findings=[finding(value) for value in parts], summaries=[], parts=parts, binding={}, callback=callback))
        await asyncio.gather(*(event.wait() for event in starts.values()))
        assert len(events) == 3
        assert all(event["state"] == "verification_case_started" and event["completedCases"] == 0
                   for event in events)
        for case_id in ("case-2", "case-3", "case-1"):
            releases[case_id].set()
            await completions[case_id].wait()
        await pending
    completed = [event for event in events if event["state"] == "verification_case_completed"]
    assert [event["caseId"] for event in completed] == ["case-2", "case-3", "case-1"]
    assert [event["completedCases"] for event in completed] == [1, 2, 3]
    assert [event["outcome"] for event in completed] == ["partial", "complete", "complete"]
    assert all(event["totalCases"] == 3 for event in events)
    assert "PR=42 case=case-2 duration_ms=" in caplog.text
    assert "outcome=partial diagnostics=1" in caplog.text
    assert "Source unavailable; discovery retained" not in caplog.text
