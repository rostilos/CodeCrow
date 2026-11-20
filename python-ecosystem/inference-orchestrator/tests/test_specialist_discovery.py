"""Supplemental paid work must be selected by changes, not diff filenames."""
from types import SimpleNamespace

import pytest

from service.review.orchestrator.specialist_discovery import select_specialist_batches


@pytest.mark.parametrize('path', ['composer.lock', 'security.md', 'async/notes.txt'])
def test_headers_and_unchanged_code_do_not_select_paid_specialists(path, monkeypatch):
    monkeypatch.setenv('REVIEW_SPECIALISTS_ENABLED', 'true')
    batch = [{'file': SimpleNamespace(path=path, focus_areas=[], risk_level='MEDIUM')}]
    diff = f'diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1,2 +1,2 @@\n async with lock:\n-old_version = 1\n+new_version = 2\n'
    prepared = SimpleNamespace(diff_by_path={path: SimpleNamespace(content=diff)})
    assert select_specialist_batches([batch], prepared, 2) == []


@pytest.mark.parametrize('changed_line,focus', [('+await lock.acquire()', []), ('-lock.release()', []), ('+value = 2', ['concurrency'])])
def test_changed_concurrency_or_explicit_focus_still_selects_one_pass(changed_line, focus, monkeypatch):
    monkeypatch.setenv('REVIEW_SPECIALISTS_ENABLED', 'true')
    batch = [{'file': SimpleNamespace(path='worker.py', focus_areas=focus, risk_level='HIGH')}]
    prepared = SimpleNamespace(diff_by_path={'worker.py': SimpleNamespace(content='@@ -1 +1 @@\n' + changed_line)})
    selected = select_specialist_batches([batch], prepared, 2)
    assert len(selected) == 1
    assert selected[0][0].name == 'concurrency' and selected[0][1] is batch
    assert select_specialist_batches([batch], prepared, 0) == []
