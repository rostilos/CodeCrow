"""Regression envelopes reconstructed read-only from the Qodo20 sealed graphs."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from rag_pipeline.core.review_graph.budget import bound_graph_response
from tests.test_review_graph_budget import _bounded


def _recorded(label):
    return json.loads((Path(__file__).parent / 'fixtures' / f'{label}-ambiguity.json').read_text())


@pytest.mark.parametrize('label,budget', [('tauri-titlebar', 1200), ('ghost-deleted-js', 1800),
                                         ('tauri-titlebar', 512), ('ghost-deleted-js', 512)])
def test_recorded_ambiguity_retains_exact_disambiguation_and_progress(label, budget):
    original = _recorded(label)
    before = deepcopy(original)
    bounded = bound_graph_response(original, budget)
    _bounded(bounded, budget)
    assert bounded['status'] == 'ambiguous'
    assert bounded['candidateCount'] == original['candidateCount']
    assert bounded['candidates']
    assert 'unitId' in bounded['hint']
    assert bounded['candidateResultCount'] == len(bounded['candidates'])
    for actual, expected in zip(bounded['candidates'], original['candidates']):
        assert actual['unitId'] == expected['unitId']
    assert bounded['nextCursor'] == (len(bounded['candidates'])
                                      if len(bounded['candidates']) < original['candidateCount'] else None)
    assert bounded['truncated'] is (len(bounded['candidates']) < original['candidateCount'])
    assert original == before


def test_reduced_candidate_pages_do_not_skip_or_repeat():
    original = _recorded('tauri-titlebar')
    cursor, observed = 0, []
    for _ in range(20):
        page = deepcopy(original)
        page.update(cursor=cursor, candidates=original['candidates'][cursor:])
        bounded = bound_graph_response(page, 512)
        _bounded(bounded, 512)
        observed.extend(item['unitId'] for item in bounded['candidates'])
        following = bounded['nextCursor']
        if following is None:
            break
        assert following > cursor
        cursor = following
    assert observed == [item['unitId'] for item in original['candidates']]


def test_oversized_candidate_identity_reports_exhaustion_without_cursor_loop():
    original = _recorded('tauri-titlebar')
    original['candidates'][0]['unitId'] = 'unit:' + 'x' * 6000
    bounded = bound_graph_response(original, 512)
    _bounded(bounded, 512)
    assert bounded['candidateCount'] == 9
    assert bounded['candidates'] == []
    assert bounded['nextCursor'] is None
    assert bounded['reason'] == 'candidate_budget_exhausted'
    assert 'path' in bounded['hint']


def test_duplicate_ambiguity_metadata_preserves_first_exact_candidate():
    original = _recorded('tauri-titlebar')
    original['operation'] = 'traverse_review_graph'
    original['ambiguousTargets'] = [deepcopy(original)]
    original['candidates'][0]['path'] = '漢' * 1200
    bounded = bound_graph_response(original, 512)
    _bounded(bounded, 512)
    assert bounded['candidateCount'] == 9
    assert bounded['candidates'][0]['unitId'] == original['candidates'][0]['unitId']
    assert bounded['nextCursor'] == 1


def test_unbudgeted_ambiguity_retains_full_facts():
    original = _recorded('tauri-titlebar')
    assert bound_graph_response(original, None) is original


def test_already_empty_page_is_not_reported_as_budget_exhaustion():
    original = _recorded('tauri-titlebar')
    original.update(cursor=9, candidates=[], candidateResultCount=0)
    bounded = bound_graph_response(original, 4000)
    _bounded(bounded, 4000)
    assert bounded['candidateCount'] == 9
    assert bounded['nextCursor'] is None
    assert bounded.get('reason') != 'candidate_budget_exhausted'
