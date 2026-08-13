from model.dtos import ReviewRequestDto
from model.output_schemas import CodeReviewIssue
from service.review.orchestrator.report_renderer import render_verified_report


def _request():
    return ReviewRequestDto(
        projectId=1,
        projectVcsRepoSlug="org/repo",
        projectVcsWorkspace="org",
        projectWorkspace="org",
        projectNamespace="org",
        targetBranchName="main",
        sourceBranchName="feature",
        currentCommitHash="abc",
        commitHash="abc",
        analysisType="PULL_REQUEST",
        analysisMode="FULL",
        aiProvider="test",
        aiModel="test",
        aiApiKey="test",
        changedFiles=[],
        deletedFiles=[],
        prTitle="Safer parser",
    )


def _issue(severity="MEDIUM", title="Null dereference"):
    return CodeReviewIssue(
        severity=severity,
        category="BUG_RISK",
        file="src/a.py",
        line=12,
        title=title,
        reason="reason",
        suggestedFixDescription="fix",
        codeSnippet="use(value)",
    )


def test_empty_report_does_not_claim_unverified_quality_metric():
    report = render_verified_report(_request(), [])
    assert "No confirmed actionable defects" in report
    assert "precision" not in report.lower()
    assert "f1" not in report.lower()


def test_report_membership_is_stable_and_sorted_by_severity():
    report = render_verified_report(
        _request(),
        [_issue("LOW", "Low"), _issue("HIGH", "High")],
        incomplete_candidates=2,
        rejected_candidates=1,
    )
    assert report.index("High") < report.index("Low")
    assert "withheld" in report
    assert "Rejected unsupported candidates: **1**" in report


def test_resolved_compatibility_rows_are_not_rendered_as_findings():
    issue = _issue()
    issue.isResolved = True
    report = render_verified_report(_request(), [issue])
    assert "Null dereference" not in report

