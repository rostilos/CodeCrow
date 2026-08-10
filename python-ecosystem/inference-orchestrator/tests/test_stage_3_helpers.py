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
    _stage_3_verification_issue_map,
    _validated_mcp_dismissals,
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

    def test_lists_every_issue_with_stable_verification_ids(self):
        issues = [CodeReviewIssue(
            id=f"I-{index}", file=f"src/f{index}.py", line=index + 2,
            severity="LOW", category="CODE_QUALITY", reason=f"reason {index}",
            suggestedFixDescription="fix",
        ) for index in range(14)]
        result = _summarize_issues_for_stage_3(issues)

        assert '"verification_id":"issue_0"' in result
        assert '"verification_id":"issue_13"' in result
        assert '"original_id":"I-13"' in result

    def test_excludes_resolved_history_records(self):
        resolved = CodeReviewIssue(
            id="OLD-1", file="a.py", line=1, severity="HIGH",
            category="BUG_RISK", reason="old issue",
            suggestedFixDescription="already fixed", isResolved=True,
        )

        result = _summarize_issues_for_stage_3([resolved])

        assert "No issues" in result
        assert "OLD-1" not in result

    def test_reason_compaction_only_removes_exact_repetition(self):
        issue = CodeReviewIssue(
            file="a.py", line=1, severity="HIGH", category="BUG_RISK",
            title="Repeated title",
            reason=(
                "Repeated title\n\nRoot cause.\n\nMore proof.\n\n"
                "More proof.\n\nImpact: request fails."
            ),
            suggestedFixDescription="Fix it.",
        )

        records = json.loads(
            _summarize_issues_for_stage_3([issue]).split(
                "Complete verification records (JSON):\n", 1
            )[1]
        )

        reason = records[0]["reason"]
        assert "Repeated title" not in reason
        assert "Root cause." in reason
        assert reason.count("More proof.") == 1
        assert "Impact: request fails." in reason

    def test_related_locations_are_recovered_from_persisted_reason(self):
        issue = CodeReviewIssue(
            file="a.py", line=1, severity="HIGH", category="BUG_RISK",
            reason="Root cause.\n\nAlso affects: b.py:20, c.py:30",
            suggestedFixDescription="Fix it.",
        )

        records = json.loads(
            _summarize_issues_for_stage_3([issue]).split(
                "Complete verification records (JSON):\n", 1
            )[1]
        )

        assert records[0]["related_locations"] == ["b.py:20", "c.py:30"]


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


class TestValidatedMcpDismissals:
    def test_requires_successful_read_at_exact_revision_for_every_location(self):
        issue = CodeReviewIssue(
            file="src/a.py", line=10, severity="HIGH", category="BUG_RISK",
            title="Shared defect", reason="One root cause.",
            suggestedFixDescription="Fix it.",
            relatedLocations=["src/b.py:20"],
        )
        issue_map = _stage_3_verification_issue_map([issue])
        executor = SimpleNamespace(call_log=[
            {
                "tool": "getBranchFileContent",
                "args": {
                    "filePath": "src/a.py", "branch": "abc123",
                    "verificationId": "issue_0",
                },
                "success": True,
                "evidence_valid": True,
                "evidence_complete_file": True,
            },
        ])

        assert _validated_mcp_dismissals(
            ["issue_0"], issue_map, executor, "abc123"
        ) == []

        executor.call_log.append({
            "tool": "getBranchFileContent",
            "args": {
                "filePath": "src/b.py", "branch": "abc123",
                "verificationId": "issue_0",
            },
            "success": True,
            "evidence_valid": True,
            "evidence_complete_file": False,
            "evidence_start_line": 1,
            "evidence_end_line": 100,
        })
        assert _validated_mcp_dismissals(
            ["issue_0"], issue_map, executor, "abc123"
        ) == ["issue_0"]

    def test_wrong_revision_failed_or_unknown_dismissal_fails_open(self):
        issue = CodeReviewIssue(
            file="src/a.py", line=10, severity="HIGH", category="BUG_RISK",
            reason="Concrete defect.", suggestedFixDescription="Fix it.",
        )
        issue_map = _stage_3_verification_issue_map([issue])
        executor = SimpleNamespace(call_log=[
            {
                "tool": "getBranchFileContent",
                "args": {
                    "filePath": "src/a.py", "branch": "main",
                    "verificationId": "issue_0",
                },
                "success": True,
                "evidence_valid": True,
                "evidence_complete_file": True,
            },
            {
                "tool": "getBranchFileContent",
                "args": {
                    "filePath": "src/a.py", "branch": "abc123",
                    "verificationId": "issue_0",
                },
                "success": False,
                "evidence_valid": False,
            },
        ])

        assert _validated_mcp_dismissals(
            ["issue_0", "issue_99"], issue_map, executor, "abc123"
        ) == []

    def test_window_must_cover_the_bound_issue_line(self):
        issue = CodeReviewIssue(
            file="src/a.py", line=500, severity="HIGH", category="BUG_RISK",
            reason="Concrete defect.", suggestedFixDescription="Fix it.",
        )
        issue_map = _stage_3_verification_issue_map([issue])
        executor = SimpleNamespace(call_log=[{
            "tool": "getBranchFileContent",
            "args": {
                "filePath": "src/a.py", "branch": "abc123",
                "verificationId": "issue_0",
            },
            "success": True,
            "evidence_valid": True,
            "evidence_complete_file": False,
            "evidence_start_line": 1,
            "evidence_end_line": 100,
        }])

        assert _validated_mcp_dismissals(
            ["issue_0"], issue_map, executor, "abc123"
        ) == []

        executor.call_log[0]["evidence_start_line"] = 420
        executor.call_log[0]["evidence_end_line"] = 580
        assert _validated_mcp_dismissals(
            ["issue_0"], issue_map, executor, "abc123"
        ) == ["issue_0"]
