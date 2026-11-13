"""Saved benchmark failure shapes; no model calls or corpus reruns."""
import json
from types import SimpleNamespace

import pytest

from model.output_schemas import CodeReviewIssue
from service.review.candidate_ledger import CandidateEvidenceLedger
from service.review.orchestrator.mcp_tool_executor import McpToolExecutor
from service.review.orchestrator.stage_3_mcp_verification import (
    extract_verification_assessments, mcp_read_covers_location,
)
from service.review.orchestrator.verification_agent import _verification_prompt


def issue(line=10, file='src/component.ts'):
    return CodeReviewIssue(file=file, line=line, severity='HIGH',
        category='BUG_RISK', title='Changed predicate rejects valid input',
        reason='The equality branch throws for the same renderer.',
        suggestedFixDescription='Reject only different renderers.',
        codeSnippet='if (manager && currentRendererId === rendererId) {', scope='BLOCK')


def request():
    return SimpleNamespace(projectVcsWorkspace='workspace', projectVcsRepoSlug='repo',
        mcpLocalOnly=True, localRepoPath='/bound/base', localRepoRevision='base-sha',
        localReviewOverlayPath='/bound/head', localRepoTargetBranch='main')


def test_rejected_candidate_preserves_original_coordinates_and_exact_payload():
    ledger = CandidateEvidenceLedger()
    candidate = issue()
    original = candidate.model_dump(mode='json', by_alias=True)
    ledger.register(candidate, stage='stage_1', source_key='batch:0',
        review_unit_ids=['unit'], prompt_hunk_ids=['hunk'], generation_prompt='exact prompt')
    candidate.line = 99
    candidate.codeSnippet = 'canonicalized()'
    ledger.reject(candidate, gate='candidate_provenance', code='anchor_outside_generation_unit')
    record = ledger.summary()['records'][0]
    assert record['originalIssue'] == original
    assert record['promptHunkIds'] == ['hunk']


def test_latest_unknown_assessment_does_not_inherit_earlier_support():
    ledger = CandidateEvidenceLedger()
    candidate = issue()
    ledger.register(candidate, stage='stage_1', source_key='batch:0',
        review_unit_ids=['unit'], prompt_hunk_ids=['hunk'], generation_prompt='exact prompt')
    ledger.assess(candidate, {'verdict': 'supported', 'stage': 'stage_1_verification',
                            'attempted': True, 'completed': True})
    ledger.assess(candidate, {'verdict': 'unknown', 'stage': 'stage_3_verification',
                            'attempted': True, 'completed': True, 'reasonCode': 'assessed'})
    ledger.publish([candidate])
    assert ledger.verification_summary()['supported'] == 0
    assert ledger.verification_summary()['unknown'] == 1


@pytest.mark.asyncio(loop_scope='function')
async def test_one_observed_window_binds_to_both_candidate_anchors_but_not_distant_issue():
    class Session:
        async def call_tool(self, name, arguments):
            return SimpleNamespace(content=[SimpleNamespace(text=json.dumps({
                'fileContent': 'if (manager && currentRendererId === rendererId) {\n  throw new Error();\n}',
                'startLine': 1, 'endLine': 30, 'completeFile': False,
            }))])
    issues = {'issue_0': issue(10), 'issue_1': issue(12), 'issue_2': issue(200)}
    executor = McpToolExecutor(SimpleNamespace(session=Session()), request(), 'stage_3',
                               review_revision='head-sha', verification_issues=issues)
    await executor.execute_tool('getReviewFileContent', {'filePath': 'src/component.ts',
                                                        'verificationId': 'issue_0'})
    assert ('issue_1', 'src/component.ts') in executor.verification_source
    assert ('issue_2', 'src/component.ts') not in executor.verification_source
    assert mcp_read_covers_location(executor.call_log[0], 'issue_1', 'src/component.ts', 12, 'head-sha')
    assert not mcp_read_covers_location(executor.call_log[0], 'issue_1', 'src/component.ts', 12, 'another-sha')
    _, assessments = extract_verification_assessments(json.dumps({'report': 'ok', 'assessments': [{
        'issue_id': 'issue_1', 'verdict': 'supported', 'reason': 'The exact equality branch is present.',
        'supporting_evidence': [{'file_path': 'src/component.ts',
                               'snippet': 'if (manager && currentRendererId === rendererId) {'}],
    }]}), executor, issues)
    assert assessments['issue_1']['verdict'] == 'supported'


def test_predicate_verification_prompt_requests_trigger_and_branch_outcome():
    # ASP.NET7 was dismissed after === was described as rejecting different IDs.
    prompt = _verification_prompt('if (manager && currentRendererId === rendererId) { throw new Error(); }')
    assert 'candidate trigger' in prompt
    assert 'predicate' in prompt
    assert 'same' in prompt and 'different' in prompt
    assert 'comments' in prompt


@pytest.mark.asyncio(loop_scope='function')
@pytest.mark.parametrize('local_only,has_snippet', [(True, True), (False, True), (True, False)])
async def test_stage3_acquires_shared_anchor_evidence_before_model_assessment(local_only, has_snippet):
    from service.review.orchestrator.stage_3_mcp_verification import (
        Stage3McpRuntime, execute_stage_3_mcp_verification,
    )
    source = 'if (manager && currentRendererId === rendererId) {\n  throw new Error();\n}'
    calls = []

    class Session:
        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            return SimpleNamespace(content=[SimpleNamespace(text=json.dumps({
                'fileContent': source, 'startLine': 1, 'endLine': 30, 'completeFile': False,
            }))])

    class Llm:
        def bind_tools(self, _tools):
            return self

        async def ainvoke(self, messages, **_kwargs):
            assert any(source in m.get('content', '') for m in messages if isinstance(m, dict))
            assert any('candidate trigger' in m.get('content', '') for m in messages if isinstance(m, dict))
            return SimpleNamespace(tool_calls=[], content=json.dumps({'report': 'assessed', 'assessments': [
                {'issue_id': key, 'verdict': 'supported', 'reason': 'Equality enters the throw branch.',
                 'supporting_evidence': [{'file_path': 'src/component.ts', 'snippet': source}]}
                for key in ('issue_0', 'issue_1')
            ]}), response_metadata={})

    async def fallback(*_args, **_kwargs):
        return {'report': 'fallback'}

    runtime = Stage3McpRuntime(input_token_target=lambda _request: 10000,
        estimate_messages_tokens=lambda _messages, **_kwargs: 1,
        continuation_messages=lambda _prompt, _records: [], invoke_report=fallback,
        response_finished_by_length=lambda _response: False)
    req = request()
    req.mcpLocalOnly = local_only
    candidates = {'issue_0': issue(10), 'issue_1': issue(12)}
    if not has_snippet:
        for candidate in candidates.values():
            candidate.codeSnippet = ''
    result = await execute_stage_3_mcp_verification(Llm(), req, 'verify both candidates',
        SimpleNamespace(session=Session()), 'head-sha', candidates, runtime)
    assert [a['verdict'] for a in result['verification_assessments']] == ['supported', 'supported']
    assert len(calls) == 1


@pytest.mark.parametrize('corpus,stage,snippet,line', [
    ('Ghost', 'stage_1', '`// Only add UTM parameters if they are defined (not undefined/null)`', 109),
    ('cal.com', 'stage_2', '+import { trpc } from "@calcom/trpc/react";', 12),
])
def test_saved_diff_anchor_recovery_preserves_producer_packet(corpus, stage, snippet, line):
    """Actual reviewed diffs, reconstructed model decoration/old-line hints.

    Historical rejected candidates omitted their snippets, so these are input
    shape regressions rather than claims to replay the lost original output.
    """
    from pathlib import Path
    from service.review.orchestrator.orchestrator import _register_stage_2_candidates
    from service.review.orchestrator.verification_agent import apply_candidate_provenance_gate
    from service.review.orchestrator.stage_1_file_review import Stage1ReviewUnitState
    from utils.diff_processor import DiffProcessor
    fixtures = json.loads((Path(__file__).parent/'fixtures/benchmark_anchor_regressions.json').read_text())
    fixture = next(x for x in fixtures if f'/{corpus}/' in x['url'])
    processed = DiffProcessor().process(fixture['diff'])
    candidate = issue(line, fixture['path']).model_copy(update={'codeSnippet': snippet})
    original = candidate.model_dump(mode='json', by_alias=True)
    hunks = {h.id for h in processed.files[0].review_hunks}
    units = Stage1ReviewUnitState(units_by_hunk={h: {'unit'} for h in hunks},
        unit_owner={'unit': 1}, completed_unit_ids={'unit'}, registered=True)
    ledger = CandidateEvidenceLedger()
    req = SimpleNamespace(previousCodeAnalysisIssues=[], enrichmentData=None)
    if stage == 'stage_2':
        _register_stage_2_candidates([candidate], req, processed, units, ledger, hunks, {},
                                    {'generationPromptDigest': 'sha256:'+'a'*64})
    else:
        ledger.register(candidate, stage=stage, source_key='batch:0', review_unit_ids=['unit'],
                        prompt_hunk_ids=hunks, generation_prompt='actual source hunk packet')
    assert apply_candidate_provenance_gate([candidate], req, processed, ledger, units.units_by_hunk) == [candidate]
    record = ledger.summary()['records'][0]
    assert record['originalIssue'] == original
    assert set(record['generationPromptHunkIds']) == hunks
    assert candidate.codeSnippet in fixture['diff']
    assert candidate.line != line


@pytest.mark.parametrize('stage', ['stage_1', 'stage_2'])
@pytest.mark.parametrize('snippet,retained', [("const repository = require('./DonationBookshelfRepository');", True),
                                            ('missingImport()', False)])
def test_rename_impact_anchor_uses_real_new_path_source(stage, snippet, retained):
    from tests.test_path_change_impact import NEW, request_for_rename
    from service.review.evidence_scopes import process_review_evidence_scopes
    from service.review.orchestrator.orchestrator import _register_stage_2_candidates
    from service.review.orchestrator.verification_agent import apply_candidate_provenance_gate
    from service.review.orchestrator.stage_1_file_review import Stage1ReviewUnitState
    req = request_for_rename()
    processed = process_review_evidence_scopes(req).review
    hunk = processed.files[0].review_hunks[0]
    candidate = issue(1, NEW).model_copy(update={'codeSnippet': snippet})
    units = Stage1ReviewUnitState(units_by_hunk={hunk.id: {'unit'}}, unit_owner={'unit': 1},
                                completed_unit_ids={'unit'}, registered=True)
    ledger = CandidateEvidenceLedger()
    if stage == 'stage_2':
        _register_stage_2_candidates([candidate], req, processed, units, ledger, {hunk.id}, {},
                                    {'generationPromptDigest': 'sha256:'+'a'*64})
    else:
        ledger.register(candidate, stage=stage, source_key='rename', review_unit_ids=['unit'],
                        prompt_hunk_ids=[hunk.id], generation_prompt=processed.files[0].content)
    kept = apply_candidate_provenance_gate([candidate], req, processed, ledger, units.units_by_hunk)
    assert bool(kept) == retained
    assert ledger.summary()['records'][0]['generationPromptHunkIds'] == [hunk.id]


@pytest.mark.asyncio(loop_scope='function')
@pytest.mark.parametrize('failure', ['source_error', 'context_limit'])
async def test_unobserved_prefetch_cannot_authorize_dismissal(failure):
    from service.review.orchestrator.stage_3_mcp_verification import Stage3McpRuntime, execute_stage_3_mcp_verification
    source = 'if (manager && currentRendererId === rendererId) { throw new Error(); }'
    class Session:
        async def call_tool(self, name, arguments):
            if failure == 'source_error':
                raise RuntimeError('source unavailable')
            return SimpleNamespace(content=[SimpleNamespace(text=json.dumps({
                'fileContent': source, 'startLine': 1, 'endLine': 30, 'completeFile': False,
            }))])
    class Llm:
        def bind_tools(self, _tools):
            return self
        async def ainvoke(self, messages, **_kwargs):
            assert not any(source in m.get('content', '') for m in messages if isinstance(m, dict))
            return SimpleNamespace(tool_calls=[], content=json.dumps({'report': 'retained', 'assessments': [{
                'issue_id': 'issue_0', 'verdict': 'disproven',
                'reason': 'Unobserved source must not authorize rejection.',
                'contradicting_evidence': [{'file_path': 'src/component.ts', 'snippet': source}],
            }]}), response_metadata={})
    async def fallback(*_args, **_kwargs):
        return {'report': 'retained'}
    runtime = Stage3McpRuntime(input_token_target=lambda _request: 10000,
        estimate_messages_tokens=lambda messages, **_kwargs: 10001 if any(source in m.get('content', '') for m in messages if isinstance(m, dict)) else 1,
        continuation_messages=lambda prompt, _records: [{'role': 'user', 'content': prompt}],
        invoke_report=fallback, response_finished_by_length=lambda _response: False)
    result = await execute_stage_3_mcp_verification(Llm(), request(), 'verify',
        SimpleNamespace(session=Session()), 'head-sha', {'issue_0': issue()}, runtime)
    assert result['verification_assessments'][0]['verdict'] == 'unknown'
    assert result['dismissed_issue_object_ids'] == []


@pytest.mark.asyncio(loop_scope='function')
async def test_grafana_independent_methods_survive_while_same_query_root_merges():
    """Grafana94942 method distinction; mocked grouping tests host behavior only."""
    from unittest.mock import MagicMock, AsyncMock
    from model.output_schemas import SemanticDeduplicationDecision, SemanticDuplicateGroup
    from service.review.orchestrator.reconciliation import _dedup_batch_with_llm, deduplicate_final_issues
    parsing = issue(17, 'pkg/expr/sql/db.go').model_copy(update={
        'title': 'Replacement database makes all SQL parsing fail',
        'reason': 'RunCommands always returns not implemented, so TablesList cannot parse valid SQL.',
        'codeSnippet': 'func (db *DB) RunCommands() error {'})
    query = issue(20, 'pkg/expr/sql/db.go').model_copy(update={
        'title': 'Replacement database rejects every SQL query',
        'reason': 'QueryFramesInto always returns not implemented, so valid frames produce no result.',
        'codeSnippet': 'func (db *DB) QueryFramesInto() error {'})
    duplicate_query = query.model_copy(update={'reason': 'Every SQL expression fails because QueryFramesInto unconditionally errors.'})
    assert deduplicate_final_issues([parsing, query]) == [parsing, query]
    llm = MagicMock()
    llm.with_structured_output.return_value.ainvoke = AsyncMock(return_value=SemanticDeduplicationDecision(
        duplicate_groups=[SemanticDuplicateGroup(keeper_index=1, duplicate_indices=[2], confidence='HIGH',
            rationale='The same unconditional QueryFramesInto stub is described twice; RunCommands is separate.')]))
    retained = await _dedup_batch_with_llm(llm, [parsing, query, duplicate_query],
                                         {0: 'file_group', 1: 'file_group', 2: 'file_group'})
    assert retained == [parsing, query]
