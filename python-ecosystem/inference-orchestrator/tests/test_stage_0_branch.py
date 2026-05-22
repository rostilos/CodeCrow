"""
Extended tests for stages 0 and branch_analysis modules.

Covers: execute_stage_0_planning, execute_branch_analysis,
        execute_branch_reconciliation_direct
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from service.review.orchestrator.stage_0_planning import execute_stage_0_planning
from service.review.orchestrator.branch_analysis import (
    execute_branch_analysis,
    execute_branch_reconciliation_direct,
)
from model.multi_stage import ReviewPlan, FileGroup, ReviewFile


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
        assert result.file_groups[0].priority == "HIGH"
        assert result.file_groups[-1].priority == "LOW"


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
