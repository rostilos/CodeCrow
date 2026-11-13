"""Acquired changed source must reach bounded prompts without coverage cutoffs."""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from model.dtos import ReviewRequestDto
from model.multi_stage import ReviewFile, ReviewPlan
from service.review.evidence_scopes import process_review_evidence_scopes
from service.review.orchestrator.stage_1_file_review import (
    _build_stage_1_prepared_context, _expand_oversized_stage1_evidence_batches,
    _prepare_stage1_prompt_material, _render_stage1_prompt, _estimated_prompt_tokens,
)


def changed_file(path, count=180):
    return (f'diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n' + ''.join(
        f'@@ -{i + 1},1 +{i + 1},1 @@\n-old_{i}\n+MARKER_{i:04d}_' + 'x' * 180 + '\n'
        for i in range(count)))


def request(**kwargs):
    return ReviewRequestDto(projectId=7, projectVcsWorkspace='owner', projectVcsRepoSlug='repo',
        projectWorkspace='tenant', projectNamespace='project', aiProvider='openai', aiModel='test',
        aiApiKey='test', pullRequestId=9, commitHash='head', **kwargs)


@pytest.mark.parametrize('incremental', [False, True])
def test_complete_text_survives_full_and_incremental_review_parsing(incremental):
    delta = changed_file('large.py')
    full = changed_file('earlier.py', 1) + delta
    req = request(rawDiff=full, changedFiles=['large.py'],
        **({'analysisMode': 'INCREMENTAL', 'deltaDiff': delta} if incremental else {}))
    scopes = process_review_evidence_scopes(req)
    prepared = _build_stage_1_prepared_context(req, scopes.review, incremental)
    for parsed in (scopes.review, scopes.full_pr, prepared.diff_source):
        large = next(f for f in parsed.files if f.path == 'large.py')
        assert len(large.review_hunks) == 180 and not large.omitted_hunk_ids
        assert 'MARKER_0179_' in large.content
    assert ('earlier.py' in prepared.diff_by_path) is (not incremental)
    assert 'earlier.py' in {f.path for f in scopes.full_pr.files}


def test_all_primary_local_evidence_shards_are_admitted_under_small_prompt_budget():
    raw = changed_file('large.py', 300)
    req = request(rawDiff=raw, changedFiles=['large.py'])
    scopes = process_review_evidence_scopes(req)
    prepared = _build_stage_1_prepared_context(req, scopes.review, False)
    item = {'file': ReviewFile(path='large.py', focus_areas=[], risk_level='MEDIUM'), 'priority': 'MEDIUM'}
    budget = 6500
    batches = _expand_oversized_stage1_evidence_batches([[item]], req, prepared, False, budget)
    prompts = [_render_stage1_prompt(_prepare_stage1_prompt_material(req, b, prepared, False), '')[0]
               for b in batches]
    assert len(prompts) > 3
    assert all(_estimated_prompt_tokens(p) <= budget for p in prompts)
    joined = '\n'.join(prompts)
    assert all(f'MARKER_{i:04d}_' in joined for i in range(300))
    assert not any(item.get('_omitted_hunk_ids') or item.get('_omitted_stage1_unit_count')
                   for batch in batches for item in batch)


@pytest.mark.asyncio
async def test_stage1_executes_every_expanded_shard_even_with_one_call_profile(monkeypatch):
    from service.review.orchestrator import stage_1_file_review as stage
    raw = changed_file('large.py', 300)
    req = request(rawDiff=raw, changedFiles=['large.py'])
    scopes = process_review_evidence_scopes(req)
    item = {'file': ReviewFile(path='large.py', focus_areas=[], risk_level='MEDIUM'), 'priority': 'MEDIUM'}
    monkeypatch.setattr(stage, 'create_smart_batches_wrapper', AsyncMock(return_value=[[item]]))
    monkeypatch.setattr(stage, '_stage1_packing_token_limit', lambda request, summary_minimum_tokens=None: 6500)
    reviewed = []
    async def review(llm, request, batch, rag_client, prepared_context, is_incremental, **kwargs):
        material = _prepare_stage1_prompt_material(request, batch, prepared_context, is_incremental)
        reviewed.append(_render_stage1_prompt(material, '')[0])
        return []
    monkeypatch.setattr(stage, 'review_file_batch', review)
    await stage.execute_stage_1_file_reviews(None, req,
        ReviewPlan(analysis_summary='Review all acquired changes', file_groups=[], cross_file_concerns=[]),
        rag_client=None, processed_diff=scopes.review,
        inference_profile=SimpleNamespace(invocation_cap=lambda name: 0 if name == 'specialist_passes' else 1))
    assert len(reviewed) > 3
    assert all(_estimated_prompt_tokens(p) <= 6500 for p in reviewed)
    assert all(f'MARKER_{i:04d}_' in '\n'.join(reviewed) for i in range(300))
