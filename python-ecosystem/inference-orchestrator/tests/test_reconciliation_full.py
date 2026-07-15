"""Extended tests for reconciliation: _format_issues_for_prompt, _build_batches, _dedup_batch_with_llm."""
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock, patch
from service.review.orchestrator.reconciliation import (
    _env_int,
    _format_issues_for_prompt,
    _build_batches,
    _dedup_batch_with_llm,
    deduplicate_issues,
    deduplicate_final_issues_llm,
    deduplicate_final_issues,
    format_previous_issues_for_batch,
    issue_matches_files,
    reconcile_previous_issues,
)
from model.output_schemas import DeduplicatedIssueList
from utils.diff_processor import DiffChangeType, DiffFile, ProcessedDiff


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

    @pytest.mark.asyncio(loop_scope="function")
    async def test_unstructured_provider_path(self):
        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=MagicMock(content='{"kept_indices":[1]}'))
        issues = [_make_issue(file="a.py"), _make_issue(file="b.py")]
        with patch(
            "service.review.orchestrator.reconciliation.supports_structured_output",
            return_value=False,
        ), patch(
            "service.review.orchestrator.reconciliation.parse_llm_response",
            new=AsyncMock(return_value=DeduplicatedIssueList(kept_indices=[1])),
        ):
            assert await _dedup_batch_with_llm(llm, issues) == [issues[1]]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_valid_all_indices_keeps_every_issue(self):
        llm = MagicMock()
        llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=DeduplicatedIssueList(kept_indices=[0, 1])
        )
        issues = [_make_issue(file="a.py"), _make_issue(file="b.py")]
        assert await _dedup_batch_with_llm(llm, issues) == issues


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

    def test_semantic_scan_can_match_a_later_existing_issue(self):
        issues = [
            _make_issue(file="a.py", line=2, reason="alpha root cause"),
            _make_issue(file="a.py", line=3, reason="entirely different"),
            _make_issue(file="a.py", line=4, reason="entirely different"),
        ]
        result = deduplicate_final_issues(issues)
        assert result == issues[:2]


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

    @pytest.mark.asyncio(loop_scope="function")
    async def test_single_returns_same_object_and_multi_preserves_batch_order(self):
        one = _make_issue()
        assert await deduplicate_final_issues_llm(MagicMock(), [one]) == [one]

        issues = [_make_issue(file="a.py"), _make_issue(file="b.py")]
        with patch(
            "service.review.orchestrator.reconciliation._dedup_batch_with_llm",
            new=AsyncMock(side_effect=lambda _llm, batch: batch[:1]),
        ):
            result = await deduplicate_final_issues_llm(MagicMock(), issues)
        assert result == [issues[0]]


class TestReconciliationBoundaries:
    def test_env_object_inputs_same_version_and_resolved_history(self, monkeypatch):
        monkeypatch.delenv("COUNT", raising=False)
        assert _env_int("COUNT", 2) == 2
        monkeypatch.setenv("COUNT", "bad")
        assert _env_int("COUNT", 2) == 2
        monkeypatch.setenv("COUNT", "5")
        assert _env_int("COUNT", 2) == 5

        assert issue_matches_files(SimpleNamespace(file="root/a.py"), ["a.py"])
        resolved = {
            "id": "x", "file": "a.py", "line": 1, "severity": "LOW",
            "reason": "same", "prVersion": 1, "status": "resolved",
            "resolutionExplanation": "fixed", "resolvedInPrVersion": 2,
        }
        open_issue = dict(resolved, status="open")
        result = deduplicate_issues([SimpleNamespace(**open_issue), resolved])
        assert result[0]["status"] == "resolved"
        history = format_previous_issues_for_batch([resolved])
        assert "Resolved in: v2" in history

        older = dict(resolved, prVersion=0, status="open")
        assert deduplicate_issues([resolved, older])[0]["status"] == "resolved"
        no_description = dict(
            resolved, resolutionExplanation=None, resolvedInPrVersion=3,
            reason="same",
        )
        assert "Resolved in: v3" in format_previous_issues_for_batch([no_description])

    @pytest.mark.asyncio(loop_scope="function")
    async def test_new_resolution_processed_diff_and_invalid_line_fallbacks(self):
        request = MagicMock()
        request.previousCodeAnalysisIssues = [SimpleNamespace(
            id="42", file="a.py", line="bad", severity="HIGH",
            category="BUG_RISK", reason="Original", status="open",
        )]
        request.currentCommitHash = "new-commit"
        request.commitHash = "old-commit"
        request.deltaDiff = "+change"
        processed = ProcessedDiff(files=[DiffFile(
            path="a.py", change_type=DiffChangeType.MODIFIED, content="+change"
        )])
        new_issue = {
            "id": "42", "file": "a.py", "line": "also-bad",
            "reason": "Updated", "isResolved": True,
            "resolutionReason": "fixed now", "codeSnippet": "new anchor",
        }

        result = await reconcile_previous_issues(request, [new_issue], processed)

        data = result[0].model_dump()
        assert data["line"] == 1
        assert data["isResolved"] is True
        assert data["resolutionExplanation"] == "fixed now"
        assert data["resolvedInCommit"] == "new-commit"
        assert data["codeSnippet"] == "new anchor"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_semantic_scan_skips_resolved_and_uses_open_match(self):
        request = MagicMock()
        request.previousCodeAnalysisIssues = [
            {"id": "old", "file": "a.py", "line": 1, "severity": "LOW",
             "category": "STYLE", "reason": "same root cause", "status": "resolved"},
            {"id": "open", "file": "a.py", "line": 2, "severity": "HIGH",
             "category": "BUG_RISK", "reason": "same root cause", "status": "open"},
        ]
        request.currentCommitHash = None
        request.commitHash = "commit"
        request.deltaDiff = ""
        new_issue = {
            "file": "a.py", "line": 3, "reason": "same root cause",
            "isResolved": False,
        }

        result = await reconcile_previous_issues(request, [new_issue])

        by_id = {issue.id: issue for issue in result}
        assert by_id["open"].line == 3
        assert by_id["old"].isResolved is True

    @pytest.mark.asyncio(loop_scope="function")
    async def test_previous_same_anchor_is_not_reported_twice(self):
        request = MagicMock()
        request.previousCodeAnalysisIssues = [{
            "id": "previous", "file": "a.py", "line": 8,
            "severity": "LOW", "category": "STYLE", "reason": "old",
            "status": "open",
        }]
        request.currentCommitHash = "commit"
        request.commitHash = "commit"
        request.deltaDiff = ""
        new_issue = {"file": "a.py", "line": 8, "reason": "unrelated new"}

        result = await reconcile_previous_issues(request, [new_issue])

        assert result == [new_issue]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_model_previous_entries_and_previous_line_fallback(self):
        previous = _make_issue(file="a.py", line=5)
        previous.model_dump.return_value.update({"id": "model", "status": "open"})
        request = MagicMock(
            previousCodeAnalysisIssues=[previous], currentCommitHash="c",
            commitHash="c", deltaDiff="",
        )
        result = await reconcile_previous_issues(request, [{
            "id": "model", "file": "a.py", "line": 0,
            "reason": "new", "isResolved": False,
        }])
        assert result[0].line == 5

    @pytest.mark.asyncio(loop_scope="function")
    async def test_previous_without_id_and_different_file_semantic_scan(self):
        request = MagicMock(
            previousCodeAnalysisIssues=[
                {"file": "other.py", "line": 1, "reason": "same", "status": "open"},
                {"id": "kept", "file": "a.py", "line": 2, "reason": "different", "status": "open"},
            ],
            currentCommitHash="c", commitHash="c", deltaDiff="",
        )
        result = await reconcile_previous_issues(request, [{
            "file": "new.py", "line": 3, "reason": "same but new",
        }])
        assert len(result) == 3
