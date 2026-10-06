"""Provider-free integration regressions for optional review work and evidence."""
import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from model.output_schemas import CodeReviewIssue
from model.multi_stage import ReviewFile, ReviewPlan
from service.review.candidate_ledger import CandidateEvidenceLedger
from service.review.execution_scheduler import FairReviewScheduler, review_execution, review_model_slot, set_review_deadline
from service.review.inference_usage import current_inference_usage, provider_usage, record_model_response
from service.review.orchestrator import stage_1_file_review as stage1
from service.review.orchestrator import verification_agent as verifier
from service.review.orchestrator.inference_policy import build_review_inference_profile, adapt_review_inference_profile
from service.review.orchestrator.reconciliation import compute_issue_fingerprint, issues_are_conservative_duplicates, issues_are_semantic_dedup_candidates, deduplicate_cross_batch_issues
from service.review.orchestrator.stage_1_local_packing import Stage1ReviewUnitState
from service.review.orchestrator.stage_3_mcp_verification import extract_verification_assessments, validated_mcp_dismissals
from tests.test_verification_full import _FakeToolLLM, _FakeResponse
from utils.diff_processor import DiffProcessor


def finding(**kwargs):
    return CodeReviewIssue(file="a.py", line=1, severity="HIGH", category="BUG_RISK", title="Crash on missing input",
        reason="The changed call dereferences a missing input and crashes.", suggestedFixDescription="Check input.", codeSnippet="return user.name", **kwargs)


def test_native_cache_and_reasoning_usage_are_optional_observations():
    assert provider_usage({"response_metadata": {"token_usage": {
        "prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28,
        "prompt_tokens_details": {"cached_tokens": 10},
        "completion_tokens_details": {"reasoning_tokens": 6},
    }}}) == {"inputTokens": 20, "outputTokens": 8, "totalTokens": 28,
             "cachedInputTokens": 10, "reasoningOutputTokens": 6}
    assert provider_usage({"response_metadata": {"token_usage": None}}) is None


def register(ledger, candidate, source="core"):
    ledger.register(candidate, stage="stage_1", source_key=source, review_unit_ids=["unit"], prompt_hunk_ids=["hunk"], generation_prompt=source)


@pytest.mark.asyncio
async def test_attempt_usage_is_request_local_and_missing_fields_are_unknown():
    scheduler = FairReviewScheduler(1)
    async def run(input_tokens):
        with review_execution(scheduler, asyncio.Semaphore(1)):
            try:
                async with review_model_slot("discovery"):
                    raise TimeoutError("provider timeout")
            except TimeoutError:
                pass
            async with review_model_slot("discovery"):
                await asyncio.sleep(0)
                record_model_response({"raw": {"usage_metadata": {"input_tokens": input_tokens, "output_tokens": 4}}})
            return current_inference_usage()
    first, second = await asyncio.gather(run(11), run(23))
    for result, expected in [(first, 11), (second, 23)]:
        stage = result["stages"]["discovery"]
        assert result["attempts"] == 2 and stage["failed"] == 1 and stage["completed"] == 1
        assert stage["observedInputTokens"] == expected and stage["inputTokensKnownCalls"] == 1
        assert stage["inputTokens"] is None and stage["totalTokens"] is None
        assert stage["totalTokensKnownCalls"] == 0
    assert current_inference_usage() == {}
    mixed = SimpleNamespace(result=[SimpleNamespace(type="ai", usage_metadata={"input_tokens": 10, "total_tokens": 15}), SimpleNamespace(type="ai", usage_metadata={"input_tokens": 7})])
    assert provider_usage(mixed) == {"inputTokens": 17}
    mixed.result.append(SimpleNamespace(type="ai"))
    assert provider_usage(mixed) is None


def test_adaptive_risk_budget_preserves_operator_caps_and_core_coverage(monkeypatch):
    request = SimpleNamespace(changedFiles=["a.py"], rawDiff="+return user.name", deltaDiff=None)
    monkeypatch.setenv("REVIEW_STAGE_2_PACKETS_MAX_INVOCATIONS", "1")
    profile = build_review_inference_profile(request, None)
    adapted = adapt_review_inference_profile(profile, issues=[finding(evidenceGaps=["caller input contract"])])
    assert adapted.invocation_cap("stage_2_packets") == 1
    assert adapted.invocation_cap("stage_1_total") == profile.invocation_cap("stage_1_total")
    assert adapted.invocation_cap("specialist_passes") == 2
    assert set(adapted.allocation_reasons) == {"high_severity_candidates", "unresolved_evidence_gaps"}


@pytest.mark.parametrize("verdict,snippet,expected", [("disproven", "class Foo:", 0), ("disproven", "if user is None:", 1), ("unknown", "class Foo:", 1), ("supported", "class Foo:", 1)])
@pytest.mark.asyncio
async def test_verifier_retains_unknown_and_only_drops_observed_counterevidence(verdict, snippet, expected):
    candidate = finding()
    request = MagicMock(enrichmentData=SimpleNamespace(fileContents=[SimpleNamespace(path="a.py", content="class Foo:\n    return user.name", skipped=False)]), rawDiff=None, deltaDiff=None, previousCodeAnalysisIssues=[])
    ledger = CandidateEvidenceLedger()
    register(ledger, candidate)
    citation = "supporting_evidence" if verdict == "supported" else "contradicting_evidence"
    llm = _FakeToolLLM([_FakeResponse(content=json.dumps({"assessments": [{"issue_id": "issue_0", "verdict": verdict, "reason": "Concrete guard checked", citation: [{"file_path": "a.py", "snippet": snippet}]}]}))])
    kept = await verifier.run_verification_agent(llm, [candidate], request, candidate_ledger=ledger)
    assert len(kept) == expected
    checked = ledger.summary()["records"][0]["verificationAssessments"][-1]
    assert checked["verdict"] == ("unknown" if snippet not in "class Foo:\n    return user.name" else verdict)
    ledger.publish(kept)
    ledger.assert_terminal()


@pytest.mark.asyncio
async def test_optional_graph_observation_is_bound_to_actual_tool_output():
    tool = SimpleNamespace(name="getImpactRadius", ainvoke=AsyncMock(return_value=json.dumps({"status": "ok", "relations": [{"from": "caller", "to": "guard"}]})))
    args = {"filePath": "a.py"}
    fingerprint = hashlib.sha256(json.dumps([tool.name, args], sort_keys=True, default=str).encode()).hexdigest()
    evidence_id = "verification:" + fingerprint[:20]
    llm = _FakeToolLLM([
        _FakeResponse(tool_calls=[{"id": "call1", "name": tool.name, "args": args}]),
        _FakeResponse(content=json.dumps({"assessments": [{"issue_id": "issue_0", "verdict": "disproven", "contradicting_evidence": [{"evidence_id": evidence_id, "snippet": '"to": "guard"'}]}]})),
    ])
    observations = {}
    result = await verifier._run_verification_tool_loop(llm, "Check the guard", optional_tools=(tool,), observations=observations)
    assessment = verifier._validated_assessment(result.assessments[0], {}, observations)
    assert assessment["verdict"] == "disproven"
    assert assessment["contradictingEvidence"][0]["tool"] == tool.name
    observations[evidence_id]["status"] = "unavailable"
    assert verifier._validated_assessment(result.assessments[0], {}, observations)["verdict"] == "unknown"
    tool.ainvoke.assert_awaited_once_with(args)


def test_final_verifier_read_alone_cannot_dismiss_and_quotes_are_observed():
    candidate = finding()
    executor = SimpleNamespace(verification_source={("issue_0", "a.py"): "if user is None: return"}, call_log=[{
        "tool": "getBranchFileContent", "args": {"filePath": "a.py", "branch": "head", "verificationId": "issue_0"},
        "success": True, "evidence_valid": True, "evidence_complete_file": True,
    }])
    assert validated_mcp_dismissals(["issue_0"], {"issue_0": candidate}, executor, "head") == []
    for quote, expected in [("nonexistent guard", []), ("if user is None: return", ["issue_0"])]:
        text = "report\n<!-- VERIFICATION_ASSESSMENTS: " + json.dumps([{"issue_id": "issue_0", "verdict": "disproven", "contradicting_evidence": [{"file_path": "a.py", "snippet": quote}]}]) + " -->"
        report, assessments = extract_verification_assessments(text, executor, {"issue_0": candidate})
        assert report == "report"
        assert validated_mcp_dismissals(["issue_0"], {"issue_0": candidate}, executor, "head", assessments) == expected


def test_base_head_are_distinct_deterministic_and_semantic_candidates():
    head, base = finding(), finding(anchorSide="BASE")
    assert compute_issue_fingerprint(head.model_dump()) != compute_issue_fingerprint(base.model_dump())
    assert not issues_are_conservative_duplicates(head, base)
    assert not issues_are_semantic_dedup_candidates(head, base)


@pytest.mark.parametrize("failure", [None, "timeout", "error"])
@pytest.mark.asyncio
async def test_specialist_work_is_bounded_distinct_and_closes_partial_candidates(monkeypatch, failure):
    diff = DiffProcessor().process("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-return user\n+return user.name # authorization tenant transaction rollback\n")
    file = ReviewFile(path="a.py", focus_areas=["authorization", "transaction"], risk_level="HIGH")
    request = MagicMock(rawDiff="", deltaDiff=None, changedFiles=["a.py"], enrichmentData=None, taskContext=None, useMcpTools=False)
    batches = [[{"file": file, "priority": "HIGH"}]]
    monkeypatch.setattr(stage1, "create_smart_batches_wrapper", AsyncMock(return_value=batches))
    monkeypatch.setenv("REVIEW_SPECIALIST_MAX_PASSES", "2")
    monkeypatch.setenv("REVIEW_SPECIALIST_TIMEOUT_SECONDS", "0.025")
    seen = []
    core = finding()
    partial = []
    ledger = CandidateEvidenceLedger()
    async def review(_llm, _request, batch, *_args, **kwargs):
        specialist = kwargs.get("specialist")
        name = specialist.name if specialist else "core"
        seen.append((name, tuple(batch[0]["_hunk_ids"])))
        candidate = core if name == "core" else finding(hypothesis=name)
        register(kwargs["candidate_ledger"], candidate, name)
        if specialist and failure:
            partial.append(candidate)
            if failure == "timeout":
                await asyncio.Event().wait()
            raise RuntimeError("optional provider unavailable")
        return [candidate]
    monkeypatch.setattr(stage1, "review_file_batch", review)
    events = []
    state = Stage1ReviewUnitState()
    profile = build_review_inference_profile(request, diff)
    profile = adapt_review_inference_profile(profile, issues=[finding()])
    with review_execution(FairReviewScheduler(1), asyncio.Semaphore(1)):
        set_review_deadline(asyncio.get_running_loop().time() + 1)
        result = await stage1.execute_stage_1_file_reviews(object(), request, ReviewPlan(analysis_summary="review", file_groups=[], cross_file_concerns=[]), None,
            processed_diff=diff, candidate_ledger=ledger, review_unit_state=state, inference_profile=profile, event_callback=events.append)
    assert result[0] is core and seen[0][0] == "core"
    assert all(hunks == seen[0][1] and hunks for _, hunks in seen)
    state.assert_complete()
    if failure:
        assert result == [core]
        assert all(record["terminalState"] == "rejected" for record in ledger.summary()["records"] if record["sourceKey"] != "core")
        assert any(event.get("outcome") in {"timeout", "unavailable"} for event in events)
    else:
        assert {name for name, _ in seen} == {"core", "security", "state_integrity"}
        assert len(result) == 3
    ledger.publish(result)
    ledger.assert_terminal()


def test_real_sdk_message_reducer_and_tool_recovery():
    import subprocess
    import sys
    from pathlib import Path
    result = subprocess.run([sys.executable, str(Path(__file__).with_name("evidence_sdk_boundary.py"))], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.asyncio
async def test_stalled_optional_verifier_preserves_candidate_and_external_cancellation(monkeypatch):
    candidate = finding()
    request = MagicMock(enrichmentData=SimpleNamespace(fileContents=[SimpleNamespace(path="a.py", content="return user.name", skipped=False)]), rawDiff=None, deltaDiff=None, previousCodeAnalysisIssues=[])
    cancelled = asyncio.Event()
    async def stalled(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    monkeypatch.setattr(verifier, "_run_verification_tool_loop", stalled)
    with review_execution(FairReviewScheduler(1), asyncio.Semaphore(1)):
        set_review_deadline(asyncio.get_running_loop().time() + .1)
        assert await verifier.run_verification_agent(object(), [candidate], request) == [candidate]
    assert cancelled.is_set()
    cancelled.clear()
    task = asyncio.create_task(verifier.run_verification_agent(object(), [candidate], request))
    await asyncio.sleep(.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


@pytest.mark.parametrize("raw", ["not-a-number", "nan", "inf", "-inf"])
def test_malformed_optional_timeout_uses_finite_default(monkeypatch, raw):
    from service.review.execution_scheduler import optional_timeout_seconds
    monkeypatch.setenv("REVIEW_GRAPH_PREPARATION_TIMEOUT_SECONDS", raw)
    assert optional_timeout_seconds("REVIEW_GRAPH_PREPARATION_TIMEOUT_SECONDS", 120) == 120


def test_specialist_duplicates_consolidate_without_losing_distinct_defects():
    core = finding()
    overlapping = finding(hypothesis="security: crash on missing input")
    distinct = finding(hypothesis="transaction rollback")
    distinct.title = "Committed deletion survives failed replacement"
    distinct.reason = "The delete commits before the replacement write; failure permanently loses persisted data."
    distinct.codeSnippet = "database.delete(user)"
    distinct.line = 20
    result = deduplicate_cross_batch_issues([core, overlapping, distinct])
    assert len(result) == 2 and distinct in result


@pytest.mark.asyncio
async def test_final_verification_timeout_falls_back_with_retained_candidates(monkeypatch):
    from service.review.orchestrator.stage_3_mcp_verification import execute_stage_3_mcp_verification, Stage3McpRuntime
    cancelled = asyncio.Event()
    class Llm:
        def bind_tools(self, tools):
            return self
        async def ainvoke(self, *args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
    report = AsyncMock(return_value={"report": "retained findings", "dismissed_issue_ids": []})
    runtime = Stage3McpRuntime(input_token_target=lambda _: 10000, estimate_messages_tokens=lambda *args, **kwargs: 10,
        continuation_messages=lambda *args: [], invoke_report=report, response_finished_by_length=lambda _: False)
    monkeypatch.setenv("REVIEW_FINAL_VERIFICATION_TIMEOUT_SECONDS", "0.02")
    result = await execute_stage_3_mcp_verification(Llm(), SimpleNamespace(projectVcsWorkspace="tenant", projectVcsRepoSlug="repo"), "prompt", object(), "revision", {"issue_0": finding()}, runtime)
    assert cancelled.is_set()
    assert result["report"] == "retained findings"
    assert result["dismissed_issue_ids"] == []
    assert result["verification_assessments"][0]["reasonCode"] == "verification_timeout"
    assert result["verification_assessments"][0]["completed"] is False
    report.assert_awaited_once()


@pytest.mark.asyncio
async def test_deleted_candidate_side_is_host_derived_before_history_matching():
    from service.review.orchestrator.reconciliation import reconcile_previous_issues
    diff = DiffProcessor().process("diff --git a/a.py b/a.py\ndeleted file mode 100644\n--- a/a.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-return user.name\n")
    candidate = finding()
    old = finding(id="stored", anchorSide="HEAD")
    request = SimpleNamespace(previousCodeAnalysisIssues=[{**old.model_dump(), "status":"OPEN"}], currentCommitHash="head", commitHash="head", deltaDiff=None)
    verifier.normalize_candidate_anchor_sides([candidate, old], request, diff)
    assert candidate.anchorSide == "BASE" and old.anchorSide == "HEAD"
    result = await reconcile_previous_issues(request, [candidate], diff)
    assert len(result) == 2
    assert candidate.id != "stored"


@pytest.mark.asyncio
async def test_normal_verifier_uses_optional_runtime_for_deleted_file_without_head_source():
    from service.agent import AgentExecutionService
    diff = DiffProcessor().process("diff --git a/a.py b/a.py\ndeleted file mode 100644\n--- a/a.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-return user.name\n")
    candidate = finding(anchorSide="BASE", evidenceGaps=["does removal break configured import check"])
    request = MagicMock(enrichmentData=None, rawDiff=None, deltaDiff=None, previousCodeAnalysisIssues=[])
    tool = SimpleNamespace(name="runVerificationCheck", ainvoke=AsyncMock(return_value=json.dumps({"status": "failed", "exitCode": 1, "revision": "proposed-tree-sha"})))
    args = {"checkName": "import-check", "hypothesis": "removal breaks imports"}
    digest = hashlib.sha256(json.dumps([tool.name, args], sort_keys=True, default=str).encode()).hexdigest()
    llm = _FakeToolLLM([
        _FakeResponse(tool_calls=[{"id": "runtime1", "name": tool.name, "args": args}]),
        _FakeResponse(content=json.dumps({"assessments": [{"issue_id": "issue_0", "verdict": "supported", "supporting_evidence": [{"evidence_id": "verification:" + digest[:20], "snippet": '"exitCode": 1'}]}]})),
    ])
    service = AgentExecutionService(llm=llm, client=None)
    service._available_tools = (tool, SimpleNamespace(name="arbitrary-host-tool"))
    ledger = CandidateEvidenceLedger()
    register(ledger, candidate)
    assert await verifier.run_verification_agent(llm, [candidate], request, diff, ledger, agent_service=service) == [candidate]
    assert ledger.summary()["records"][0]["verificationAssessments"][-1]["verdict"] == "supported"
    assert "arbitrary-host-tool" not in [getattr(item, "name", "") for item in llm.tools]
    tool.ainvoke.assert_awaited_once_with(args)


def test_stage_2_conversion_preserves_hypothesis_and_evidence_gaps():
    from model.multi_stage import CrossFileIssue
    from service.review.orchestrator.orchestrator import _convert_cross_file_issues
    candidate = CrossFileIssue(id="cross1", severity="HIGH", category="BUG_RISK", title="Caller imports removed module", primary_file="a.py", affected_files=["a.py", "caller.py"],
        description="Removal breaks callers", evidence="Exact import", business_impact="Startup fails", suggestion="Update caller", hypothesis="Caller imports deleted module", evidenceGaps=["generated import list"])
    converted = _convert_cross_file_issues([candidate])[0]
    assert converted.hypothesis == candidate.hypothesis and converted.evidenceGaps == candidate.evidenceGaps
