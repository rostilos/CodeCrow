"""
Unit tests for model.output_schemas — CodeReviewIssue validators, CodeReviewOutput,
SummarizeOutput, AskOutput, DeduplicatedIssueList, ReconciliationOutput.
"""
import pytest
from model.output_schemas import (
    CodeReviewIssue,
    CodeReviewOutput,
    SummarizeOutput,
    DeduplicatedIssueList,
    AskOutput,
    ReconciliationResolvedIssue,
    ReconciliationOutput,
)


# ── CodeReviewIssue.parse_line_to_int ────────────────────────────

class TestParseLineToInt:

    def test_int_passthrough(self):
        issue = CodeReviewIssue(
            severity="HIGH", category="SECURITY", file="a.py",
            line=42, reason="r", suggestedFixDescription="f",
        )
        assert issue.line == 42

    def test_float_truncated(self):
        issue = CodeReviewIssue(
            severity="HIGH", category="SECURITY", file="a.py",
            line=42.7, reason="r", suggestedFixDescription="f",
        )
        assert issue.line == 42

    def test_string_numeric(self):
        issue = CodeReviewIssue(
            severity="HIGH", category="SECURITY", file="a.py",
            line="99", reason="r", suggestedFixDescription="f",
        )
        assert issue.line == 99

    def test_range_string_takes_start(self):
        issue = CodeReviewIssue(
            severity="HIGH", category="SECURITY", file="a.py",
            line="42-45", reason="r", suggestedFixDescription="f",
        )
        assert issue.line == 42

    def test_none_returns_zero(self):
        issue = CodeReviewIssue(
            severity="HIGH", category="SECURITY", file="a.py",
            line=None, reason="r", suggestedFixDescription="f",
        )
        assert issue.line == 0

    def test_invalid_string_returns_zero(self):
        issue = CodeReviewIssue(
            severity="HIGH", category="SECURITY", file="a.py",
            line="abc", reason="r", suggestedFixDescription="f",
        )
        assert issue.line == 0

    def test_whitespace_stripped(self):
        issue = CodeReviewIssue(
            severity="HIGH", category="SECURITY", file="a.py",
            line="  55  ", reason="r", suggestedFixDescription="f",
        )
        assert issue.line == 55


# ── CodeReviewIssue.normalize_scope ──────────────────────────────

class TestNormalizeScope:

    @pytest.mark.parametrize("raw,expected", [
        ("LINE", "LINE"),
        ("BLOCK", "BLOCK"),
        ("FUNCTION", "FUNCTION"),
        ("FILE", "FILE"),
        ("line", "LINE"),
        ("block", "BLOCK"),
        # Aliases
        ("METHOD", "FUNCTION"),
        ("RANGE", "BLOCK"),
        ("CLASS", "FILE"),
        ("MODULE", "FILE"),
        ("GLOBAL", "FILE"),
        # Unknown → LINE
        ("UNKNOWN", "LINE"),
        (None, "LINE"),
    ])
    def test_normalization(self, raw, expected):
        issue = CodeReviewIssue(
            severity="LOW", category="STYLE", file="b.py",
            line=1, scope=raw, reason="r", suggestedFixDescription="f",
        )
        assert issue.scope == expected


# ── CodeReviewIssue.coerce_code_snippet ──────────────────────────

class TestCoerceCodeSnippet:

    def test_none_becomes_empty(self):
        issue = CodeReviewIssue(
            severity="LOW", category="STYLE", file="b.py",
            line=1, reason="r", suggestedFixDescription="f",
            codeSnippet=None,
        )
        assert issue.codeSnippet == ""

    def test_string_stripped(self):
        issue = CodeReviewIssue(
            severity="LOW", category="STYLE", file="b.py",
            line=1, reason="r", suggestedFixDescription="f",
            codeSnippet="  x = 1  ",
        )
        assert issue.codeSnippet == "x = 1"


class TestResolutionCompatibility:

    def test_resolution_reason_is_preserved_in_model_dump(self):
        issue = CodeReviewIssue(
            id="12524", severity="INFO", category="BUG_RISK", file="a.py",
            line=10, reason="Historical issue", suggestedFixDescription="Fixed",
            isResolved=True, resolutionReason="Empty-string default applied.",
        )

        assert issue.resolutionReason == "Empty-string default applied."
        assert issue.model_dump()["resolutionReason"] == "Empty-string default applied."

    def test_snippet_contract_exempts_matched_historical_resolution(self):
        description = CodeReviewIssue.model_fields["codeSnippet"].description

        assert "NEW FINDINGS" in description
        assert "historical issue with isResolved=true" in description

    def test_active_and_historical_descriptions_are_distinguished(self):
        reason = CodeReviewIssue.model_fields["reason"].description
        fix = CodeReviewIssue.model_fields["suggestedFixDescription"].description

        assert "new/active finding" in reason
        assert "historical isResolved record preserves" in reason
        assert "new/active finding" in fix
        assert "historical isResolved record may preserve" in fix


# ── CodeReviewOutput ─────────────────────────────────────────────

class TestCodeReviewOutput:

    def test_with_issues(self):
        output = CodeReviewOutput(
            comment="All good",
            issues=[
                CodeReviewIssue(
                    severity="LOW", category="STYLE", file="c.py",
                    line=1, reason="r", suggestedFixDescription="f",
                ),
            ],
        )
        assert len(output.issues) == 1

    def test_empty_issues(self):
        output = CodeReviewOutput(comment="Nothing")
        assert output.issues == []


# ── SummarizeOutput ──────────────────────────────────────────────

class TestSummarizeOutput:

    def test_defaults(self):
        out = SummarizeOutput(summary="Changes made")
        assert out.diagram == ""
        assert out.diagramType == "MERMAID"


# ── DeduplicatedIssueList ────────────────────────────────────────

class TestDeduplicatedIssueList:

    def test_basic(self):
        d = DeduplicatedIssueList(kept_indices=[0, 2, 4])
        assert d.kept_indices == [0, 2, 4]


# ── AskOutput ────────────────────────────────────────────────────

class TestAskOutput:

    def test_basic(self):
        out = AskOutput(answer="Because...")
        assert out.answer == "Because..."


# ── ReconciliationOutput ─────────────────────────────────────────

class TestReconciliationOutput:

    def test_basic(self):
        resolved = ReconciliationResolvedIssue(
            issueId="42", isResolved=True,
            resolutionReason="Null check added on line 45",
        )
        output = ReconciliationOutput(
            comment="3 resolved", issues=[resolved],
        )
        assert len(output.issues) == 1
        assert output.issues[0].isResolved is True

    def test_empty_issues(self):
        output = ReconciliationOutput(comment="None resolved")
        assert output.issues == []
