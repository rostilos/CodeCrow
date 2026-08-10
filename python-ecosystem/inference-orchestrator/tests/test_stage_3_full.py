"""Tests for stage_3_aggregation: summarizers, dismissed issues, MCP stage 3."""
import json
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock, patch
from model.output_schemas import CodeReviewIssue
from service.review.orchestrator.stage_3_aggregation import (
    execute_stage_3_aggregation,
    _summarize_issues_for_stage_3,
    _summarize_plan_for_stage_3,
    _extract_dismissed_issues,
    _stage_3_with_mcp,
)


# ── _summarize_issues_for_stage_3 ─────────────────────────────


class TestSummarizeIssuesStage3:
    def test_empty_issues(self):
        result = _summarize_issues_for_stage_3([])
        assert "No issues found" in result

    def test_severity_counts(self):
        issues = []
        for sev in ["HIGH", "HIGH", "MEDIUM", "LOW"]:
            issue = MagicMock()
            issue.severity = sev
            issue.category = "BUG_RISK"
            issue.id = f"id-{sev}"
            issue.title = f"Title {sev}"
            issue.file = "a.py"
            issue.reason = "Some reason text here"
            issues.append(issue)
        result = _summarize_issues_for_stage_3(issues)
        assert "Total issues: 4" in result
        assert "HIGH: 2" in result
        assert "MEDIUM: 1" in result

    def test_complete_records_keep_stable_verification_identity(self):
        critical = MagicMock()
        critical.severity = "CRITICAL"
        critical.category = "SECURITY"
        critical.id = "c1"
        critical.title = "Critical Bug"
        critical.file = "main.py"
        critical.reason = "SQL injection"

        low = MagicMock()
        low.severity = "LOW"
        low.category = "STYLE"
        low.id = "l1"
        low.title = "Style issue"
        low.file = "utils.py"
        low.reason = "Naming convention"

        result = _summarize_issues_for_stage_3([low, critical])
        records = json.loads(result.split("Complete verification records (JSON):\n", 1)[1])
        assert records[0]["verification_id"] == "issue_0"
        assert records[0]["original_id"] == "l1"
        assert records[1]["verification_id"] == "issue_1"
        assert records[1]["original_id"] == "c1"

    def test_all_issue_ids_listed(self):
        issues = []
        for i in range(3):
            issue = MagicMock()
            issue.severity = "MEDIUM"
            issue.category = "BUG_RISK"
            issue.id = f"issue-{i}"
            issue.title = ""
            issue.file = "a.py"
            issue.reason = "Reason"
            issues.append(issue)
        result = _summarize_issues_for_stage_3(issues)
        assert "issue-0" in result
        assert "issue-1" in result
        assert "issue-2" in result

    def test_issue_without_title(self):
        issue = MagicMock()
        issue.severity = "HIGH"
        issue.category = "BUG_RISK"
        issue.id = "no-title"
        issue.title = ""
        issue.file = "a.py"
        issue.reason = "Missing import causes failure"
        result = _summarize_issues_for_stage_3([issue])
        assert "no-title" in result

    def test_issue_without_id(self):
        issue = MagicMock()
        issue.severity = "HIGH"
        issue.category = "BUG_RISK"
        issue.id = ""
        issue.title = "Some title"
        issue.file = "a.py"
        issue.reason = "Reason here"
        result = _summarize_issues_for_stage_3([issue])
        assert "Total issues: 1" in result


# ── _summarize_plan_for_stage_3 ───────────────────────────────


class TestSummarizePlanStage3:
    def test_basic_plan(self):
        plan = MagicMock()
        group = MagicMock()
        f1 = MagicMock()
        f1.path = "a.py"
        group.files = [f1]
        group.priority = "HIGH"
        plan.file_groups = [group]
        plan.cross_file_concerns = []
        result = _summarize_plan_for_stage_3(plan)
        assert "Total files planned" in result
        assert "HIGH: 1" in result

    def test_cross_file_concerns(self):
        plan = MagicMock()
        group = MagicMock()
        group.files = []
        group.priority = "MEDIUM"
        plan.file_groups = [group]
        plan.cross_file_concerns = ["Concern A", "Concern B"]
        result = _summarize_plan_for_stage_3(plan)
        assert "Concern A" in result
        assert "Concern B" in result

    def test_many_files_truncated(self):
        plan = MagicMock()
        group = MagicMock()
        files = [MagicMock(path=f"file_{i}.py") for i in range(25)]
        group.files = files
        group.priority = "LOW"
        plan.file_groups = [group]
        plan.cross_file_concerns = []
        result = _summarize_plan_for_stage_3(plan)
        assert "... and 5 more" in result


# ── _extract_dismissed_issues ─────────────────────────────────


class TestExtractDismissedIssues:
    def test_no_marker(self):
        content = "Just a report"
        report, dismissed = _extract_dismissed_issues(content)
        assert report == content
        assert dismissed == []

    def test_extracts_ids(self):
        content = 'Report text\n<!-- DISMISSED_ISSUES: ["id1", "id2"] -->\nMore'
        report, dismissed = _extract_dismissed_issues(content)
        assert "id1" in dismissed
        assert "id2" in dismissed
        assert "DISMISSED_ISSUES" not in report

    def test_malformed_json(self):
        content = '<!-- DISMISSED_ISSUES: not_json -->'
        report, dismissed = _extract_dismissed_issues(content)
        assert dismissed == []

    def test_non_list_value(self):
        content = '<!-- DISMISSED_ISSUES: {"key": "val"} -->'
        report, dismissed = _extract_dismissed_issues(content)
        assert dismissed == []

    def test_empty_list(self):
        content = 'Hello\n<!-- DISMISSED_ISSUES: [] -->\nEnd'
        report, dismissed = _extract_dismissed_issues(content)
        assert dismissed == []


# ── execute_stage_3_aggregation ───────────────────────────────


class TestExecuteStage3Aggregation:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_basic_no_mcp(self):
        llm = MagicMock()
        resp = MagicMock()
        resp.content = "Final report: all good"
        llm.ainvoke = AsyncMock(return_value=resp)

        request = MagicMock()
        request.projectVcsRepoSlug = "repo"
        request.pullRequestId = 42
        request.prAuthor = "dev"
        request.prTitle = "Fix stuff"
        request.changedFiles = ["a.py"]
        request.targetBranchName = "main"
        request.previousCodeAnalysisIssues = []
        request.currentCommitHash = None
        request.commitHash = None
        request.taskContext = None

        plan = MagicMock()
        plan.file_groups = []
        plan.cross_file_concerns = []

        stage_2 = MagicMock()
        stage_2.model_dump_json.return_value = "{}"
        stage_2.pr_recommendation = "APPROVE"

        result = await execute_stage_3_aggregation(
            llm, request, plan, [], stage_2
        )
        assert "report" in result
        assert result["dismissed_issue_ids"] == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_incremental_review_context(self):
        llm = MagicMock()
        resp = MagicMock()
        resp.content = "Incremental report"
        llm.ainvoke = AsyncMock(return_value=resp)

        request = MagicMock()
        request.projectVcsRepoSlug = "repo"
        request.pullRequestId = 1
        request.prAuthor = "dev"
        request.prTitle = "Update"
        request.changedFiles = []
        request.targetBranchName = ""
        request.previousCodeAnalysisIssues = ["prev1", "prev2"]
        request.currentCommitHash = None
        request.commitHash = None
        request.taskContext = None

        plan = MagicMock()
        plan.file_groups = []
        plan.cross_file_concerns = []

        stage_2 = MagicMock()
        stage_2.model_dump_json.return_value = "{}"
        stage_2.pr_recommendation = "REQUEST_CHANGES"

        # No processed_diff
        result = await execute_stage_3_aggregation(
            llm, request, plan, [], stage_2, is_incremental=True
        )
        assert "report" in result

    @pytest.mark.asyncio(loop_scope="function")
    async def test_mcp_stage_dispatches(self):
        """An immutable reviewed commit enables the MCP verification loop."""
        llm = MagicMock()
        mcp_client = MagicMock()

        request = MagicMock()
        request.projectVcsRepoSlug = "repo"
        request.pullRequestId = 1
        request.prAuthor = "dev"
        request.prTitle = "Fix"
        request.changedFiles = []
        request.targetBranchName = "main"
        request.previousCodeAnalysisIssues = []
        request.currentCommitHash = "abc123"
        request.commitHash = None
        request.taskContext = None

        plan = MagicMock()
        plan.file_groups = []
        plan.cross_file_concerns = []

        stage_2 = MagicMock()
        stage_2.model_dump_json.return_value = "{}"
        stage_2.pr_recommendation = "APPROVE"

        with patch("service.review.orchestrator.stage_3_aggregation._stage_3_with_mcp") as mock_mcp:
            mock_mcp.return_value = {"report": "mcp report", "dismissed_issue_ids": ["x"]}
            result = await execute_stage_3_aggregation(
                llm, request, plan, [], stage_2,
                mcp_client=mcp_client, use_mcp_tools=True,
            )
            mock_mcp.assert_called_once()
            assert result["dismissed_issue_ids"] == ["x"]


class TestStage3McpVerification:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_successful_revision_read_validates_fresh_issue_dismissal(self):
        issue = CodeReviewIssue(
            file="src/a.py", line=10, severity="HIGH", category="BUG_RISK",
            reason="Claim to verify.", suggestedFixDescription="Fix it.",
        )
        request = SimpleNamespace(
            projectVcsWorkspace="workspace",
            projectVcsRepoSlug="repo",
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
        final_response = SimpleNamespace(
            content=(
                "Verified report\n"
                '<!-- DISMISSED_ISSUES: ["issue_0"] -->'
            ),
            tool_calls=[],
            response_metadata={},
        )
        bound_llm = MagicMock()
        bound_llm.ainvoke = AsyncMock(
            side_effect=[tool_response, final_response]
        )
        llm = MagicMock()
        llm.bind_tools.return_value = bound_llm
        mcp_client = MagicMock()
        mcp_client.session.call_tool = AsyncMock(return_value=SimpleNamespace(
            content=[SimpleNamespace(text="current source")]
        ))

        result = await _stage_3_with_mcp(
            llm,
            request,
            "prompt",
            mcp_client,
            "commit-abc",
            {"issue_0": issue},
        )

        assert result["report"] == "Verified report"
        assert result["dismissed_issue_ids"] == []
        assert result["dismissed_issue_keys"] == ["issue_0"]
        assert result["dismissed_issue_object_ids"] == [id(issue)]
        call_args = mcp_client.session.call_tool.await_args.args[1]
        assert call_args["branch"] == "commit-abc"
        assert call_args["startLine"] == 1
        assert call_args["endLine"] == 90
