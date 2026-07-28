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
    _fetch_cross_module_context,
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
        assert "Foo.java" in result
        assert "Bar" in result

    def test_with_cross_imports(self):
        enrichment = SimpleNamespace(
            relationships=[],
            fileMetadata=[_meta("a.py", imports=["b"])],
        )
        result = _build_architecture_context(enrichment, ["b.py"])
        assert "imports" in result.lower()

    def test_large_context_is_bounded_and_keeps_high_value_edges_first(self):
        relationships = [
            _rel(
                f"src/package/Source{index:03d}.java",
                f"src/package/Target{index:03d}.java",
                "SAME_PACKAGE",
                f"package-{index:03d}",
            )
            for index in range(100)
        ]
        relationships.append(
            _rel(
                "src/child/Child.java",
                "src/base/Base.java",
                "EXTENDS",
                "Base",
            )
        )
        enrichment = SimpleNamespace(
            relationships=relationships,
            fileMetadata=[
                _meta(
                    f"src/package/Source{index:03d}.java",
                    imports=[f"external.library.Type{value}" for value in range(20)],
                )
                for index in range(40)
            ],
        )

        result = _build_architecture_context(
            enrichment,
            [],
            max_chars=8_000,
        )
        payload = json.loads(result.split("\n", 1)[1])

        assert len(result) <= 8_000
        assert any(
            relationship["type"] == "EXTENDS"
            for relationship in payload["relationships"]
        )
        assert payload["inventory"]["omitted_relationship_count"] > 0
        assert payload["inventory"]["omitted_metadata_file_count"] > 0
        assert "unknown, not evidence" in payload["inventory"]["omission_semantics"]

    def test_path_table_cannot_overrun_the_total_budget(self):
        very_long_path = "src/" + ("nested/" * 800) + "Source.java"
        enrichment = SimpleNamespace(
            relationships=[
                _rel(very_long_path, "src/Target.java", "IMPORTS", "Target")
            ],
            fileMetadata=[],
        )

        result = _build_architecture_context(
            enrichment,
            [],
            max_chars=2_000,
        )
        payload = json.loads(result.split("\n", 1)[1])

        assert len(result) <= 2_000
        assert payload["relationships"] == []
        assert payload["inventory"]["omitted_relationship_count"] == 1


# ── _detect_migration_paths ──────────────────────────────────

class TestDetectMigrationPaths:
    def test_none_diff(self):
        result = _detect_migration_paths(None)
        assert "not pre-classified" in result

    def test_no_migrations(self):
        diff = SimpleNamespace(files=[SimpleNamespace(path="src/main.py")])
        result = _detect_migration_paths(diff)
        assert "not pre-classified" in result

    def test_has_migrations(self):
        diff = SimpleNamespace(files=[
            SimpleNamespace(path="db/migrate/001_create_users.sql"),
            SimpleNamespace(path="src/main.py"),
        ])
        result = _detect_migration_paths(diff)
        assert "not pre-classified" in result

    def test_sql_file(self):
        diff = SimpleNamespace(files=[SimpleNamespace(path="schema.sql")])
        result = _detect_migration_paths(diff)
        assert "not pre-classified" in result


@pytest.mark.asyncio(loop_scope="function")
async def test_cross_module_context_does_not_guess_main_without_target_branch():
    request = MagicMock()
    request.get_rag_branch.return_value = None
    request.get_rag_base_branch.return_value = None
    rag = MagicMock()
    rag.search_for_duplicates = AsyncMock()

    result = await _fetch_cross_module_context(rag, request)

    assert result == ""
    rag.search_for_duplicates.assert_not_awaited()


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
            resolutionReason="client lifecycle field",
            resolutionExplanation="internal lifecycle field",
        )
        result = json.loads(_slim_issues_for_stage_2([issue]))
        assert len(result) == 1
        assert "suggestedFixDiff" not in result[0]
        assert "suggestedFixDescription" not in result[0]
        assert "resolutionReason" not in result[0]
        assert "resolutionExplanation" not in result[0]
        assert result[0]["file"] == "a.py"

    def test_empty_list(self):
        result = json.loads(_slim_issues_for_stage_2([]))
        assert result == []

    def test_excludes_resolved_history_records(self):
        resolved = CodeReviewIssue(
            id="12524",
            file="Shipping/MethodList.php",
            line=60,
            severity="MEDIUM",
            category="BUG_RISK",
            reason="Previous return-type issue",
            suggestedFixDescription="Use a string default.",
            isResolved=True,
        )

        assert json.loads(_slim_issues_for_stage_2([resolved])) == []

    def test_large_finding_set_is_bounded_without_one_file_starving_others(self):
        issues = [
            CodeReviewIssue(
                id=f"file-{index}",
                file=f"src/File{index:03d}.py",
                line=index + 1,
                severity="HIGH",
                category="BUG_RISK",
                title=f"Finding in file {index}",
                reason="Concrete current defect " + ("detail " * 30),
                codeSnippet=f"broken_{index}()",
                suggestedFixDescription="Correct the defect.",
            )
            for index in range(30)
        ]
        issues.extend(
            CodeReviewIssue(
                id=f"noisy-{index}",
                file="src/File000.py",
                line=index + 100,
                severity="MEDIUM",
                category="BUG_RISK",
                title=f"Additional noisy finding {index}",
                reason="Another current defect " + ("detail " * 30),
                codeSnippet=f"also_broken_{index}()",
                suggestedFixDescription="Correct the defect.",
            )
            for index in range(100)
        )

        result = _slim_issues_for_stage_2(issues, max_chars=30_000)
        payload = json.loads(result)

        assert len(result) <= 30_000
        assert any(item.get("file") == "src/File029.py" for item in payload)
        inventory = payload[-1]["_codecrow_prompt_inventory"]
        assert inventory["total_current_findings"] == 130
        assert inventory["omitted_findings"] > 0
        assert "not proof" in inventory["omission_semantics"]
