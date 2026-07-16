"""Full-file policy coverage for the multi-stage review coordinator."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import service.review.orchestrator.orchestrator as orchestrator_module
from model.multi_stage import (
    CrossFileAnalysisResult,
    CrossFileIssue,
    FileGroup,
    ReviewFile,
    ReviewPlan,
)
from model.output_schemas import CodeReviewIssue
from service.review.orchestrator.orchestrator import MultiStageReviewOrchestrator
from service.review.telemetry import StageOutcome
from utils.diff_processor import DiffChangeType, DiffFile, ProcessedDiff


def _request(**overrides):
    values = {
        "projectId": 1,
        "pullRequestId": 2,
        "projectWorkspace": "workspace",
        "projectNamespace": "namespace",
        "changedFiles": ["src/a.py"],
        "previousCodeAnalysisIssues": [],
        "reconciliationFileContents": {},
        "rawDiff": "diff --git a/src/a.py b/src/a.py\n+new",
        "deltaDiff": None,
        "analysisMode": "FULL",
        "useMcpTools": False,
        "enrichmentData": None,
        "executionManifest": None,
    }
    values.update(overrides)
    request = MagicMock(**values)
    request.get_rag_branch.return_value = overrides.get("rag_branch", "feature")
    return request


def _issue(issue_id="i1"):
    return CodeReviewIssue(
        id=issue_id, severity="HIGH", category="BUG_RISK", file="src/a.py",
        line=1, title="Issue", reason="Reason", suggestedFixDescription="Fix",
    )


def _plan():
    return ReviewPlan(
        analysis_summary="plan",
        file_groups=[FileGroup(
            group_id="g", priority="HIGH", rationale="risk",
            files=[ReviewFile(path="src/a.py", focus_areas=[], risk_level="HIGH")],
        )],
        cross_file_concerns=[],
    )


def _profile(fast=False):
    return SimpleNamespace(
        fast_check_enabled=fast,
        describe=lambda: "fast" if fast else "full",
    )


class TestOrchestratorTelemetryHelpers:
    def test_env_log_coverage_plans_artifacts_and_failure_containment(self, monkeypatch):
        monkeypatch.delenv("FLAG", raising=False)
        assert orchestrator_module._env_bool("FLAG", True)
        monkeypatch.setenv("FLAG", "on")
        assert orchestrator_module._env_bool("FLAG", False)
        monkeypatch.delenv("COUNT", raising=False)
        assert orchestrator_module._env_int("COUNT", 2) == 2
        monkeypatch.setenv("COUNT", "bad")
        assert orchestrator_module._env_int("COUNT", 2) == 2
        monkeypatch.setenv("COUNT", "3")
        assert orchestrator_module._env_int("COUNT", 2) == 3
        assert "pr=n/a" in orchestrator_module._review_log_id(
            MagicMock(projectId=1, pullRequestId=None)
        )

        assert MultiStageReviewOrchestrator._hunk_coverage(None).inventory == 0
        represented = DiffFile(
            path="a.py", change_type=DiffChangeType.MODIFIED,
            content="+x", hunks=["h1", "h2"], is_skipped=False,
        )
        skipped = DiffFile(
            path="b.py", change_type=DiffChangeType.MODIFIED,
            content="+y", hunks=["h3"], is_skipped=True,
        )
        coverage = MultiStageReviewOrchestrator._hunk_coverage(
            ProcessedDiff(files=[represented, skipped]), {"a.py"}
        )
        assert (coverage.inventory, coverage.represented, coverage.unrepresented) == (3, 2, 1)
        broken = MagicMock()
        type(broken).files = property(lambda _self: (_ for _ in ()).throw(RuntimeError("bad")))
        assert MultiStageReviewOrchestrator._hunk_coverage(broken).inventory == 0
        assert MultiStageReviewOrchestrator._planned_paths(MagicMock(file_groups=[])) == set()
        bad_plan = MagicMock()
        type(bad_plan).file_groups = property(
            lambda _self: (_ for _ in ()).throw(RuntimeError("bad"))
        )
        assert MultiStageReviewOrchestrator._planned_paths(bad_plan) == set()
        odd_plan = MagicMock(file_groups=[], files_to_skip=[MagicMock(path=None)])
        assert MultiStageReviewOrchestrator._planned_paths(odd_plan) == set()

        orch = MultiStageReviewOrchestrator(MagicMock(), MagicMock())
        orch._record_stage(
            name="planning", producer="stage_0", outcome=StageOutcome.COMPLETE,
            started_ns=0,
        )
        model = _issue()
        assert orch._candidate_artifact_id(model).startswith("candidate:")
        assert orch._candidate_artifact_id({"a": 1}).startswith("candidate:")

        telemetry = MagicMock()
        telemetry.record_stage.side_effect = RuntimeError("reject")
        telemetry.record_lineage.side_effect = RuntimeError("reject")
        orch.telemetry = telemetry
        orch._record_stage(
            name="planning", producer="stage_0", outcome=StageOutcome.COMPLETE,
            started_ns=0,
        )
        orch._record_lineage(producer="test", inputs=[model], outputs=[model])


class TestPrIndexLifecycle:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_disabled_missing_inputs_and_missing_pr_are_skipped(self):
        orch = MultiStageReviewOrchestrator(MagicMock(), MagicMock(), rag_client=None)
        processed = ProcessedDiff(files=[])
        with patch.object(orchestrator_module, "INTERNAL_PR_INDEX_ENABLED", False):
            await orch._index_pr_files(_request(), processed)
        await orch._index_pr_files(_request(), processed)
        orch.rag_client = MagicMock()
        await orch._index_pr_files(_request(pullRequestId=None), processed)

    @pytest.mark.asyncio(loop_scope="function")
    async def test_indexes_full_content_excludes_deleted_and_handles_provider_results(self):
        exact = DiffFile(
            path="src/a.py", change_type=DiffChangeType.MODIFIED, content="+partial"
        )
        suffix = DiffFile(
            path="src/b.py", change_type=DiffChangeType.MODIFIED, content="+partial-b"
        )
        deleted = DiffFile(
            path="src/deleted.py", change_type=DiffChangeType.DELETED, content="-old"
        )
        empty = DiffFile(path="src/empty.py", change_type=DiffChangeType.ADDED, content="")
        enrichment = SimpleNamespace(fileContents=[
            SimpleNamespace(path="root.py", content="root", skipped=False),
            SimpleNamespace(path="src/a.py", content="full-a", skipped=False),
            SimpleNamespace(path="repo/root/src/b.py", content="full-b", skipped=False),
            SimpleNamespace(path="skip.py", content="skip", skipped=True),
        ])
        rag = MagicMock()
        rag.index_pr_files = AsyncMock(return_value={"status": "indexed", "chunks_indexed": 2})
        orch = MultiStageReviewOrchestrator(MagicMock(), MagicMock(), rag_client=rag)
        await orch._index_pr_files(
            _request(enrichmentData=enrichment),
            ProcessedDiff(files=[exact, suffix, deleted, empty]),
        )
        files = rag.index_pr_files.await_args.kwargs["files"]
        assert {item["path"] for item in files} == {"src/a.py", "src/b.py"}
        assert exact.full_content == "full-a" and suffix.full_content == "full-b"
        assert orch._pr_indexed is True and orch._pr_number == 2

        empty_lookup = SimpleNamespace(fileContents=[
            SimpleNamespace(path="ignored.py", content="", skipped=False),
        ])
        await orch._index_pr_files(
            _request(enrichmentData=empty_lookup), ProcessedDiff(files=[empty])
        )

        rag.index_pr_files = AsyncMock(return_value={"status": "skipped"})
        orch._pr_indexed = False
        await orch._index_pr_files(_request(enrichmentData=None), ProcessedDiff(files=[exact]))
        assert orch._pr_indexed is False
        rag.index_pr_files = AsyncMock(side_effect=RuntimeError("rag"))
        diff_only = DiffFile(
            path="src/diff.py", change_type=DiffChangeType.MODIFIED, content="+diff"
        )
        await orch._index_pr_files(_request(), ProcessedDiff(files=[diff_only]))

    @pytest.mark.asyncio(loop_scope="function")
    async def test_no_indexable_files_and_cleanup_success_failure_and_skip(self):
        rag = MagicMock(delete_pr_files=AsyncMock())
        orch = MultiStageReviewOrchestrator(MagicMock(), MagicMock(), rag_client=rag)
        await orch._index_pr_files(_request(), ProcessedDiff(files=[]))
        await orch._cleanup_pr_files(_request())
        orch._pr_number = 2
        await orch._cleanup_pr_files(_request())
        assert orch._pr_number is None and orch._pr_indexed is False
        orch._pr_number = 2
        rag.delete_pr_files = AsyncMock(side_effect=RuntimeError("delete"))
        await orch._cleanup_pr_files(_request())
        assert orch._pr_number is None


class TestBranchReconciliationCoverage:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_delegated_branch_analysis_and_empty_reconciliation(self):
        orch = MultiStageReviewOrchestrator(MagicMock(), MagicMock())
        with patch.object(
            orchestrator_module, "execute_branch_analysis",
            new=AsyncMock(return_value={"issues": []}),
        ):
            assert (await orch.execute_branch_analysis("prompt"))["issues"] == []
        result = await orch.execute_batched_branch_analysis(
            _request(), {"previousCodeAnalysisIssues": []}
        )
        assert result["issues"] == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_single_direct_and_legacy_failure_paths(self):
        issue = {"id": "1", "file": "a.py", "title": "issue", "severity": "HIGH"}
        direct_request = _request(reconciliationFileContents={"a.py": "content"})
        orch = MultiStageReviewOrchestrator(MagicMock(), MagicMock())
        with patch.object(
            orchestrator_module, "execute_branch_reconciliation_direct",
            new=AsyncMock(return_value={"issues": "invalid", "comment": "done"}),
        ):
            result = await orch.execute_batched_branch_analysis(
                direct_request, {"previousCodeAnalysisIssues": [issue]}
            )
        assert result["issues"] == "invalid"

        with patch.object(
            orchestrator_module, "execute_branch_analysis",
            new=AsyncMock(side_effect=RuntimeError("agent")),
        ):
            with pytest.raises(RuntimeError, match="agent"):
                await orch.execute_batched_branch_analysis(
                    _request(reconciliationFileContents={}),
                    {"previousCodeAnalysisIssues": [issue]},
                )

        duplicates = [issue, dict(issue, id="2")]
        with patch.object(
            orchestrator_module, "execute_branch_reconciliation_direct",
            new=AsyncMock(return_value={"issues": [], "comment": "deduped"}),
        ):
            result = await orch.execute_batched_branch_analysis(
                direct_request, {"previousCodeAnalysisIssues": duplicates}
            )
        assert result["comment"] == "deduped"

        blank = {"file": "a.py", "title": "", "reason": ""}
        assert orch._deduplicate_previous_issues([blank, dict(blank)]) == [blank, blank]

        assert orch._filter_diff_for_files(
            "not a diff\ndiff --git a/a.py b/a.py\n+x", {"a.py"}
        ).endswith("+x")

    @pytest.mark.asyncio(loop_scope="function")
    async def test_multi_batch_direct_merges_success_and_contains_failed_batch(self):
        issues = [
            {"id": str(i), "file": f"f{i}.py", "title": f"issue {i}", "severity": "HIGH"}
            for i in range(2)
        ]
        request = _request(
            reconciliationFileContents={"f0.py": "zero", "f1.py": "one"},
            rawDiff=(
                "diff --git a/f0.py b/f0.py\n+x\n"
                "diff --git a/f1.py b/f1.py\n+y\n"
            ),
        )
        orch = MultiStageReviewOrchestrator(MagicMock(), MagicMock())
        orch._BRANCH_BATCH_MAX_ISSUES = 1
        invoke = AsyncMock(side_effect=[
            {"issues": [issues[0]], "comment": "ok"}, RuntimeError("batch"),
        ])
        with patch.object(
            orchestrator_module, "execute_branch_reconciliation_direct", invoke
        ):
            result = await orch.execute_batched_branch_analysis(
                request, {"previousCodeAnalysisIssues": issues}
            )
        assert result["issues"] == [issues[0]]
        assert "FAILED" in result["comment"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_multi_batch_legacy_path_collects_comments(self):
        issues = [
            {"id": str(i), "file": f"f{i}.py", "title": f"issue {i}", "severity": "LOW"}
            for i in range(2)
        ]
        orch = MultiStageReviewOrchestrator(MagicMock(), MagicMock())
        orch._BRANCH_BATCH_MAX_ISSUES = 1
        with patch.object(
            orchestrator_module, "execute_branch_analysis",
            new=AsyncMock(side_effect=[
                {"issues": [], "comment": None},
                {"issues": [], "comment": "checked"},
            ]),
        ):
            result = await orch.execute_batched_branch_analysis(
                _request(reconciliationFileContents={}),
                {"previousCodeAnalysisIssues": issues},
            )
        assert result["issues"] == []
        assert result["comment"].count("checked") == 1


class TestFullPipelineCoverage:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_full_profile_executes_all_stages_and_dismisses_issue(self):
        telemetry = MagicMock()
        telemetry.model_usage_for.return_value = MagicMock()
        orch = MultiStageReviewOrchestrator(
            MagicMock(), MagicMock(), rag_client=MagicMock(), telemetry=telemetry
        )
        request = _request(previousCodeAnalysisIssues=[{"id": "old"}], useMcpTools=True)
        issue = _issue()
        cross = CrossFileIssue(
            id="cross", severity="MEDIUM", category="ARCHITECTURE",
            title="Cross", primary_file="src/a.py", affected_files=["src/a.py", "src/b.py"],
            description="description", evidence="evidence", business_impact="impact",
            suggestion="fix", line=2, codeSnippet="call()",
        )
        cross_result = CrossFileAnalysisResult(
            pr_risk_level="MEDIUM", cross_file_issues=[cross], data_flow_concerns=[],
            pr_recommendation="REVIEW", confidence="HIGH",
        )

        async def index(*_args):
            orch._pr_indexed = True

        with patch.object(
            orchestrator_module, "build_review_inference_profile", return_value=_profile(False)
        ), patch.object(
            orchestrator_module, "with_stage_output_cap", side_effect=lambda llm, *_args: llm
        ), patch.object(orch, "_index_pr_files", side_effect=index), patch.object(
            orchestrator_module, "execute_stage_0_planning", new=AsyncMock(return_value=_plan())
        ), patch.object(
            orchestrator_module, "prefetch_stage_2_cross_module_context",
            new=AsyncMock(return_value="prefetched"),
        ), patch.object(
            orchestrator_module, "execute_stage_1_file_reviews",
            new=AsyncMock(return_value=[issue]),
        ), patch.object(
            orchestrator_module, "deduplicate_cross_batch_issues", side_effect=lambda values: values
        ), patch.object(
            orchestrator_module, "reconcile_previous_issues", new=AsyncMock(return_value=[issue])
        ), patch.object(
            orchestrator_module, "run_verification_agent", new=AsyncMock(return_value=[issue])
        ), patch.object(
            orchestrator_module, "should_run_stage_2", return_value=(True, "policy")
        ), patch.object(
            orchestrator_module, "execute_stage_2_cross_file", new=AsyncMock(return_value=cross_result)
        ), patch.object(
            orchestrator_module, "run_deterministic_evidence_gate", side_effect=lambda values, *_args: values
        ), patch.object(
            orchestrator_module, "should_use_fast_dedup", return_value=False
        ), patch.object(
            orchestrator_module, "deduplicate_final_issues_llm",
            new=AsyncMock(side_effect=lambda _llm, values: values[1:]),
        ), patch.object(
            orchestrator_module, "execute_stage_3_aggregation",
            new=AsyncMock(return_value={"report": "report", "dismissed_issue_ids": ["i1"]}),
        ):
            result = await orch.orchestrate_review(request)
        assert result == {"comment": "report", "issues": [
            item for item in result["issues"] if item["id"] == "cross"
        ]}
        assert telemetry.record_stage.call_count >= 8
        assert telemetry.record_lineage.call_count >= 6

    @pytest.mark.asyncio(loop_scope="function")
    async def test_incremental_nonfast_profile_can_skip_stage2_and_cancel_prefetch(self):
        orch = MultiStageReviewOrchestrator(MagicMock(), MagicMock(), rag_client=MagicMock())
        request = _request(analysisMode="INCREMENTAL", deltaDiff="+delta")
        issue = _issue()

        async def slow_prefetch(*_args, **_kwargs):
            await asyncio.sleep(60)

        with patch.object(
            orchestrator_module, "build_review_inference_profile", return_value=_profile(False)
        ), patch.object(
            orchestrator_module, "with_stage_output_cap", side_effect=lambda llm, *_args: llm
        ), patch.object(orch, "_index_pr_files", new=AsyncMock()), patch.object(
            orchestrator_module, "execute_stage_0_planning", new=AsyncMock(return_value=_plan())
        ), patch.object(
            orchestrator_module, "prefetch_stage_2_cross_module_context", side_effect=slow_prefetch
        ), patch.object(
            orchestrator_module, "execute_stage_1_file_reviews", new=AsyncMock(return_value=[issue])
        ), patch.object(
            orchestrator_module, "deduplicate_cross_batch_issues", side_effect=lambda values: values
        ), patch.object(
            orchestrator_module, "run_verification_agent", new=AsyncMock(return_value=[issue])
        ), patch.object(
            orchestrator_module, "should_run_stage_2", return_value=(False, "policy")
        ), patch.object(
            orchestrator_module, "run_deterministic_evidence_gate", side_effect=lambda values, *_args: values
        ), patch.object(
            orchestrator_module, "should_use_fast_dedup", return_value=False
        ), patch.object(
            orchestrator_module, "deduplicate_final_issues_llm", new=AsyncMock(return_value=[])
        ), patch.object(
            orchestrator_module, "execute_stage_3_aggregation",
            new=AsyncMock(return_value={"report": "incremental", "dismissed_issue_ids": []}),
        ):
            result = await orch.orchestrate_review(request)
        assert result == {"comment": "incremental", "issues": []}

    @pytest.mark.asyncio(loop_scope="function")
    async def test_fast_profile_skips_verification_stage2_and_uses_local_dedup(self):
        orch = MultiStageReviewOrchestrator(MagicMock(), MagicMock(), rag_client=None)
        request = _request()
        issue = _issue()
        with patch.object(
            orchestrator_module, "build_review_inference_profile", return_value=_profile(True)
        ), patch.object(
            orchestrator_module, "with_stage_output_cap", side_effect=lambda llm, *_args: llm
        ), patch.object(orch, "_index_pr_files", new=AsyncMock()), patch.object(
            orchestrator_module, "execute_stage_0_planning", new=AsyncMock(return_value=_plan())
        ), patch.object(
            orchestrator_module, "execute_stage_1_file_reviews", new=AsyncMock(return_value=[issue])
        ), patch.object(
            orchestrator_module, "deduplicate_cross_batch_issues", side_effect=lambda values: values
        ), patch.object(orchestrator_module, "VERIFICATION_ENABLED", False), patch.object(
            orchestrator_module, "should_run_stage_2", return_value=(False, "small")
        ), patch.object(
            orchestrator_module, "run_deterministic_evidence_gate", side_effect=lambda values, *_args: values
        ), patch.object(
            orchestrator_module, "should_use_fast_dedup", return_value=True
        ), patch.object(
            orchestrator_module, "deduplicate_final_issues", side_effect=lambda values: values
        ), patch.object(
            orchestrator_module, "execute_stage_3_aggregation",
            new=AsyncMock(return_value={"report": "fast", "dismissed_issue_ids": []}),
        ):
            result = await orch.orchestrate_review(request)
        assert result["comment"] == "fast"
        assert result["issues"][0]["id"] == "i1"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_stage_failure_cancels_background_index_and_is_propagated(self):
        orch = MultiStageReviewOrchestrator(MagicMock(), MagicMock(), rag_client=MagicMock())

        async def slow_index(*_args):
            await asyncio.sleep(60)

        with patch.object(
            orchestrator_module, "build_review_inference_profile", return_value=_profile(False)
        ), patch.object(
            orchestrator_module, "with_stage_output_cap", side_effect=lambda llm, *_args: llm
        ), patch.object(orch, "_index_pr_files", side_effect=slow_index), patch.object(
            orchestrator_module, "execute_stage_0_planning",
            new=AsyncMock(side_effect=RuntimeError("planning")),
        ):
            with pytest.raises(RuntimeError, match="planning"):
                await orch.orchestrate_review(_request())


class TestOrchestratorConversionCoverage:
    def test_skipped_paths_and_minimal_cross_file_issue_fallbacks(self):
        plan = _plan()
        plan.files_to_skip = [
            SimpleNamespace(path="skip-a.py"),
            SimpleNamespace(path="skip-b.py"),
            SimpleNamespace(path=None),
        ]
        orch = MultiStageReviewOrchestrator(MagicMock(), MagicMock())
        updated = orch._ensure_all_files_planned(plan, ["src/a.py"])
        assert {item.path for item in updated.files_to_skip if item.path} == {
            "skip-a.py", "skip-b.py"
        }

        minimal = CrossFileIssue(
            id="minimal", severity="LOW", category="ARCHITECTURE",
            title="Minimal", primary_file="", affected_files=[],
            description="", evidence="", business_impact="",
            suggestion="", line=None, codeSnippet=None,
        )
        converted = orchestrator_module._convert_cross_file_issues([minimal])[0]
        assert converted.file == "cross-file"
        assert converted.line == 1
        assert converted.reason == "Minimal"
