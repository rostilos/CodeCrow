"""Tests for stage_3_aggregation: summarizers, dismissed issues, MCP stage 3."""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from service.review.orchestrator.stage_3_aggregation import (
    execute_stage_3_aggregation,
    _invoke_stage_3_report,
    _response_finished_by_length,
    _stage_3_with_mcp,
    _summarize_issues_for_stage_3,
    _summarize_plan_for_stage_3,
    _extract_dismissed_issues,
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

    def test_top_findings_priority_order(self):
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
        lines = result.split("\n")
        # Critical should appear before LOW in the top findings section
        top_lines = [l for l in lines if "[CRITICAL]" in l or "[LOW]" in l]
        assert len(top_lines) == 2
        assert "CRITICAL" in top_lines[0]
        assert "LOW" in top_lines[1]

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
        content = '<!-- DISMISSED_ISSUES: [not_json] -->'
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
        """When use_mcp_tools=True and target_branch given, dispatches to MCP."""
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


class TestStage3InvocationAndTools:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_plain_report_retries_output_cap(self):
        primary = MagicMock()
        capped = MagicMock(content="partial", response_metadata={"finish_reason": "length"})
        primary.ainvoke = AsyncMock(return_value=capped)
        fallback = MagicMock()
        fallback.ainvoke = AsyncMock(return_value=MagicMock(content="complete"))

        result = await _invoke_stage_3_report(primary, "prompt", fallback)

        assert result == {"report": "complete", "dismissed_issue_ids": []}
        fallback.ainvoke.assert_awaited_once()

    def test_finish_reason_variants_and_non_mapping_generation_info(self):
        assert _response_finished_by_length(
            MagicMock(response_metadata={"stop_reason": "MAX_TOKENS"})
        )
        response = MagicMock(response_metadata={}, generation_info="unknown")
        assert not _response_finished_by_length(response)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_mcp_executes_tool_then_returns_clean_report(self):
        llm = MagicMock()
        first = MagicMock(tool_calls=[{"name": "search", "args": {"q": "x"}, "id": "1"}])
        second = MagicMock(
            tool_calls=[],
            content='Report\n<!-- DISMISSED_ISSUES: ["old", null, ""] -->',
            response_metadata={},
        )
        bound = MagicMock()
        bound.ainvoke = AsyncMock(side_effect=[first, second])
        llm.bind_tools.return_value = bound
        executor = MagicMock()
        executor.get_tool_definitions.return_value = [{"name": "search"}]
        executor.execute_tool = AsyncMock(return_value={"found": True})

        with patch(
            "service.review.orchestrator.stage_3_aggregation.McpToolExecutor",
            return_value=executor,
        ):
            result = await _stage_3_with_mcp(
                llm, MagicMock(), "prompt", MagicMock(), "main"
            )

        assert result == {"report": "Report", "dismissed_issue_ids": ["old"]}
        executor.execute_tool.assert_awaited_once_with("search", {"q": "x"})
        tool_message = next(
            message for message in bound.ainvoke.await_args_list[1].args[0]
            if isinstance(message, dict) and message.get("role") == "tool"
        )
        assert tool_message["tool_call_id"] == "1"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_mcp_retries_uncapped_and_falls_back_after_exception(self):
        capped_llm = MagicMock()
        capped_bound = MagicMock()
        capped_bound.ainvoke = AsyncMock(return_value=MagicMock(
            tool_calls=[], content="partial",
            response_metadata={"finishReason": "max_output_tokens"},
        ))
        capped_llm.bind_tools.return_value = capped_bound
        fallback = MagicMock()
        fallback_bound = MagicMock()
        fallback_bound.ainvoke = AsyncMock(return_value=MagicMock(
            tool_calls=[], content="complete", response_metadata={},
        ))
        fallback.bind_tools.return_value = fallback_bound
        executor = MagicMock()
        executor.get_tool_definitions.return_value = []

        with patch(
            "service.review.orchestrator.stage_3_aggregation.McpToolExecutor",
            return_value=executor,
        ):
            result = await _stage_3_with_mcp(
                capped_llm, MagicMock(), "prompt", MagicMock(), "main", fallback
            )
        assert result["report"] == "complete"

        broken = MagicMock()
        broken.bind_tools.side_effect = RuntimeError("bind failed")
        with patch(
            "service.review.orchestrator.stage_3_aggregation.McpToolExecutor",
            return_value=executor,
        ), patch(
            "service.review.orchestrator.stage_3_aggregation._invoke_stage_3_report",
            new=AsyncMock(return_value={"report": "plain", "dismissed_issue_ids": []}),
        ) as plain:
            result = await _stage_3_with_mcp(
                broken, MagicMock(), "prompt", MagicMock(), "main"
            )
        assert result["report"] == "plain"
        plain.assert_awaited_once()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_mcp_iteration_limit_falls_back_to_plain_report(self):
        llm = MagicMock()
        bound = MagicMock()
        bound.ainvoke = AsyncMock(return_value=MagicMock(
            tool_calls=[{"name": "search", "args": {}, "id": "id"}]
        ))
        llm.bind_tools.return_value = bound
        executor = MagicMock()
        executor.get_tool_definitions.return_value = []
        executor.execute_tool = AsyncMock(return_value="ok")
        with patch(
            "service.review.orchestrator.stage_3_aggregation.McpToolExecutor",
            return_value=executor,
        ), patch(
            "service.review.orchestrator.stage_3_aggregation._invoke_stage_3_report",
            new=AsyncMock(return_value={"report": "plain", "dismissed_issue_ids": []}),
        ) as plain:
            result = await _stage_3_with_mcp(
                llm, MagicMock(), "prompt", MagicMock(), "main"
            )
        assert result["report"] == "plain"
        assert executor.execute_tool.await_count == 15
        plain.assert_awaited_once()
