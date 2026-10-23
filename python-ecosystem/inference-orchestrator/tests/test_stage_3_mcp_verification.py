"""Focused boundary tests for the extracted Stage 3 MCP subsystem."""

from types import SimpleNamespace
from pathlib import Path
import asyncio
import json

import pytest

from llm.reasoning_policy import ReasoningEffort
from model.output_schemas import CodeReviewIssue
from service.review.orchestrator import stage_3_aggregation
from service.review.orchestrator import stage_3_mcp_verification
from service.review.orchestrator.stage_3_mcp_verification import (
    Stage3McpRuntime,
    execute_stage_3_mcp_verification,
)


def test_aggregation_preserves_legacy_helper_imports() -> None:
    """Existing callers can keep importing the historical private names."""
    assert (
        stage_3_aggregation._extract_dismissed_issues
        is stage_3_mcp_verification.extract_dismissed_issues
    )
    assert (
        stage_3_aggregation._stage_3_verification_issue_map
        is stage_3_mcp_verification.verification_issue_map
    )
    assert (
        stage_3_aggregation._validated_mcp_dismissals
        is stage_3_mcp_verification.validated_mcp_dismissals
    )


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize("model_name,configured,expected", [
    ("other", None, 16_384),
    ("deepseek/deepseek-v4.1-flash-1017", None, 131_072),
    ("deepseek/deepseek-v4.1-flash-1017", 262_144, 262_144),
])
async def test_mcp_subsystem_runs_through_the_typed_runtime_boundary(model_name, configured, expected) -> None:
    response = SimpleNamespace(
        content="bounded verification report",
        tool_calls=[],
        response_metadata={},
    )

    class BoundLlm:
        async def ainvoke(self, _messages, **_kwargs):
            assert _kwargs["max_tokens"] == expected
            return response

    class Llm:
        def bind_tools(self, tool_definitions):
            assert tool_definitions
            return BoundLlm()

    async def unexpected_report_fallback(*_args, **_kwargs):
        raise AssertionError("the plain report fallback should not be used")

    runtime = Stage3McpRuntime(
        input_token_target=lambda _request: 10_000,
        estimate_messages_tokens=lambda _messages, **_kwargs: 1,
        continuation_messages=lambda _prompt, _records: [],
        invoke_report=unexpected_report_fallback,
        response_finished_by_length=lambda _response: False,
    )
    request = SimpleNamespace(
        projectVcsWorkspace="workspace",
        projectVcsRepoSlug="repository",
    )

    llm = Llm()
    llm.model_name, llm.max_tokens = model_name, configured
    result = await execute_stage_3_mcp_verification(
        llm,
        request,
        "prompt",
        SimpleNamespace(),
        "commit-sha",
        {},
        runtime,
    )

    assert result == {
        "report": "bounded verification report",
        "verification_assessments": [],
        "dismissed_issue_ids": [],
        "dismissed_issue_keys": [],
        "dismissed_issue_object_ids": [],
    }


@pytest.mark.asyncio(loop_scope="function")
async def test_length_recovery_requests_reasoning_free_plain_report() -> None:
    response = SimpleNamespace(
        content="",
        tool_calls=[],
        response_metadata={"finish_reason": "max_tokens"},
    )

    class BoundLlm:
        async def ainvoke(self, _messages, **_kwargs):
            return response

    class Llm:
        def bind_tools(self, _tool_definitions):
            return BoundLlm()

    captured = {}

    async def invoke_report(
        llm,
        prompt,
        fallback_llm=None,
        allow_retry=True,
        reasoning_effort=ReasoningEffort.LOW,
    ):
        captured.update({
            "llm": llm,
            "prompt": prompt,
            "fallback_llm": fallback_llm,
            "allow_retry": allow_retry,
            "reasoning_effort": reasoning_effort,
        })
        return {"report": "recovered report"}

    runtime = Stage3McpRuntime(
        input_token_target=lambda _request: 10_000,
        estimate_messages_tokens=lambda _messages, **_kwargs: 1,
        continuation_messages=lambda _prompt, _records: [],
        invoke_report=invoke_report,
        response_finished_by_length=lambda candidate: (
            candidate.response_metadata.get("finish_reason") == "max_tokens"
        ),
    )
    request = SimpleNamespace(
        projectVcsWorkspace="workspace",
        projectVcsRepoSlug="repository",
    )
    llm = Llm()

    result = await execute_stage_3_mcp_verification(
        llm,
        request,
        "prompt",
        SimpleNamespace(),
        "commit-sha",
        {},
        runtime,
        fallback_llm=llm,
    )

    assert result["report"] == "recovered report"
    assert result["verification_assessments"] == []
    assert result["dismissed_issue_object_ids"] == []
    assert captured == {
        "llm": llm,
        "prompt": "prompt",
        "fallback_llm": None,
        "allow_retry": False,
        "reasoning_effort": ReasoningEffort.NONE,
    }


@pytest.mark.asyncio(loop_scope="function")
async def test_tool_result_is_followed_by_tools_disabled_terminal_dismissal() -> None:
    issue = CodeReviewIssue(
        file="src/a.py",
        line=10,
        severity="HIGH",
        category="BUG_RISK",
        reason="Claim to verify.",
        suggestedFixDescription="Fix it.",
    )
    tool_response = SimpleNamespace(
        content="",
        tool_calls=[{
            "id": "call-1",
            "name": "getBranchFileContent",
            "args": {
                "filePath": "src/a.py",
                "verificationId": "issue_0",
            },
        }],
        response_metadata={},
    )
    terminal_response = SimpleNamespace(
        content=(
            "Verified report\n"
            '<!-- DISMISSED_ISSUES: ["issue_0"] -->'
                '<!-- VERIFICATION_ASSESSMENTS: [{"issue_id":"issue_0","verdict":"disproven","reason":"Guard rules out trigger","contradicting_evidence":[{"file_path":"src/a.py","snippet":"current source"}]}] -->'
        ),
        tool_calls=[],
        response_metadata={},
    )

    class ToolBoundLlm:
        async def ainvoke(self, _messages, **_kwargs):
            return tool_response

    class Llm:
        def __init__(self):
            self.bound_definitions = []
            self.terminal_messages = None

        def bind_tools(self, tool_definitions):
            self.bound_definitions.append(tool_definitions)
            return ToolBoundLlm()

        async def ainvoke(self, messages, **_kwargs):
            self.terminal_messages = list(messages)
            return terminal_response

    class VcsSession:
        def __init__(self):
            self.calls = []

        async def call_tool(self, name, arguments):
            self.calls.append((name, dict(arguments)))
            return SimpleNamespace(content=[SimpleNamespace(text=(
                '{"fileContent":"current source","startLine":1,'
                '"endLine":90,"completeFile":false}'
            ))])

    async def unexpected_report_fallback(*_args, **_kwargs):
        raise AssertionError("the plain report fallback should not be used")

    runtime = Stage3McpRuntime(
        input_token_target=lambda _request: 10_000,
        estimate_messages_tokens=lambda _messages, **_kwargs: 1,
        continuation_messages=lambda _prompt, _records: [],
        invoke_report=unexpected_report_fallback,
        response_finished_by_length=lambda _response: False,
    )
    request = SimpleNamespace(
        projectVcsWorkspace="workspace",
        projectVcsRepoSlug="repository",
        localRepoPath="/tmp/target",
        localRepoRevision="target-sha",
        localRepoTargetBranch="main",
        localReviewOverlayPath="/tmp/proposed",
    )
    llm = Llm()
    vcs_session = VcsSession()

    result = await execute_stage_3_mcp_verification(
        llm,
        request,
        "prompt",
        SimpleNamespace(session=vcs_session),
        "review-sha",
        {"issue_0": issue},
        runtime,
    )

    assert result["report"] == "Verified report"
    assert result["dismissed_issue_keys"] == ["issue_0"]
    assert result["dismissed_issue_object_ids"] == [id(issue)]
    assert len(llm.bound_definitions) == 1
    assert llm.bound_definitions[0]
    assert llm.terminal_messages is not None
    assert any(
        isinstance(message, dict)
        and message.get("role") == "tool"
        and "current source" in message.get("content", "")
        for message in llm.terminal_messages
    )
    assert "tools are now disabled" in llm.terminal_messages[-1]["content"]
    assert vcs_session.calls[0][0] == "getReviewFileContent"
    assert "branch" not in vcs_session.calls[0][1]


@pytest.mark.asyncio(loop_scope="function")
async def test_terminal_failure_retains_issue_and_ignores_tool_turn_marker() -> None:
    issue = CodeReviewIssue(
        file="src/a.py",
        line=10,
        severity="HIGH",
        category="BUG_RISK",
        reason="Claim to verify.",
        suggestedFixDescription="Fix it.",
    )
    tool_response = SimpleNamespace(
        content='<!-- DISMISSED_ISSUES: ["issue_0"] -->'
                '<!-- VERIFICATION_ASSESSMENTS: [{"issue_id":"issue_0","verdict":"disproven","reason":"Guard rules out trigger","contradicting_evidence":[{"file_path":"src/a.py","snippet":"current source"}]}] -->',
        tool_calls=[{
            "id": "call-1",
            "name": "getBranchFileContent",
            "args": {
                "filePath": "src/a.py",
                "verificationId": "issue_0",
            },
        }],
        response_metadata={},
    )

    class ToolBoundLlm:
        async def ainvoke(self, _messages, **_kwargs):
            return tool_response

    class Llm:
        def bind_tools(self, _tool_definitions):
            return ToolBoundLlm()

        async def ainvoke(self, _messages, **_kwargs):
            raise RuntimeError("terminal provider failure")

    class VcsSession:
        async def call_tool(self, _name, _arguments):
            return SimpleNamespace(
                content=[SimpleNamespace(text="current source")]
            )

    async def unexpected_report_fallback(*_args, **_kwargs):
        raise AssertionError("the plain report fallback should not be used")

    runtime = Stage3McpRuntime(
        input_token_target=lambda _request: 10_000,
        estimate_messages_tokens=lambda _messages, **_kwargs: 1,
        continuation_messages=lambda _prompt, _records: [],
        invoke_report=unexpected_report_fallback,
        response_finished_by_length=lambda _response: False,
    )
    request = SimpleNamespace(
        projectVcsWorkspace="workspace",
        projectVcsRepoSlug="repository",
    )

    result = await execute_stage_3_mcp_verification(
        Llm(),
        request,
        "prompt",
        SimpleNamespace(session=VcsSession()),
        "review-sha",
        {"issue_0": issue},
        runtime,
    )

    assert result["dismissed_issue_ids"] == []
    assert result["dismissed_issue_keys"] == []
    assert result["dismissed_issue_object_ids"] == []


def test_structured_assessments_preserve_valid_records_and_mark_missing_or_invalid():
    issues = {f"issue_{i}": CodeReviewIssue(file="a.py", line=1, severity="HIGH", category="BUG_RISK",
              reason="Guard allegedly absent", suggestedFixDescription="Guard it") for i in range(3)}
    executor = SimpleNamespace(verification_source={("issue_0", "a.py"): ["if missing: return"]})
    content = json.dumps({"report": "Review summary", "assessments": [
        {"issue_id": "issue_0", "verdict": "disproven", "contradicting_evidence": [
            {"file_path": "a.py", "snippet": "if missing: return"}]},
        {"issue_id": "issue_1", "verdict": "invented"}]})
    report, assessments = stage_3_mcp_verification.extract_verification_assessments(content, executor, issues)
    assert report == "Review summary"
    assert assessments["issue_0"]["verdict"] == "disproven"
    assert assessments["issue_0"]["completed"] is True
    assert all(assessments[key]["verdict"] == "unknown" and not assessments[key]["completed"]
               for key in ("issue_1", "issue_2"))


@pytest.mark.asyncio
async def test_summary_only_first_answer_gets_explicit_terminal_assessment():
    issue = CodeReviewIssue(file="a.py", line=1, severity="HIGH", category="BUG_RISK",
                            reason="Potential crash", suggestedFixDescription="Guard it")
    class Llm:
        def bind_tools(self, _tools):
            return SimpleNamespace(ainvoke=self.acquire)
        async def acquire(self, _messages, **_kwargs):
            return SimpleNamespace(content="Unverified summary", tool_calls=[])
        async def ainvoke(self, messages, **_kwargs):
            assert "tools are now disabled" in messages[-1]["content"]
            return SimpleNamespace(content=json.dumps({"report": "Qualified summary", "assessments": [
                {"issue_id": "issue_0", "verdict": "unknown", "reason": "Caller source not acquired"}]}), tool_calls=[])
    async def no_fallback(*_args, **_kwargs):
        raise AssertionError("No fallback needed")
    runtime = Stage3McpRuntime(lambda _: 10000, lambda *a, **k: 1, lambda p, r: [], no_fallback, lambda _: False)
    result = await execute_stage_3_mcp_verification(Llm(), SimpleNamespace(projectVcsWorkspace="tenant", projectVcsRepoSlug="repo"),
        "prompt", object(), "head", {"issue_0": issue}, runtime)
    assert result["report"] == "Qualified summary"
    assert result["verification_assessments"][0]["completed"] is True
    assert result["verification_assessments"][0]["verdict"] == "unknown"
    assert result["dismissed_issue_keys"] == []


@pytest.mark.asyncio
async def test_recorded_prefetch_budget_goes_directly_to_assessment():
    """Cal.com 11059 exhausted all five reads before a cancelled selection call."""
    fixture = json.loads((Path(__file__).parent / "fixtures" / "stage3_prefetched_tool_budget.json").read_text())
    issues = {record["verification_id"]: CodeReviewIssue(**{
        key: value for key, value in record.items() if key != "verification_id"
    }) for record in fixture["issues"]}
    sources = {entry["source"]["filePath"]: entry["source"] for entry in fixture["observed_sources"]}
    reads, calls = [], []

    class Session:
        async def call_tool(self, name, args):
            reads.append(dict(args))
            return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(sources[args["filePath"]]))])

    class Llm:
        def bind_tools(self, _tools):
            raise AssertionError("All repository calls were spent on the recorded anchors")

        async def ainvoke(self, messages, **_kwargs):
            calls.append(messages)
            assert "tools are now disabled" in messages[-1]["content"]
            rendered = json.dumps(messages)
            assert all(json.dumps(source["fileContent"])[1:-1] in rendered for source in sources.values())
            assessments = [{"issue_id": key, "verdict": "unknown", "reason": "Caller evidence unavailable"}
                           for key in issues]
            assessments[-1].update(verdict="disproven", contradicting_evidence=[{
                "file_path": issues[assessments[-1]["issue_id"]].file, "snippet": "invented guard"}])
            return SimpleNamespace(content=json.dumps({"report": "Acquired evidence retained", "assessments": assessments}),
                                   tool_calls=[])

    async def no_fallback(*_args, **_kwargs):
        raise AssertionError("One tools-disabled assessment completes the review")

    runtime = Stage3McpRuntime(lambda _: 60_000, lambda *a, **k: 1,
        stage_3_aggregation._stage_3_mcp_continuation_messages, no_fallback, lambda _: False)
    result = await execute_stage_3_mcp_verification(Llm(),
        SimpleNamespace(projectVcsWorkspace="calcom", projectVcsRepoSlug="cal.com"),
        "Assess all assigned findings", SimpleNamespace(session=Session()), "reviewed-head", issues, runtime)

    assert len(reads) == 5
    assert len(calls) == 1
    assert len(result["verification_assessments"]) == len(issues) == 9
    assert all(receipt["verdict"] == "unknown" for receipt in result["verification_assessments"])
    assert result["dismissed_issue_keys"] == []


@pytest.mark.asyncio
async def test_acquisition_timeout_preserves_completed_source_for_terminal_assessment(monkeypatch):
    monkeypatch.setenv("REVIEW_FINAL_VERIFICATION_TIMEOUT_SECONDS", "0.1")
    issues = {f"issue_{i}": CodeReviewIssue(file=f"{i}.py", line=1, severity="HIGH", category="BUG_RISK",
              reason="Concrete claim", suggestedFixDescription="Fix it") for i in range(2)}
    cancelled = asyncio.Event()
    class Session:
        async def call_tool(self, _name, args):
            if args["verificationId"] == "issue_1":
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            return SimpleNamespace(content=[SimpleNamespace(text="safe_guard()")])
    class Llm:
        def bind_tools(self, _tools):
            return SimpleNamespace(ainvoke=self.acquire)
        async def acquire(self, _messages, **_kwargs):
            return SimpleNamespace(content="", tool_calls=[{"id": f"call-{i}", "name": "getBranchFileContent",
                "args": {"filePath": f"{i}.py", "verificationId": f"issue_{i}"}} for i in range(2)])
        async def ainvoke(self, messages, **_kwargs):
            assert cancelled.is_set()
            assert "safe_guard()" in json.dumps(messages)
            assert not any(isinstance(m, dict) and m.get("role") == "tool" for m in messages)
            return SimpleNamespace(content=json.dumps({"report": "Partial evidence retained", "assessments": [
                {"issue_id": "issue_0", "verdict": "supported", "supporting_evidence": [{"file_path": "0.py", "snippet": "safe_guard()"}]},
                {"issue_id": "issue_1", "verdict": "unknown", "reason": "Source read timed out"}]}), tool_calls=[])
    async def no_fallback(*_args, **_kwargs):
        raise AssertionError("Reserved finalization should succeed")
    runtime = Stage3McpRuntime(lambda _: 10000, lambda *a, **k: 1,
        stage_3_aggregation._stage_3_mcp_continuation_messages, no_fallback, lambda _: False)
    result = await execute_stage_3_mcp_verification(Llm(), SimpleNamespace(projectVcsWorkspace="tenant", projectVcsRepoSlug="repo"),
        "prompt", SimpleNamespace(session=Session()), "head", issues, runtime)
    assert [a["verdict"] for a in result["verification_assessments"]] == ["supported", "unknown"]
    assert result["dismissed_issue_keys"] == []


@pytest.mark.asyncio
async def test_timeout_recovery_assesses_existing_source_without_an_extra_call(monkeypatch):
    """Both benchmark corpora paid for summary-only recovery after two timeouts."""
    monkeypatch.setenv("REVIEW_FINAL_VERIFICATION_TIMEOUT_SECONDS", "0.05")
    issues = {f"issue_{i}": CodeReviewIssue(file=f"{i}.py", line=1, severity="HIGH", category="BUG_RISK",
              reason="Claim to verify", suggestedFixDescription="Fix it") for i in range(2)}
    calls = []

    class Session:
        async def call_tool(self, _name, args):
            if args["filePath"] == "1.py":
                return SimpleNamespace(content=[SimpleNamespace(text="Tool call failed: unavailable")], isError=True)
            return SimpleNamespace(content=[SimpleNamespace(text="if invalid: return")])

    class Llm:
        def bind_tools(self, _tools):
            return self

        async def ainvoke(self, _messages, **_kwargs):
            calls.append("cancelled assessment")
            await asyncio.Event().wait()

    async def recover(_llm, prompt, **kwargs):
        calls.append("existing recovery")
        assert "if invalid: return" in prompt
        assert "one assessment for every assigned Verification ID" in prompt
        assert kwargs["allow_retry"] is False
        return {"report": json.dumps({"report": "Recovered assessment", "assessments": [
            {"issue_id": key, "verdict": "disproven", "reason": "Existing guard handles trigger",
             "contradicting_evidence": [{"file_path": issue.file, "snippet": "if invalid: return"}]}
            for key, issue in issues.items()
        ]})}

    runtime = Stage3McpRuntime(lambda _: 10000, lambda *a, **k: 1,
        stage_3_aggregation._stage_3_mcp_continuation_messages, recover, lambda _: False)
    result = await execute_stage_3_mcp_verification(Llm(),
        SimpleNamespace(projectVcsWorkspace="tenant", projectVcsRepoSlug="repo"),
        "Assess both findings", SimpleNamespace(session=Session()), "head", issues, runtime)
    assert calls == ["cancelled assessment", "cancelled assessment", "existing recovery"]
    assert result["report"] == "Recovered assessment"
    assert result["dismissed_issue_keys"] == ["issue_0"]
    assert [receipt["verdict"] for receipt in result["verification_assessments"]] == ["disproven", "unknown"]


@pytest.mark.asyncio
async def test_timeout_recovery_cannot_dismiss_when_source_was_omitted():
    issue = CodeReviewIssue(file="a.py", line=1, severity="HIGH", category="BUG_RISK",
                            reason="Concrete claim", suggestedFixDescription="Fix it")
    records = [{"iteration": 0, "toolResults": [
        {"name": "getBranchFileContent", "arguments": {}, "result": "omitted source"}
    ]}]
    state = {"records": records, "token_target": 1, "attempted": True, "executor": object()}

    async def report(_llm, prompt, **_kwargs):
        assert "executive summary only" in prompt
        assert "omitted source" not in prompt
        return {"report": "Candidate retained", "dismissed_issue_object_ids": [id(issue)]}

    runtime = Stage3McpRuntime(lambda _: 1, lambda *a, **k: 2,
        stage_3_aggregation._stage_3_mcp_continuation_messages, report, lambda _: False)
    result = await stage_3_mcp_verification._verification_report_fallback(
        object(), "prompt", runtime, state, {"issue_0": issue}, "verification_timeout")
    assert result["dismissed_issue_object_ids"] == []
    assert result["verification_assessments"][0]["verdict"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_fallback", [False, True])
@pytest.mark.parametrize("prior_assessment", [False, True])
async def test_report_fallback_is_consumed_once_across_outer_timeout(
    monkeypatch, cancel_fallback, prior_assessment,
):
    issues = {f"issue_{i}": CodeReviewIssue(
        file=f"{i}.py", line=1, severity="HIGH", category="BUG_RISK",
        reason="Concrete claim", suggestedFixDescription="Fix it",
    ) for i in range(2)}
    timeouts, calls, decisions = [], [], []
    cancelled = asyncio.Event()
    original_timeout = asyncio.timeout

    def capture_timeout(delay):
        timeout = original_timeout(delay)
        timeouts.append(timeout)
        return timeout

    monkeypatch.setattr(stage_3_mcp_verification.asyncio, "timeout", capture_timeout)
    monkeypatch.setattr(stage_3_mcp_verification, "optional_work_budget", lambda _: 60)
    monkeypatch.setattr(stage_3_mcp_verification, "record_compute_decision",
                        lambda label, **fields: decisions.append((label, fields)))

    class Session:
        async def call_tool(self, _name, _arguments):
            return SimpleNamespace(content=[SimpleNamespace(text="if invalid: return")])

    class Llm:
        def bind_tools(self, _tools):
            return SimpleNamespace(ainvoke=self.acquire)

        async def acquire(self, _messages, **_kwargs):
            calls.append("acquire")
            assessments = [{
                "issue_id": "issue_0", "verdict": "supported",
                "reason": "Previously checked source",
                "supporting_evidence": [{"file_path": "0.py", "snippet": "if invalid: return"}],
            }] if prior_assessment else []
            return SimpleNamespace(content=json.dumps({
                "report": "Initial summary", "assessments": assessments,
            }), tool_calls=[], response_metadata={})

        async def ainvoke(self, prompt, **_kwargs):
            if isinstance(prompt, list):
                calls.append("terminal")
                return SimpleNamespace(content="{unfinished", tool_calls=[],
                                       response_metadata={"finish_reason": "length"})
            calls.append("fallback")
            if cancel_fallback and len(calls) == 3:
                # Expire the actual outer optional timeout only after its inner
                # fallback has started; no wall-clock sleep or timing race.
                timeouts[0].reschedule(asyncio.get_running_loop().time())
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            return SimpleNamespace(content="Fallback summary", tool_calls=[], response_metadata={})

    runtime = Stage3McpRuntime(
        lambda _: 60_000, lambda *args, **kwargs: 1,
        stage_3_aggregation._stage_3_mcp_continuation_messages,
        stage_3_aggregation._invoke_stage_3_report,
        stage_3_aggregation._response_finished_by_length,
    )
    result = await execute_stage_3_mcp_verification(
        Llm(), SimpleNamespace(projectVcsWorkspace="tenant", projectVcsRepoSlug="repo"),
        "Assess both findings", SimpleNamespace(session=Session()), "head", issues, runtime,
    )

    assert calls == ["acquire", "terminal", "fallback"]
    assert cancelled.is_set() is cancel_fallback
    assert result["report"] == ("Initial summary" if cancel_fallback else "Fallback summary")
    assert result["dismissed_issue_ids"] == result["dismissed_issue_keys"] == result["dismissed_issue_object_ids"] == []
    receipts = result["verification_assessments"]
    assert {receipt["candidateObjectId"] for receipt in receipts} == {id(issue) for issue in issues.values()}
    assert [receipt["verdict"] for receipt in receipts] == ["supported" if prior_assessment else "unknown", "unknown"]
    assert receipts[0]["completed"] is prior_assessment
    assert receipts[1]["completed"] is False
    if prior_assessment:
        assert receipts[0]["reason"] == "Previously checked source"
    if cancel_fallback:
        assert ("final_verification_report", {"outcome": "fallback_already_consumed"}) in decisions


@pytest.mark.asyncio
@pytest.mark.parametrize("final_response", ["error", "malformed"])
async def test_partial_assessment_survives_terminal_and_report_failure(final_response):
    issues = {f"issue_{i}": CodeReviewIssue(file=f"{i}.py", line=1, severity="HIGH", category="BUG_RISK",
              reason="Concrete claim", suggestedFixDescription="Fix it") for i in range(2)}
    class Llm:
        def bind_tools(self, _tools):
            return SimpleNamespace(ainvoke=self.acquire)
        async def acquire(self, _messages, **_kwargs):
            return SimpleNamespace(content=json.dumps({"report": "Initial summary", "assessments": [
                {"issue_id": "issue_0", "verdict": "unknown", "reason": "Caller unavailable"}]}), tool_calls=[])
        async def ainvoke(self, _messages, **_kwargs):
            if final_response == "error":
                raise RuntimeError("provider unavailable")
            return SimpleNamespace(content=json.dumps({"report": "Final summary", "assessments": [
                {"issue_id": "issue_1", "verdict": "invented"}]}), tool_calls=[])
    async def failed_report(*_args, **_kwargs):
        raise RuntimeError("report provider unavailable")
    runtime = Stage3McpRuntime(lambda _: 10000, lambda *a, **k: 1, lambda p, r: [], failed_report, lambda _: False)
    result = await execute_stage_3_mcp_verification(Llm(), SimpleNamespace(projectVcsWorkspace="tenant", projectVcsRepoSlug="repo"),
        "prompt", object(), "head", issues, runtime)
    receipts = result["verification_assessments"]
    assert receipts[0]["completed"] is True
    assert receipts[0]["reason"] == "Caller unavailable"
    assert receipts[1]["completed"] is False
    assert result["dismissed_issue_object_ids"] == []
    assert result["report"] in {"Initial summary", "Final summary"}


@pytest.mark.asyncio
async def test_output_limit_fallback_records_every_candidate_without_dismissals():
    issue = CodeReviewIssue(file="a.py", line=1, severity="HIGH", category="BUG_RISK",
                            reason="Concrete claim", suggestedFixDescription="Fix it")
    class Llm:
        def bind_tools(self, _tools):
            return self
        async def ainvoke(self, _messages, **_kwargs):
            return SimpleNamespace(content="{unfinished", tool_calls=[])
    async def report(*_args, **_kwargs):
        return {"report": "Retained candidate", "dismissed_issue_object_ids": [id(issue)]}
    runtime = Stage3McpRuntime(lambda _: 10000, lambda *a, **k: 1, lambda p, r: [], report, lambda _: True)
    result = await execute_stage_3_mcp_verification(Llm(), SimpleNamespace(projectVcsWorkspace="tenant", projectVcsRepoSlug="repo"),
        "prompt", object(), "head", {"issue_0": issue}, runtime)
    assert result["verification_assessments"][0]["reasonCode"] == "verification_output_limit"
    assert result["verification_assessments"][0]["attempted"] is True
    assert result["verification_assessments"][0]["completed"] is False
    assert result["dismissed_issue_object_ids"] == []


def test_related_range_requires_both_endpoints_and_preserves_display_strings():
    from utils.path_identity import repository_source_location
    issue = CodeReviewIssue(file="helper.ts", line=15, severity="HIGH", category="BUG_RISK",
        reason="Contract mismatch", suggestedFixDescription="Normalize it",
        relatedLocations=["webhook.ts", "webhook.ts:12-93", "helper.ts:3-20"])
    locations = stage_3_mcp_verification.required_verification_locations(issue)
    assert locations == {("helper.ts", 15), ("helper.ts", 3), ("helper.ts", 20), ("webhook.ts", 12), ("webhook.ts", 93)}
    assert stage_3_mcp_verification.verification_record("issue_0", issue)["relatedLocations"] == ["helper.ts:3-20", "webhook.ts", "webhook.ts:12-93"]
    evidence = {"tool": "getBranchFileContent", "args": {"filePath": "webhook.ts", "verificationId": "issue_0", "branch": "head"},
        "success": True, "evidence_valid": True, "evidence_start_line": 1, "evidence_end_line": 92}
    covers = stage_3_mcp_verification.mcp_read_covers_location
    assert covers(evidence, "issue_0", "webhook.ts", 12, "head")
    assert not covers(evidence, "issue_0", "webhook.ts", 93, "head")
    evidence["evidence_end_line"] = 93
    assert covers(evidence, "issue_0", "webhook.ts", 93, "head")
    assert repository_source_location("webhook.ts:93-12") == ("webhook.ts:93-12", 0, 0)
    assert repository_source_location("webhook.ts:bad-range") == ("webhook.ts:bad-range", 0, 0)


def test_decoded_source_view_retains_raw_receipt_and_exact_quote_characters():
    from service.review.orchestrator.verification_agent import _decoded_verification_source_view
    source = 'if value == "safe":\r\n    return value\r\n'
    raw = json.dumps({"fileContent": source, "startLine": 1, "endLine": 2, "completeFile": True})
    view = _decoded_verification_source_view(raw)
    assert view.endswith(source)
    record = {"iteration": 1, "assistantContent": "", "toolResults": [{"toolCallId": "read-1",
        "name": "getReviewFileContent", "arguments": {"filePath": "a.py", "verificationId": "issue_0"}, "result": raw}]}
    messages = stage_3_aggregation._stage_3_mcp_continuation_messages("Review prompt", [record])
    assert source in messages[0]["content"]
    assert json.dumps([record], sort_keys=True, ensure_ascii=False, separators=(",", ":")) in messages[0]["content"]
    assert _decoded_verification_source_view("unavailable") == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("omit_source,missing_anchor,timeout", [
    (False, False, False), (True, False, False), (False, True, False), (False, False, True),
], ids=["guard-disproves", "omitted-guard", "missing-mcp-anchor", "timeout-retains-guard"])
async def test_recorded_cal_guard_handoff_accepts_admitted_source_or_mcp_anchor(
    monkeypatch, omit_source, missing_anchor, timeout,
):
    """Replay the actual source/assessment that fixed Cal11059's false bypass claim."""
    from utils.diff_processor import DiffProcessor
    fixture = json.loads((Path(__file__).parent / "fixtures/stage3_prefetched_tool_budget.json").read_text())
    issues = {item["verification_id"]: CodeReviewIssue.model_validate(item) for item in fixture["issues"]}
    sources = {item["source"]["filePath"]: item["source"] for item in fixture["observed_sources"]}
    primary = issues["issue_0"].file
    guard = "process.env.CALCOM_WEBHOOK_SECRET && process.env.CALCOM_APP_CREDENTIAL_ENCRYPTION_KEY;"
    reads, calls = [], []
    if timeout:
        monkeypatch.setenv("REVIEW_FINAL_VERIFICATION_TIMEOUT_SECONDS", "0.05")

    class Session:
        async def call_tool(self, _name, args):
            reads.append(args)
            if missing_anchor and args["filePath"] == primary:
                return SimpleNamespace(content=[SimpleNamespace(text="unavailable")], isError=True)
            return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(sources[args["filePath"]]))], isError=False)

    def response():
        return json.dumps({"report": "Recorded guard assessment", "assessments": [
            fixture["bound_source_assessment"] if key == "issue_0" else
            {"issue_id": key, "verdict": "unknown", "reason": "Not evaluated by this regression"}
            for key in issues
        ]})

    class Llm:
        def bind_tools(self, _tools):
            raise AssertionError("Five original anchor reads already consumed the tool budget")
        async def ainvoke(self, messages, **_kwargs):
            calls.append("model")
            assert (guard in messages[0]["content"]) is not omit_source
            if timeout:
                await asyncio.Event().wait()
            return SimpleNamespace(content=response(), tool_calls=[])

    async def recover(_llm, prompt, **kwargs):
        assert timeout and guard in prompt
        assert kwargs["allow_retry"] is False
        calls.append("recovery")
        return {"report": response()}

    runtime = Stage3McpRuntime(lambda _: 60_000,
        lambda messages, **kwargs: 60_001 if omit_source and "Request HEAD source:" in str(messages) else 1,
        stage_3_aggregation._stage_3_mcp_continuation_messages, recover, lambda _: False)
    result = await execute_stage_3_mcp_verification(Llm(),
        SimpleNamespace(projectVcsWorkspace="calcom", projectVcsRepoSlug="cal.diy", mcpLocalOnly=True),
        "Assess the recorded findings", SimpleNamespace(session=Session()), fixture["review_revision"], issues, runtime,
        processed_diff=DiffProcessor().process(fixture["request_head_diff"]))

    assert len(reads) == 5
    assert calls == (["model", "recovery"] if timeout else ["model"])
    assert len(result["verification_assessments"]) == 9
    assert result["dismissed_issue_keys"] == ([] if omit_source else ["issue_0"])
    first = result["verification_assessments"][0]
    assert first["verdict"] == ("unknown" if omit_source else "disproven")
    # A failed MCP read does not erase the same anchor already admitted from HEAD.


def test_request_head_hunks_remain_separate_and_exclude_deleted_or_malformed_source():
    from utils.diff_processor import DiffProcessor
    from service.review.orchestrator.verification_agent import VerificationAssessment, _validated_assessment
    diff = DiffProcessor().process(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
        "@@ -1,1 +1,1 @@\n-old\n+first\n@@ -9,1 +9,1 @@\n-old\n+second\n"
        "diff --git a/deleted.py b/deleted.py\ndeleted file mode 100644\n--- a/deleted.py\n+++ /dev/null\n@@ -1,1 +0,0 @@\n-secret\n"
        "diff --git a/incomplete.py b/incomplete.py\n--- a/incomplete.py\n+++ b/incomplete.py\n@@ -1,1 +1,2 @@\n-old\n+partial\n")
    executor = SimpleNamespace(verification_source={}, call_log=[])
    runtime = Stage3McpRuntime(lambda _: 10000, lambda *a, **k: 1,
        stage_3_aggregation._stage_3_mcp_continuation_messages, None, lambda _: False)
    prompt = stage_3_mcp_verification._admit_request_head_hunks(diff, "prompt", [{"role": "user", "content": "prompt"}],
        executor, {"issue_0": object()}, "head", runtime, {"records": [], "token_target": 10000})
    assert executor.verification_source == {("issue_0", "a.py"): ["first", "second"]}
    assert not executor.call_log
    assert "secret" not in prompt and "partial" not in prompt
    assessment = VerificationAssessment(issue_id="issue_0", verdict="disproven", reason="invented adjacency",
        contradicting_evidence=[{"file_path": "a.py", "snippet": "first\nsecond"}])
    assert _validated_assessment(assessment, {"a.py": ["first", "second"]})["verdict"] == "unknown"


@pytest.mark.asyncio
async def test_optional_head_source_failure_preserves_partial_admission_and_verification(monkeypatch):
    issue = CodeReviewIssue(file="a.py", line=1, severity="HIGH", category="BUG_RISK", reason="claim", suggestedFixDescription="fix")
    calls = []
    def fail_after_admission(diff, prompt, messages, executor, issues, revision, runtime, state):
        state["source_augmented_prompt"] = prompt + "\nADMITTED SOURCE"
        messages[0] = {"role": "user", "content": state["source_augmented_prompt"]}
        executor.verification_source[("issue_0", "guard.py")] = ["enabled = False"]
        raise ValueError("later optional source unavailable")
    monkeypatch.setattr(stage_3_mcp_verification, "_admit_request_head_hunks", fail_after_admission)
    class Llm:
        def bind_tools(self, _tools):
            return self
        async def ainvoke(self, messages, **_kwargs):
            calls.append(messages)
            assert "ADMITTED SOURCE" in messages[0]["content"]
            return SimpleNamespace(content=json.dumps({"report": "retained", "assessments": [
                {"issue_id": "issue_0", "verdict": "unknown", "reason": "not enough source"}]}), tool_calls=[])
    async def forbidden_fallback(*args, **kwargs):
        raise AssertionError("Optional handoff failure must preserve normal verification")
    runtime = Stage3McpRuntime(lambda _: 10000, lambda *a, **k: 1,
        stage_3_aggregation._stage_3_mcp_continuation_messages, forbidden_fallback, lambda _: False)
    result = await execute_stage_3_mcp_verification(Llm(), SimpleNamespace(projectVcsWorkspace="tenant", projectVcsRepoSlug="repo"),
        "prompt", object(), "head", {"issue_0": issue}, runtime, processed_diff=object())
    assert len(calls) == 1
    assert result["verification_assessments"][0]["completed"] is True
    assert not result["dismissed_issue_keys"]


@pytest.mark.asyncio
async def test_aggregation_wrapper_forwards_processed_diff(monkeypatch):
    processed_diff = object()
    captured = {}
    async def verifier(*args, **kwargs):
        captured.update(kwargs)
        return {"report": "ok"}
    monkeypatch.setattr(stage_3_aggregation, "execute_stage_3_mcp_verification", verifier)
    await stage_3_aggregation._stage_3_with_mcp(object(), SimpleNamespace(mcpLocalOnly=True), "prompt", object(), "head", {},
        processed_diff=processed_diff)
    assert captured["processed_diff"] is processed_diff


def test_stage3_prompt_matches_admitted_head_anchor_coverage():
    from utils.prompts.constants_mcp import STAGE_3_MCP_VERIFICATION_SECTION
    assert "Every admitted block is bound to all assigned" in STAGE_3_MCP_VERIFICATION_SECTION
    assert "line/range anchors within its\npath and bounds" in STAGE_3_MCP_VERIFICATION_SECTION
    assert "A related file without line numbers needs an observed window" in STAGE_3_MCP_VERIFICATION_SECTION
    assert "BASE and primary path-only anchors still require successful\nVerification-ID-bound file-content reads" in STAGE_3_MCP_VERIFICATION_SECTION
    assert "every primary and related anchor covered by an admitted HEAD range" in STAGE_3_MCP_VERIFICATION_SECTION
    assert "blocks do not replace" not in STAGE_3_MCP_VERIFICATION_SECTION
    assert "source retrieved for that verification ID" not in STAGE_3_MCP_VERIFICATION_SECTION
