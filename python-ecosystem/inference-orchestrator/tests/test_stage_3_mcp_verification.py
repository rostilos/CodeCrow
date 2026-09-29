"""Focused boundary tests for the extracted Stage 3 MCP subsystem."""

from types import SimpleNamespace

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
async def test_mcp_subsystem_runs_through_the_typed_runtime_boundary() -> None:
    response = SimpleNamespace(
        content="bounded verification report",
        tool_calls=[],
        response_metadata={},
    )

    class BoundLlm:
        async def ainvoke(self, _messages, **_kwargs):
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

    result = await execute_stage_3_mcp_verification(
        Llm(),
        request,
        "prompt",
        SimpleNamespace(),
        "commit-sha",
        {},
        runtime,
    )

    assert result == {
        "report": "bounded verification report",
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

    assert result == {"report": "recovered report"}
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
        content='<!-- DISMISSED_ISSUES: ["issue_0"] -->',
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
