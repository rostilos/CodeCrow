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
    _infer_cross_file_concerns,
    _mechanical_skip_reason,
    _representative_changed_lines,
    _representative_hunk_headers,
    _truncate_planning_line,
    _build_diff_lookup,
    _summarize_file_for_planning,
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
            is_skipped=False,
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

    @pytest.mark.asyncio(loop_scope="function")
    async def test_local_plan_uses_processed_paths_refactoring_and_limited_diff(self):
        request = MagicMock()
        request.changedFiles = []
        limited = DiffFile(
            path="src/large.py",
            change_type=DiffChangeType.MODIFIED,
            content="@@ -1 +1 @@\n-old\n+new",
            skip_reason="File too large for full diff",
        )
        processed = ProcessedDiff(
            files=[limited], refactoring_signals=["file moved without behavior change"]
        )

        plan = await execute_stage_0_planning(
            MagicMock(), request, processed_diff=processed, use_local_planning=True
        )

        assert plan.file_groups[0].files[0].path == "src/large.py"
        assert plan.file_groups[0].files[0].focus_areas == ["SUMMARY_REVIEW"]
        assert plan.cross_file_concerns == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_unstructured_planning_path_parses_provider_response(self):
        request = MagicMock()
        request.changedFiles = ["src/app.py"]
        request.projectVcsRepoSlug = "repo"
        request.pullRequestId = 1
        request.prTitle = None
        request.prAuthor = None
        request.sourceBranchName = None
        request.targetBranchName = None
        request.commitHash = None
        request.taskContext = None
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=MagicMock(content="{}"))
        expected = ReviewPlan(analysis_summary="parsed", file_groups=[])

        with patch(
            "service.review.orchestrator.stage_0_planning.supports_structured_output",
            return_value=False,
        ), patch(
            "service.review.orchestrator.stage_0_planning.parse_llm_response",
            new=AsyncMock(return_value=expected),
        ):
            result = await execute_stage_0_planning(llm, request)

        assert result is expected

    def test_planning_helper_boundaries(self):
        deleted = MagicMock()
        deleted.is_binary = False
        deleted.skip_reason = "Deleted file"
        deleted.change_type.value = "modified"
        assert _mechanical_skip_reason(deleted) == "Deleted file has no new code to review."

        headers = _representative_hunk_headers(
            "\n".join([f"@@ hunk {i} @@" for i in range(4)]), limit=2
        )
        assert headers == ["@@ hunk 0 @@", "@@ hunk 1 @@"]
        changed = _representative_changed_lines(
            "+++ header\n--- header\n+one\n-two\n+three", limit=2
        )
        assert changed == ["+one", "-two"]
        assert _truncate_planning_line("short", max_length=8) == "short"
        assert _truncate_planning_line("0123456789", max_length=8) == "01234..."
        assert _infer_cross_file_concerns(["one.py"]) == []
        assert _infer_cross_file_concerns(["one.py", "two.py"])

    def test_lookup_and_summary_branch_shapes(self):
        plain = DiffFile(
            path="a.py", change_type=DiffChangeType.MODIFIED,
            content="@@ hunk @@\n context", is_skipped=True, skip_reason="Binary file",
        )
        assert _build_diff_lookup(ProcessedDiff(files=[plain])) == {"a.py": plain}
        hunk_only = _summarize_file_for_planning("a.py", plain)
        assert "representative_hunk_headers" in hunk_only
        assert "representative_changed_lines" not in hunk_only

        changed_only = DiffFile(
            path="b.py", change_type=DiffChangeType.ADDED, content="+new"
        )
        summary = _summarize_file_for_planning("b.py", changed_only)
        assert "representative_changed_lines" in summary
        assert "representative_hunk_headers" not in summary
        assert _summarize_file_for_planning("missing.py")["type"] == "MODIFIED"

        request = MagicMock(changedFiles=["a.py"])
        plan = _build_fallback_review_plan(request, ProcessedDiff(files=[plain]))
        assert plan.file_groups == [] and len(plan.files_to_skip) == 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_empty_structured_plan_uses_raw_parse(self):
        request = MagicMock(
            changedFiles=[], projectVcsRepoSlug="repo", pullRequestId=1,
            prTitle=None, prAuthor=None, sourceBranchName=None, targetBranchName=None,
            commitHash=None, taskContext=None,
        )
        llm = MagicMock()
        llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=None)
        llm.ainvoke = AsyncMock(return_value=MagicMock(content="{}"))
        expected = ReviewPlan(analysis_summary="raw", file_groups=[])
        with patch(
            "service.review.orchestrator.stage_0_planning.parse_llm_response",
            new=AsyncMock(return_value=expected),
        ):
            assert await execute_stage_0_planning(llm, request) is expected


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

    @pytest.mark.asyncio(loop_scope="function")
    async def test_parses_final_stream_text_and_handles_empty_stream(self):
        from model.output_schemas import CodeReviewOutput

        async def text_stream(*args, **kwargs):
            yield "intermediate"
            yield "final json"

        async def empty_stream(*args, **kwargs):
            if False:
                yield None

        with patch("service.review.orchestrator.branch_analysis.RecursiveMCPAgent") as agent_cls, patch(
            "service.review.orchestrator.branch_analysis.parse_llm_response",
            new=AsyncMock(return_value=CodeReviewOutput(issues=[], comment="parsed")),
        ):
            agent_cls.return_value.stream = text_stream
            result = await execute_branch_analysis(MagicMock(), MagicMock(), "prompt")
            assert result == {"issues": [], "comment": "parsed"}

            agent_cls.return_value.stream = empty_stream
            result = await execute_branch_analysis(MagicMock(), MagicMock(), "prompt")
            assert result == {"issues": [], "comment": "No issues found."}

    @pytest.mark.asyncio(loop_scope="function")
    async def test_ignores_non_text_intermediate_stream_items(self):
        from model.output_schemas import CodeReviewOutput

        async def stream(*args, **kwargs):
            yield object()
            yield "final"

        with patch("service.review.orchestrator.branch_analysis.RecursiveMCPAgent") as agent_cls, patch(
            "service.review.orchestrator.branch_analysis.parse_llm_response",
            new=AsyncMock(return_value=CodeReviewOutput(issues=[], comment="parsed")),
        ):
            agent_cls.return_value.stream = stream
            result = await execute_branch_analysis(MagicMock(), MagicMock(), "prompt")
        assert result["comment"] == "parsed"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_propagates_agent_failure_after_emitting_error(self):
        callback = MagicMock()

        async def broken_stream(*args, **kwargs):
            raise RuntimeError("agent failed")
            yield None

        with patch("service.review.orchestrator.branch_analysis.RecursiveMCPAgent") as agent_cls:
            agent_cls.return_value.stream = broken_stream
            with pytest.raises(RuntimeError, match="agent failed"):
                await execute_branch_analysis(MagicMock(), MagicMock(), "prompt", callback)

        assert any(call.args[0].get("type") == "error" for call in callback.call_args_list)


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

    @pytest.mark.asyncio(loop_scope="function")
    async def test_unstructured_empty_response_returns_no_resolutions(self):
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=MagicMock(content=""))
        with patch(
            "service.review.orchestrator.branch_analysis.supports_structured_output",
            return_value=False,
        ):
            result = await execute_branch_reconciliation_direct(llm, "prompt")

        assert result == {"issues": [], "comment": "No issues resolved."}

    @pytest.mark.asyncio(loop_scope="function")
    async def test_unexpected_structured_value_falls_through_to_raw(self):
        llm = MagicMock()
        llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value={"unexpected": True}
        )
        llm.ainvoke = AsyncMock(return_value=MagicMock(content=""))
        result = await execute_branch_reconciliation_direct(llm, "prompt")
        assert result == {"issues": [], "comment": "No issues resolved."}

    @pytest.mark.asyncio(loop_scope="function")
    async def test_unstructured_failure_is_emitted_and_propagated(self):
        callback = MagicMock()
        llm = MagicMock()
        llm.ainvoke = AsyncMock(side_effect=RuntimeError("provider failed"))
        with patch(
            "service.review.orchestrator.branch_analysis.supports_structured_output",
            return_value=False,
        ):
            with pytest.raises(RuntimeError, match="provider failed"):
                await execute_branch_reconciliation_direct(llm, "prompt", callback)

        assert any(call.args[0].get("type") == "error" for call in callback.call_args_list)
