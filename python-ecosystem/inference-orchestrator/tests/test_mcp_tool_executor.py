"""
Unit tests for service.review.orchestrator.mcp_tool_executor — McpToolExecutor.
"""
import asyncio
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from service.review.orchestrator.mcp_tool_executor import McpToolExecutor
from service.review.telemetry import (
    ExecutionIdentity,
    ExecutionTelemetryRecorder,
    MemoryTelemetrySink,
    StageOutcome,
    VersionAttribution,
    bind_telemetry,
    reset_telemetry,
)


def _make_request():
    return SimpleNamespace(
        projectVcsWorkspace="ws",
        projectVcsRepoSlug="repo",
    )


def _make_manifest_request():
    return SimpleNamespace(
        projectVcsWorkspace="ws",
        projectVcsRepoSlug="repo",
        executionManifest=SimpleNamespace(executionId="candidate-1"),
    )


# ── Construction ─────────────────────────────────────────────

class TestConstruction:
    def test_valid_stage(self):
        e = McpToolExecutor(MagicMock(), _make_request(), "stage_1")
        assert e.allowed_tools == {"getBranchFileContent"}
        assert e.max_calls == 3

    def test_stage_3(self):
        e = McpToolExecutor(MagicMock(), _make_request(), "stage_3")
        assert "getPullRequestComments" in e.allowed_tools
        assert e.max_calls == 5

    def test_manifest_bound_stage_3_excludes_live_comment_tool(self):
        e = McpToolExecutor(MagicMock(), _make_manifest_request(), "stage_3")
        assert "getPullRequestComments" not in e.allowed_tools

    def test_invalid_stage(self):
        with pytest.raises(ValueError, match="Unknown stage"):
            McpToolExecutor(MagicMock(), _make_request(), "stage_99")

    def test_unknown_allowed_tool_is_ignored_in_definitions(self):
        executor = McpToolExecutor(MagicMock(), _make_request(), "stage_1")
        executor.allowed_tools = {"unknown", "getBranchFileContent"}
        definitions = executor.get_tool_definitions()
        assert [item["function"]["name"] for item in definitions] == [
            "getBranchFileContent"
        ]


# ── execute_tool ─────────────────────────────────────────────

class TestExecuteTool:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_disallowed_tool(self):
        e = McpToolExecutor(MagicMock(), _make_request(), "stage_1")
        result = await e.execute_tool("deleteBranch", {})
        assert "not allowed" in result

    @pytest.mark.asyncio(loop_scope="function")
    async def test_manifest_bound_request_rejects_live_comment_read(self):
        client = MagicMock()
        client.session.call_tool = AsyncMock()
        executor = McpToolExecutor(client, _make_manifest_request(), "stage_3")

        result = await executor.execute_tool(
            "getPullRequestComments", {"pullRequestId": "42"}
        )

        assert "not bound" in result
        assert executor.call_count == 0
        client.session.call_tool.assert_not_awaited()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_budget_exhausted(self):
        e = McpToolExecutor(MagicMock(), _make_request(), "stage_1")
        e.call_count = 3  # already at max
        result = await e.execute_tool("getBranchFileContent", {"filePath": "a.py", "branch": "main"})
        assert "budget exhausted" in result.lower() or "budget" in result.lower()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_successful_call(self):
        mock_client = MagicMock()
        block = SimpleNamespace(text="file content here")
        mock_client.session.call_tool = AsyncMock(
            return_value=SimpleNamespace(content=[block])
        )
        e = McpToolExecutor(mock_client, _make_request(), "stage_1")
        result = await e.execute_tool("getBranchFileContent", {"filePath": "a.py", "branch": "main"})
        assert result == "file content here"
        assert e.call_count == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_legacy_stage_3_comment_call_skips_file_revision_binding(self):
        mock_client = MagicMock()
        mock_client.session.call_tool = AsyncMock(return_value="comments")
        executor = McpToolExecutor(mock_client, _make_request(), "stage_3")

        result = await executor.execute_tool(
            "getPullRequestComments",
            {"pullRequestId": "42"},
        )

        assert result == "comments"
        assert mock_client.session.call_tool.await_args.args[1] == {
            "pullRequestId": "42",
            "workspace": "ws",
            "repoSlug": "repo",
        }

    @pytest.mark.asyncio(loop_scope="function")
    async def test_call_failure(self):
        mock_client = MagicMock()
        mock_client.session.call_tool = AsyncMock(side_effect=Exception("timeout"))
        e = McpToolExecutor(mock_client, _make_request(), "stage_1")
        result = await e.execute_tool("getBranchFileContent", {"filePath": "a.py", "branch": "main"})
        assert "failed" in result.lower()
        assert len(e.call_log) == 1
        assert e.call_log[0]["success"] is False
        assert e.call_log[0]["error"] == "Exception"
        assert "timeout" not in repr(e.call_log)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_tool_outcomes_are_recorded_without_arguments(self):
        recorder = ExecutionTelemetryRecorder(
            identity=ExecutionIdentity(
                execution_id="tool-test",
                base_revision="a" * 40,
                head_revision="b" * 40,
            ),
            versions=VersionAttribution(
                provider="scripted",
                model="fixture-v1",
                prompt_version="prompt-v1",
                rules_version="rules-v1",
                policy_version="policy-v1",
                index_version="rag-commit-" + "c" * 40,
            ),
            sink=MemoryTelemetrySink(),
        )
        mock_client = MagicMock()
        mock_client.session.call_tool = AsyncMock(
            return_value=SimpleNamespace(content=[])
        )
        executor = McpToolExecutor(mock_client, _make_request(), "stage_1")
        token = bind_telemetry(recorder)
        try:
            await executor.execute_tool(
                "getBranchFileContent",
                {"filePath": "secret/customer.py", "branch": "private"},
            )
            await executor.execute_tool("deleteBranch", {"credential": "secret-123"})
        finally:
            reset_telemetry(token)

        assert [call.outcome for call in recorder.tool_calls] == [
            StageOutcome.COMPLETE,
            StageOutcome.SKIPPED,
        ]
        assert "secret/customer.py" not in repr(recorder.tool_calls)
        assert "secret-123" not in repr(recorder.tool_calls)
        assert executor.call_log == [
            {"tool": "getBranchFileContent", "success": True}
        ]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_tool_telemetry_failure_does_not_replace_tool_result(self):
        recorder = MagicMock()
        recorder.record_tool_call.side_effect = RuntimeError("telemetry closed")
        mock_client = MagicMock()
        mock_client.session.call_tool = AsyncMock(return_value="tool result")
        executor = McpToolExecutor(mock_client, _make_request(), "stage_1")
        token = bind_telemetry(recorder)
        try:
            result = await executor.execute_tool(
                "getBranchFileContent", {"filePath": "a.py", "branch": "main"}
            )
        finally:
            reset_telemetry(token)

        assert result == "tool result"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_prefills_workspace(self):
        mock_client = MagicMock()
        mock_client.session.call_tool = AsyncMock(
            return_value=SimpleNamespace(content=[])
        )
        e = McpToolExecutor(mock_client, _make_request(), "stage_1")
        await e.execute_tool("getBranchFileContent", {"filePath": "a.py", "branch": "main"})
        call_args = mock_client.session.call_tool.call_args[0]
        assert call_args[1]["workspace"] == "ws"
        assert call_args[1]["repoSlug"] == "repo"


# ── get_tool_definitions ─────────────────────────────────────

class TestGetToolDefinitions:
    def test_stage_1_definitions(self):
        e = McpToolExecutor(MagicMock(), _make_request(), "stage_1")
        defs = e.get_tool_definitions()
        assert len(defs) == 1
        assert defs[0]["function"]["name"] == "getBranchFileContent"

    def test_stage_3_definitions(self):
        e = McpToolExecutor(MagicMock(), _make_request(), "stage_3")
        defs = e.get_tool_definitions()
        names = {d["function"]["name"] for d in defs}
        assert "getBranchFileContent" in names
        assert "getPullRequestComments" in names

    def test_manifest_bound_stage_3_definitions_exclude_live_comments(self):
        e = McpToolExecutor(MagicMock(), _make_manifest_request(), "stage_3")
        names = {d["function"]["name"] for d in e.get_tool_definitions()}
        assert names == {"getBranchFileContent"}


# ── Properties ───────────────────────────────────────────────

class TestProperties:
    def test_budget_remaining(self):
        e = McpToolExecutor(MagicMock(), _make_request(), "stage_1")
        assert e.budget_remaining == 3
        e.call_count = 2
        assert e.budget_remaining == 1

    def test_budget_exhausted(self):
        e = McpToolExecutor(MagicMock(), _make_request(), "stage_1")
        assert e.budget_exhausted is False
        e.call_count = 3
        assert e.budget_exhausted is True

    def test_summary(self):
        e = McpToolExecutor(MagicMock(), _make_request(), "stage_1")
        s = e.summary()
        assert "stage_1" in s
        assert "0/3" in s
