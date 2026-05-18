"""
Unit tests for service.review.orchestrator.mcp_tool_executor — McpToolExecutor.
"""
import asyncio
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from service.review.orchestrator.mcp_tool_executor import McpToolExecutor


def _make_request():
    return SimpleNamespace(
        projectVcsWorkspace="ws",
        projectVcsRepoSlug="repo",
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

    def test_invalid_stage(self):
        with pytest.raises(ValueError, match="Unknown stage"):
            McpToolExecutor(MagicMock(), _make_request(), "stage_99")


# ── execute_tool ─────────────────────────────────────────────

class TestExecuteTool:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_disallowed_tool(self):
        e = McpToolExecutor(MagicMock(), _make_request(), "stage_1")
        result = await e.execute_tool("deleteBranch", {})
        assert "not allowed" in result

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
    async def test_call_failure(self):
        mock_client = MagicMock()
        mock_client.session.call_tool = AsyncMock(side_effect=Exception("timeout"))
        e = McpToolExecutor(mock_client, _make_request(), "stage_1")
        result = await e.execute_tool("getBranchFileContent", {"filePath": "a.py", "branch": "main"})
        assert "failed" in result.lower()
        assert len(e.call_log) == 1
        assert e.call_log[0]["success"] is False

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
