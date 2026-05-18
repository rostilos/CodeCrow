"""
Unit tests for service.review.orchestrator.stage_3_aggregation — helpers.
"""
import json
import pytest
from types import SimpleNamespace
from model.output_schemas import CodeReviewIssue
from model.multi_stage import ReviewPlan, FileGroup, ReviewFile
from service.review.orchestrator.stage_3_aggregation import (
    _summarize_issues_for_stage_3,
    _summarize_plan_for_stage_3,
    _extract_dismissed_issues,
)


# ── _summarize_issues_for_stage_3 ───────────────────────────

class TestSummarizeIssues:
    def test_no_issues(self):
        result = _summarize_issues_for_stage_3([])
        assert "No issues" in result

    def test_with_issues(self):
        issues = [
            CodeReviewIssue(
                id="ISS-1", file="a.py", line=10, severity="HIGH",
                category="BUG_RISK", reason="null pointer", title="NPE",
                suggestedFixDescription="Fix NPE"
            ),
            CodeReviewIssue(
                id="ISS-2", file="b.py", line=20, severity="LOW",
                category="CODE_QUALITY", reason="naming",
                suggestedFixDescription="Rename"
            ),
        ]
        result = _summarize_issues_for_stage_3(issues)
        assert "Total issues: 2" in result
        assert "HIGH" in result
        assert "ISS-1" in result

    def test_sorting_by_severity(self):
        issues = [
            CodeReviewIssue(id="L1", file="a.py", line=1, severity="LOW", category="CODE_QUALITY", reason="x", suggestedFixDescription="f"),
            CodeReviewIssue(id="C1", file="b.py", line=2, severity="CRITICAL", category="BUG_RISK", reason="y", suggestedFixDescription="f"),
        ]
        result = _summarize_issues_for_stage_3(issues)
        # CRITICAL should appear before LOW in top findings
        crit_pos = result.find("C1")
        low_pos = result.find("L1")
        assert crit_pos < low_pos


# ── _summarize_plan_for_stage_3 ──────────────────────────────

class TestSummarizePlan:
    def test_basic_plan(self):
        plan = ReviewPlan(
            analysis_summary="Test summary",
            file_groups=[
                FileGroup(
                    group_id="g1",
                    priority="HIGH",
                    rationale="important",
                    files=[ReviewFile(path="a.py"), ReviewFile(path="b.py")]
                ),
            ],
            cross_file_concerns=["data flow issue"],
        )
        result = _summarize_plan_for_stage_3(plan)
        assert "Total files planned for review: 2" in result
        assert "HIGH: 2 files" in result
        assert "data flow issue" in result


# ── _extract_dismissed_issues ────────────────────────────────

class TestExtractDismissedIssues:
    def test_no_marker(self):
        content = "Some report without markers"
        clean, dismissed = _extract_dismissed_issues(content)
        assert clean == content
        assert dismissed == []

    def test_valid_marker(self):
        content = 'Report text\n<!-- DISMISSED_ISSUES: ["ISS-1", "ISS-2"] -->\nMore text'
        clean, dismissed = _extract_dismissed_issues(content)
        assert "ISS-1" in dismissed
        assert "ISS-2" in dismissed
        assert "DISMISSED_ISSUES" not in clean

    def test_invalid_json_in_marker(self):
        content = '<!-- DISMISSED_ISSUES: [not json] -->'
        clean, dismissed = _extract_dismissed_issues(content)
        assert dismissed == []

    def test_non_list_marker(self):
        content = '<!-- DISMISSED_ISSUES: {"a": 1} -->'
        clean, dismissed = _extract_dismissed_issues(content)
        assert dismissed == []
