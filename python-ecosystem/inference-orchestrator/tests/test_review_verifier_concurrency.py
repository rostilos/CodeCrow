"""Offline scheduling checks; no latency, cost or review-quality measurements."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from service.review import review_service, verifier
from service.review.review_service import ReviewService
from service.review.review_stages import DiscoveryResult
from service.review.verifier import ReviewVerifier, VerificationResult
from service.review.verification_plan import VerificationPlan


@pytest.fixture(autouse=True)
def offline_verification_plan(monkeypatch):
    async def plan(llm, request, cases, *args):
        return VerificationPlan(cases=list(cases))

    mocked = AsyncMock(side_effect=plan)
    monkeypatch.setattr(verifier, "plan_verification_cases", mocked)
    return mocked


def inputs(label, count=2):
    parts = [SimpleNamespace(id=f"{label}-{index}", path=f"{label}/{index}.py", side="proposed",
                             anchors={2: f"changed_{label}_{index}()"},
                             diff=f"@@ -2 +2 @@\n-old()\n+changed_{label}_{index}()")
             for index in range(1, count + 1)]
    findings = [{"partId": part.id, "file": part.path, "line": 2, "title": part.id,
                 "reason": f"Defect in {part.id}"} for part in parts]
    return {"llm": object(), "request": SimpleNamespace(pullRequestId=label, aiProvider="openai"),
            "findings": findings, "summaries": [], "parts": parts,
            "binding": {"workspace": label, "project": label}}


@pytest.mark.asyncio
async def test_two_requests_share_discovery_capacity_and_keep_ordered_isolated_results(monkeypatch):
    semaphore = asyncio.Semaphore(2)
    active = set()
    peak = 0
    entered = asyncio.Event()
    release_discovery = asyncio.Event()
    second_done = {label: asyncio.Event() for label in ("a", "b")}
    finished = {label: [] for label in ("a", "b")}
    states, tools_by_request, cache_by_request = [], {"a": [], "b": []}, {}
    reconciled = []

    def start(key):
        nonlocal peak
        active.add(key)
        peak = max(peak, len(active))
        assert len(active) <= 2

    async def discovery():
        async with semaphore:
            start("discovery")
            try:
                await release_discovery.wait()
            finally:
                active.remove("discovery")

    async def verify_case(self, llm, request, state, tools, payload, batch_ids, *, previously_verified=False):
        label, case_id = request.pullRequestId, payload["caseId"]
        key = (label, case_id)
        start(key)
        try:
            entered.set()
            states.append(state)
            tools_by_request[label].append(tools)
            cache_by_request.setdefault(label, tools.cache)
            assert tools.cache is cache_by_request[label]
            assert tools.cache.setdefault("request-owner", label) == label
            assert all(item["result"]["path"].startswith(label + "/") for item in state.evidence.values())
            assert len(state.candidates) == 1
            assert state.parts[next(iter(state.candidates.values()))["partId"]].path in tools.focus_paths
            # Each case starts its own handle registry, even while cache is shared.
            shown = tools.navigation.project({"units": [{"unitId": f"{label}-{case_id}", "path": "example.py"}]})
            assert shown["units"][0]["unitId"] == "unit@1"
            if case_id == "case-1":
                await second_done[label].wait()
            else:
                second_done[label].set()
            finished[label].append(case_id)
            return VerificationResult(issues=list(state.candidates.values()),
                                      decisions=[{"candidateId": "candidate-1", "verdict": "keep"}],
                                      diagnostics=[case_id], warnings=[case_id])
        finally:
            active.remove(key)

    async def reconcile(llm, request, issues):
        key = (request.pullRequestId, "reconciliation")
        start(key)
        try:
            assert set(finished[request.pullRequestId]) == {"case-1", "case-2"}
            assert [issue["title"] for issue in issues] == [f"{request.pullRequestId}-1", f"{request.pullRequestId}-2"]
            reconciled.append(request.pullRequestId)
            await asyncio.sleep(0)
            return SimpleNamespace(issues=issues, diagnostics=[], conflicts=[])
        finally:
            active.remove(key)

    monkeypatch.setattr(ReviewVerifier, "_verify_case", verify_case)
    monkeypatch.setattr(verifier, "reconcile_issues", reconcile)
    discovery_task = asyncio.create_task(discovery())
    await asyncio.sleep(0)
    events = {"a": [], "b": []}
    requests = [asyncio.create_task(ReviewVerifier(None).verify(
        **inputs(label), semaphore=semaphore, callback=events[label].append)) for label in ("a", "b")]
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert len(active) == 2 and "discovery" in active
        release_discovery.set()
        results = await asyncio.wait_for(asyncio.gather(*requests), 2)
        await discovery_task
    finally:
        release_discovery.set()
        for task in [discovery_task, *requests]:
            if not task.done():
                task.cancel()
        await asyncio.gather(discovery_task, *requests, return_exceptions=True)
    assert peak == 2 and not active
    assert len({id(state) for state in states}) == 4
    assert len({id(tool) for values in tools_by_request.values() for tool in values}) == 4
    assert cache_by_request["a"] is not cache_by_request["b"]
    assert set(reconciled) == {"a", "b"}
    assert all(order == ["case-2", "case-1"] for order in finished.values())
    for label, result in zip(("a", "b"), results):
        assert [issue["title"] for issue in result.issues] == [f"{label}-1", f"{label}-2"]
        assert [decision["caseId"] for decision in result.decisions] == ["case-1", "case-2"]
        assert result.diagnostics == ["case-1: case-1", "case-2: case-2"]
        assert result.warnings == ["case-1", "case-2"]
        assert [event["message"] for event in events[label] if event["state"] == "verifying"] == [
            "Verifying related changes 1 of 2", "Verifying related changes 2 of 2"]


@pytest.mark.asyncio
async def test_standalone_verifier_remains_serial_without_shared_semaphore(monkeypatch):
    active = 0
    seen = []

    async def verify_case(self, llm, request, state, tools, payload, batch_ids, *, previously_verified=False):
        nonlocal active
        active += 1
        assert active == 1
        seen.append(payload["caseId"])
        await asyncio.sleep(0)
        active -= 1
        return VerificationResult(issues=[])

    monkeypatch.setattr(ReviewVerifier, "_verify_case", verify_case)
    await ReviewVerifier(None).verify(**inputs("serial", count=3))
    assert seen == ["case-1", "case-2", "case-3"]


@pytest.mark.asyncio
async def test_case_failure_preserves_healthy_sibling_and_releases_shared_slot(monkeypatch):
    semaphore = asyncio.Semaphore(2)
    completed = []

    async def verify_case(self, llm, request, state, tools, payload, batch_ids, *, previously_verified=False):
        if payload["caseId"] == "case-1":
            raise RuntimeError("case setup unavailable")
        await asyncio.sleep(0)
        completed.append(payload["caseId"])
        return VerificationResult(issues=[{**next(iter(state.candidates.values())), "title": "verified sibling"}],
                                  decisions=[{"candidateId": "candidate-1", "verdict": "keep"}])

    async def reconcile(llm, request, issues):
        assert completed == ["case-2"]
        return SimpleNamespace(issues=issues, diagnostics=[], conflicts=[])

    monkeypatch.setattr(ReviewVerifier, "_verify_case", verify_case)
    monkeypatch.setattr(verifier, "reconcile_issues", reconcile)
    result = await ReviewVerifier(None).verify(**inputs("failure"), semaphore=semaphore)
    assert [issue["title"] for issue in result.issues] == ["failure-1", "verified sibling"]
    assert result.decisions == [{"caseId": "case-2", "candidateId": "candidate-1", "verdict": "keep"}]
    assert result.diagnostics == ["case-1: Verifier unavailable; discovery results retained: case setup unavailable"]
    await asyncio.wait_for(semaphore.acquire(), 1)
    await asyncio.wait_for(semaphore.acquire(), 1)
    semaphore.release()
    semaphore.release()


@pytest.mark.asyncio
async def test_cancelling_request_stops_cases_and_releases_capacity(monkeypatch):
    semaphore = asyncio.Semaphore(2)
    both_started = asyncio.Event()
    active = set()
    stopped = []

    async def verify_case(self, llm, request, state, tools, payload, batch_ids, *, previously_verified=False):
        key = payload["caseId"]
        active.add(key)
        if len(active) == 2:
            both_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            active.remove(key)
            stopped.append(key)

    monkeypatch.setattr(ReviewVerifier, "_verify_case", verify_case)
    reconciliation = AsyncMock()
    monkeypatch.setattr(verifier, "reconcile_issues", reconciliation)
    task = asyncio.create_task(ReviewVerifier(None).verify(**inputs("cancel", count=3), semaphore=semaphore))
    await asyncio.wait_for(both_started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not active and set(stopped) == {"case-1", "case-2"}
    reconciliation.assert_not_awaited()
    await asyncio.wait_for(semaphore.acquire(), 1)
    await asyncio.wait_for(semaphore.acquire(), 1)
    semaphore.release()
    semaphore.release()


@pytest.mark.asyncio
async def test_service_uses_same_slot_for_discovery_cross_summary_and_verifier(monkeypatch):
    from model.dtos import ReviewRequestDto

    service = ReviewService(rag_client=SimpleNamespace(enabled=False))
    service._batch_semaphore = asyncio.Semaphore(1)
    monkeypatch.setattr(service, "_prepare_context", AsyncMock(return_value=({}, [])))
    monkeypatch.setattr(service, "_owner_source", AsyncMock(return_value=[]))
    monkeypatch.setattr(review_service.LLMFactory, "create_llm", lambda *args, **kwargs: object())
    visited = []

    async def discover(**kwargs):
        assert service._batch_semaphore.locked()
        visited.append("discovery")
        part = kwargs["batch"].parts[0]
        return DiscoveryResult(findings=[{"partId": part.id, "file": part.path, "line": 1,
                                           "title": "Defect", "reason": "Changed behavior fails"}], reviewed={part.id})

    async def cross(**kwargs):
        assert service._batch_semaphore.locked()
        visited.append("cross")
        return DiscoveryResult()

    async def verify(self, **kwargs):
        assert kwargs["semaphore"] is service._batch_semaphore
        assert not service._batch_semaphore.locked()
        async with kwargs["semaphore"]:
            visited.append("verify")
        return VerificationResult(issues=kwargs["findings"])

    monkeypatch.setattr(review_service, "review_batch", discover)
    monkeypatch.setattr(review_service, "review_cross_batch", cross)
    monkeypatch.setattr(ReviewVerifier, "verify", verify)
    request = ReviewRequestDto(projectId=1, projectVcsWorkspace="provider", projectVcsRepoSlug="repo",
        projectWorkspace="tenant", projectNamespace="project", aiProvider="openai", aiModel="offline",
        aiApiKey="offline", pullRequestId=17, targetBranchName="main", sourceBranchName="feature",
        currentCommitHash="head", targetHeadCommitHash="base",
        rawDiff="diff --git a/source.py b/source.py\n--- a/source.py\n+++ b/source.py\n@@ -1 +1 @@\n-old()\n+new()\n")
    result = await asyncio.wait_for(service._review(request, None), 2)
    assert result["status"] == "complete"
    assert visited == ["discovery", "cross", "verify"]
