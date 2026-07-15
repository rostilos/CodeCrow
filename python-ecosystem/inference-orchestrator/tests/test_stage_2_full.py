"""Tests for stage_2_cross_file helpers: architecture context, migration detection, slim issues."""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from service.review.orchestrator.stage_2_cross_file import (
    _build_architecture_context,
    _build_pr_change_summary,
    _build_task_history_context,
    _detect_migration_paths,
    _fetch_cross_module_context,
    _invoke_stage_2_llm,
    _slim_issues_for_stage_2,
    _to_jsonable,
    execute_stage_2_cross_file,
)
from model.multi_stage import CrossFileAnalysisResult
from utils.diff_processor import DiffChangeType, DiffFile, ProcessedDiff


# ── _build_architecture_context ───────────────────────────────


class TestBuildArchitectureContext:
    def test_no_enrichment(self):
        result = _build_architecture_context(enrichment=None, changed_files=[])
        assert "No architecture context" in result

    def test_relationships_section(self):
        enrichment = MagicMock()
        rel = MagicMock()
        rel.sourceFile = "a.py"
        rel.targetFile = "b.py"
        rel.relationshipType = "imports"
        rel.matchedOn = "SomeClass"
        enrichment.relationships = [rel]
        enrichment.fileMetadata = []
        result = _build_architecture_context(enrichment, ["a.py", "b.py"])
        assert "a.py" in result
        assert "imports" in result
        assert "SomeClass" in result

    def test_class_hierarchy(self):
        enrichment = MagicMock()
        enrichment.relationships = []
        meta = MagicMock()
        meta.path = "Foo.java"
        meta.extendsClasses = ["BaseClass"]
        meta.implementsInterfaces = ["IFoo"]
        meta.imports = []
        enrichment.fileMetadata = [meta]
        result = _build_architecture_context(enrichment, [])
        assert "BaseClass" in result
        assert "IFoo" in result

    def test_cross_file_imports(self):
        enrichment = MagicMock()
        enrichment.relationships = []
        meta = MagicMock()
        meta.path = "a.py"
        meta.extendsClasses = []
        meta.implementsInterfaces = []
        meta.imports = ["b.py", "external_lib"]
        enrichment.fileMetadata = [meta]
        result = _build_architecture_context(enrichment, ["a.py", "b.py"])
        assert "imports" in result
        assert "external_lib" in result

    def test_no_ext_or_impl(self):
        enrichment = MagicMock()
        enrichment.relationships = []
        meta = MagicMock()
        meta.path = "a.py"
        meta.extendsClasses = []
        meta.implementsInterfaces = []
        meta.imports = []
        enrichment.fileMetadata = [meta]
        result = _build_architecture_context(enrichment, [])
        assert "Structured enrichment context" in result
        assert "a.py" in result

    def test_no_matched_on(self):
        enrichment = MagicMock()
        rel = MagicMock()
        rel.sourceFile = "a.py"
        rel.targetFile = "b.py"
        rel.relationshipType = "imports"
        rel.matchedOn = None
        enrichment.relationships = [rel]
        enrichment.fileMetadata = []
        result = _build_architecture_context(enrichment, [])
        assert "imports" in result
        assert "matched on" not in result

    def test_jsonable_handles_models_collections_enums_objects_and_fallbacks(self):
        from pydantic import BaseModel
        from enum import Enum

        class Sample(BaseModel):
            value: int

        class Kind(Enum):
            ACTIVE = "active"

        class ObjectValue:
            def __init__(self):
                self.visible = Sample(value=2)
                self._hidden = "secret"
                self.empty = None

        assert _to_jsonable(Sample(value=1)) == {"value": 1}
        assert _to_jsonable({"items": (Sample(value=3), Kind.ACTIVE)}) == {
            "items": [{"value": 3}, "active"]
        }
        assert _to_jsonable(ObjectValue()) == {"visible": {"value": 2}}
        assert _to_jsonable(type("Empty", (), {})()).startswith("<")
        assert _to_jsonable(object()).startswith("<object object at")


# ── _build_pr_change_summary ────────────────────────────────────


class TestBuildPrChangeSummary:
    def test_includes_summarized_oversized_text_diff(self):
        diff_file = DiffFile(
            path="src/big.py",
            change_type=DiffChangeType.MODIFIED,
            additions=4,
            deletions=0,
            content=(
                "diff --git a/src/big.py b/src/big.py\n"
                "--- a/src/big.py\n"
                "+++ b/src/big.py\n"
                "[CodeCrow Summary: diff too large for full inclusion]\n"
                "+line_one()\n"
                "+line_two()\n"
            ),
            is_skipped=False,
            skip_reason="File too large: 999999 bytes > 1",
        )
        processed = ProcessedDiff(files=[diff_file], total_files=1)

        result = _build_pr_change_summary(processed, ["src/big.py"])

        assert "src/big.py" in result
        assert "CodeCrow Summary" in result
        assert "+line_one()" in result

    def test_includes_globally_compacted_text_diff(self):
        diff_file = DiffFile(
            path="src/after_limit.py",
            change_type=DiffChangeType.MODIFIED,
            additions=2,
            deletions=0,
            content=(
                "diff --git a/src/after_limit.py b/src/after_limit.py\n"
                "--- a/src/after_limit.py\n"
                "+++ b/src/after_limit.py\n"
                "[CodeCrow Summary: diff compacted for global raw-diff limit]\n"
                "Change statistics: +2 lines added, -0 lines removed\n"
                "+line_one()\n"
                "+line_two()\n"
            ),
            is_skipped=False,
            skip_reason="Would exceed total size limit: 120000",
        )
        processed = ProcessedDiff(files=[diff_file], total_files=1)

        result = _build_pr_change_summary(processed, ["src/after_limit.py"])

        assert "src/after_limit.py" in result
        assert "Would exceed total size limit" in result
        assert "CodeCrow Summary" in result
        assert "+line_two()" in result

    def test_changed_file_only_summary_empty_and_file_cap(self):
        assert _build_pr_change_summary(None, []) == "No changed file summary available."
        result = _build_pr_change_summary(None, ["a.py", "b.py"], max_files=1)
        assert "- a.py" in result
        assert "and 1 more files" in result
        assert _build_pr_change_summary(None, ["a.py"], max_files=1) == "- a.py"

    def test_processed_summary_deduplicates_notes_and_honors_limits(self):
        files = [
            DiffFile(
                path=f"src/f{i}.py",
                change_type=DiffChangeType.ADDED,
                additions=3,
                content="@@ -0,0 +1,3 @@\n@@ -0,0 +1,3 @@\n+a\n+b\n+c",
            )
            for i in range(3)
        ]
        processed = ProcessedDiff(files=files)

        capped = _build_pr_change_summary(
            processed, [], max_files=1, max_changed_lines_per_file=1
        )
        assert capped.count("Affected region:") == 1
        assert "+a" in capped and "+b" not in capped
        assert "and 2 more changed files" in capped

        truncated = _build_pr_change_summary(processed, [], max_chars=20)
        assert "summary truncated" in truncated

        assert _build_pr_change_summary(ProcessedDiff(files=[]), []) == (
            "No changed file summary available."
        )
        unchanged = DiffFile(
            path="src/context.py", change_type=DiffChangeType.MODIFIED,
            content=" context only",
        )
        assert "Representative changed lines" not in _build_pr_change_summary(
            ProcessedDiff(files=[unchanged]), []
        )


# ── _detect_migration_paths ───────────────────────────────────


class TestDetectMigrationPaths:
    def test_no_processed_diff(self):
        result = _detect_migration_paths(None)
        assert "not pre-classified" in result

    def test_no_migrations(self):
        diff = MagicMock()
        f = MagicMock()
        f.path = "src/main.py"
        diff.files = [f]
        result = _detect_migration_paths(diff)
        assert "not pre-classified" in result

    def test_sql_file_detected(self):
        diff = MagicMock()
        f = MagicMock()
        f.path = "db/schema.sql"
        diff.files = [f]
        result = _detect_migration_paths(diff)
        assert "not pre-classified" in result

    def test_migration_path_detected(self):
        diff = MagicMock()
        f1 = MagicMock()
        f1.path = "src/db/migrate/001_add_table.rb"
        f2 = MagicMock()
        f2.path = "src/alembic/versions/abc.py"  # needs /alembic/ with leading slash
        diff.files = [f1, f2]
        result = _detect_migration_paths(diff)
        assert "not pre-classified" in result

    def test_flyway_detected(self):
        diff = MagicMock()
        f = MagicMock()
        f.path = "src/main/resources/flyway/V1__init.sql"
        diff.files = [f]
        result = _detect_migration_paths(diff)
        assert "not pre-classified" in result


# ── _slim_issues_for_stage_2 ─────────────────────────────────


class TestSlimIssuesForStage2:
    def test_empty(self):
        result = _slim_issues_for_stage_2([])
        assert json.loads(result) == []

    def test_strips_verbose_fields(self):
        issue = MagicMock()
        issue.model_dump.return_value = {
            "id": "1",
            "severity": "HIGH",
            "suggestedFixDiff": "big patch",
            "suggestedFixDescription": "fix it",
            "resolutionExplanation": "resolved",
            "resolvedInCommit": "abc",
            "visibility": "public",
        }
        result = json.loads(_slim_issues_for_stage_2([issue]))
        assert len(result) == 1
        assert "suggestedFixDiff" not in result[0]
        assert "suggestedFixDescription" not in result[0]
        assert "resolutionExplanation" not in result[0]
        assert "resolvedInCommit" not in result[0]
        assert "visibility" not in result[0]
        assert result[0]["id"] == "1"


# ── execute_stage_2_cross_file ────────────────────────────────


class TestExecuteStage2CrossFile:
    def test_task_history_context_ignores_non_string_mock_attribute(self):
        request = MagicMock()

        result = _build_task_history_context(request)

        assert result == "No prior task history available."

    @pytest.mark.asyncio(loop_scope="function")
    async def test_structured_output_success(self):
        from model.multi_stage import CrossFileAnalysisResult
        llm = MagicMock()
        expected = CrossFileAnalysisResult(
            cross_file_issues=[],
            duplication_findings=[],
            pr_recommendation="APPROVE",
            pr_risk_level="LOW",
            confidence="HIGH",
        )
        structured = MagicMock()
        structured.ainvoke = AsyncMock(return_value=expected)
        llm.with_structured_output.return_value = structured

        request = MagicMock()
        request.projectVcsRepoSlug = "repo"
        request.prTitle = "title"
        request.commitHash = "abc"
        request.enrichmentData = None
        request.changedFiles = []
        request.projectRules = None

        plan = MagicMock()
        plan.cross_file_concerns = []

        result = await execute_stage_2_cross_file(
            llm, request, [], plan
        )
        assert result.pr_recommendation == "APPROVE"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_fallback_parse(self):
        from model.multi_stage import CrossFileAnalysisResult
        llm = MagicMock()
        structured = MagicMock()
        structured.ainvoke = AsyncMock(side_effect=Exception("structured fail"))
        llm.with_structured_output.return_value = structured

        resp = MagicMock()
        resp.content = '{"cross_file_issues":[],"duplication_findings":[],"pr_recommendation":"APPROVE"}'
        llm.ainvoke = AsyncMock(return_value=resp)

        request = MagicMock()
        request.projectVcsRepoSlug = "repo"
        request.prTitle = "title"
        request.commitHash = "abc"
        request.enrichmentData = None
        request.changedFiles = []
        request.projectRules = None

        plan = MagicMock()
        plan.cross_file_concerns = []

        with patch("service.review.orchestrator.stage_2_cross_file.parse_llm_response") as mock_parse:
            mock_parse.return_value = CrossFileAnalysisResult(
                cross_file_issues=[], duplication_findings=[], pr_recommendation="APPROVE",
                pr_risk_level="LOW", confidence="HIGH",
            )
            result = await execute_stage_2_cross_file(llm, request, [], plan)
            assert result.pr_recommendation == "APPROVE"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_prefetched_context_and_task_history_are_forwarded(self):
        request = MagicMock()
        request.projectVcsRepoSlug = "repo"
        request.prTitle = None
        request.commitHash = None
        request.enrichmentData = None
        request.changedFiles = []
        request.projectRules = None
        request.taskContext = None
        request.taskHistoryContext = "  prior decision  "
        plan = MagicMock(cross_file_concerns=[])
        expected = CrossFileAnalysisResult(
            cross_file_issues=[], duplication_findings=[],
            pr_recommendation="APPROVE", pr_risk_level="LOW", confidence="HIGH",
        )

        with patch(
            "service.review.orchestrator.stage_2_cross_file.PromptBuilder.build_stage_2_cross_file_prompt",
            return_value="prompt",
        ) as build_prompt, patch(
            "service.review.orchestrator.stage_2_cross_file._invoke_stage_2_llm",
            new=AsyncMock(return_value=expected),
        ):
            result = await execute_stage_2_cross_file(
                MagicMock(), request, [], plan,
                prefetched_cross_module_context="prefetched",
            )

        assert result is expected
        assert build_prompt.call_args.kwargs["cross_module_context"] == "prefetched"
        assert build_prompt.call_args.kwargs["task_history_context"] == "prior decision"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_retries_with_fallback_and_raises_when_both_fail(self):
        request = MagicMock(
            projectVcsRepoSlug="repo", prTitle="title", commitHash="abc",
            enrichmentData=None, changedFiles=[], projectRules=None,
            taskContext=None, taskHistoryContext=None,
        )
        plan = MagicMock(cross_file_concerns=[])
        primary, fallback = MagicMock(), MagicMock()
        expected = CrossFileAnalysisResult(
            cross_file_issues=[], duplication_findings=[],
            pr_recommendation="APPROVE", pr_risk_level="LOW", confidence="HIGH",
        )
        invoke = AsyncMock(side_effect=[None, expected])
        with patch(
            "service.review.orchestrator.stage_2_cross_file._invoke_stage_2_llm", invoke
        ):
            assert await execute_stage_2_cross_file(
                primary, request, [], plan, fallback_llm=fallback
            ) is expected
        assert [call.args[0] for call in invoke.await_args_list] == [primary, fallback]

        with patch(
            "service.review.orchestrator.stage_2_cross_file._invoke_stage_2_llm",
            new=AsyncMock(return_value=None),
        ):
            with pytest.raises(ValueError, match="failed after capped"):
                await execute_stage_2_cross_file(
                    primary, request, [], plan, fallback_llm=primary
                )

        with patch(
            "service.review.orchestrator.stage_2_cross_file._invoke_stage_2_llm",
            new=AsyncMock(return_value=None),
        ):
            with pytest.raises(ValueError, match="failed after capped"):
                await execute_stage_2_cross_file(
                    primary, request, [], plan, fallback_llm=fallback
                )

    @pytest.mark.asyncio(loop_scope="function")
    async def test_invoke_handles_empty_structured_unstructured_and_failure(self):
        llm = MagicMock()
        llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=None)
        llm.ainvoke = AsyncMock(return_value=MagicMock(content="json"))
        expected = CrossFileAnalysisResult(
            cross_file_issues=[], duplication_findings=[],
            pr_recommendation="APPROVE", pr_risk_level="LOW", confidence="HIGH",
        )
        with patch(
            "service.review.orchestrator.stage_2_cross_file.parse_llm_response",
            new=AsyncMock(return_value=expected),
        ):
            assert await _invoke_stage_2_llm(llm, "prompt", "test") is expected

        raw = MagicMock()
        raw.ainvoke = AsyncMock(side_effect=RuntimeError("provider"))
        with patch(
            "service.review.orchestrator.stage_2_cross_file.supports_structured_output",
            return_value=False,
        ):
            assert await _invoke_stage_2_llm(raw, "prompt", "test") is None


class TestFetchCrossModuleContext:
    def _request(self):
        request = MagicMock()
        request.get_rag_branch.return_value = "feature"
        request.get_rag_base_branch.return_value = "main"
        request.commitHash = "abc"
        request.changedFiles = ["a.py"]
        request.prTitle = "Change A"
        request.projectWorkspace = "ws"
        request.projectNamespace = "project"
        return request

    @pytest.mark.asyncio(loop_scope="function")
    async def test_no_client_queries_or_results(self):
        assert await _fetch_cross_module_context(None, self._request()) == ""
        request = self._request()
        request.prTitle = None
        request.changedFiles = []
        client = MagicMock(search_for_duplicates=AsyncMock())
        assert await _fetch_cross_module_context(client, request) == ""
        client.search_for_duplicates.assert_not_awaited()

        request.prTitle = "A title"
        client.search_for_duplicates = AsyncMock(return_value=[])
        assert await _fetch_cross_module_context(client, request) == ""

    @pytest.mark.asyncio(loop_scope="function")
    async def test_formats_unique_bounded_queries_and_recovers_from_failure(self):
        request = self._request()
        duplicate_diff = DiffFile(
            path="a.py", change_type=DiffChangeType.MODIFIED, content="+changed"
        )
        processed = ProcessedDiff(files=[duplicate_diff, duplicate_diff])
        client = MagicMock(search_for_duplicates=AsyncMock(return_value=[{"hit": 1}]))
        with patch(
            "service.review.orchestrator.stage_2_cross_file.format_duplication_context",
            return_value="formatted context",
        ) as formatter:
            result = await _fetch_cross_module_context(client, request, processed)
        assert result == "formatted context"
        queries = client.search_for_duplicates.await_args.kwargs["queries"]
        assert len(queries) == 2
        formatter.assert_called_once()

        client.search_for_duplicates = AsyncMock(side_effect=RuntimeError("rag down"))
        assert await _fetch_cross_module_context(client, request, processed) == ""

        client.search_for_duplicates = AsyncMock(return_value=[{"hit": 1}])
        with patch(
            "service.review.orchestrator.stage_2_cross_file.format_duplication_context",
            return_value="",
        ):
            assert await _fetch_cross_module_context(client, request, processed) == ""
