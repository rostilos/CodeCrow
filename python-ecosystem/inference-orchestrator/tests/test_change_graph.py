"""Dependency neighborhoods must be exact, cumulative, and honest about gaps."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from model.dtos import ReviewRequestDto
from service.review.change_graph import ChangeGraph, graph_prompt_context
from service.review import change_summary as scan


def request(**kwargs):
    return ReviewRequestDto(projectId=7, projectVcsWorkspace='owner', projectVcsRepoSlug='repo',
        projectWorkspace='tenant', projectNamespace='project', aiProvider='openai', aiModel='test',
        aiApiKey='test', pullRequestId=9, commitHash='head', currentCommitHash='head',
        targetHeadCommitHash='base', localRepoRevision='base', localRepoTargetBranch='main',
        targetBranchName='main', localRepoPath='/repo', localReviewOverlayPath='/overlay',
        ragCollectionTarget='base-target', ragBaseGenerationManifestSha256='base-manifest',
        ragBaseGenerationRevision='base', ragReviewCollectionTarget='review-target',
        ragReviewGenerationManifestSha256='review-manifest', **kwargs)


def unit(path, name, start=10, end=20):
    return dict(unitId=path+':'+name, path=path, name=name, qualifiedName=name,
                startLine=start, endLine=end, kind='function', recordType='source_unit')


def relation(identity, source, target, line=12):
    return dict(evidenceId=identity, kind='CALLS', relation='calls', source=source['name'],
        target=target['name'], sourceUnit=source, targetUnit=target,
        origin=dict(path=source['path'], line=line, extractor='ast'),
        relatedPaths=[source['path'], target['path']], attributes={})


def snapshot(which):
    return (dict(revision='base', generationManifestSha256='base-manifest') if which == 'base'
        else dict(revision='head', sourceRevision='head', generationManifestSha256='review-manifest'))


def file(path='changed.py', **kwargs):
    return dict(path=path, changeType='modified', ranges=[dict(base=[12, 2], head=[12, 2])], **kwargs)


@pytest.mark.asyncio
async def test_paged_base_and_proposed_graph_keeps_incoming_consumers_and_dependency_changes():
    changed, caller, old, new = (unit('changed.py', 'changed'), unit('consumer.py', 'caller'),
                               unit('old.py', 'old'), unit('new.py', 'new'))
    relations = {
        'base': [relation('r-incoming-base', caller, changed), relation('r-old', changed, old)],
        'proposed': [relation('r-incoming-head', caller, changed), relation('r-new', changed, new)],
    }
    async def query(which, **args):
        assert args['workspace'] == 'tenant' and args['project'] == 'project'
        if which == 'base':
            assert args['collection_target'] == 'base-target'
        else:
            assert args['review_collection_target'] == 'review-target'
            assert args['source_revision'] == 'head'
        values = [changed] if args['pattern'] == 'file_summary' else relations[which]
        cursor = args['cursor']
        return dict(snapshot=snapshot(which), results=values[cursor:cursor+1],
                    nextCursor=cursor+1 if cursor+1 < len(values) else None)
    client = SimpleNamespace(query_code_graph=AsyncMock(side_effect=lambda **kw: None))
    async def base(**kw): return await query('base', **kw)
    async def proposed(**kw): return await query('proposed', **kw)
    client.query_code_graph = AsyncMock(side_effect=base)
    client.query_review_graph = AsyncMock(side_effect=proposed)
    graph = ChangeGraph(request(), [file()], client)
    await graph.collect(['changed.py'])
    result = graph.render()
    assert result['coverage']['status'] == 'complete'
    assert {e['change'] for e in result['edges']} == {'added', 'removed', 'retained'}
    assert {n['path'] for n in result['nodes']} == {'changed.py', 'consumer.py', 'old.py', 'new.py'}
    assert any(q['cursor'] == 1 for q in result['queries'])
    count = client.query_code_graph.call_count
    await graph.collect(['changed.py'])
    assert client.query_code_graph.call_count == count
    ids = {n['id'] for n in result['nodes']}
    assert all(o['source'] in ids and o['target'] in ids for e in result['edges'] for o in e['observations'])
    compact = graph_prompt_context(result, ['changed.py'])
    assert sum(sum(row[3]) for row in compact['edges']) == len(result['edges'])
    assert ['file', 'consumer.py'] in compact['entities'] and ['file', 'new.py'] in compact['entities']
    detail = compact['symbolDetail']
    for row, original in zip(detail['edges'], result['edges']):
        assert detail['relationKinds'][row[0]] == [original['kind'], original['relationship']]
        assert detail['changes'][row[1]] == original['change']
        for observation in row[2]:
            assert 0 <= observation[1] < len(detail['nodes'])
            assert 0 <= observation[2] < len(detail['nodes'])


@pytest.mark.asyncio
async def test_stale_or_failed_graph_preserves_source_review_and_does_not_claim_removed_edges():
    changed, external = unit('changed.py', 'changed'), unit('external.py', 'caller')
    async def base(**kw):
        return dict(snapshot=snapshot('base'), results=[changed] if kw['pattern'] == 'file_summary'
                    else [relation('r', external, changed)])
    client = SimpleNamespace(query_code_graph=AsyncMock(side_effect=base),
        query_review_graph=AsyncMock(return_value=dict(snapshot={'sourceRevision': 'different'}, results=[])))
    graph = ChangeGraph(request(), [file()], client)
    await graph.collect(['changed.py'])
    result = graph.render()
    assert result['coverage']['status'] == 'degraded'
    assert result['edges'][0]['change'] == 'unknown'
    assert all(n['snapshot'] == 'base' for n in result['nodes'])
    assert 'revision/generation' in str(result['coverage']['diagnostics'])


@pytest.mark.asyncio
async def test_changed_files_keep_import_dependencies_outside_changed_line_starts():
    container = {**unit('index.tsx', 'index.tsx', 1, 100), 'kind': 'file'}
    sibling = unit('index.tsx', 'unmodifiedSibling', 80, 95)
    old, new = unit('old.tsx', 'old'), unit('new.tsx', 'isBaseVisualize')
    retained = {**relation('import-old', container, old, 1), 'kind': 'IMPORTS', 'relation': 'imports'}
    added = {**relation('import-new', container, new, 2), 'kind': 'typescript-import-binding', 'relation': 'resolves-to'}
    unrelated = relation('sibling-call', sibling, old, 90)
    async def query(which, **kw):
        return dict(snapshot=snapshot(which), results=([container, sibling] if kw['pattern']=='file_summary'
            else [retained, unrelated, *([added] if which=='proposed' else [])]))
    async def base(**kw): return await query('base', **kw)
    async def proposed(**kw): return await query('proposed', **kw)
    graph = ChangeGraph(request(), [dict(path='index.tsx', changeType='modified',
        changedRanges=[dict(base=[3,0], head=[3,1])])],
        SimpleNamespace(query_code_graph=base, query_review_graph=proposed))
    await graph.collect(['index.tsx'])
    result = graph.render()
    assert {e['change'] for e in result['edges']} == {'retained', 'added'}
    assert {o['evidenceId'] for e in result['edges'] for o in e['observations']} == {'import-old', 'import-new'}
    visible = graph_prompt_context(result, ['index.tsx'])
    assert ['file', 'new.tsx'] in visible['entities'] and ['file', 'old.tsx'] in visible['entities']


@pytest.mark.asyncio
async def test_unresolved_symbol_is_not_misrepresented_as_a_repository_file():
    changed = unit('changed.py', 'changed')
    edge = relation('r', changed, unit('external.py', 'package.api'))
    edge['targetUnit'] = None
    async def query(which, **kw):
        return dict(snapshot=snapshot(which), results=[changed] if kw['pattern'] == 'file_summary' else [edge])
    async def base(**kw): return await query('base', **kw)
    async def proposed(**kw): return await query('proposed', **kw)
    graph = ChangeGraph(request(), [file()], SimpleNamespace(query_code_graph=base, query_review_graph=proposed))
    await graph.collect(['changed.py'])
    result = graph.render()
    unresolved = [n for n in result['nodes'] if n['resolution'] == 'unresolved']
    assert unresolved and all('path' not in n for n in unresolved)
    assert result['coverage']['unresolvedNodes'] == 2


@pytest.mark.asyncio
async def test_plugin_resolved_file_does_not_claim_verified_member_declaration():
    changed = unit('changed.py', 'changed')
    edge = relation('import-call', changed, unit('dependency.py', 'policy::allows'))
    edge.update(targetUnit=None, kind='python-call-resolution', relation='calls-resolved-target')
    async def query(which, **kw):
        return dict(snapshot=snapshot(which), results=[changed] if kw['pattern'] == 'file_summary' else [edge])
    async def base(**kw): return await query('base', **kw)
    async def proposed(**kw): return await query('proposed', **kw)
    graph = ChangeGraph(request(), [file()], SimpleNamespace(query_code_graph=base, query_review_graph=proposed))
    await graph.collect(['changed.py'])
    result = graph.render()
    targets = [n for n in result['nodes'] if n['resolution'] == 'file_only']
    assert len(targets) == 2 and all(n['path'] == 'dependency.py' and 'unitId' not in n for n in targets)
    visible = graph_prompt_context(result, ['changed.py'])
    assert ['file', 'dependency.py'] in visible['entities']
    assert any(n[3] == 2 for n in visible['symbolDetail']['nodes'])


@pytest.mark.asyncio
async def test_incoming_unchanged_consumer_is_retained_when_only_import_file_is_resolved():
    changed, caller = unit('changed.py', 'changed'), unit('consumer.py', 'caller')
    edge = relation('import-consumer', caller, changed, line=3)
    edge.update(targetUnit=None, kind='python-call-resolution', relation='calls-resolved-target')
    async def query(which, **kw):
        return dict(snapshot=snapshot(which), results=[changed] if kw['pattern'] == 'file_summary' else [edge])
    async def base(**kw): return await query('base', **kw)
    async def proposed(**kw): return await query('proposed', **kw)
    graph = ChangeGraph(request(), [file()], SimpleNamespace(query_code_graph=base, query_review_graph=proposed))
    await graph.collect(['changed.py'])
    result = graph.render()
    assert len(result['edges']) == 1 and result['edges'][0]['change'] == 'retained'
    assert {n['path'] for n in result['nodes']} == {'changed.py', 'consumer.py'}
    assert any(n['resolution'] == 'file_only' for n in result['nodes'])


@pytest.mark.asyncio
@pytest.mark.parametrize('include_unknown', [False, True])
async def test_inferred_links_accept_indexed_evidence_paths_without_resolved_source_symbols(monkeypatch, include_unknown):
    monkeypatch.setattr(scan, 'supports_structured_output', lambda _: False)
    changed, caller = unit('changed.py', 'changed', 1, 2), unit('consumer.py', 'caller')
    edge = relation('consumer-import', caller, changed)
    edge.update(sourceUnit=None, targetUnit=None, kind='python-import-binding', relation='resolves-import')
    async def query(which, **kw):
        return dict(snapshot=snapshot(which), results=[changed] if kw['pattern']=='file_summary' else [edge])
    async def base(**kw): return await query('base', **kw)
    async def proposed(**kw): return await query('proposed', **kw)
    async def invoke(prompt, **kwargs):
        links = [dict(source='consumer.py', target='changed.py', relationship='imports', evidence='inferred from indexed import')]
        if include_unknown:
            links.append(dict(source='missing.py', target='changed.py', relationship='imports', evidence='unverified inference'))
        return SimpleNamespace(content=json.dumps(dict(summary='Updates changed behavior',
            files=[dict(path='changed.py', summary='Rejects empty input', changes=['Rejects empty input'])],
            changeAreas=[dict(title='Validation', paths=['changed.py'], changes=['Rejects empty input'])],
            relationships=links)))
    raw = 'diff --git a/changed.py b/changed.py\n--- a/changed.py\n+++ b/changed.py\n@@ -1 +1 @@\n-old\n+new\n'
    result = await scan.scan_pr_changes(SimpleNamespace(ainvoke=invoke),
        request(rawDiff=raw, changedFiles=['changed.py']),
        rag_client=SimpleNamespace(query_code_graph=base, query_review_graph=proposed))
    assert not any(n.get('path') == 'consumer.py' for n in result['graph']['nodes'])
    assert any(o['origin']['path'] == 'consumer.py' for e in result['graph']['edges'] for o in e['observations'])
    assert result['semanticStatus'] == ('degraded' if include_unknown else 'complete')
    assert len(result['relationships']) == 1
    assert result['relationships'][0]['source'] == 'consumer.py'
    assert result['relationships'][0]['provenance'] == 'model_inferred'


@pytest.mark.asyncio
async def test_dense_symbol_graph_does_not_skip_semantic_scan(monkeypatch):
    monkeypatch.setattr(scan, 'supports_structured_output', lambda _: False)
    changed = unit('changed.py', 'changed', 1, 2)
    edges = [relation(str(i), changed, unit('dependency.py', f'helper{i}'), 1) for i in range(24)]
    for edge in edges:
        edge['attributes'] = {'signature': 'argument, ' * 100}
    async def query(which, **kw):
        return dict(snapshot=snapshot(which), results=[changed] if kw['pattern']=='file_summary' else edges)
    async def base(**kw): return await query('base', **kw)
    async def proposed(**kw): return await query('proposed', **kw)
    prompts = []
    async def invoke(prompt, **kwargs):
        prompts.append(prompt)
        return SimpleNamespace(content=json.dumps(dict(summary='Updates validation',
            files=[dict(path='changed.py', summary='Rejects empty input')],
            changeAreas=[dict(title='Validation', paths=['changed.py'], changes=['Rejects empty input'])])))
    raw = 'diff --git a/changed.py b/changed.py\n--- a/changed.py\n+++ b/changed.py\n@@ -1 +1 @@\n-old\n+new\n'
    result = await scan.scan_pr_changes(SimpleNamespace(ainvoke=invoke),
        request(rawDiff=raw, changedFiles=['changed.py'], maxAllowedTokens=24000),
        rag_client=SimpleNamespace(query_code_graph=base, query_review_graph=proposed))
    assert result['semanticStatus'] == 'complete' and len(prompts) == 1
    assert result['files'][0]['scanStatus'] == 'scanned'
    visible = json.JSONDecoder().raw_decode(prompts[0].split('Host-collected cumulative dependency graph:\n')[1])[0]
    focused = graph_prompt_context(result['graph'], ['changed.py'])
    assert len(json.dumps(focused).encode()) // 2 > 4000
    assert visible['edges'] == focused['edges'] and visible['entities'] == focused['entities']
    assert sum(sum(row[3]) for row in visible['edges']) == len(edges)
    assert len(result['graph']['edges']) == len(edges)
    assert len(focused['symbolDetail']['edges']) == len(edges)
    assert all(o['attributes']['signature'] for e in result['graph']['edges'] for o in e['observations'])


@pytest.mark.asyncio
async def test_two_hundred_files_keep_first_middle_last_behavior_when_model_omits_prior_items(monkeypatch):
    monkeypatch.setattr(scan, 'supports_structured_output', lambda _: False)
    paths = [f'area{i//20}/f{i}.py' for i in range(200)]
    raw = ''.join(f'diff --git a/{p} b/{p}\n--- a/{p}\n+++ b/{p}\n@@ -1 +1 @@\n-old\n+new\n' for p in paths)
    prompts = []
    async def invoke(prompt, **kwargs):
        prompts.append(prompt)
        batch = json.loads(prompt.rsplit(':\n', 1)[1])
        return SimpleNamespace(content=json.dumps(dict(summary='Most recent area',
            files=[dict(path=r['path'], summary='Changes behavior for '+r['path'], changes=['Old input now rejected']) for r in batch],
            changeAreas=[dict(title=r['path'].split('/')[0], paths=[r['path']],
                changes=['Rejects invalid input in '+r['path']]) for r in batch], relationships=[])))
    result = await scan.scan_pr_changes(SimpleNamespace(ainvoke=invoke), request(rawDiff=raw, changedFiles=paths))
    assert len(prompts) == 14
    assert len(result['files']) == 200
    assert sum(len(a['changes']) for a in result['changeAreas']) == 200
    for path in (paths[0], paths[100], paths[-1]):
        assert 'Rejects invalid input in '+path in result['summary']
    assert all(result['scanReceipts'][i]['retainedChangeDetails'] < result['scanReceipts'][i+1]['retainedChangeDetails']
               for i in range(13))
    assert result['semanticStatus'] == 'complete'
    assert result['graph']['coverage']['status'] == 'degraded'


@pytest.mark.asyncio
async def test_empty_feature_area_retains_concrete_file_changes_in_rendered_summary(monkeypatch):
    monkeypatch.setattr(scan, 'supports_structured_output', lambda _: False)
    raw = 'diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n'
    async def invoke(prompt, **kwargs):
        return SimpleNamespace(content=json.dumps(dict(summary='Updates validation',
            files=[dict(path='a.py', summary='Changes validation', changes=['Rejects empty input', 'Reports the field name'])],
            changeAreas=[dict(title='Validation', paths=['a.py'], changes=[])], relationships=[])))
    result = await scan.scan_pr_changes(SimpleNamespace(ainvoke=invoke), request(rawDiff=raw, changedFiles=['a.py']))
    assert 'Rejects empty input' in result['summary'] and 'Reports the field name' in result['summary']
    assert any('no feature description' in message for message in result['diagnostics'])


def test_changed_ranges_exclude_unmodified_context_and_keep_insertion_anchor():
    from utils.diff_processor import DiffProcessor
    text = ('diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n'
            '@@ -10,3 +10,4 @@\n context\n-old\n+new\n+another\n context\n')
    files, _ = scan._scan_records(DiffProcessor().process(text), [], 4000)
    assert files[0]['changedRanges'] == [{'base': [11, 1], 'head': [11, 2]}]


@pytest.mark.asyncio
async def test_changing_one_method_does_not_pull_every_sibling_from_container():
    root = unit('changed.py', 'Module', 1, 200)
    root['kind'] = 'file'
    method = unit('changed.py', 'changed', 10, 20)
    sibling = unit('changed.py', 'unrelated', 80, 100)
    external = unit('elsewhere.py', 'external')
    async def query(which, **kw):
        return dict(snapshot=snapshot(which), results=[root, method, sibling] if kw['pattern'] == 'file_summary'
            else [relation('needed', method, external, 12), relation('irrelevant', root, sibling, 80)])
    async def base(**kw): return await query('base', **kw)
    async def proposed(**kw): return await query('proposed', **kw)
    graph = ChangeGraph(request(), [file()], SimpleNamespace(query_code_graph=base, query_review_graph=proposed))
    await graph.collect(['changed.py'])
    result = graph.render()
    assert len(result['edges']) == 1
    assert {o['evidenceId'] for o in result['edges'][0]['observations']} == {'needed'}
