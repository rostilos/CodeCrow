"""
Unit tests for service.review.orchestrator.mcp_tool_executor — McpToolExecutor.
"""
import asyncio
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from model.output_schemas import CodeReviewIssue
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
        assert e.max_calls == 4

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
        e.call_count = 4  # already at max
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
        assert e.call_log[0]["result_chars"] == len("file content here")
        assert e.call_log[0]["evidence_valid"] is True
        assert e.call_log[0]["evidence_complete_file"] is True

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
    async def test_mcp_error_result_is_not_valid_file_evidence(self):
        mock_client = MagicMock()
        mock_client.session.call_tool = AsyncMock(return_value=SimpleNamespace(
            content=[SimpleNamespace(text="Error executing tool: unavailable")],
            isError=True,
        ))
        e = McpToolExecutor(mock_client, _make_request(), "stage_1")

        await e.execute_tool(
            "getBranchFileContent",
            {"filePath": "a.py", "branch": "main"},
        )

        assert e.call_log[0]["success"] is False
        assert e.call_log[0]["evidence_valid"] is False

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

    @pytest.mark.asyncio(loop_scope="function")
    async def test_stage_3_pins_file_reads_to_reviewed_revision(self):
        mock_client = MagicMock()
        mock_client.session.call_tool = AsyncMock(
            return_value=SimpleNamespace(content=[SimpleNamespace(text="source")])
        )
        e = McpToolExecutor(
            mock_client,
            _make_request(),
            "stage_3",
            review_revision="commit-abc",
        )

        await e.execute_tool(
            "getBranchFileContent",
            {"filePath": "a.py", "branch": "main"},
        )

        call_args = mock_client.session.call_tool.call_args[0][1]
        assert call_args["branch"] == "commit-abc"
        assert e.call_log[0]["args"]["branch"] == "commit-abc"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_stage_3_requests_window_around_bound_finding_anchor(self):
        issue = CodeReviewIssue(
            file="src/a.py", line=500, severity="HIGH", category="BUG_RISK",
            reason="Concrete defect.", suggestedFixDescription="Fix it.",
        )
        mock_client = MagicMock()
        mock_client.session.call_tool = AsyncMock(
            return_value=SimpleNamespace(content=[SimpleNamespace(
                text=(
                    '{"fileContent":"source","startLine":420,'
                    '"endLine":580,"totalLines":1000,"completeFile":false}'
                )
            )])
        )
        e = McpToolExecutor(
            mock_client,
            _make_request(),
            "stage_3",
            review_revision="commit-abc",
            verification_issues={"issue_0": issue},
        )

        await e.execute_tool("getBranchFileContent", {
            "filePath": "src/a.py",
            "branch": "main",
            "verificationId": "issue_0",
        })

        call_args = mock_client.session.call_tool.await_args.args[1]
        assert call_args["startLine"] == 420
        assert call_args["endLine"] == 580
        assert e.call_log[0]["evidence_valid"] is True
        assert e.call_log[0]["evidence_structured"] is True
        assert e.call_log[0]["evidence_start_line"] == 420
        assert e.call_log[0]["evidence_end_line"] == 580

    @pytest.mark.asyncio(loop_scope="function")
    async def test_stage_3_raw_adapter_response_is_bound_to_requested_window(self):
        issue = CodeReviewIssue(
            file="src/a.py", line=500, severity="HIGH", category="BUG_RISK",
            reason="Concrete defect.", suggestedFixDescription="Fix it.",
        )
        mock_client = MagicMock()
        mock_client.session.call_tool = AsyncMock(
            return_value=SimpleNamespace(
                content=[SimpleNamespace(text="raw source window")]
            )
        )
        e = McpToolExecutor(
            mock_client,
            _make_request(),
            "stage_3",
            review_revision="commit-abc",
            verification_issues={"issue_0": issue},
        )

        await e.execute_tool("getBranchFileContent", {
            "filePath": "src/a.py",
            "branch": "main",
            "verificationId": "issue_0",
        })

        assert e.call_log[0]["evidence_valid"] is True
        assert e.call_log[0]["evidence_structured"] is False
        assert e.call_log[0]["evidence_complete_file"] is False
        assert e.call_log[0]["evidence_start_line"] == 420
        assert e.call_log[0]["evidence_end_line"] == 580

    @pytest.mark.asyncio(loop_scope="function")
    @pytest.mark.parametrize("tool_text", [
        '{"error":"file not found"}',
        'Error executing tool: permission denied',
        (
            '{"fileContent":"[CodeCrow Filter: file too large, omitted]",'
            '"completeFile":false}'
        ),
        '{"fileContent":"","completeFile":true}',
    ])
    async def test_error_empty_and_filtered_results_are_not_source_evidence(
        self,
        tool_text,
    ):
        mock_client = MagicMock()
        mock_client.session.call_tool = AsyncMock(
            return_value=SimpleNamespace(
                content=[SimpleNamespace(text=tool_text)]
            )
        )
        e = McpToolExecutor(
            mock_client,
            _make_request(),
            "stage_3",
            review_revision="commit-abc",
        )

        await e.execute_tool("getBranchFileContent", {
            "filePath": "src/a.py",
            "branch": "commit-abc",
            "verificationId": "issue_0",
        })

        assert e.call_log[0]["success"] is True
        assert e.call_log[0]["evidence_valid"] is False


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
        file_tool = next(
            definition for definition in defs
            if definition["function"]["name"] == "getBranchFileContent"
        )
        assert "verificationId" in file_tool["function"]["parameters"]["required"]
        assert "branch" not in file_tool["function"]["parameters"]["required"]

    def test_related_location_in_reason_drives_a_bound_source_window(self):
        issue = CodeReviewIssue(
            file="src/a.py", line=10, severity="HIGH", category="BUG_RISK",
            reason=(
                "One root cause.\n\n"
                "Also affects: src/b.py:700, src/c.py:900"
            ),
            suggestedFixDescription="Fix it.",
        )
        executor = McpToolExecutor(
            MagicMock(), _make_request(), "stage_3",
            review_revision="commit-abc",
            verification_issues={"issue_0": issue},
        )

        assert executor._verification_line_for_path(
            "issue_0", "src/b.py"
        ) == 700


# ── Properties ───────────────────────────────────────────────

class TestProperties:
    def test_budget_remaining(self):
        e = McpToolExecutor(MagicMock(), _make_request(), "stage_1")
        assert e.budget_remaining == 4
        e.call_count = 2
        assert e.budget_remaining == 2

    def test_budget_exhausted(self):
        e = McpToolExecutor(MagicMock(), _make_request(), "stage_1")
        assert e.budget_exhausted is False
        e.call_count = 4
        assert e.budget_exhausted is True

    def test_summary(self):
        e = McpToolExecutor(MagicMock(), _make_request(), "stage_1")
        s = e.summary()
        assert "stage_1" in s
        assert "0/4" in s
