"""Tests for stage_2_cross_file helpers: architecture context, migration detection, slim issues."""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from service.review.orchestrator.stage_2_cross_file import (
    _build_architecture_context,
    _detect_migration_paths,
    _slim_issues_for_stage_2,
    execute_stage_2_cross_file,
)


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
        rel.relationshipType = MagicMock(value="imports")
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
        assert "Cross-file imports" in result

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
        assert "No architecture context" in result

    def test_no_matched_on(self):
        enrichment = MagicMock()
        rel = MagicMock()
        rel.sourceFile = "a.py"
        rel.targetFile = "b.py"
        rel.relationshipType = MagicMock(value="imports")
        rel.matchedOn = None
        enrichment.relationships = [rel]
        enrichment.fileMetadata = []
        result = _build_architecture_context(enrichment, [])
        assert "imports" in result
        assert "matched on" not in result


# ── _detect_migration_paths ───────────────────────────────────


class TestDetectMigrationPaths:
    def test_no_processed_diff(self):
        result = _detect_migration_paths(None)
        assert "No migration scripts" in result

    def test_no_migrations(self):
        diff = MagicMock()
        f = MagicMock()
        f.path = "src/main.py"
        diff.files = [f]
        result = _detect_migration_paths(diff)
        assert "No migration scripts" in result

    def test_sql_file_detected(self):
        diff = MagicMock()
        f = MagicMock()
        f.path = "db/schema.sql"
        diff.files = [f]
        result = _detect_migration_paths(diff)
        assert "schema.sql" in result

    def test_migration_path_detected(self):
        diff = MagicMock()
        f1 = MagicMock()
        f1.path = "src/db/migrate/001_add_table.rb"
        f2 = MagicMock()
        f2.path = "src/alembic/versions/abc.py"  # needs /alembic/ with leading slash
        diff.files = [f1, f2]
        result = _detect_migration_paths(diff)
        assert "001_add_table.rb" in result
        assert "abc.py" in result

    def test_flyway_detected(self):
        diff = MagicMock()
        f = MagicMock()
        f.path = "src/main/resources/flyway/V1__init.sql"
        diff.files = [f]
        result = _detect_migration_paths(diff)
        assert "V1__init.sql" in result


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
