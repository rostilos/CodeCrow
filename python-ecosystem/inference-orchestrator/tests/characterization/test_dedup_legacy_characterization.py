"""P0-02 characterization of destructive Python issue de-duplication."""

import pytest

from model.output_schemas import CodeReviewIssue
from service.review.orchestrator.reconciliation import (
    deduplicate_cross_batch_issues,
    deduplicate_final_issues,
)


pytestmark = pytest.mark.legacy_defect


def _issue(issue_id, file_path, line, reason):
    return CodeReviewIssue(
        id=issue_id,
        severity="HIGH",
        category="BUG_RISK",
        file=file_path,
        line=line,
        reason=reason,
        suggestedFixDescription="Fix it",
        codeSnippet="unsafe_call()",
    )


def test_legacy_defect_cross_batch_reason_similarity_ignores_file_and_anchor():
    issues = [
        _issue("a", "src/a.py", 20, "Dereference can fail when value is absent"),
        _issue("b", "src/b.py", 30, "Dereference can fail when value is absent"),
    ]

    result = deduplicate_cross_batch_issues(issues)

    assert [issue.id for issue in result] == ["a"]


def test_legacy_defect_line_one_absorbs_a_distinct_later_finding():
    issues = [
        _issue("line-one", "src/main.py", 1, "Import-time failure"),
        _issue("later", "src/main.py", 99, "Runtime state corruption"),
    ]

    result = deduplicate_final_issues(issues)

    assert [issue.id for issue in result] == ["line-one"]


def test_ordinary_distinct_files_with_different_reasons_survive():
    issues = [
        _issue("a", "src/a.py", 20, "Null dereference in request parsing"),
        _issue("b", "src/b.py", 30, "Unbounded retry after persistence failure"),
    ]

    result = deduplicate_cross_batch_issues(issues)

    assert [issue.id for issue in result] == ["a", "b"]
