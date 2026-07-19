"""Extended tests for reconciliation: _format_issues_for_prompt, _build_batches, _dedup_batch_with_llm."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from service.review.orchestrator.reconciliation import (
    _format_issues_for_prompt,
    _build_batches,
    _dedup_batch_with_llm,
    deduplicate_final_issues_llm,
    deduplicate_final_issues,
)


def _make_issue(file="a.py", line=10, severity="HIGH", category="BUG_RISK",
                title="Issue", reason="Something wrong"):
    issue = MagicMock()
    issue.model_dump.return_value = {
        "file": file,
        "line": line,
        "severity": severity,
        "category": category,
        "title": title,
        "reason": reason,
    }
    issue.file = file
    issue.line = line
    issue.severity = severity
    issue.category = category
    issue.title = title
    issue.reason = reason
    return issue


# ── _format_issues_for_prompt ─────────────────────────────────


class TestFormatIssuesForPrompt:
    def test_formats_single_issue(self):
        result = _format_issues_for_prompt([_make_issue()])
        assert "[0]" in result
        assert "HIGH" in result
        assert "BUG_RISK" in result
        assert "a.py" in result

    def test_formats_multiple(self):
        issues = [_make_issue(file=f"f{i}.py") for i in range(3)]
        result = _format_issues_for_prompt(issues)
        assert "[0]" in result
        assert "[1]" in result
        assert "[2]" in result

    def test_no_title(self):
        issue = _make_issue(title="")
        result = _format_issues_for_prompt([issue])
        assert "[0]" in result


# ── _build_batches ────────────────────────────────────────────


class TestBuildBatches:
    def test_single_batch(self):
        issues = [_make_issue(file="a.py") for _ in range(3)]
        batches = _build_batches(issues, max_batch_size=10)
        assert len(batches) == 1
        assert len(batches[0]) == 3

    def test_respects_max_size(self):
        issues = [_make_issue(file=f"f{i}.py") for i in range(10)]
        batches = _build_batches(issues, max_batch_size=3)
        for batch in batches:
            assert len(batch) <= 3

    def test_same_file_not_split(self):
        issues = [_make_issue(file="a.py") for _ in range(5)]
        batches = _build_batches(issues, max_batch_size=3)
        # All 5 issues for same file should be in one batch (oversized)
        assert len(batches) == 1
        assert len(batches[0]) == 5

    def test_empty(self):
        assert _build_batches([]) == []


# ── _dedup_batch_with_llm ────────────────────────────────────


class TestDedupBatchWithLlm:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_keeps_selected_indices(self):
        from model.output_schemas import DeduplicatedIssueList
        llm = MagicMock()
        structured = MagicMock()
        structured.ainvoke = AsyncMock(
            return_value=DeduplicatedIssueList(kept_indices=[0, 2])
        )
        llm.with_structured_output.return_value = structured

        issues = [_make_issue(file=f"f{i}.py") for i in range(3)]
        result = await _dedup_batch_with_llm(llm, issues)
        assert len(result) == 2

    @pytest.mark.asyncio(loop_scope="function")
    async def test_invalid_indices_keeps_all(self):
        from model.output_schemas import DeduplicatedIssueList
        llm = MagicMock()
        structured = MagicMock()
        structured.ainvoke = AsyncMock(
            return_value=DeduplicatedIssueList(kept_indices=[99])
        )
        llm.with_structured_output.return_value = structured

        issues = [_make_issue() for _ in range(2)]
        result = await _dedup_batch_with_llm(llm, issues)
        assert len(result) == 2  # All kept as fallback

    @pytest.mark.asyncio(loop_scope="function")
    async def test_exception_falls_back(self):
        llm = MagicMock()
        structured = MagicMock()
        structured.ainvoke = AsyncMock(side_effect=Exception("fail"))
        llm.with_structured_output.return_value = structured

        issues = [_make_issue() for _ in range(2)]
        result = await _dedup_batch_with_llm(llm, issues)
        # Falls back to algorithmic dedup
        assert len(result) >= 1


# ── deduplicate_final_issues ──────────────────────────────────


class TestDeduplicateFinalIssues:
    def test_no_duplicates(self):
        issues = [
            _make_issue(file="a.py", line=10, reason="Issue A"),
            _make_issue(file="b.py", line=20, reason="Issue B"),
        ]
        result = deduplicate_final_issues(issues)
        assert len(result) == 2

    def test_exact_duplicates(self):
        issues = [
            _make_issue(file="a.py", line=10, severity="HIGH", reason="Same issue"),
            _make_issue(file="a.py", line=10, severity="HIGH", reason="Same issue"),
        ]
        result = deduplicate_final_issues(issues)
        assert len(result) == 1


# ── deduplicate_final_issues_llm ──────────────────────────────


class TestDeduplicateFinalIssuesLlm:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_small_set_uses_algorithmic(self):
        # Less than the batch threshold → should just use algorithmic
        issues = [_make_issue(file=f"f{i}.py") for i in range(2)]
        llm = MagicMock()
        result = await deduplicate_final_issues_llm(llm, issues)
        assert len(result) >= 1

    @pytest.mark.asyncio(loop_scope="function")
    async def test_empty_returns_empty(self):
        llm = MagicMock()
        result = await deduplicate_final_issues_llm(llm, [])
        assert result == []
