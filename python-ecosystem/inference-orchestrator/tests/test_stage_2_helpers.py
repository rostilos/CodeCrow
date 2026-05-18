"""
Unit tests for service.review.orchestrator.stage_2_cross_file — helpers.
"""
import json
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from model.output_schemas import CodeReviewIssue
from service.review.orchestrator.stage_2_cross_file import (
    _build_architecture_context,
    _detect_migration_paths,
    _slim_issues_for_stage_2,
)


# ── _build_architecture_context ──────────────────────────────

def _rel(src, tgt, rtype, matched=None):
    return SimpleNamespace(
        sourceFile=src,
        targetFile=tgt,
        relationshipType=SimpleNamespace(value=rtype),
        matchedOn=matched,
    )


def _meta(path, imports=None, extends=None, implements=None):
    return SimpleNamespace(
        path=path,
        imports=imports or [],
        extendsClasses=extends or [],
        implementsInterfaces=implements or [],
    )


class TestBuildArchitectureContext:
    def test_no_enrichment(self):
        result = _build_architecture_context(None, None)
        assert "No architecture context" in result

    def test_with_relationships(self):
        enrichment = SimpleNamespace(
            relationships=[_rel("a.py", "b.py", "IMPORTS", "module_b")],
            fileMetadata=[],
        )
        result = _build_architecture_context(enrichment, ["a.py"])
        assert "a.py" in result
        assert "IMPORTS" in result

    def test_with_hierarchy(self):
        enrichment = SimpleNamespace(
            relationships=[],
            fileMetadata=[_meta("Foo.java", extends=["Bar"])],
        )
        result = _build_architecture_context(enrichment, ["Foo.java"])
        assert "extends Bar" in result

    def test_with_cross_imports(self):
        enrichment = SimpleNamespace(
            relationships=[],
            fileMetadata=[_meta("a.py", imports=["b"])],
        )
        result = _build_architecture_context(enrichment, ["b.py"])
        assert "imports" in result.lower()


# ── _detect_migration_paths ──────────────────────────────────

class TestDetectMigrationPaths:
    def test_none_diff(self):
        result = _detect_migration_paths(None)
        assert "No migration" in result

    def test_no_migrations(self):
        diff = SimpleNamespace(files=[SimpleNamespace(path="src/main.py")])
        result = _detect_migration_paths(diff)
        assert "No migration" in result

    def test_has_migrations(self):
        diff = SimpleNamespace(files=[
            SimpleNamespace(path="db/migrate/001_create_users.sql"),
            SimpleNamespace(path="src/main.py"),
        ])
        result = _detect_migration_paths(diff)
        assert "001_create_users.sql" in result

    def test_sql_file(self):
        diff = SimpleNamespace(files=[SimpleNamespace(path="schema.sql")])
        result = _detect_migration_paths(diff)
        assert "schema.sql" in result


# ── _slim_issues_for_stage_2 ────────────────────────────────

class TestSlimIssues:
    def test_strips_fields(self):
        issue = CodeReviewIssue(
            file="a.py",
            line=10,
            severity="HIGH",
            category="BUG_RISK",
            reason="bug",
            suggestedFixDiff="diff here",
            suggestedFixDescription="fix desc",
        )
        result = json.loads(_slim_issues_for_stage_2([issue]))
        assert len(result) == 1
        assert "suggestedFixDiff" not in result[0]
        assert "suggestedFixDescription" not in result[0]
        assert result[0]["file"] == "a.py"

    def test_empty_list(self):
        result = json.loads(_slim_issues_for_stage_2([]))
        assert result == []
