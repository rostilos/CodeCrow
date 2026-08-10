"""Extended tests for reconciliation: _format_issues_for_prompt, _build_batches, _dedup_batch_with_llm."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from model.output_schemas import (
    CodeReviewIssue,
    SemanticDeduplicationDecision,
    SemanticDuplicateGroup,
)
from service.review.orchestrator.reconciliation import (
    _format_issues_for_prompt,
    _build_batches,
    _build_semantic_dedup_batches,
    _dedup_batch_with_llm,
    _semantic_candidate_groups,
    deduplicate_final_issues_llm,
    deduplicate_final_issues,
    issues_are_semantic_dedup_candidates,
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


def _real_issue(file="a.py", line=10, severity="HIGH", category="BUG_RISK",
                title="Issue", reason="Something wrong", issue_id=None,
                code_snippet=""):
    return CodeReviewIssue(
        id=issue_id,
        file=file,
        line=line,
        severity=severity,
        category=category,
        title=title,
        reason=reason,
        suggestedFixDescription="Fix the root cause.",
        codeSnippet=code_snippet,
    )


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
    async def test_merges_only_explicit_high_confidence_group(self):
        llm = MagicMock()
        structured = MagicMock()
        structured.ainvoke = AsyncMock(
            return_value=SemanticDeduplicationDecision(duplicate_groups=[
                SemanticDuplicateGroup(
                    keeper_index=0,
                    duplicate_indices=[1],
                    confidence="HIGH",
                    rationale="One missing authorization guard.",
                )
            ])
        )
        llm.with_structured_output.return_value = structured

        issues = [
            _real_issue(
                line=10,
                title="Authorization guard missing from update path",
                reason="The update path writes account data without checking the workspace role.",
            ),
            _real_issue(
                line=40,
                title="Authorization guard missing from update path",
                reason="Account data is written by this update path before the workspace role is checked.",
            ),
        ]
        result = await _dedup_batch_with_llm(
            llm,
            issues,
            {0: "candidate_0", 1: "candidate_0"},
        )
        assert len(result) == 1
        assert result[0].relatedLocations == ["a.py:40"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_invalid_indices_keeps_all(self):
        llm = MagicMock()
        structured = MagicMock()
        structured.ainvoke = AsyncMock(
            return_value=SemanticDeduplicationDecision(duplicate_groups=[
                SemanticDuplicateGroup(
                    keeper_index=0,
                    duplicate_indices=[99],
                    confidence="HIGH",
                    rationale="Malformed index.",
                )
            ])
        )
        llm.with_structured_output.return_value = structured

        issues = [
            _real_issue(line=10, reason="First independent problem."),
            _real_issue(line=20, reason="Second independent problem."),
        ]
        result = await _dedup_batch_with_llm(
            llm,
            issues,
            {0: "candidate_0", 1: "candidate_0"},
        )
        assert result == issues

    @pytest.mark.asyncio(loop_scope="function")
    async def test_uncertain_decision_keeps_all(self):
        llm = MagicMock()
        structured = MagicMock()
        structured.ainvoke = AsyncMock(
            return_value=SemanticDeduplicationDecision(duplicate_groups=[
                SemanticDuplicateGroup(
                    keeper_index=0,
                    duplicate_indices=[1],
                    confidence="MEDIUM",
                    rationale="Possibly related.",
                )
            ])
        )
        llm.with_structured_output.return_value = structured
        issues = [
            _real_issue(line=10, reason="First problem."),
            _real_issue(line=20, reason="Second problem."),
        ]

        result = await _dedup_batch_with_llm(llm, issues)

        assert result == issues

    @pytest.mark.asyncio(loop_scope="function")
    async def test_exception_falls_back(self):
        llm = MagicMock()
        structured = MagicMock()
        structured.ainvoke = AsyncMock(side_effect=Exception("fail"))
        llm.with_structured_output.return_value = structured

        issues = [
            _real_issue(line=10, reason="First independent problem."),
            _real_issue(line=20, reason="Second independent problem."),
        ]
        result = await _dedup_batch_with_llm(llm, issues)
        assert result == issues


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
    async def test_non_candidates_do_not_use_model_tokens(self):
        issues = [
            _real_issue(file="a.py", title="SQL injection", reason="Raw SQL uses user input."),
            _real_issue(file="b.py", title="Cache stampede", reason="Cache misses fan out."),
        ]
        llm = MagicMock()
        result = await deduplicate_final_issues_llm(llm, issues)
        assert result == issues
        llm.with_structured_output.assert_not_called()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_sends_complete_candidate_evidence_but_not_singletons(self):
        long_evidence = "Evidence marker " + ("complete-context " * 900)
        candidate_a = _real_issue(
            line=10,
            title="Workspace authorization is missing from account update",
            reason=long_evidence + "role check is absent",
        )
        candidate_b = _real_issue(
            line=50,
            title="Workspace authorization is missing from account update",
            reason=long_evidence + "role validation is absent",
        )
        singleton = _real_issue(
            file="different.py",
            title="Independent resource leak",
            reason="SINGLETON-MUST-NOT-BE-SENT",
        )
        llm = MagicMock()
        structured = MagicMock()
        structured.ainvoke = AsyncMock(
            return_value=SemanticDeduplicationDecision(duplicate_groups=[])
        )
        llm.with_structured_output.return_value = structured

        result = await deduplicate_final_issues_llm(
            llm,
            [candidate_a, singleton, candidate_b],
        )

        assert result == [candidate_a, singleton, candidate_b]
        prompt = structured.ainvoke.await_args.args[0]
        assert "complete-context" in prompt
        assert "role validation is absent" in prompt
        assert "SINGLETON-MUST-NOT-BE-SENT" not in prompt

    def test_same_anchor_independent_roots_are_not_candidates(self):
        authorization = _real_issue(
            line=25,
            title="Request processing defect",
            reason="The caller can update another workspace because ownership is never checked.",
            code_snippet="service.update(request)",
        )
        transaction = _real_issue(
            line=25,
            title="Request processing defect",
            reason="The database transaction is committed before the downstream write completes.",
            code_snippet="service.update(request)",
        )

        assert not issues_are_semantic_dedup_candidates(
            authorization,
            transaction,
        )

    @pytest.mark.asyncio(loop_scope="function")
    async def test_cross_file_shared_root_keeps_both_locations(self):
        first = _real_issue(
            file="config/RagConfig.java",
            line=30,
            title="RAG constructor arity no longer matches shared configuration",
            reason=(
                "The shared RAG configuration now requires four constructor "
                "arguments, but this caller supplies three and cannot compile."
            ),
        )
        second = _real_issue(
            file="test/RagConfigTest.java",
            line=70,
            title="RAG constructor arity no longer matches shared configuration",
            reason=(
                "The shared RAG configuration requires four constructor arguments; "
                "this test still supplies three and cannot compile."
            ),
        )
        assert issues_are_semantic_dedup_candidates(first, second)
        llm = MagicMock()
        structured = MagicMock()
        structured.ainvoke = AsyncMock(
            return_value=SemanticDeduplicationDecision(duplicate_groups=[
                SemanticDuplicateGroup(
                    keeper_index=0,
                    duplicate_indices=[1],
                    confidence="HIGH",
                    rationale="One constructor contract change breaks both callers.",
                )
            ])
        )
        llm.with_structured_output.return_value = structured

        result = await deduplicate_final_issues_llm(llm, [first, second])

        assert len(result) == 1
        assert result[0].file == "config/RagConfig.java"
        assert result[0].relatedLocations == ["test/RagConfigTest.java:70"]
        assert "Also affects: test/RagConfigTest.java:70" in result[0].reason

    @pytest.mark.asyncio(loop_scope="function")
    async def test_empty_returns_empty(self):
        llm = MagicMock()
        result = await deduplicate_final_issues_llm(llm, [])
        assert result == []
