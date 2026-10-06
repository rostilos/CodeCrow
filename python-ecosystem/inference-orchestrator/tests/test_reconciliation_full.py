"""Extended tests for reconciliation: _format_issues_for_prompt, _build_batches, _dedup_batch_with_llm."""
import json
import asyncio
from pathlib import Path
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from model.output_schemas import (
    CodeReviewIssue,
    SemanticDeduplicationDecision,
    SemanticDuplicateGroup,
    SemanticCoveredSummary,
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
    _dedup_prompt,
    _dedup_refinement_prompt,
    _format_semantic_batch,
    _estimate_dedup_input_tokens,
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
    async def test_indivisible_oversized_group_is_retained_without_provider_call(self):
        marker = "\U0001f9ea" * 20_000
        issues = [
            _real_issue(
                line=10,
                title="Workspace authorization is missing",
                reason=f"{marker} first complete root-cause record",
            ),
            _real_issue(
                line=40,
                title="Workspace authorization is missing",
                reason=f"{marker} second complete root-cause record",
            ),
        ]
        llm = MagicMock()

        result = await _dedup_batch_with_llm(
            llm,
            issues,
            {0: "candidate_0", 1: "candidate_0"},
            input_token_target=1_000,
        )

        assert result == issues
        llm.with_structured_output.assert_not_called()

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
    async def test_merges_transitive_high_confidence_root_inside_host_group(self):
        issues = [
            _real_issue(
                line=10,
                title="Async forEach cleanup not awaited",
                reason=(
                    "The async forEach cleanup is not awaited so deletion runs "
                    "after the function returns."
                ),
            ),
            _real_issue(
                line=13,
                title="Async cleanup rejection not awaited",
                reason=(
                    "The async cleanup is not awaited, deletion runs after "
                    "return, and rejection escapes error handling."
                ),
            ),
            _real_issue(
                line=16,
                title="Cleanup rejection escapes error handling",
                reason=(
                    "The cleanup rejection escapes the error handler and "
                    "leaves the old event undeleted."
                ),
            ),
        ]
        assert issues_are_semantic_dedup_candidates(issues[0], issues[1])
        assert issues_are_semantic_dedup_candidates(issues[1], issues[2])
        assert issues_are_semantic_dedup_candidates(issues[0], issues[2])
        assert _semantic_candidate_groups(issues) == [issues]

        llm = MagicMock()
        structured = MagicMock()
        structured.ainvoke = AsyncMock(
            return_value=SemanticDeduplicationDecision(duplicate_groups=[
                SemanticDuplicateGroup(
                    keeper_index=0,
                    duplicate_indices=[1, 2],
                    confidence="HIGH",
                    rationale="One async cleanup root manifests three ways.",
                )
            ])
        )
        llm.with_structured_output.return_value = structured

        result = await _dedup_batch_with_llm(
            llm,
            issues,
            {0: "candidate_0", 1: "candidate_0", 2: "candidate_0"},
        )

        assert len(result) == 1
        assert result[0].relatedLocations == ["a.py:13", "a.py:16"]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_rejects_high_confidence_merge_across_host_groups(self):
        llm = MagicMock()
        structured = MagicMock()
        structured.ainvoke = AsyncMock(
            return_value=SemanticDeduplicationDecision(duplicate_groups=[
                SemanticDuplicateGroup(
                    keeper_index=0,
                    duplicate_indices=[1],
                    confidence="HIGH",
                    rationale="Model incorrectly crosses host groups.",
                )
            ])
        )
        llm.with_structured_output.return_value = structured
        issues = [
            _real_issue(line=10, reason="First root defect."),
            _real_issue(line=20, reason="Independent root defect."),
        ]

        result = await _dedup_batch_with_llm(
            llm,
            issues,
            {0: "candidate_0", 1: "candidate_1"},
        )

        assert result == issues

    @pytest.mark.asyncio(loop_scope="function")
    async def test_missing_host_group_mapping_grants_no_merge_scope(self):
        llm = MagicMock()
        structured = MagicMock()
        structured.ainvoke = AsyncMock(
            return_value=SemanticDeduplicationDecision(duplicate_groups=[
                SemanticDuplicateGroup(
                    keeper_index=0,
                    duplicate_indices=[1],
                    confidence="HIGH",
                    rationale="No host group was supplied.",
                )
            ])
        )
        llm.with_structured_output.return_value = structured
        issues = [
            _real_issue(line=10, reason="First root defect."),
            _real_issue(line=20, reason="Second root defect."),
        ]

        result = await _dedup_batch_with_llm(llm, issues)

        assert result == issues

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
    async def test_full_review_compares_different_files_and_retains_distinct_defects(self):
        issues = [
            _real_issue(file="a.py", title="SQL injection", reason="Raw SQL uses user input."),
            _real_issue(file="b.py", title="Cache stampede", reason="Cache misses fan out."),
        ]
        llm = MagicMock()
        llm.with_structured_output.return_value.ainvoke = AsyncMock(
            return_value=SemanticDeduplicationDecision()
        )
        result = await deduplicate_final_issues_llm(llm, issues)
        assert result == issues
        assert llm.with_structured_output.return_value.ainvoke.await_count == 2
        assert 'including an empty proposal' in llm.with_structured_output.return_value.ainvoke.await_args.args[0]

    @pytest.mark.asyncio(loop_scope="function")
    async def test_sends_complete_evidence_including_lexical_singletons(self):
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
            reason="INDEPENDENT-FINDING-STILL-COMPARED",
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
        assert "INDEPENDENT-FINDING-STILL-COMPARED" in prompt

    def test_same_anchor_independent_roots_are_candidates_but_not_locally_deleted(self):
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

        assert issues_are_semantic_dedup_candidates(
            authorization,
            transaction,
        )
        assert deduplicate_final_issues([authorization, transaction]) == [authorization, transaction]

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


_FROZEN_DUPLICATES = json.loads(
    (Path(__file__).parent / 'fixtures' / 'verifier_duplicate_findings.json').read_text()
)


def _decision_llm(decision):
    llm = MagicMock()
    llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=decision)
    return llm


def test_semantic_prompt_requires_exact_indices_and_shared_keeper_cause():
    issues = [_real_issue(title=f'Finding {i}') for i in range(3)]
    prompt = _dedup_prompt(_format_semantic_batch(issues, dict.fromkeys(range(3), 'candidate_0')))
    assert 'Valid index range: 0..2 (inclusive)' in prompt
    assert 'exact supplied ZERO-BASED index values' in prompt
    assert 'INCLUDING the keeper' in prompt
    assert 'title and suggested fix cover the whole group' in prompt
    assert 'without dropping the keeper\'s own assertion' in prompt
    assert 'covered_by_indices must name FINAL retained representatives' in prompt


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize('case', _FROZEN_DUPLICATES['same_location_pairs'])
async def test_frozen_pairs_are_compared_and_only_explicit_duplicate_decisions_merge(case):
    """Real saved inputs, controlled model decisions; not an inference-quality test."""
    issues = [CodeReviewIssue.model_validate(data) for data in case['issues']]
    duplicate = case['classification'] == 'same_defect_overlap'
    assert issues_are_semantic_dedup_candidates(*issues)
    decision = SemanticDeduplicationDecision(duplicate_groups=[
        SemanticDuplicateGroup(keeper_index=0, duplicate_indices=[1], confidence='HIGH')
    ] if duplicate else [])
    llm = _decision_llm(decision)
    consolidated = []

    result = await deduplicate_final_issues_llm(
        llm, issues, on_consolidated=lambda keeper, sources: consolidated.append((keeper, sources)),
    )

    assert llm.with_structured_output.return_value.ainvoke.await_count == 2
    assert len(result) == (1 if duplicate else 2)
    assert len(consolidated) == int(duplicate)
    if duplicate:
        assert result[0] is issues[0]  # Synthetic CROSS identity does not override the keeper.
        assert {id(source) for source in consolidated[0][1]} == {id(issue) for issue in issues}


@pytest.mark.parametrize('case', _FROZEN_DUPLICATES['cross_anchor_groups'])
def test_oversized_nomination_recognizes_frozen_helper_and_caller_evidence(case):
    issues = [CodeReviewIssue.model_validate(data) for data in case['issues']]
    groups = _semantic_candidate_groups(issues)
    assert groups
    assert {id(issue) for group in groups for issue in group} == {id(issue) for issue in issues}


@pytest.mark.asyncio(loop_scope="function")
async def test_covered_summary_keeps_distinct_der_defects_and_provenance():
    from service.review.candidate_ledger import CandidateEvidenceLedger
    case = _FROZEN_DUPLICATES['cross_anchor_groups'][3]
    assert case['comment_indices'] == [2, 3, 5]
    issues = [CodeReviewIssue.model_validate(data) for data in case['issues']]
    original_reasons = [issue.reason for issue in issues]
    issues[2].evidenceRefs = ['saved-summary-source']
    calls = []
    ledger = CandidateEvidenceLedger()
    for index, issue in enumerate(issues):
        ledger.register(issue, stage='stage_2' if index == 2 else 'stage_1',
                        source_key=str(index), review_unit_ids=['unit'],
                        prompt_hunk_ids=['hunk'], generation_prompt='frozen fixture callback check')

    def record_consolidation(keeper, sources):
        calls.append((keeper, list(sources)))
        ledger.consolidate(keeper, sources)

    llm = _decision_llm(SemanticDeduplicationDecision(covered_summaries=[
        SemanticCoveredSummary(summary_index=2, covered_by_indices=[0, 1], confidence='HIGH'),
    ]))

    result = await deduplicate_final_issues_llm(
        llm, issues, on_consolidated=record_consolidation,
    )

    assert result == issues[:2]
    assert [issue.reason for issue in result] == original_reasons[:2]
    assert all('saved-summary-source' in issue.evidenceRefs for issue in result)
    assert len(calls) == 2
    assert calls[0][0] is issues[0] and calls[1][0] is issues[1]
    assert all(issues[2] in sources for _, sources in calls)
    summary_id = ledger.record_for(issues[2]).candidate_id
    assert all(summary_id in ledger.record_for(issue).consolidated_candidate_ids for issue in result)
    assert all(ledger.record_for(issue).assessments[-1]['reasonCode'] == 'consolidated_evidence' for issue in result)


@pytest.mark.asyncio(loop_scope="function")
@pytest.mark.parametrize('summary', [
    SemanticCoveredSummary(summary_index=2, covered_by_indices=[0, 99], confidence='HIGH'),
    SemanticCoveredSummary(summary_index=2, covered_by_indices=[0], confidence='HIGH'),
    SemanticCoveredSummary(summary_index=2, covered_by_indices=[0, 1], confidence='LOW'),
    SemanticCoveredSummary(summary_index=0, covered_by_indices=[0, 1], confidence='HIGH'),
])
async def test_malformed_or_uncertain_summary_coverage_retains_all(summary):
    issues = [_real_issue(file=f'{i}.py', title=f'Root {i}', reason=f'Defect {i}') for i in range(3)]
    result = await deduplicate_final_issues_llm(
        _decision_llm(SemanticDeduplicationDecision(covered_summaries=[summary])), issues,
    )
    assert result == issues


@pytest.mark.asyncio(loop_scope="function")
async def test_full_review_cannot_merge_different_source_sides():
    issues = [_real_issue(reason='Head current regression'), _real_issue(reason='Base removed regression')]
    issues[1].anchorSide = 'BASE'
    decision = SemanticDeduplicationDecision(duplicate_groups=[
        SemanticDuplicateGroup(keeper_index=0, duplicate_indices=[1], confidence='HIGH'),
    ])
    result = await deduplicate_final_issues_llm(_decision_llm(decision), issues)
    assert result == issues


@pytest.mark.asyncio(loop_scope="function")
async def test_overlapping_budgeted_batches_resolve_to_current_canonical(monkeypatch):
    import service.review.orchestrator.reconciliation as reconciliation
    issues = [_real_issue(line=10 * (i + 1), title=f'Caller {i}', reason=(f'Caller {i}: ' + 'supporting source ' * 300)) for i in range(3)]
    decision = SemanticDeduplicationDecision(duplicate_groups=[
        SemanticDuplicateGroup(keeper_index=0, duplicate_indices=[1], confidence='HIGH', merged_reason='One shared defect with all caller evidence.'),
    ])
    target = _estimate_dedup_input_tokens(_dedup_refinement_prompt(_format_semantic_batch(issues[:2], {0:'candidate_0', 1:'candidate_0'}), decision)) + 50
    monkeypatch.setattr(reconciliation, '_dedup_input_token_target', lambda _: target)
    batches = _build_semantic_dedup_batches([issues], input_token_target=target)
    assert len(batches) > 1
    for left in range(3):
        for right in range(left + 1, 3):
            assert any(issues[left] in batch and issues[right] in batch for batch, _ in batches)
    llm = _decision_llm(decision)
    calls = []
    result = await deduplicate_final_issues_llm(llm, issues, on_consolidated=lambda keeper, sources: calls.append((keeper, list(sources))))
    assert len(result) == 1
    assert result[0].relatedLocations == ['a.py:20', 'a.py:30']
    assert len(calls) == 2
    for invocation in llm.with_structured_output.return_value.ainvoke.await_args_list:
        assert _estimate_dedup_input_tokens(invocation.args[0]) <= target


@pytest.mark.asyncio(loop_scope="function")
async def test_semantic_keeper_retains_broad_contract_metadata_from_real_cal11059():
    case = _FROZEN_DUPLICATES['keeper_metadata_regression']
    issues = [CodeReviewIssue.model_validate(data) for data in case['issues']]
    selected = issues[0].model_copy(deep=True)
    assert len(issues[1].title) > len(selected.title)
    assert len(issues[2].suggestedFixDescription) > len(selected.suggestedFixDescription)
    decision = SemanticDeduplicationDecision(duplicate_groups=[
        SemanticDuplicateGroup(keeper_index=0, duplicate_indices=[1, 2], confidence='HIGH'),
    ])

    result = await deduplicate_final_issues_llm(_decision_llm(decision), issues)

    assert result == [issues[0]]
    assert result[0].title == selected.title
    assert result[0].suggestedFixDescription == selected.suggestedFixDescription
    assert result[0].suggestedFixDiff == selected.suggestedFixDiff
    assert result[0].severity == 'HIGH'
    assert {f'{issue.file}:{issue.line}' for issue in issues[1:]} <= set(result[0].relatedLocations)
    assert set(selected.relatedLocations) <= set(result[0].relatedLocations)


@pytest.mark.asyncio(loop_scope="function")
async def test_historical_identity_wins_semantic_keeper_and_keeps_evidence():
    current = _real_issue(file='caller.py', line=40, title='Shared root', reason='Caller hits the missing guard.', code_snippet='shared(value)')
    historical = _real_issue(file='shared.py', line=5, issue_id='392', title='Long historical caller-specific title', reason='Shared function has no guard.')
    historical.suggestedFixDescription = 'Long historical caller-specific workaround instead of shared-root repair.'
    historical.suggestedFixDiff = 'Old caller-specific patch'
    current.evidenceRefs = ['caller-source']
    historical.evidenceRefs = ['helper-source']
    current.evidenceGaps = ['Missing deployment configuration']
    decision = SemanticDeduplicationDecision(duplicate_groups=[
        SemanticDuplicateGroup(keeper_index=0, duplicate_indices=[1], confidence='HIGH'),
    ])
    calls = []
    result = await deduplicate_final_issues_llm(_decision_llm(decision), [current, historical], on_consolidated=lambda keeper, sources: calls.append((keeper, sources)))
    assert result == [historical]
    assert result[0].id == '392'
    assert result[0].title == current.title
    assert result[0].suggestedFixDescription == current.suggestedFixDescription
    assert result[0].suggestedFixDiff is None
    assert result[0].evidenceRefs == ['caller-source', 'helper-source']
    assert result[0].relatedLocations == ['caller.py:40']
    assert result[0].evidenceGaps == ['Missing deployment configuration']
    assert 'Caller hits the missing guard.' in result[0].reason
    assert 'Shared function has no guard.' in result[0].reason
    assert calls[0][0] is historical


@pytest.mark.asyncio(loop_scope='function')
async def test_real_cal_proposal_is_refined_before_any_mutation():
    case = _FROZEN_DUPLICATES['refinement_regression']
    issues = [CodeReviewIssue.model_validate(data) for data in case['issues']]
    before = [issue.model_dump() for issue in issues]
    proposal = SemanticDeduplicationDecision.model_validate(case['proposal'])
    refinement = SemanticDeduplicationDecision.model_validate(case['refinement'])
    calls = []
    prompts = []

    async def respond(prompt, **kwargs):
        prompts.append(prompt)
        assert [issue.model_dump() for issue in issues] == before
        assert not calls
        if len(prompts) == 1:
            return proposal
        assert 'MISSED duplicate assertions' in prompt
        assert 'Untrusted proposed decisions' in prompt
        assert 'computed property expressions' in prompt  # Original schema assertion survives proposal input.
        return refinement

    llm = _decision_llm(proposal)
    llm.with_structured_output.return_value.ainvoke.side_effect = respond
    result = await deduplicate_final_issues_llm(llm, issues, on_consolidated=lambda keeper, sources: calls.append((keeper, sources)))

    assert len(prompts) == 2
    assert len(result) == 10
    assert issues[2] in result and issues[15] in result
    assert all(issues[index] in result for index in [8, 10, 13])
    assert 'numeric expiry' in issues[2].reason
    assert 'raw fetch Response' in issues[15].reason
    assert issues[15].title == before[15]['title']
    assert any(keeper is issues[15] and issues[1] in sources and issues[2] not in sources for keeper, sources in calls)


@pytest.mark.asyncio(loop_scope='function')
async def test_refinement_can_recover_a_missed_duplicate_from_empty_proposal():
    issues = [_real_issue(line=1, reason='A caller reaches the missing guard.'), _real_issue(line=2, reason='The shared guard is missing.')]
    decision = SemanticDeduplicationDecision(duplicate_groups=[
        SemanticDuplicateGroup(keeper_index=1, duplicate_indices=[0], confidence='HIGH'),
    ])
    llm = _decision_llm(decision)
    llm.with_structured_output.return_value.ainvoke.side_effect = [SemanticDeduplicationDecision(), decision]
    result = await deduplicate_final_issues_llm(llm, issues)
    assert result == [issues[1]]
    assert llm.with_structured_output.return_value.ainvoke.await_count == 2


@pytest.mark.asyncio(loop_scope='function')
@pytest.mark.parametrize('failure', ['unavailable', 'timeout', 'invalid'])
async def test_failed_refinement_never_applies_or_mutates_proposal(monkeypatch, failure):
    import service.review.orchestrator.reconciliation as reconciliation
    issues = [_real_issue(line=1, title='Independent validation', reason='Schema loses expiry.'), _real_issue(line=2, title='Wrong response', reason='Raw response lacks data.')]
    before = [issue.model_dump() for issue in issues]
    proposal = SemanticDeduplicationDecision(duplicate_groups=[
        SemanticDuplicateGroup(keeper_index=0, duplicate_indices=[1], confidence='HIGH'),
    ])
    calls = []
    count = 0

    async def respond(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 1:
            return proposal
        if failure == 'timeout':
            await asyncio.Event().wait()
        if failure == 'invalid':
            return SemanticDeduplicationDecision(duplicate_groups=[
                SemanticDuplicateGroup(keeper_index=0, duplicate_indices=[99], confidence='HIGH'),
            ])
        raise RuntimeError('provider unavailable')

    monkeypatch.setattr(reconciliation, 'optional_work_budget', lambda _: 0.01 if failure == 'timeout' else 60)
    llm = _decision_llm(proposal)
    llm.with_structured_output.return_value.ainvoke.side_effect = respond
    result = await deduplicate_final_issues_llm(llm, issues, on_consolidated=lambda *args: calls.append(args))
    assert result == issues
    assert [issue.model_dump() for issue in issues] == before
    assert count == 2 and not calls


@pytest.mark.asyncio(loop_scope='function')
@pytest.mark.parametrize('limit', ['time', 'tokens'])
async def test_refinement_budget_includes_proposal_and_retains_originals(monkeypatch, limit):
    import service.review.orchestrator.reconciliation as reconciliation
    issues = [_real_issue(line=1, reason='Schema loses expiry.'), _real_issue(line=2, reason='Raw response lacks data.')]
    before = [issue.model_dump() for issue in issues]
    proposal = SemanticDeduplicationDecision(duplicate_groups=[
        SemanticDuplicateGroup(keeper_index=0, duplicate_indices=[1], confidence='HIGH', merged_reason='Long synthesis ' * 300),
    ])
    llm = _decision_llm(proposal)
    if limit == 'time':
        monkeypatch.setattr(reconciliation, 'optional_work_budget', lambda _: 0)
        target = 60000
    else:
        text = _format_semantic_batch(issues, {0:'candidate_0',1:'candidate_0'})
        target = _estimate_dedup_input_tokens(_dedup_prompt(text)) + 10
        assert _estimate_dedup_input_tokens(_dedup_refinement_prompt(text, proposal)) > target
    result = await _dedup_batch_with_llm(llm, issues, {0:'candidate_0',1:'candidate_0'}, input_token_target=target)
    assert result == issues and [issue.model_dump() for issue in issues] == before
    llm.with_structured_output.return_value.ainvoke.assert_awaited_once()
