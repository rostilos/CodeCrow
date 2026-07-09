"""
Tests for MultiStageReviewOrchestrator helper methods and _convert_cross_file_issues.

Covers: _filter_diff_for_files, _split_issues_into_batches,
        _deduplicate_previous_issues, _ensure_all_files_planned,
        _count_files, _convert_cross_file_issues
"""
import pytest
from unittest.mock import MagicMock

from service.review.orchestrator.orchestrator import (
    MultiStageReviewOrchestrator,
    _convert_cross_file_issues,
)
from model.multi_stage import (
    CrossFileIssue,
    ReviewPlan,
    ReviewFile,
    FileGroup,
    FileToSkip,
)
from model.output_schemas import CodeReviewIssue


@pytest.fixture
def orchestrator():
    return MultiStageReviewOrchestrator(
        llm=MagicMock(),
        mcp_client=MagicMock(),
    )


# ── _filter_diff_for_files ──────────────────────────────────────

class TestFilterDiffForFiles:
    def test_filters_relevant_sections(self):
        raw_diff = (
            "diff --git a/src/main.py b/src/main.py\n"
            "--- a/src/main.py\n"
            "+++ b/src/main.py\n"
            "@@ -1,3 +1,4 @@\n"
            "+import os\n"
            "diff --git a/src/utils.py b/src/utils.py\n"
            "--- a/src/utils.py\n"
            "+++ b/src/utils.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        result = MultiStageReviewOrchestrator._filter_diff_for_files(
            raw_diff, {"src/main.py"}
        )
        assert "src/main.py" in result
        assert "src/utils.py" not in result

    def test_none_diff(self):
        assert MultiStageReviewOrchestrator._filter_diff_for_files(None, {"a.py"}) is None

    def test_empty_files(self):
        assert MultiStageReviewOrchestrator._filter_diff_for_files("diff...", set()) is None

    def test_no_matching_files(self):
        raw_diff = "diff --git a/other.py b/other.py\n---\n+++\n@@\n"
        result = MultiStageReviewOrchestrator._filter_diff_for_files(
            raw_diff, {"missing.py"}
        )
        assert result is None

    def test_both_a_b_paths_checked(self):
        raw_diff = "diff --git a/old_name.py b/new_name.py\n---\n+++\n@@\n"
        result = MultiStageReviewOrchestrator._filter_diff_for_files(
            raw_diff, {"new_name.py"}
        )
        assert result is not None


# ── _split_issues_into_batches ───────────────────────────────────

class TestSplitIssuesIntoBatches:
    def test_single_batch(self, orchestrator):
        issues = [{"file": "a.py", "title": "Issue 1"}]
        batches = orchestrator._split_issues_into_batches(issues)
        assert len(batches) == 1
        assert len(batches[0]) == 1

    def test_groups_by_file(self, orchestrator):
        issues = [
            {"file": "a.py", "title": "1"},
            {"file": "a.py", "title": "2"},
            {"file": "b.py", "title": "3"},
        ]
        batches = orchestrator._split_issues_into_batches(issues)
        assert len(batches) >= 1
        total = sum(len(b) for b in batches)
        assert total == 3

    def test_respects_max_issues_cap(self, orchestrator):
        # Create more issues than _BRANCH_BATCH_MAX_ISSUES
        issues = [{"file": f"file{i}.py", "title": f"Issue {i}"} for i in range(50)]
        batches = orchestrator._split_issues_into_batches(issues)
        for batch in batches:
            assert len(batch) <= orchestrator._BRANCH_BATCH_MAX_ISSUES

    def test_empty_issues(self, orchestrator):
        batches = orchestrator._split_issues_into_batches([])
        assert batches == []

    def test_unknown_file(self, orchestrator):
        issues = [{"title": "No file key"}]
        batches = orchestrator._split_issues_into_batches(issues)
        assert len(batches) == 1


# ── _deduplicate_previous_issues ─────────────────────────────────

class TestDeduplicatePreviousIssues:
    def test_empty(self):
        assert MultiStageReviewOrchestrator._deduplicate_previous_issues([]) == []

    def test_no_duplicates(self):
        issues = [
            {"file": "a.py", "lineHash": "h1", "category": "BUG", "title": "Bug A", "severity": "HIGH"},
            {"file": "b.py", "lineHash": "h2", "category": "BUG", "title": "Bug B", "severity": "HIGH"},
        ]
        result = MultiStageReviewOrchestrator._deduplicate_previous_issues(issues)
        assert len(result) == 2

    def test_location_dedup(self):
        issues = [
            {"file": "a.py", "lineHash": "abc", "category": "BUG", "title": "Bug 1", "severity": "HIGH"},
            {"file": "a.py", "lineHash": "abc", "category": "BUG", "title": "Bug 1 again", "severity": "MEDIUM"},
        ]
        result = MultiStageReviewOrchestrator._deduplicate_previous_issues(issues)
        assert len(result) == 1
        assert result[0]["severity"] == "HIGH"  # Keeps higher severity

    def test_semantic_dedup(self):
        issues = [
            {"file": "a.py", "lineHash": "h1", "category": "BUG", "title": "Null pointer dereference in handler", "severity": "HIGH"},
            {"file": "a.py", "lineHash": "h2", "category": "BUG", "title": "Null pointer dereference in the handler method", "severity": "MEDIUM"},
        ]
        result = MultiStageReviewOrchestrator._deduplicate_previous_issues(issues)
        assert len(result) == 1  # Similar titles deduped

    def test_different_files_not_deduped(self):
        issues = [
            {"file": "a.py", "lineHash": "h1", "category": "BUG", "title": "Same title", "severity": "HIGH"},
            {"file": "b.py", "lineHash": "h1", "category": "BUG", "title": "Same title", "severity": "HIGH"},
        ]
        result = MultiStageReviewOrchestrator._deduplicate_previous_issues(issues)
        assert len(result) == 2  # Different files = different issues

    def test_no_line_hash_not_location_deduped(self):
        issues = [
            {"file": "a.py", "lineHash": "", "category": "BUG", "title": "Issue A", "severity": "HIGH"},
            {"file": "a.py", "lineHash": "", "category": "BUG", "title": "Issue B completely different", "severity": "MEDIUM"},
        ]
        result = MultiStageReviewOrchestrator._deduplicate_previous_issues(issues)
        assert len(result) == 2  # Different titles, no lineHash


# ── _ensure_all_files_planned ────────────────────────────────────

class TestEnsureAllFilesPlanned:
    def test_no_missing(self, orchestrator):
        plan = ReviewPlan(
            analysis_summary="ok",
            file_groups=[
                FileGroup(
                    group_id="g1",
                    priority="HIGH",
                    rationale="test",
                    files=[ReviewFile(path="a.py")],
                )
            ],
        )
        result = orchestrator._ensure_all_files_planned(plan, ["a.py"])
        assert len(result.file_groups) == 1

    def test_adds_missing_files(self, orchestrator):
        plan = ReviewPlan(
            analysis_summary="ok",
            file_groups=[
                FileGroup(
                    group_id="g1",
                    priority="HIGH",
                    rationale="test",
                    files=[ReviewFile(path="a.py")],
                )
            ],
        )
        result = orchestrator._ensure_all_files_planned(plan, ["a.py", "b.py", "c.py"])
        assert len(result.file_groups) == 2
        catchall = result.file_groups[-1]
        assert catchall.group_id == "uncategorized"
        paths = [f.path for f in catchall.files]
        assert "b.py" in paths
        assert "c.py" in paths

    def test_does_not_readd_files_skipped_by_stage_0(self, orchestrator):
        plan = ReviewPlan(
            analysis_summary="ok",
            file_groups=[],
            files_to_skip=[
                FileToSkip(path="package-lock.json", reason="No substantive changed content")
            ],
        )

        result = orchestrator._ensure_all_files_planned(
            plan,
            ["package-lock.json", "src/app.py"],
        )

        assert len(result.file_groups) == 1
        assert [f.path for f in result.file_groups[0].files] == ["src/app.py"]
        assert result.files_to_skip[0].path == "package-lock.json"

    def test_empty_changed_files(self, orchestrator):
        plan = ReviewPlan(
            analysis_summary="ok",
            file_groups=[],
        )
        result = orchestrator._ensure_all_files_planned(plan, [])
        assert len(result.file_groups) == 0


# ── _count_files ─────────────────────────────────────────────────

class TestCountFiles:
    def test_counts_all(self, orchestrator):
        plan = ReviewPlan(
            analysis_summary="ok",
            file_groups=[
                FileGroup(
                    group_id="g1",
                    priority="HIGH",
                    rationale="test",
                    files=[ReviewFile(path="a.py"), ReviewFile(path="b.py")],
                ),
                FileGroup(
                    group_id="g2",
                    priority="MEDIUM",
                    rationale="test",
                    files=[ReviewFile(path="c.py")],
                ),
            ],
        )
        assert orchestrator._count_files(plan) == 3

    def test_empty_plan(self, orchestrator):
        plan = ReviewPlan(analysis_summary="ok", file_groups=[])
        assert orchestrator._count_files(plan) == 0


# ── _convert_cross_file_issues ───────────────────────────────────

class TestConvertCrossFileIssues:
    def test_basic_conversion(self):
        cfi = CrossFileIssue(
            id="cfi-1",
            severity="HIGH",
            category="ARCHITECTURE",
            title="Circular dependency",
            primary_file="a.py",
            affected_files=["a.py", "b.py"],
            description="Circular dependency between modules",
            evidence="a imports b, b imports a",
            business_impact="Hard to maintain",
            suggestion="Use dependency injection",
            line=42,
            codeSnippet="import b",
        )
        result = _convert_cross_file_issues([cfi])
        assert len(result) == 1
        issue = result[0]
        assert issue.id == "cfi-1"
        assert issue.severity == "HIGH"
        assert issue.file == "a.py"
        assert issue.line == 42
        assert issue.codeSnippet == "import b"
        assert "Also affects: b.py" in issue.reason

    def test_no_primary_file_uses_first_affected(self):
        cfi = CrossFileIssue(
            id="cfi-2",
            severity="MEDIUM",
            category="BUG",
            title="Missing error handling",
            primary_file="",
            affected_files=["x.py", "y.py"],
            description="desc",
            evidence="ev",
            business_impact="impact",
            suggestion="fix",
        )
        result = _convert_cross_file_issues([cfi])
        assert result[0].file == "x.py"

    def test_no_line_defaults_to_1(self):
        cfi = CrossFileIssue(
            id="cfi-3",
            severity="LOW",
            category="STYLE",
            title="Inconsistent naming",
            affected_files=["a.py"],
            description="d",
            evidence="e",
            business_impact="b",
            suggestion="s",
        )
        result = _convert_cross_file_issues([cfi])
        assert result[0].line == 1

    def test_empty_list(self):
        assert _convert_cross_file_issues([]) == []

    def test_multiple_issues(self):
        issues = [
            CrossFileIssue(
                id=f"cfi-{i}",
                severity="MEDIUM",
                category="BUG",
                title=f"Issue {i}",
                affected_files=["a.py"],
                description="d",
                evidence="e",
                business_impact="b",
                suggestion="s",
            )
            for i in range(3)
        ]
        result = _convert_cross_file_issues(issues)
        assert len(result) == 3
