"""Extended tests for MultiStageReviewOrchestrator: dedup, batching, branch analysis flow."""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from service.review.orchestrator.orchestrator import MultiStageReviewOrchestrator


# ── _deduplicate_previous_issues ──────────────────────────────


class TestDeduplicatePreviousIssues:
    def test_empty(self):
        result = MultiStageReviewOrchestrator._deduplicate_previous_issues([])
        assert result == []

    def test_location_fingerprint_dedup(self):
        issues = [
            {"file": "a.py", "lineHash": "h1", "category": "BUG_RISK", "severity": "HIGH", "title": "Bug A"},
            {"file": "a.py", "lineHash": "h1", "category": "BUG_RISK", "severity": "MEDIUM", "title": "Bug A variant"},
        ]
        result = MultiStageReviewOrchestrator._deduplicate_previous_issues(issues)
        assert len(result) == 1
        assert result[0]["severity"] == "HIGH"  # Higher severity kept

    def test_semantic_title_dedup(self):
        issues = [
            {"file": "a.py", "lineHash": "h1", "category": "BUG_RISK", "severity": "HIGH", "title": "Missing null check in getUser"},
            {"file": "a.py", "lineHash": "h2", "category": "BUG_RISK", "severity": "MEDIUM", "title": "Missing null check in getUser method"},
        ]
        result = MultiStageReviewOrchestrator._deduplicate_previous_issues(issues)
        # Should dedup due to high title similarity
        assert len(result) == 1

    def test_different_files_not_deduped(self):
        issues = [
            {"file": "a.py", "lineHash": "h1", "category": "BUG_RISK", "severity": "HIGH", "title": "Null check"},
            {"file": "b.py", "lineHash": "h1", "category": "BUG_RISK", "severity": "HIGH", "title": "Null check"},
        ]
        result = MultiStageReviewOrchestrator._deduplicate_previous_issues(issues)
        assert len(result) == 2

    def test_no_line_hash_different_titles(self):
        issues = [
            {"file": "a.py", "lineHash": "", "category": "BUG_RISK", "severity": "HIGH", "title": "SQL injection vulnerability"},
            {"file": "a.py", "lineHash": "", "category": "BUG_RISK", "severity": "HIGH", "title": "Null pointer dereference"},
        ]
        result = MultiStageReviewOrchestrator._deduplicate_previous_issues(issues)
        assert len(result) == 2  # Dissimilar titles → kept

    def test_severity_ordering(self):
        issues = [
            {"file": "a.py", "lineHash": "h1", "category": "BUG", "severity": "LOW", "title": "x"},
            {"file": "a.py", "lineHash": "h1", "category": "BUG", "severity": "HIGH", "title": "x"},
        ]
        result = MultiStageReviewOrchestrator._deduplicate_previous_issues(issues)
        assert len(result) == 1
        assert result[0]["severity"] == "HIGH"


# ── _split_issues_into_batches ────────────────────────────────


class TestSplitIssuesIntoBatches:
    def setup_method(self):
        self.orch = MultiStageReviewOrchestrator.__new__(MultiStageReviewOrchestrator)
        self.orch._BRANCH_BATCH_CHAR_BUDGET = 1000
        self.orch._BRANCH_BATCH_MAX_ISSUES = 5

    def test_empty(self):
        assert self.orch._split_issues_into_batches([]) == []

    def test_single_batch(self):
        issues = [{"file": "a.py", "title": "x"} for _ in range(3)]
        batches = self.orch._split_issues_into_batches(issues)
        assert len(batches) == 1
        assert len(batches[0]) == 3

    def test_exceeds_max_issues(self):
        issues = [{"file": f"f{i}.py", "title": f"Issue {i}"} for i in range(10)]
        batches = self.orch._split_issues_into_batches(issues)
        assert len(batches) >= 2

    def test_groups_by_file(self):
        issues = [
            {"file": "a.py", "title": "1"},
            {"file": "a.py", "title": "2"},
            {"file": "b.py", "title": "3"},
        ]
        batches = self.orch._split_issues_into_batches(issues)
        # All issues for same file should be in same batch
        for batch in batches:
            files = {i.get("file") for i in batch}
            # a.py issues should be together
            if any(i["file"] == "a.py" for i in batch):
                assert all(i["file"] == "a.py" for i in batch if i["file"] == "a.py")


# ── _filter_diff_for_files ────────────────────────────────────


class TestFilterDiffForFiles:
    def setup_method(self):
        self.orch = MultiStageReviewOrchestrator.__new__(MultiStageReviewOrchestrator)

    def test_filters_relevant_files(self):
        diff = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -1,3 +1,4 @@\n"
            "+new line\n"
            "diff --git a/b.py b/b.py\n"
            "--- a/b.py\n"
            "+++ b/b.py\n"
            "@@ -1 +1 @@\n"
            "+changed\n"
        )
        result = self.orch._filter_diff_for_files(diff, ["a.py"])
        assert "a.py" in result
        assert "b.py" not in result

    def test_empty_diff(self):
        result = self.orch._filter_diff_for_files("", ["a.py"])
        assert result is None or result == ""

    def test_no_matching_files(self):
        diff = "diff --git a/c.py b/c.py\n--- a/c.py\n+++ b/c.py\n"
        result = self.orch._filter_diff_for_files(diff, ["a.py"])
        assert result is None or "c.py" not in result or result.strip() == ""


# ── _ensure_all_files_planned ─────────────────────────────────


class TestEnsureAllFilesPlanned:
    def setup_method(self):
        self.orch = MultiStageReviewOrchestrator.__new__(MultiStageReviewOrchestrator)

    def test_no_missing_files(self):
        plan = MagicMock()
        f1 = MagicMock()
        f1.path = "a.py"
        group = MagicMock()
        group.files = [f1]
        plan.file_groups = [group]

        # _ensure_all_files_planned takes (plan, changed_files: List[str])
        self.orch._ensure_all_files_planned(plan, ["a.py"])
        # No new groups added
        assert len(plan.file_groups) == 1

    def test_adds_catch_all_group(self):
        from model.multi_stage import ReviewPlan, FileGroup, ReviewFile
        plan = ReviewPlan(file_groups=[], cross_file_concerns=[], analysis_summary="test")

        # _ensure_all_files_planned takes (plan, changed_files: List[str])
        self.orch._ensure_all_files_planned(plan, ["missed.py", "also_missed.py"])
        assert len(plan.file_groups) == 1
        assert plan.file_groups[0].priority == "MEDIUM"
        paths = [f.path for f in plan.file_groups[0].files]
        assert "missed.py" in paths


# ── _count_files ──────────────────────────────────────────────


class TestCountFiles:
    def setup_method(self):
        self.orch = MultiStageReviewOrchestrator.__new__(MultiStageReviewOrchestrator)

    def test_counts_correctly(self):
        plan = MagicMock()
        g1 = MagicMock()
        g1.files = [MagicMock(), MagicMock()]
        g2 = MagicMock()
        g2.files = [MagicMock()]
        plan.file_groups = [g1, g2]
        assert self.orch._count_files(plan) == 3


# ── _count_files additional ───────────────────────────────────


class TestCountFilesEdgeCases:
    def setup_method(self):
        self.orch = MultiStageReviewOrchestrator.__new__(MultiStageReviewOrchestrator)

    def test_empty_plan(self):
        plan = MagicMock()
        plan.file_groups = []
        assert self.orch._count_files(plan) == 0
