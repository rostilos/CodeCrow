"""
Unit tests for service.review.orchestrator.stage_2_cross_file — helpers.
"""
import json
from types import SimpleNamespace
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
        assert "Foo.java" in result
        assert "Bar" in result

    def test_with_cross_imports(self):
        enrichment = SimpleNamespace(
            relationships=[],
            fileMetadata=[_meta("a.py", imports=["b"])],
        )
        result = _build_architecture_context(enrichment, ["b.py"])
        assert "imports" in result.lower()

    def test_large_context_preserves_records_and_marks_bounded_metadata_detail(self):
        relationships = [
            _rel(
                f"src/package/Source{index:03d}.java",
                f"src/package/Target{index:03d}.java",
                "CALLS",
                f"method-{index:03d}",
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

        result = _build_architecture_context(enrichment, [])
        payload = json.loads(result.split("\n", 1)[1])

        assert len(payload["relationships"]) == 101
        assert len(payload["file_metadata"]) == 40
        assert any(
            relationship["type"] == "EXTENDS"
            for relationship in payload["relationships"]
        )
        assert all(
            "external.library.Type7" in metadata.get("imports", [])
            for metadata in payload["file_metadata"]
        )
        assert all(
            "external.library.Type19" not in metadata.get("imports", [])
            for metadata in payload["file_metadata"]
        )
        assert all(
            metadata.get("imports_omitted") == 12
            for metadata in payload["file_metadata"]
        )
        assert payload["inventory"]["record_inventory_complete"] is True
        assert payload["inventory"]["metadata_detail_complete"] is False
        assert payload["inventory"]["complete"] is False

    def test_path_table_preserves_very_long_paths(self):
        very_long_path = "src/" + ("nested/" * 800) + "Source.java"
        enrichment = SimpleNamespace(
            relationships=[
                _rel(very_long_path, "src/Target.java", "IMPORTS", "Target")
            ],
            fileMetadata=[],
        )

        result = _build_architecture_context(enrichment, [])
        payload = json.loads(result.split("\n", 1)[1])

        assert very_long_path in payload["path_table"].values()
        assert len(payload["relationships"]) == 1


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


# ── _slim_issues_for_stage_2 ────────────────────────────────

class TestSlimIssues:
    def test_preserves_complete_finding_fields(self):
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
        assert result[0]["suggestedFixDiff"] == "diff here"
        assert result[0]["suggestedFixDescription"] == "fix desc"
        assert result[0]["resolutionReason"] == "client lifecycle field"
        assert result[0]["resolutionExplanation"] == "internal lifecycle field"
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

    def test_large_finding_set_preserves_every_current_finding(self):
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

        result = _slim_issues_for_stage_2(issues)
        payload = json.loads(result)

        assert len(payload) == 130
        assert any(item.get("file") == "src/File029.py" for item in payload)
        assert any(item.get("id") == "noisy-99" for item in payload)
