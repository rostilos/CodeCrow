"""
Extended tests for stages 0 and branch_analysis modules.

Covers: execute_stage_0_planning, execute_branch_analysis,
        execute_branch_reconciliation_direct
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from service.review.orchestrator.stage_0_planning import (
    execute_stage_0_planning,
    _build_fallback_review_plan,
)
from service.review.orchestrator.branch_analysis import (
    execute_branch_analysis,
    execute_branch_reconciliation_direct,
)
from model.multi_stage import ReviewPlan, FileGroup, ReviewFile
from utils.diff_processor import DiffFile, DiffChangeType, ProcessedDiff


# ── execute_stage_0_planning ─────────────────────────────────────

class TestStage0Planning:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_returns_review_plan_from_structured_output(self):
        """Stage 0 should return a ReviewPlan when structured output succeeds."""
        expected_plan = ReviewPlan(
            analysis_summary="Test plan",
            file_groups=[
                FileGroup(
                    group_id="g1",
                    priority="HIGH",
                    rationale="Core logic",
                    files=[ReviewFile(path="a.py")],
                )
            ],
        )
        mock_llm = MagicMock()
        structured = MagicMock()
        structured.ainvoke = AsyncMock(return_value=expected_plan)
        mock_llm.with_structured_output.return_value = structured

        request = MagicMock()
        request.changedFiles = ["a.py"]
        request.deletedFiles = []
        request.rawDiff = "diff content"
        request.prTitle = "Test PR"
        request.prDescription = "desc"
        request.enrichmentData = None
        request.projectRules = None

        result = await execute_stage_0_planning(
            mock_llm, request, is_incremental=False
        )
        assert isinstance(result, ReviewPlan)
        assert result.analysis_summary == "Test plan"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_fallback_on_structured_failure(self):
        """Stage 0 returns a local plan when AI planning fails."""
        mock_llm = MagicMock()
        structured = MagicMock()
        structured.ainvoke = AsyncMock(side_effect=Exception("API error"))
        mock_llm.with_structured_output.return_value = structured
        mock_llm.ainvoke = AsyncMock(side_effect=Exception("Also fails"))

        request = MagicMock()
        request.changedFiles = ["a.py", "b.py"]
        request.deletedFiles = []
        request.rawDiff = "diff"
        request.prTitle = "PR"
        request.prDescription = "desc"
        request.enrichmentData = None
        request.projectRules = None

        result = await execute_stage_0_planning(
            mock_llm, request, is_incremental=False
        )

        assert isinstance(result, ReviewPlan)
        assert result.analysis_summary.startswith("Fallback review plan")
        assert [f.path for g in result.file_groups for f in g.files] == ["a.py", "b.py"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_fallback_on_empty_raw_response(self):
        """Stage 0 does not fail the review when raw AI output is empty."""
        mock_llm = MagicMock()
        structured = MagicMock()
        structured.ainvoke = AsyncMock(side_effect=Exception("API error"))
        mock_llm.with_structured_output.return_value = structured
        raw_response = MagicMock()
        raw_response.content = ""
        mock_llm.ainvoke = AsyncMock(return_value=raw_response)

        request = MagicMock()
        request.changedFiles = ["src/auth/service.py", "README.md"]
        request.deletedFiles = []
        request.rawDiff = "diff"
        request.prTitle = "PR"
        request.prDescription = "desc"
        request.enrichmentData = None
        request.projectRules = None

        result = await execute_stage_0_planning(
            mock_llm, request, is_incremental=False
        )

        assert isinstance(result, ReviewPlan)
        assert len(result.file_groups) == 1
        assert result.file_groups[0].priority == "MEDIUM"
        assert [f.path for f in result.file_groups[0].files] == ["src/auth/service.py", "README.md"]
        assert all(not f.focus_areas for f in result.file_groups[0].files)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_planning_prompt_includes_bounded_diff_evidence(self):
        """Stage 0 should expose neutral diff evidence for LLM risk/skip decisions."""
        expected_plan = ReviewPlan(
            analysis_summary="Test plan",
            file_groups=[
                FileGroup(
                    group_id="g1",
                    priority="MEDIUM",
                    rationale="Core logic",
                    files=[ReviewFile(path="src/big.py")],
                )
            ],
        )
        mock_llm = MagicMock()
        structured = MagicMock()
        structured.ainvoke = AsyncMock(return_value=expected_plan)
        mock_llm.with_structured_output.return_value = structured

        request = MagicMock()
        request.changedFiles = ["src/big.py"]
        request.deletedFiles = []
        request.rawDiff = "diff content"
        request.prTitle = "PR"
        request.prDescription = "desc"
        request.enrichmentData = None
        request.projectRules = None
        request.taskContext = None
        request.projectVcsRepoSlug = "repo"
        request.pullRequestId = 123
        request.prAuthor = "dev"
        request.sourceBranchName = "feature"
        request.targetBranchName = "main"
        request.commitHash = "abc"

        diff_file = DiffFile(
            path="src/big.py",
            change_type=DiffChangeType.MODIFIED,
            content=(
                "diff --git a/src/big.py b/src/big.py\n"
                "--- a/src/big.py\n"
                "+++ b/src/big.py\n"
                "@@ -1 +1,2 @@\n"
                "+added_line()\n"
            ),
            additions=1,
            deletions=0,
            is_skipped=True,
            skip_reason="File too large: 999999 bytes > 1",
        )

        result = await execute_stage_0_planning(
            mock_llm,
            request,
            is_incremental=False,
            processed_diff=ProcessedDiff(files=[diff_file]),
        )

        prompt = structured.ainvoke.call_args.args[0]
        assert result is expected_plan
        assert '"diff_was_limited": true' in prompt
        assert '"processed_skip_reason": "File too large: 999999 bytes > 1"' in prompt
        assert "+added_line()" in prompt
        assert "FULL_DIFF_REVIEW" in prompt

    def test_fallback_plan_skips_only_mechanically_unreviewable_files(self):
        request = MagicMock()
        request.changedFiles = ["assets/logo.png", "src/app.py"]

        binary_file = DiffFile(
            path="assets/logo.png",
            change_type=DiffChangeType.BINARY,
            content="",
            is_binary=True,
            is_skipped=True,
            skip_reason="Binary file",
        )
        code_file = DiffFile(
            path="src/app.py",
            change_type=DiffChangeType.MODIFIED,
            content="+run()\n",
            additions=1,
        )

        result = _build_fallback_review_plan(
            request,
            ProcessedDiff(files=[binary_file, code_file]),
        )

        assert [f.path for g in result.file_groups for f in g.files] == ["src/app.py"]
        assert [f.path for f in result.files_to_skip] == ["assets/logo.png"]


# ── execute_branch_analysis ──────────────────────────────────────

class TestBranchAnalysis:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_returns_result_from_agent(self):
        """Branch analysis returns parsed result from RecursiveMCPAgent."""
        from model.output_schemas import CodeReviewOutput
        mock_llm = MagicMock()
        mock_client = MagicMock()

        with patch("service.review.orchestrator.branch_analysis.RecursiveMCPAgent") as MockAgent:
            agent_instance = MagicMock()
            # Simulate streaming that yields a CodeReviewOutput directly
            output = CodeReviewOutput(issues=[], comment="No issues found.")

            async def fake_stream(*args, **kwargs):
                yield output

            agent_instance.stream = fake_stream
            MockAgent.return_value = agent_instance

            result = await execute_branch_analysis(
                mock_llm, mock_client, "test prompt", None
            )
            assert isinstance(result, dict)
            assert "issues" in result
            assert result["comment"] == "No issues found."


# ── execute_branch_reconciliation_direct ─────────────────────────

class TestBranchReconciliationDirect:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_returns_parsed_structured_output(self):
        """Direct reconciliation returns result from structured output."""
        from model.output_schemas import ReconciliationOutput
        mock_llm = MagicMock()
        structured = MagicMock()
        recon_output = ReconciliationOutput(issues=[], comment="done")
        structured.ainvoke = AsyncMock(return_value=recon_output)
        mock_llm.with_structured_output.return_value = structured

        result = await execute_branch_reconciliation_direct(
            mock_llm, "prompt", None
        )
        assert "issues" in result
        assert result["comment"] == "done"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_falls_back_on_structured_failure(self):
        """Direct reconciliation falls back when structured output fails."""
        from model.output_schemas import ReconciliationOutput
        mock_llm = MagicMock()
        structured = MagicMock()
        structured.ainvoke = AsyncMock(side_effect=Exception("structured fail"))
        mock_llm.with_structured_output.return_value = structured

        # Fallback: ainvoke returns text
        mock_response = MagicMock()
        mock_response.content = '{"issues": [], "comment": "fallback"}'
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        with patch("service.review.orchestrator.branch_analysis.parse_llm_response") as mock_parse:
            mock_parse.return_value = ReconciliationOutput(issues=[], comment="parsed fallback")
            result = await execute_branch_reconciliation_direct(
                mock_llm, "prompt", None
            )
            assert isinstance(result, dict)
            assert "issues" in result
