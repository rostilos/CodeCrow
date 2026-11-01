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
@pytest.mark.parametrize('declaration', ['declares-file-alias', 'declares-package'])
@pytest.mark.parametrize('case', ['unique', 'paged', 'ambiguous', 'missing-file', 'suffix-only',
    'case-mismatch', 'wrong-relation', 'no-origin', 'no-related-path', 'stale', 'truncated', 'stalled', 'unavailable',
    'escape', 'absolute'])
async def test_declared_file_alias_requires_complete_unique_bound_evidence(case, declaration):
    source, alias = 'src/config.ts', '@pkg/admin'
    target = 'packages/admin/package.json' if declaration == 'declares-package' else 'src/routes/admin.ts'
    calls = []
    async def query(**kw):
        calls.append(kw)
        assert kw['workspace'] == 'tenant' and kw['project'] == 'project'
        assert kw['source_revision'] == 'head' and kw['review_generation_manifest_sha256'] == 'review-manifest'
        result = dict(snapshot=snapshot('proposed'), results=[])
        if kw['pattern'] == 'file_search':
            assert kw['target'] == target
            result['results'] = ([] if case == 'missing-file' else
                [unit(('other/' if case == 'suffix-only' else '') + target, 'admin')])
            return result
        assert kw['pattern'] == 'relations_of' and kw['target'] == alias and kw['focus_paths'] == [source]
        fact = dict(evidenceId='alias-' + str(kw['cursor']), kind='package-export', relation=declaration,
            source=alias, target=target, origin=dict(path=target if declaration == 'declares-package' else 'package.json', line=1), relatedPaths=[target])
        if case == 'case-mismatch': fact['source'] = alias.upper()
        if case == 'wrong-relation': fact['relation'] = 'imports'
        if case == 'no-origin': fact.pop('origin')
        if case == 'no-related-path': fact['relatedPaths'] = []
        if case == 'ambiguous' and kw['cursor']:
            fact.update(target='other.ts', relatedPaths=['other.ts'])
            if declaration == 'declares-package': fact['origin']['path'] = 'other.ts'
        if case in {'escape', 'absolute'}:
            unsafe = '../outside.json' if case == 'escape' else '/outside.json'
            fact.update(target=unsafe, relatedPaths=[unsafe], origin=dict(path=unsafe, line=1))
        result['results'] = [fact]
        if case in {'paged', 'ambiguous'} and kw['cursor'] == 0: result['nextCursor'] = 1
        if case == 'stale': result['snapshot']['generationManifestSha256'] = 'wrong'
        if case == 'truncated': result['truncated'] = True
        if case == 'stalled': result['nextCursor'] = kw['cursor']
        if case == 'unavailable': result['status'] = 'unavailable'
        return result
    graph = ChangeGraph(request(), [file(source)], SimpleNamespace(query_review_graph=query))
    resolved = await graph.resolve_file_alias(source, alias)
    if case in {'unique', 'paged'}:
        expected = ['alias-0', 'alias-1'] if case == 'paged' else ['alias-0']
        resolution = dict(path=target, snapshot='proposed', evidenceIds=expected)
        if declaration == 'declares-package': resolution['referenceKind'] = 'package_manifest'
        assert resolved == resolution
        assert set(graph.relations['proposed']) == set(expected)
        assert not graph.diagnostics
    else:
        assert resolved is None and not graph.relations['proposed']
        if case in {'stale', 'truncated', 'stalled', 'unavailable'}: assert graph.diagnostics


@pytest.mark.asyncio
@pytest.mark.parametrize('deleted', [False, True])
@pytest.mark.parametrize('case', ['unique', 'duplicate', 'foreign-origin'])
async def test_package_identity_precedes_export_alias_without_hiding_ambiguous_packages(deleted, case):
    source, alias, manifest = 'config.ts', '@pkg/admin', 'packages/admin/package.json'
    which = 'base' if deleted else 'proposed'
    calls = []
    async def query(label, **kw):
        assert label == which
        calls.append(kw)
        if kw['pattern'] == 'file_search':
            return dict(snapshot=snapshot(label), results=[unit(kw['target'], kw['target'])])
        if not kw['cursor']:
            return dict(snapshot=snapshot(label), nextCursor=1, results=[dict(
                evidenceId='export', relation='declares-file-alias', source=alias, target='src/admin.ts',
                relatedPaths=['src/admin.ts'], origin=dict(path=manifest, line=1))])
        fact = dict(evidenceId='package', relation='declares-package', source=alias, target=manifest,
            relatedPaths=[manifest], origin=dict(path='wrong.json' if case == 'foreign-origin' else manifest, line=1))
        facts = [fact]
        if case == 'duplicate':
            facts.append({**fact, 'evidenceId': 'other-package', 'target': 'other/package.json',
                          'relatedPaths': ['other/package.json'], 'origin': dict(path='other/package.json', line=1)})
        return dict(snapshot=snapshot(label), results=facts)
    async def base(**kw): return await query('base', **kw)
    async def proposed(**kw): return await query('proposed', **kw)
    graph = ChangeGraph(request(), [{**file(source), 'changeType': 'deleted' if deleted else 'modified'}],
                        SimpleNamespace(query_code_graph=base, query_review_graph=proposed))
    resolved = await graph.resolve_file_alias(source, alias)
    assert [call['cursor'] for call in calls if call['pattern'] == 'relations_of'] == [0, 1]
    if case == 'duplicate':
        assert resolved is None and not graph.relations[which]
    elif case == 'unique':
        assert resolved == dict(path=manifest, snapshot=which, evidenceIds=['package'], referenceKind='package_manifest')
    else:
        assert resolved == dict(path='src/admin.ts', snapshot=which, evidenceIds=['export'])


@pytest.mark.asyncio
@pytest.mark.parametrize('deleted', [False, True])
@pytest.mark.parametrize('alias', ['.', './api/route-utils'])
@pytest.mark.parametrize('case', ['unique', 'ambiguous', 'wrong-owner', 'wrong-key', 'missing-file', 'pattern'])
async def test_local_export_key_requires_owning_source_and_one_existing_target(deleted, alias, case):
    source, target = 'packages/core/package.json', 'packages/core/src/api/route-utils.ts'
    which = 'base' if deleted else 'proposed'
    calls = []

    async def query(**kw):
        calls.append(kw)
        assert kw['workspace'] == 'tenant' and kw['project'] == 'project'
        assert kw['repository_generation_manifest_sha256' if deleted else 'review_generation_manifest_sha256'] == ('base-manifest' if deleted else 'review-manifest')
        if kw['pattern'] == 'file_search':
            assert kw['target'] == target
            return dict(snapshot=snapshot(which), results=[] if case == 'missing-file' else [unit(target, 'route-utils')])
        assert kw['pattern'] == 'relations_of' and kw['target'] == source
        current = 'packages/core/src/other.ts' if kw['cursor'] else target
        fact = dict(evidenceId=f'export-{kw["cursor"]}', relation='declares-file-alias',
            source='emdash' if alias == '.' else 'emdash/api/route-utils', target=current,
            origin=dict(path='other/package.json' if case == 'wrong-owner' else source),
            relatedPaths=[current], attributes=dict(exportKey='./other' if case == 'wrong-key' else './api/*' if case == 'pattern' else alias))
        result = dict(snapshot=snapshot(which), results=[fact])
        if case == 'ambiguous' and not kw['cursor']: result['nextCursor'] = 1
        return result

    graph = ChangeGraph(request(), [dict(path=source, changeType='deleted' if deleted else 'modified')],
        SimpleNamespace(query_code_graph=query, query_review_graph=query))
    resolved = await graph.resolve_file_alias(source, alias)
    assert resolved == (dict(path=target, snapshot=which, evidenceIds=['export-0']) if case == 'unique' else None)
    assert all(call['target'] != alias for call in calls)
    if case == 'ambiguous': assert len(calls) == 2


@pytest.mark.parametrize('deleted', [False, True])
def test_export_pattern_key_is_source_bound_and_never_a_concrete_file(deleted):
    source, key = 'packages/core/package.json', './routes/*'
    graph = ChangeGraph(request(), [dict(path=source, changeType='deleted' if deleted else 'modified')], None)
    which = 'base' if deleted else 'proposed'
    fact = dict(evidenceId='pattern', relation='declares-file-pattern', source=source,
        target='packages/core/src/routes/*', origin=dict(path=source), attributes=dict(exportKey=key))
    graph.relations[which]['pattern'] = fact
    assert graph.import_reference(source, key) == dict(kind='file_pattern', resolution='unresolved', snapshot=which, evidenceIds=['pattern'])
    assert graph.import_reference('other/package.json', key) is None
    assert graph.import_reference(source, './routes/login.ts') is None
@pytest.mark.asyncio
async def test_deleted_source_alias_uses_base_snapshot():
    async def base(**kw):
        assert kw['repository_revision'] == 'base' and kw['repository_generation_manifest_sha256'] == 'base-manifest'
        assert kw['workspace'] == 'tenant' and kw['project'] == 'project'
        values = ([dict(evidenceId='alias', relation='declares-file-alias', source='pkg/admin', target='src/admin.ts',
            relatedPaths=['src/admin.ts'], origin=dict(path='package.json'))] if kw['pattern'] == 'relations_of'
            else [unit('src/admin.ts', 'admin')])
        return dict(snapshot=snapshot('base'), results=values)
    proposed = AsyncMock(side_effect=AssertionError('Deleted source must use base'))
    graph = ChangeGraph(request(), [dict(path='old.ts', changeType='deleted')],
        SimpleNamespace(query_code_graph=base, query_review_graph=proposed))
    assert await graph.resolve_file_alias('old.ts', 'pkg/admin') == dict(path='src/admin.ts', snapshot='base', evidenceIds=['alias'])
    proposed.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('declaration', ['declares-file-alias', 'declares-package'])
async def test_scan_normalizes_declared_alias_but_preserves_inferred_relationship(monkeypatch, declaration):
    source, alias, target = 'src/config.ts', '@pkg/admin', 'src/admin.ts'
    if declaration == 'declares-package': target = 'packages/admin/package.json'
    async def query(which, **kw):
        values = []
        if kw['pattern'] in {'file_summary', 'file_search'} and kw['target'] in {source, target}:
            values = [unit(kw['target'], kw['target'])]
        if kw['pattern'] == 'relations_of' and kw['target'] == alias:
            values = [dict(evidenceId='decl', kind='package-export', relation=declaration,
                source=alias, target=target, relatedPaths=[target],
                origin=dict(path=target if declaration == 'declares-package' else 'package.json', line=1))]
        return dict(snapshot=snapshot(which), results=values)
    async def base(**kw): return await query('base', **kw)
    async def proposed(**kw): return await query('proposed', **kw)
    monkeypatch.setattr(scan, 'supports_structured_output', lambda _: False)
    response = dict(summary='Configures the admin entry.', files={source: dict(summary='Adds an admin entry.')},
        relationships=[dict(source=source, target=alias, relationship='DECLARES_ADMIN_ENTRY', evidence='Entry declaration.')])
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content=json.dumps(response))))
    raw = f'diff --git a/{source} b/{source}\n--- a/{source}\n+++ b/{source}\n@@ -1 +1 @@\n-old\n+adminEntry: "{alias}"\n'
    result = await scan.scan_pr_changes(llm, request(rawDiff=raw, changedFiles=[source]),
        rag_client=SimpleNamespace(query_code_graph=base, query_review_graph=proposed))
    assert result['status'] == 'complete', result['diagnostics']
    edge, = result['relationships']
    assert edge['target'] == target and edge['provenance'] == 'model_inferred'
    assert edge['evidence'].startswith('Entry declaration.') and alias in edge['evidence']
    assert 'runtime resolution unverified' in edge['evidence'] and 'decl' in edge['evidence']
    if declaration == 'declares-package': assert 'Package identity' in edge['evidence']
    llm.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('binding', ['exact', 'qualified', 'dependency', 'dependency-near', 'configured', 'configured-near', 'configured-foreign', 'configured-stale', 'resolved', 'missing', 'foreign', 'near', 'relative', 'relative-parent', 'relative-foreign', 'relative-missing', 'relative-stale', 'relative-near', 'absolute', 'stale', 'forged', 'pattern', 'pattern-relative', 'pattern-foreign', 'pattern-stale', 'pattern-guessed', 'pattern-qualified'])
async def test_scan_retains_proven_module_imports_without_claiming_a_file(monkeypatch, binding):
    source, target = 'src/runtime.ts', 'virtual:app/config'
    if binding.startswith('relative'):
        target = '../../utils/site-identity' if binding == 'relative-parent' else './missing.ts'
    if binding == 'absolute':
        target = '/outside/config.ts'
    pattern = './locales/${locale}.mjs' if binding == 'pattern-relative' else '@package/locales/${locale}/messages.mjs'
    if binding.startswith('pattern'):
        target = pattern.replace('${locale}', 'en') if binding == 'pattern-guessed' else pattern
    queries = []
    async def query(which, **kw):
        queries.append(kw)
        assert kw['workspace'] == 'tenant' and kw['project'] == 'project'
        edge = dict(evidenceId='import-proof', kind='language-import', relation='imports',
            source='src/runtime', target=target if binding == 'exact' else target + '::default',
            origin=dict(path=source, line=1, extractor='plugin'), sourceUnit=None, targetUnit=None,
            relatedPaths=[source], attributes={})
        if binding.startswith('pattern'):
            edge.update(relation='imports-pattern', target=pattern + ('::default' if binding == 'pattern-qualified' else ''))
        if binding in {'foreign', 'pattern-foreign', 'configured-foreign', 'relative-foreign'}: edge['origin']['path'] = 'src/other.ts'
        if binding in {'near', 'relative-near'}: edge['target'] = target + 'uration::default'
        if binding.startswith('dependency'):
            edge.update(relation='declares-dependency', target=target if binding == 'dependency' else target + '::default')
        if binding.startswith('configured'):
            edge.update(relation='configures-module', target=target + ('::default' if binding == 'configured-near' else ''))
        if binding == 'resolved':
            edge.update(relation='resolves-import', target='src/config',
                        relatedPaths=[source, 'src/config.ts'], attributes=dict(specifier=target))
        values = [unit(source, source, 1, 2)] if kw['pattern'] == 'file_summary' else [edge]
        if kw['target'] != source or binding in {'missing', 'forged', 'relative-missing'} and kw['pattern'] != 'file_summary':
            values = []
        observed = snapshot(which)
        if binding in {'stale', 'pattern-stale', 'configured-stale', 'relative-stale'}: observed['generationManifestSha256'] = 'foreign-generation'
        return dict(snapshot=observed, results=values)
    async def base(**kw): return await query('base', **kw)
    async def proposed(**kw): return await query('proposed', **kw)
    monkeypatch.setattr(scan, 'supports_structured_output', lambda _: False)
    output = dict(summary='Reads runtime configuration.',
        files={source: dict(summary='Imports configuration and reads its locale settings.')},
        relationships=[dict(source=source, target=target, relationship='IMPORTS', evidence='Added import.',
                            targetReference=dict(kind='module', evidenceIds=['forged']))])
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content=json.dumps(output))))
    raw = f'diff --git a/{source} b/{source}\n--- a/{source}\n+++ b/{source}\n@@ -1 +1,2 @@\n-old\n+import config from "{target}";\n+read(config.locale);\n'
    result = await scan.scan_pr_changes(llm, request(rawDiff=raw, changedFiles=[source]),
        rag_client=SimpleNamespace(query_code_graph=base, query_review_graph=proposed))
    if binding in {'exact', 'qualified', 'dependency', 'configured', 'resolved', 'pattern', 'pattern-relative', 'relative', 'relative-parent'}:
        assert result['status'] == 'complete', result['diagnostics']
        edge, = result['relationships']
        assert edge['provenance'] == 'model_inferred'
        assert not any(q['target'] == target for q in queries)
        if binding == 'resolved':
            assert edge['target'] == 'src/config.ts' and 'targetReference' not in edge
        else:
            assert edge['target'] == target
            assert edge['targetReference'] == dict(kind='module_pattern' if binding.startswith('pattern') else 'module', resolution='unresolved',
                snapshot='proposed', evidenceIds=['import-proof'])
            assert not any(n.get('path') == target for n in result['graph']['nodes'])
            assert target in scan.change_summary_context(result)
            assert 'targetReference' in scan.change_summary_context(result)
    else:
        assert result['status'] == ('degraded' if binding in {'stale', 'pattern-stale', 'configured-stale', 'relative-stale'} else 'complete')
        assert not result['relationships'] and result['omittedRelationships']
    assert result['files'][0]['scanStatus'] == 'scanned'
    llm.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['unique', 'duplicate', 'foreign', 'stale', 'missing',
    'ambiguous-specifier', 'ambiguous-target', 'extension', 'named-directory', 'absolute', 'bare', 'exact-reference'])
async def test_relative_import_prefix_repair_requires_one_bound_source_import(monkeypatch, case):
    source, target, requested = 'pkg/auth/mode.ts', 'pkg/auth/types.ts', '../types.js'
    requested = {'extension':'../types.ts', 'named-directory':'../sub/types.js',
                 'absolute':'/types.js', 'bare':'types.js'}.get(case, requested)
    queries = []
    async def query(which, **kw):
        queries.append(kw)
        binding = dict(evidenceId='binding', relation='resolves-import', kind='import-binding',
            source=source, target=target, origin=dict(path='other.ts' if case == 'foreign' else source, line=1),
            sourceUnit=unit(source,source,1,2), targetUnit=unit(target,target,1,2),
            relatedPaths=[source,target], attributes=dict(specifier='./types.js'))
        facts = [] if case == 'missing' else [binding]
        if case in {'duplicate','ambiguous-specifier','ambiguous-target'}:
            other = {**binding, 'evidenceId':'second'}
            if case == 'ambiguous-specifier': other['attributes'] = dict(specifier='../../types.js')
            if case == 'ambiguous-target': other.update(target='other/types.ts',
                targetUnit=unit('other/types.ts','other/types.ts',1,2),relatedPaths=[source,'other/types.ts'])
            facts.append(other)
        if case == 'exact-reference':
            facts.append(dict(evidenceId='exact',kind='import',relation='imports',source=source,
                target=requested+'::Type',origin=dict(path=source,line=1),relatedPaths=[source]))
        observed = snapshot(which)
        if case == 'stale': observed['generationManifestSha256'] = 'stale'
        values = ([unit(kw['target'],kw['target'],1,2)] if kw['pattern']=='file_summary' else
                  facts if kw['target']==source else []) if kw['target'] in {source,target} else []
        return dict(snapshot=observed,results=values)
    async def base(**kw): return await query('base',**kw)
    async def proposed(**kw): return await query('proposed',**kw)
    monkeypatch.setattr(scan,'supports_structured_output',lambda _:False)
    response = dict(summary='Changes imported types.',files={source:dict(summary='Uses the Type import.')},
        relationships=[dict(source=source,target=requested,relationship='imports',evidence=f'Imports Type from `{requested}`.')])
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content=json.dumps(response))))
    raw = f'diff --git a/{source} b/{source}\n--- a/{source}\n+++ b/{source}\n@@ -1 +1 @@\n-old\n+import type {{ Type }} from "./types.js";\n'
    result = await scan.scan_pr_changes(llm,request(rawDiff=raw,changedFiles=[source]),
        rag_client=SimpleNamespace(query_code_graph=base,query_review_graph=proposed))
    if case in {'unique','duplicate','exact-reference'}:
        assert result['status'] == result['graph']['coverage']['status'] == 'complete', result['diagnostics']
        edge, = result['relationships']
        assert edge['provenance']=='model_inferred'
        assert not any(q['target']==requested for q in queries)
        if case == 'exact-reference':
            assert edge['target']==requested and edge['targetReference']['evidenceIds']==['exact']
            assert 'corrected' not in edge['evidence']
        else:
            assert edge['target']==target and 'targetReference' not in edge
            assert f'Relative import {requested} corrected to ./types.js' in edge['evidence']
            assert 'only the relative prefix changed' in edge['evidence']
    else:
        assert result['status'] == ('degraded' if case == 'stale' else 'complete')
        assert not result['relationships'] and result['omittedRelationships']
    llm.ainvoke.assert_awaited_once()


def test_relative_import_prefix_repair_uses_only_deleted_source_base_binding():
    source = 'pkg/auth/mode.ts'
    graph = ChangeGraph(request(),[dict(path=source,changeType='deleted')],None)
    fact = dict(evidenceId='base',relation='resolves-import',origin=dict(path=source),
        relatedPaths=[source,'pkg/auth/types.ts'],attributes=dict(specifier='./types.js'))
    graph.relations['base']['base'] = fact
    graph.relations['proposed']['wrong'] = {**fact,'evidenceId':'wrong', 'relatedPaths':[source,'wrong.ts']}
    assert graph.resolve_relative_import_prefix(source,'../types.js') == dict(
        path='pkg/auth/types.ts',specifier='./types.js',snapshot='base',evidenceIds=['base'])
    graph.relations['base'].clear()
    assert graph.resolve_relative_import_prefix(source,'../types.js') is None


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['unique', 'nested-quotes', 'duplicate', 'missing', 'foreign', 'base-only',
    'deleted', 'stale', 'unquoted', 'different-name', 'different-directory', 'ambiguous', 'ambiguous-binding',
    'known-file', 'exact-reference', 'generated-output', 'relative', 'equivalent-relative',
    'misleading-quote', 'absolute', 'escape', 'backslash', 'no-extension', 'wrong-kind'])
async def test_import_extension_correction_requires_unique_source_bound_target(monkeypatch, case):
    source, target, requested = 'src/ContentEditor.tsx', 'src/SeoImageField.tsx', 'src/SeoImageField.js'
    if case == 'different-name': requested = 'src/OtherField.js'
    if case == 'different-directory': requested = 'other/SeoImageField.js'
    requested = {'relative': './SeoImageField.js', 'equivalent-relative': '../src/SeoImageField.js',
        'absolute': '/src/SeoImageField.js', 'escape': '../../SeoImageField.js',
        'backslash': 'src\\SeoImageField.js', 'no-extension': 'src/SeoImageField'}.get(case, requested)
    quoted = target if case == 'unquoted' else f'`{target}`'
    if case == 'misleading-quote': quoted = '`other/Unrelated.tsx`'
    if case == 'nested-quotes': quoted = '`import { SeoImageField } from "./SeoImageField.js"`; graph target ' + quoted
    if case == 'ambiguous': quoted += ' and `src/SeoImageField.ts`'
    queries = []
    async def query(which, **kw):
        queries.append(kw)
        binding = dict(evidenceId='binding', relation='resolves-import', kind='module-resolution',
            source=source, target=target, origin=dict(path='foreign.tsx' if case == 'foreign' else source, line=1),
            sourceUnit=unit(source,source,1,2), targetUnit=unit(target,target,1,2),
            relatedPaths=[source,target], attributes=dict(specifier='./SeoImageField'))
        if case == 'wrong-kind': binding['relation'] = 'not-an-import-binding'
        facts = [] if case == 'missing' or case in {'base-only', 'deleted'} and which == 'proposed' else [binding]
        if case in {'duplicate','ambiguous','ambiguous-binding'}:
            other = {**binding, 'evidenceId':'second'}
            if case in {'ambiguous','ambiguous-binding'}: other.update(target='src/SeoImageField.ts',
                targetUnit=unit('src/SeoImageField.ts','SeoImageField',1,2),relatedPaths=[source,'src/SeoImageField.ts'])
            facts.append(other)
        if case == 'exact-reference':
            facts.append(dict(evidenceId='literal',kind='import',relation='imports',source=source,
                target=requested+'::SeoImageField',origin=dict(path=source,line=1),relatedPaths=[source]))
        known = {source,target,'src/SeoImageField.ts'} | ({requested} if case == 'known-file' else set())
        values = ([unit(kw['target'],kw['target'],1,2)] if kw['pattern']=='file_summary' else
                  facts if kw['target']==source else []) if kw['target'] in known else []
        observed = snapshot(which)
        if case == 'stale': observed['generationManifestSha256'] = 'stale'
        return dict(snapshot=observed,results=values)
    async def base(**kw): return await query('base',**kw)
    async def proposed(**kw): return await query('proposed',**kw)
    monkeypatch.setattr(scan,'supports_structured_output',lambda _:False)
    paths = [source, requested] if case == 'known-file' else [source]
    response = dict(summary='Changes component integration.',files={p:dict(summary='Updates component integration.') for p in paths},
        relationships=[dict(source=source,target=requested,
            relationship='GENERATES' if case == 'generated-output' else 'imports',
            evidence=f'The graph identifies {quoted} as the file endpoint.')])
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content=json.dumps(response))))
    from tests.test_pr_change_summary import diff
    raw = ''.join(diff(p, 'deleted' if case == 'deleted' else 'modified') for p in paths)
    result = await scan.scan_pr_changes(llm,request(rawDiff=raw,changedFiles=paths),
        rag_client=SimpleNamespace(query_code_graph=base,query_review_graph=proposed))
    if case in {'unique','nested-quotes','duplicate','deleted','known-file','exact-reference',
                'unquoted','relative','equivalent-relative','misleading-quote','no-extension'}:
        assert result['status'] == result['graph']['coverage']['status'] == 'complete', result['diagnostics']
        edge, = result['relationships']
        assert edge['provenance']=='model_inferred'
        if case in {'known-file','exact-reference'}:
            assert edge['target']==requested and 'corrected to bound' not in edge['evidence']
        elif case == 'no-extension':
            assert edge['target'] == target and f'Import path {requested} resolved to {target}' in edge['evidence']
        else:
            assert edge['target']==target and 'targetReference' not in edge
            assert f'Import target {requested} corrected to bound {target}' in edge['evidence']
            assert 'only the filename extension changed' in edge['evidence']
            assert ('by base structural evidence' if case == 'deleted' else 'by proposed structural evidence') in edge['evidence']
            assert not any(q['target']==requested for q in queries)
    else:
        assert result['status'] == ('degraded' if case == 'stale' else 'complete')
        assert not result['relationships'] and result['omittedRelationships']
    llm.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['exact', 'relative', 'foreign', 'wrong-source', 'stale', 'missing', 'concrete', 'near',
    'directory', 'directory-relative', 'directory-unquoted', 'directory-foreign', 'directory-wrong-source',
    'directory-stale', 'directory-missing', 'directory-other-pattern', 'directory-module',
    'specifier', 'specifier-foreign', 'specifier-wrong-source', 'specifier-stale', 'specifier-missing',
    'specifier-near', 'specifier-wrong-relation', 'specifier-base-only'])
async def test_declared_file_patterns_stay_source_bound_without_becoming_files(monkeypatch, case):
    source, pattern = 'pkg/package.json', 'pkg/src/routes/*'
    target = './src/routes/*' if case == 'relative' else pattern
    if case == 'concrete': target = 'pkg/src/routes/login.ts'
    if case == 'near': target = 'pkg/src/route/*'
    abbreviated = case.startswith('directory')
    if abbreviated: target = './src/routes' if case == 'directory-relative' else 'pkg/src/routes'
    declared = case.startswith('specifier')
    if declared: target = 'src/route/*' if case == 'specifier-near' else 'src/routes/*'
    binding = case.removeprefix('directory-').removeprefix('specifier-')
    queries = []
    async def query(which, **kw):
        queries.append(kw)
        fact = dict(evidenceId='pattern-proof', kind='package-export-pattern', relation='declares-file-pattern',
            source='other/package.json' if binding in {'foreign', 'wrong-source'} else source,
            target=pattern, origin=dict(path='other/package.json' if binding == 'foreign' else source),
            relatedPaths=[], attributes={'targetSpecifier': 'src/routes/*'} if declared else {})
        if case in {'directory-module', 'specifier-wrong-relation'}: fact['relation'] = 'imports'
        values = ([unit(source, source)] if kw['pattern'] == 'file_summary' else
                  [] if binding == 'missing' or binding == 'base-only' and which == 'proposed'
                  else [fact]) if kw['target'] == source else []
        observed = snapshot(which)
        if binding == 'stale': observed['generationManifestSha256'] = 'foreign-generation'
        return dict(snapshot=observed, results=values)
    async def base(**kw): return await query('base', **kw)
    async def proposed(**kw): return await query('proposed', **kw)
    monkeypatch.setattr(scan, 'supports_structured_output', lambda _: False)
    response = dict(summary='Exports route subpaths.', files={source:dict(summary='Declares ./src/routes/* as an export target pattern.')},
        relationships=[dict(source=source,target=target,relationship='DECLARES_EXPORT_PATTERN',evidence='Manifest declaration.')])
    if abbreviated and case != 'directory-unquoted':
        quoted = './src/other/*' if case == 'directory-other-pattern' else './src/routes/*'
        response['relationships'][0]['evidence'] = f'Manifest maps `./routes/*` to `{quoted}`.'
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content=json.dumps(response))))
    raw = f'diff --git a/{source} b/{source}\n--- a/{source}\n+++ b/{source}\n@@ -1 +1 @@\n-{{}}\n+{{"exports":{{"./routes/*":"./src/routes/*"}}}}\n'
    result = await scan.scan_pr_changes(llm, request(rawDiff=raw, changedFiles=[source]),
        rag_client=SimpleNamespace(query_code_graph=base, query_review_graph=proposed))
    if case in {'exact','relative','directory','directory-relative','specifier'}:
        assert result['status'] == result['graph']['coverage']['status'] == 'complete', result['diagnostics']
        edge, = result['relationships']
        assert edge['target'] == (pattern if abbreviated else target) and edge['provenance'] == 'model_inferred'
        if abbreviated:
            assert f'Target {target} restored to quoted pattern {pattern}' in edge['evidence']
        assert edge['targetReference'] == dict(kind='file_pattern', resolution='unresolved', snapshot='proposed', evidenceIds=['pattern-proof'])
        assert not any(q['target'] == target for q in queries)
        assert not any(n.get('path') in {target, pattern} for n in result['graph']['nodes'])
        assert 'kind=file_pattern' in scan.change_summary_context(result)
    else:
        assert result['status'] == ('degraded' if case.endswith('stale') else 'complete')
        assert not result['relationships'] and result['omittedRelationships']
    llm.ainvoke.assert_awaited_once()


def test_deleted_file_pattern_uses_base_declaration_only():
    source, target = 'pkg/package.json', 'pkg/src/*'
    graph = ChangeGraph(request(), [dict(path=source, changeType='deleted')], None)
    fact = dict(evidenceId='base-pattern', relation='declares-file-pattern', source=source, target=target,
                origin=dict(path=source), attributes=dict(targetSpecifier='src/*'))
    graph.relations['base']['base-pattern'] = fact
    graph.relations['proposed']['wrong-pattern'] = {**fact, 'evidenceId':'wrong-pattern'}
    assert graph.import_reference(source,target) == dict(kind='file_pattern',resolution='unresolved',snapshot='base',evidenceIds=['base-pattern'])
    assert graph.import_reference(source,'src/*') == graph.import_reference(source,target)
    assert scan._resolve_quoted_file_pattern(source, 'pkg/src', 'Exports `./src/*`.', graph) == dict(
        path=target, reference=dict(kind='file_pattern',resolution='unresolved',snapshot='base',evidenceIds=['base-pattern']))


@pytest.mark.parametrize('target', ['/pkg/src', '../pkg/src', 'pkg\\src'])
def test_quoted_pattern_does_not_repair_unsafe_directory_spelling(target):
    source = 'pkg/package.json'
    graph = ChangeGraph(request(), [file(source)], None)
    graph.relations['proposed']['proof'] = dict(evidenceId='proof', relation='declares-file-pattern',
        source=source, target='pkg/src/*', origin=dict(path=source))
    assert scan._resolve_quoted_file_pattern(source, target, 'Exports `./src/*`.', graph) is None


@pytest.mark.parametrize('target', ['virtual:config', '../utils/site-identity'])
def test_module_reference_uses_base_import_for_deleted_source(target):
    graph = ChangeGraph(request(), [dict(path='old.ts', changeType='deleted')], None)
    edge = dict(evidenceId='base-import', relation='imports', target=target + '::default',
                origin=dict(path='old.ts'))
    graph.relations['base']['base-import'] = edge
    graph.relations['proposed']['wrong-import'] = {**edge, 'evidenceId': 'wrong-import'}
    assert graph.import_reference('old.ts', target) == dict(kind='module',
        resolution='unresolved', snapshot='base', evidenceIds=['base-import'])


@pytest.mark.parametrize('deleted', [False, True])
@pytest.mark.parametrize('case', ['exact', 'foreign', 'wrong-relation', 'wrong-attribute', 'wrong-snapshot', 'near'])
def test_configured_module_exact_declared_spelling_requires_source_bound_snapshot(deleted, case):
    source, canonical, declared = 'config.yml', 'vendor/tool', 'vendor/tool@release'
    graph = ChangeGraph(request(), [dict(path=source, changeType='deleted' if deleted else 'modified')], None)
    snapshot = 'base' if deleted else 'proposed'
    edge = dict(evidenceId='declared', relation='configures-module', source=source, target=canonical,
        origin=dict(path='other.yml' if case == 'foreign' else source),
        attributes={'specifier' if case == 'wrong-attribute' else 'targetSpecifier': declared})
    if case == 'wrong-relation': edge['relation'] = 'calls'
    graph.relations[('proposed' if deleted else 'base') if case == 'wrong-snapshot' else snapshot]['declared'] = edge
    result = graph.import_reference(source, declared + ('-other' if case == 'near' else ''))
    assert result == (dict(kind='module', resolution='unresolved', snapshot=snapshot, evidenceIds=['declared'])
                      if case == 'exact' else None)


def test_import_pattern_uses_only_exact_deleted_source_evidence():
    graph = ChangeGraph(request(), [dict(path='old.astro', changeType='deleted')], None)
    pattern = './locales/${locale}.mjs'
    edge = dict(evidenceId='base-pattern', relation='imports-pattern', target=pattern,
                origin=dict(path='old.astro'))
    graph.relations['base']['base-pattern'] = edge
    graph.relations['proposed']['wrong-pattern'] = {**edge, 'evidenceId': 'wrong-pattern'}
    assert graph.import_reference('old.astro', pattern) == dict(kind='module_pattern',
        resolution='unresolved', snapshot='base', evidenceIds=['base-pattern'])
    assert graph.import_reference('old.astro', './locales/en.mjs') is None
    assert graph.import_reference('other.astro', pattern) is None
    assert graph.import_reference('other.ts', 'virtual:config') is None


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['unique', 'correct', 'ambiguous', 'outside', 'wrong-kind', 'stale', 'undescribed', 'unknown-source'])
async def test_dependency_source_uses_unique_current_described_structural_declaration(monkeypatch, case):
    wrong, actual, target = 'i18n/package.json', 'package.json', '@lunariajs/core'
    paths = [wrong, actual, *(['other/package.json'] if case == 'ambiguous' else [])]
    queries = []
    async def query(which, **kw):
        queries.append(kw)
        path = kw['target']
        values = [unit(path, path, 1, 2)] if kw['pattern'] == 'file_summary' and path in paths else []
        if which == 'proposed' and kw['pattern'] == 'relations_of' and path in paths and path != wrong:
            origin = 'outside/package.json' if case == 'outside' else path
            values = [dict(evidenceId='declaration:' + path, kind='package-dependency', relation='declares-dependency',
                source=origin, target=target, origin=dict(path=origin, line=1, extractor='plugin'),
                sourceUnit=None, targetUnit=None, relatedPaths=[origin], attributes={})]
        observed = snapshot(which)
        if case == 'stale' and which == 'proposed': observed['generationManifestSha256'] = 'wrong-generation'
        return dict(snapshot=observed, results=values)
    async def base(**kw): return await query('base', **kw)
    async def proposed(**kw): return await query('proposed', **kw)
    source = actual if case == 'correct' else 'unknown.json' if case == 'unknown-source' else wrong
    payload = dict(summary='Adds localization tooling.',
        files={p: dict(summary='' if case == 'undescribed' and p == actual else 'Updates configuration.') for p in paths},
        relationships=[dict(source=source, target=target,
            relationship='IMPORTS' if case == 'wrong-kind' else 'DECLARES_DEPENDENCY', evidence='Adds localization tooling.')])
    monkeypatch.setattr(scan, 'supports_structured_output', lambda _: False)
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content=json.dumps(payload))))
    raw = '\n'.join(f'diff --git a/{p} b/{p}\n--- a/{p}\n+++ b/{p}\n@@ -1 +1 @@\n-old\n+new\n' for p in paths)
    result = await scan.scan_pr_changes(llm, request(rawDiff=raw, changedFiles=paths),
        rag_client=SimpleNamespace(query_code_graph=base, query_review_graph=proposed))
    if case in {'unique', 'correct'}:
        assert result['status'] == 'complete', result['diagnostics']
        edge, = result['relationships']
        assert edge['source'] == actual and edge['target'] == target and edge['provenance'] == 'model_inferred'
        assert edge['targetReference']['evidenceIds'] == ['declaration:' + actual]
        assert (f'Dependency source {wrong} corrected to {actual}' in edge['evidence']) == (case == 'unique')
        assert not any(q['target'] == target for q in queries)
    else:
        assert result['status'] == ('degraded' if case in {'stale', 'undescribed'} else 'complete')
        assert not result['relationships'] and result['omittedRelationships']
    llm.ainvoke.assert_awaited_once()


def test_dependency_source_resolution_uses_base_for_deleted_file():
    graph = ChangeGraph(request(), [file('old.json') | {'changeType': 'deleted'}])
    graph.relations['base']['decl'] = dict(evidenceId='decl', relation='declares-dependency', target='pkg', origin=dict(path='old.json'))
    graph.relations['proposed']['wrong'] = dict(evidenceId='wrong', relation='declares-dependency', target='other', origin=dict(path='old.json'))
    assert graph.resolve_dependency_source('pkg', {'old.json'}) == dict(path='old.json', snapshot='base', evidenceIds=['decl'])
    assert graph.resolve_dependency_source('other', {'old.json'}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('lookup', ['unique', 'paged', 'ambiguous', 'missing', 'stale', 'unavailable', 'truncated', 'stalled'])
async def test_scan_resolves_unchanged_quoted_target_from_bound_index(monkeypatch, lookup):
    source = 'packages/core/src/astro/integration/routes.ts'
    target = 'packages/core/src/astro/routes/api/settings/email.ts'
    requested = 'packages/core/src/api/settings/email.ts'
    searches = []
    async def query(which, **kw):
        assert kw['workspace'] == 'tenant' and kw['project'] == 'project'
        if kw['pattern'] == 'file_search':
            searches.append(kw)
            assert which == 'proposed' and kw['target'] == 'api/settings/email.ts'
            assert kw['review_generation_manifest_sha256'] == 'review-manifest'
            assert kw['source_revision'] == 'head' and kw['include_source'] is False
            values = [] if lookup == 'missing' else [unit(target, f"email{kw['cursor']}", 1, 2)]
            if lookup == 'ambiguous':
                values.append(unit('other/api/settings/email.ts', 'other', 1, 2))
            result = dict(snapshot=snapshot(which), results=values)
            if lookup == 'paged' and kw['cursor'] == 0: result['nextCursor'] = 1
            if lookup == 'stale': result['snapshot']['generationManifestSha256'] = 'other-generation'
            if lookup == 'unavailable': result['status'] = 'unavailable'
            if lookup == 'truncated': result['truncated'] = True
            if lookup == 'stalled': result['nextCursor'] = kw['cursor']
            return result
        values = [unit(kw['target'], kw['target'], 1, 2)] if (
            kw['pattern'] == 'file_summary' and kw['target'] in {source, target}) else []
        return dict(snapshot=snapshot(which), results=values)
    async def base(**kw): return await query('base', **kw)
    async def proposed(**kw): return await query('proposed', **kw)
    monkeypatch.setattr(scan, 'supports_structured_output', lambda _: False)
    response = dict(summary='Adds an email settings route.',
        files={source: dict(summary='Registers the existing email handler.')},
        relationships=[dict(source=source, target=requested, relationship='INFERRED route',
                            evidence='Resolves api/settings/email.ts.')])
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content=json.dumps(response))))
    raw = f'diff --git a/{source} b/{source}\n--- a/{source}\n+++ b/{source}\n@@ -1 +1 @@\n-old\n+resolveRoute("api/settings/email.ts")\n'
    result = await scan.scan_pr_changes(llm, request(rawDiff=raw, changedFiles=[source]),
        rag_client=SimpleNamespace(query_code_graph=base, query_review_graph=proposed))
    assert searches
    if lookup in {'unique', 'paged'}:
        assert result['status'] == 'complete', result['diagnostics']
        edge, = result['relationships']
        assert edge['target'] == target and edge['provenance'] == 'model_inferred'
        assert requested in edge['evidence'] and 'api/settings/email.ts' in edge['evidence']
        assert len(searches) == (2 if lookup == 'paged' else 1)
    else:
        assert result['status'] == ('degraded' if lookup in {'stale', 'unavailable', 'truncated', 'stalled'} else 'complete')
        assert not result['relationships'] and result['omittedRelationships']
    assert result['files'][0]['scanStatus'] == 'scanned'
    llm.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_deleted_source_path_search_uses_only_bound_base_snapshot():
    async def base(**kw):
        assert kw['repository_revision'] == 'base'
        assert kw['repository_generation_manifest_sha256'] == 'base-manifest'
        assert kw['collection_target'] == 'base-target'
        assert kw['workspace'] == 'tenant' and kw['project'] == 'project'
        assert kw['pattern'] == 'file_search' and kw['target'] == 'api/email.py'
        return dict(snapshot=snapshot('base'), results=[unit('src/api/email.py', 'email')])
    proposed = AsyncMock(side_effect=AssertionError('Deleted source must use base'))
    graph = ChangeGraph(request(), [dict(path='deleted.py', changeType='deleted')],
                        SimpleNamespace(query_code_graph=base, query_review_graph=proposed))
    assert await graph.matching_paths('api/email.py', 'deleted.py') == {'src/api/email.py'}
    assert not graph.diagnostics
    proposed.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint', ['source', 'target'])
@pytest.mark.parametrize('requested', ['packages/admin/src/lib/schema.ts', 'packages/admin/src/lib.api/schema.ts'])
async def test_scan_restores_directory_spelling_from_current_described_file(monkeypatch, endpoint, requested):
    source = 'packages/admin/src/lib/api/schema.ts'
    target = 'packages/admin/src/components/FieldEditor.tsx'
    paths = {source, target}
    async def query(which, **kw):
        return dict(snapshot=snapshot(which), results=([unit(kw['target'], kw['target'])]
            if kw['target'] in paths and kw['pattern'] == 'file_summary' else []))
    async def base(**kw): return await query('base', **kw)
    async def proposed(**kw): return await query('proposed', **kw)
    monkeypatch.setattr(scan, 'supports_structured_output', lambda llm: False)
    edge = dict(source=source if endpoint == 'source' else target,
                target=target if endpoint == 'source' else source,
                relationship='shared field type', evidence='Both use FieldType with repeater support.')
    edge[endpoint] = requested
    response = dict(summary='Adds the repeater field type.',
        files=[dict(path=p, summary='Adds repeater support.') for p in sorted(paths)], relationships=[edge])
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content=json.dumps(response))))
    raw = ''.join(f'diff --git a/{p} b/{p}\n--- a/{p}\n+++ b/{p}\n@@ -1 +1 @@\n-old\n+new\n'
                  for p in sorted(paths))
    result = await scan.scan_pr_changes(llm, request(rawDiff=raw, changedFiles=sorted(paths)),
        rag_client=SimpleNamespace(query_code_graph=base, query_review_graph=proposed))
    assert result['status'] == 'complete', result['diagnostics']
    edge, = result['relationships']
    assert edge[endpoint] == source and edge['provenance'] == 'model_inferred'
    assert requested in edge['evidence'] and source in edge['evidence']
    llm.ainvoke.assert_awaited_once()


@pytest.mark.parametrize('requested,known,description,in_batch,expected', [
    ('src/types.py', {'src/domain/types.py'}, 'Defines types.', True, 'src/domain/types.py'),
    ('src/types.py', {'src/domain/types.py', 'src/other/types.py'}, 'Defines types.', True, None),
    ('src/types.py', {'src/domain/types.py'}, '', True, None),
    ('src/types.py', {'src/domain/types.py'}, 'Defines types.', False, None),
    ('types.py', {'src/domain/types.py'}, 'Defines types.', True, None),
    ('src/types.ts', {'src/domain/types.py'}, 'Defines types.', True, None),
    ('domain/src/types.py', {'src/domain/types.py'}, 'Defines types.', True, None),
    ('/src/types.py', {'src/domain/types.py'}, 'Defines types.', True, None),
    ('../src/types.py', {'src/domain/types.py'}, 'Defines types.', True, None),
    ('src/types.py', {'src/types.py', 'src/domain/types.py'}, 'Defines types.', True, None),
    ('src/domain.types.py', {'src/domain/types.py'}, 'Defines types.', True, 'src/domain/types.py'),
    ('src/domain.types.py', {'src/domain/types.py', 'src/domain.types/py'}, 'Defines types.', True, None),
    ('src/domain.types.py', {'src/domain/types.py', 'src/domain.types.py'}, 'Defines types.', True, None),
    ('src/domain.types.py', {'src/domain/types.py'}, '', True, None),
    ('src/domain.types.py', {'src/domain/types.py'}, 'Defines types.', False, None),
    ('src.domain.types.py', {'src/domain/types.py'}, 'Defines types.', True, None),
    ('other/domain.types.py', {'src/domain/types.py'}, 'Defines types.', True, None),
    ('src/domain.typos.py', {'src/domain/types.py'}, 'Defines types.', True, None),
    ('src/domain.types.ts', {'src/domain/types.py'}, 'Defines types.', True, None),
])
def test_described_path_resolution_never_guesses_an_ambiguous_or_undescribed_file(
        requested, known, description, in_batch, expected):
    path = 'src/domain/types.py'
    result = scan.CumulativeChanges(summary='Changes types.',
        files=[scan.ChangeNode(path=path, summary=description)])
    assert scan._resolve_described_path(requested, result, {path} if in_batch else set(), known) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('owner', ['plugins/auth/src/resolve.ts', 'plugins/auth/src/other.ts'])
async def test_restored_source_keeps_module_resolution_bound_to_its_own_import(monkeypatch, owner):
    source, requested = 'plugins/auth/src/resolve.ts', 'plugins/auth/src.resolve.ts'
    target = '@vendor/resolver'
    paths = {source, 'plugins/auth/src/other.ts'}
    queries = []
    async def query(which, **kw):
        queries.append(kw['target'])
        values = []
        if kw['pattern'] == 'file_summary' and kw['target'] in paths:
            values = [unit(kw['target'], kw['target'])]
        elif which == 'proposed' and kw['pattern'] == 'relations_of' and kw['target'] == owner:
            values = [dict(evidenceId='import:' + owner, kind='import', relation='imports',
                source=owner, target=target, origin=dict(path=owner, line=1, extractor='plugin'),
                relatedPaths=[owner], attributes={})]
        return dict(snapshot=snapshot(which), results=values)
    async def base(**kw): return await query('base', **kw)
    async def proposed(**kw): return await query('proposed', **kw)
    monkeypatch.setattr(scan, 'supports_structured_output', lambda _: False)
    response = dict(summary='Updates resolver usage.',
        files={path: dict(summary='Updates resolver usage.') for path in paths},
        relationships=[dict(source=requested, target=target, relationship='imports', evidence='Imports the resolver.')])
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content=json.dumps(response))))
    raw = ''.join(f'diff --git a/{p} b/{p}\n--- a/{p}\n+++ b/{p}\n@@ -1 +1 @@\n-old\n+new\n'
                  for p in sorted(paths))
    result = await scan.scan_pr_changes(llm, request(rawDiff=raw, changedFiles=sorted(paths)),
        rag_client=SimpleNamespace(query_code_graph=base, query_review_graph=proposed))
    assert requested not in queries
    if owner == source:
        assert result['status'] == 'complete', result['diagnostics']
        edge, = result['relationships']
        assert edge['source'] == source and edge['target'] == target
        assert edge['targetReference']['kind'] == 'module'
        assert edge['targetReference']['evidenceIds'] == ['import:' + source]
        assert requested in edge['evidence'] and source in edge['evidence']
        assert target not in queries
    else:
        assert result['status'] == 'complete'
        assert not result['relationships'] and result['omittedRelationships']
    llm.ainvoke.assert_awaited_once()


def test_described_path_restores_directories_without_reordering_or_reusing_components():
    path = 'src/service/domain/schema.py'
    result = scan.CumulativeChanges(summary='Changes schema.',
        files=[scan.ChangeNode(path=path, summary='Changes schema.')])
    assert scan._resolve_described_path('src/domain/schema.py', result, {path}, {path}) == path
    assert scan._resolve_described_path('src/domain/service/schema.py', result, {path}, {path}) is None
    assert scan._resolve_described_path('src/service/service/schema.py', result, {path}, {path}) is None


@pytest.mark.asyncio
async def test_scan_resolves_misrooted_path_from_unique_source_diff_reference(monkeypatch):
    source = 'packages/core/src/astro/integration/routes.ts'
    target = 'packages/core/src/astro/routes/api/auth/mode.ts'
    requested = 'packages/core/src/api/auth/mode.ts'
    paths = {source, target, 'packages/core/src/auth/mode.ts'}
    async def query(which, **kw):
        return dict(snapshot=snapshot(which), results=([unit(kw['target'], kw['target'])]
            if kw['target'] in paths and kw['pattern'] == 'file_summary' else []))
    async def base(**kw): return await query('base', **kw)
    async def proposed(**kw): return await query('proposed', **kw)
    monkeypatch.setattr(scan, 'supports_structured_output', lambda llm: False)
    response = dict(summary='Registers an authentication mode route.',
        files=[dict(path=p, summary='Updates route setup.') for p in sorted(paths)],
        relationships=[dict(source=source, target=requested, relationship='registers_route',
                            evidence='Registers api/auth/mode.ts.')])
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content=json.dumps(response))))
    raw = ''.join(f'diff --git a/{p} b/{p}\n--- a/{p}\n+++ b/{p}\n@@ -1 +1 @@\n-old\n'
                  + ('+resolveRoute("api/auth/mode.ts")\n' if p == source else '+new\n')
                  for p in sorted(paths))
    result = await scan.scan_pr_changes(llm, request(rawDiff=raw, changedFiles=sorted(paths)),
        rag_client=SimpleNamespace(query_code_graph=base, query_review_graph=proposed))
    assert result['status'] == 'complete', result['diagnostics']
    edge, = result['relationships']
    assert edge['target'] == target and edge['provenance'] == 'model_inferred'
    assert requested in edge['evidence'] and 'api/auth/mode.ts' in edge['evidence']
    assert source in edge['evidence']
    llm.ainvoke.assert_awaited_once()


@pytest.mark.parametrize('paths,source,requested,diff,expected', [
    ({'app/templates/account/card.html'}, 'view.py', 'templates/account/card.html',
     '+render("account/card.html")', 'app/templates/account/card.html'),
    ({'app/templates/account/card.html'}, 'view.py', 'account/card.html',
     '+render("account/card.html")', 'app/templates/account/card.html'),
    ({'app/templates/account/card.html', 'other/account/card.html'}, 'view.py',
     'templates/account/card.html', '+render("account/card.html")', None),
    ({'app/templates/account/card.html'}, 'other.py', 'templates/account/card.html',
     '+render("account/card.html")', None),
    ({'app/templates/account/card.html'}, 'view.py', 'templates/account/card.html',
     '+render("card.html")', None),
    ({'app/templates/account/card.html'}, 'view.py', '../templates/account/card.html',
     '+render("account/card.html")', None),
    ({'app/templates/account/card.html'}, 'view.py', '/templates/account/card.html',
     '+render("account/card.html")', None),
    ({'templates/account/card.html', 'app/templates/account/card.html'}, 'view.py',
     'templates/account/card.html', '+render("account/card.html")', None),
    ({'app/templates/account/card.html'}, 'view.py', 'templates/account/card.html',
     '+unrelated = True', None),
])
def test_diff_path_resolution_requires_unique_source_reference(paths, source, requested, diff, expected):
    result = scan._resolve_diff_path(source, requested, [dict(path='view.py', diff=diff)], paths)
    assert (result['path'] if result else None) == expected
    if result:
        assert result['references'] == ['account/card.html']


@pytest.mark.asyncio
@pytest.mark.parametrize('requested,specifier', [
    ('src/RepeaterField.js', './RepeaterField.js'),
    ('src/RepeaterField', './RepeaterField'),
    ('./RepeaterField', './RepeaterField'),
])
async def test_scan_resolves_model_import_spelling_only_from_bound_plugin_facts(monkeypatch, requested, specifier):
    source, target = 'src/ContentEditor.tsx', 'src/RepeaterField.tsx'
    edge = {**relation('import-proof', unit(source, 'ContentEditor'), unit(target, 'RepeaterField')),
            'relation': 'resolves-import', 'attributes': {'specifier': specifier}}
    async def query(which, **kw):
        assert kw['target'] != requested, 'Import spelling must resolve before graph acquisition'
        return dict(snapshot=snapshot(which), results=([unit(kw['target'], kw['target'])]
            if kw['pattern'] == 'file_summary' else [edge] if which == 'proposed' else []))
    async def base(**kw): return await query('base', **kw)
    async def proposed(**kw): return await query('proposed', **kw)
    monkeypatch.setattr(scan, 'supports_structured_output', lambda llm: False)
    response = dict(summary='Adds repeater rendering.',
        files=[dict(path=p, summary='Adds repeater rendering.') for p in (source, target)],
        changeAreas=[dict(title='Editor', paths=[source, target], changes=['Adds repeater rendering.'])],
        relationships=[dict(source=source, target=requested, relationship='imports', evidence=f'Imports {specifier}.')])
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content=json.dumps(response))))
    raw = ''.join(f'diff --git a/{p} b/{p}\n--- a/{p}\n+++ b/{p}\n@@ -1 +1 @@\n-old\n+new\n'
                  for p in (source, target))
    result = await scan.scan_pr_changes(llm, request(rawDiff=raw, changedFiles=[source, target]),
        rag_client=SimpleNamespace(query_code_graph=base, query_review_graph=proposed))
    assert result['status'] == 'complete', result['diagnostics']
    assert result['relationships'][0]['target'] == target
    assert requested in result['relationships'][0]['evidence']
    assert 'import-proof' in result['relationships'][0]['evidence']
    assert result['relationships'][0]['provenance'] == 'model_inferred'
    graph = ChangeGraph(request(), [file(source), file(target)])
    graph.relations['base']['import-proof'] = edge
    assert graph.resolve_import_path(source, requested) is None
    graph.relations['proposed']['import-proof'] = edge
    assert graph.resolve_import_path(source, requested)['path'] == target
    assert graph.resolve_import_path('other.tsx', requested) is None
    assert graph.resolve_import_path(source, 'src/Unknown.js') is None
    if specifier == './RepeaterField':
        assert graph.resolve_import_path(source, 'src/RepeaterField.js') is None
    graph.relations['proposed']['other-proof'] = {**edge, 'evidenceId': 'other-proof',
        'relatedPaths': [source, 'src/RepeaterField.ts']}
    assert graph.resolve_import_path(source, requested) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['indexed', 'excluded', 'empty', 'unknown', 'stale', 'invented', 'not-renamed'])
@pytest.mark.parametrize('reverse', [False, True])
async def test_scan_preserves_diff_rename_endpoints_without_requerying_old_name(monkeypatch, kind, reverse):
    old, new = 'old/location/link', 'new/location/link'
    requested = 'invented/missing-link' if kind == 'invented' else old
    raw = (f'diff --git a/{old} b/{new}\nsimilarity index 100%\nrename from {old}\nrename to {new}\n'
           if kind != 'not-renamed' else
           f'diff --git a/{new} b/{new}\n--- a/{new}\n+++ b/{new}\n@@ -1 +1 @@\n-old\n+new\n')
    calls = []
    async def query(which, **kw):
        calls.append((which, kw['pattern'], kw['target']))
        expected = old if which == 'base' and kind != 'not-renamed' else new
        identity = snapshot(which)
        if kind == 'stale' and which == 'proposed':
            identity['sourceRevision'] = 'different-head'
        return dict(snapshot=identity,
            results=([unit(expected, 'link')] if kind == 'indexed' and kw['target'] == expected
                     and kw['pattern'] == 'file_summary' else []),
            fileCoverage={'state': ('excluded' if kind in {'stale', 'invented', 'not-renamed'} else kind)
                          if kw['target'] == expected else 'unknown'})
    async def base(**kw): return await query('base', **kw)
    async def proposed(**kw): return await query('proposed', **kw)
    edge = dict(source=requested if reverse else new, target=new if reverse else requested,
                relationship='renamed to' if reverse else 'renamed from', evidence='The supplied diff records the rename.')
    response = dict(summary='Moves the link.', files=[dict(path=new, summary='Moves the link without editing it.')],
                    relationships=[edge])
    monkeypatch.setattr(scan, 'supports_structured_output', lambda _: False)
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content=json.dumps(response))))
    result = await scan.scan_pr_changes(llm, request(rawDiff=raw, changedFiles=[new]),
        rag_client=SimpleNamespace(query_code_graph=base, query_review_graph=proposed))
    expected_status = 'degraded' if kind == 'stale' else 'complete'
    assert result['status'] == expected_status, result['diagnostics']
    if kind not in {'invented', 'not-renamed'}:
        assert result['files'][0]['oldPath'] == old
        assert result['relationships'] == [{**edge, 'provenance': 'model_inferred'}]
        assert result['graph']['coverage']['queriedPaths'] == [new]
        assert all(target == (old if which == 'base' else new) for which, _, target in calls)
        assert not any(node.get('path') == old and node['snapshot'] == 'proposed'
                       for node in result['graph']['nodes'])
    else:
        assert result['omittedRelationships']


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,stale,expected', [
    ('binary', False, 'complete'), ('gitlink', False, 'complete'),
    ('empty', False, 'complete'), ('excluded', False, 'complete'),
    ('unknown', False, 'unavailable'), ('binary', True, 'degraded'), ('excluded', True, 'degraded'),
])
async def test_explicit_nonstructural_coverage_never_hides_unknown_source_or_stale_reads(kind, stale, expected, caplog):
    path = 'asset.bin' if kind == 'binary' else 'Cargo.lock'
    async def query(which, **kwargs):
        identity = snapshot(which)
        if stale and which == 'proposed':
            identity['sourceRevision'] = 'other'
        return {'snapshot': identity, 'results': [], 'fileCoverage': {'state': kind}}
    async def base(**kw): return await query('base', **kw)
    async def proposed(**kw): return await query('proposed', **kw)
    graph = ChangeGraph(request(), [file(path, binary=kind == 'binary', gitlink=kind == 'gitlink')],
                        SimpleNamespace(query_code_graph=base, query_review_graph=proposed))
    await graph.collect([path])
    result = graph.render()
    assert result['coverage']['status'] == expected
    assert result['nodes'] == [] and result['edges'] == []
    assert bool(result['coverage']['nonStructuralPaths']) is (expected == 'complete')
    assert bool(result['coverage']['unrepresentedPaths']) is (expected != 'complete')
    assert ('PR change graph status=degraded:' in caplog.text) is (expected == 'degraded')
    if stale:
        assert result['coverage']['diagnostics']


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
    for row in compact['edges']:
        counts = row[3] + [0] * (4 - len(row[3]))
        expected_mask = (3 if counts[0] else 0) | (2 if counts[1] else 0) | (1 if counts[2] else 0)
        origins = row[4] if len(row) > 4 else [[row[0], expected_mask]]
        assert all(mask in (1, 2, 3) for _, mask in origins)
        assert {compact['paths'][compact['entities'][index][1]] for index, _ in origins} <= {'changed.py', 'consumer.py'}
    assert ['file', compact['paths'].index('consumer.py')] in compact['entities'] and ['file', compact['paths'].index('new.py')] in compact['entities']
    detail = compact['symbolDetail']
    for row, original in zip(detail['edges'], result['edges']):
        assert detail['relationKinds'][row[0]] == [original['kind'], original['relationship']]
        assert detail['changes'][row[1]] == original['change']
        for observation in row[2]:
            assert 0 <= observation[1] < len(detail['nodes'])
            assert 0 <= observation[2] < len(detail['nodes'])


@pytest.mark.asyncio
@pytest.mark.parametrize('response,expected_diagnostic', [
    (dict(snapshot={'sourceRevision': 'different'}, results=[]), 'revision/generation'),
    (dict(status='error', status_code=422, error='focus_paths must contain file paths', results=[]),
     'graph query failed (422): focus_paths must contain file paths'),
    (dict(status='unavailable', error='index unavailable', results=[]),
     'graph query failed (unavailable): index unavailable'),
])
async def test_stale_or_failed_graph_preserves_source_review_and_does_not_claim_removed_edges(response, expected_diagnostic):
    changed, external = unit('changed.py', 'changed'), unit('external.py', 'caller')
    async def base(**kw):
        return dict(snapshot=snapshot('base'), results=[changed] if kw['pattern'] == 'file_summary'
                    else [relation('r', external, changed)])
    client = SimpleNamespace(query_code_graph=AsyncMock(side_effect=base),
        query_review_graph=AsyncMock(return_value=response))
    graph = ChangeGraph(request(), [file()], client)
    await graph.collect(['changed.py'])
    result = graph.render()
    assert result['coverage']['status'] == 'degraded'
    assert result['edges'][0]['change'] == 'unknown'
    assert all(n['snapshot'] == 'base' for n in result['nodes'])
    assert expected_diagnostic in str(result['coverage']['diagnostics'])
    if response.get('status'):
        assert 'revision/generation' not in str(result['coverage']['diagnostics'])


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
    assert ['file', visible['paths'].index('new.tsx')] in visible['entities'] and ['file', visible['paths'].index('old.tsx')] in visible['entities']


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
    assert ['file', visible['paths'].index('dependency.py')] in visible['entities']
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
    assert result['semanticStatus'] == 'complete'
    assert len(result['omittedRelationships']) == int(include_unknown)
    assert len(result['relationships']) == 1
    assert result['relationships'][0]['source'] == 'consumer.py'
    assert result['relationships'][0]['provenance'] == 'model_inferred'


@pytest.mark.asyncio
@pytest.mark.parametrize('dependency_state', ['present', 'missing', 'stale'])
async def test_inferred_dependency_outside_neighborhood_is_resolved_in_bound_graph(monkeypatch, dependency_state):
    monkeypatch.setattr(scan, 'supports_structured_output', lambda _: False)
    queried = []
    async def query(which, **kw):
        assert kw['workspace'] == 'tenant' and kw['project'] == 'project'
        assert kw['collection_target' if which == 'base' else 'review_collection_target'] == (
            'base-target' if which == 'base' else 'review-target')
        path = kw['target']
        queried.append(path)
        binding = snapshot(which)
        if path == 'dependency.py' and dependency_state == 'stale':
            binding['generationManifestSha256'] = 'other-tenant-generation'
        values = [unit(path, path, 1, 2)] if kw['pattern'] == 'file_summary' else []
        if path == 'dependency.py' and dependency_state == 'missing':
            values = []
        return dict(snapshot=binding, results=values)
    async def base(**kw): return await query('base', **kw)
    async def proposed(**kw): return await query('proposed', **kw)
    output = dict(summary='Changes caller behavior',
        files=[dict(path='changed.py', summary='Calls the existing dependency')],
        changeAreas=[dict(title='Caller', paths=['changed.py'], changes=['Calls the existing dependency'])],
        relationships=[dict(source='changed.py', target='dependency.py', relationship='calls',
                            evidence='Inferred from the changed call')])
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content=json.dumps(output))))
    raw = 'diff --git a/changed.py b/changed.py\n--- a/changed.py\n+++ b/changed.py\n@@ -1 +1 @@\n-old\n+new\n'
    result = await scan.scan_pr_changes(llm, request(rawDiff=raw, changedFiles=['changed.py']),
        rag_client=SimpleNamespace(query_code_graph=base, query_review_graph=proposed))
    assert 'dependency.py' in queried
    if dependency_state == 'present':
        assert result['status'] == 'complete'
        assert result['relationships'][0]['target'] == 'dependency.py'
        assert result['relationships'][0]['provenance'] == 'model_inferred'
        assert not any(n.get('path') == 'dependency.py' and n['changedFile'] for n in result['graph']['nodes'])
    else:
        assert result['status'] == ('degraded' if dependency_state == 'stale' else 'complete')
        assert result['omittedRelationships'] and not result['relationships']
        assert bool(result['graph']['coverage']['diagnostics']) is (dependency_state == 'stale')
    llm.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint', ['source', 'target'])
@pytest.mark.parametrize('reference', ['/_emdash/api/auth/mode', 'https://example.test/auth', '../outside'])
async def test_non_file_relationship_endpoints_skip_graph_queries_and_preserve_summary(monkeypatch, endpoint, reference):
    monkeypatch.setattr(scan, 'supports_structured_output', lambda _: False)
    queries = []

    async def query(which, **kwargs):
        queries.append(kwargs)
        if which == 'proposed':
            assert set(kwargs['focus_paths']) <= {'changed.py', 'dependency.py'}
        return dict(snapshot=snapshot(which), results=[unit(kwargs['target'], 'symbol')]
                    if kwargs['pattern'] == 'file_summary' else [])

    async def base(**kwargs): return await query('base', **kwargs)
    async def proposed(**kwargs): return await query('proposed', **kwargs)
    edge = dict(source='changed.py', target='dependency.py', relationship='calls', evidence='Inferred link')
    description = 'Updates authentication at ' + reference
    output = dict(summary=description, files=[dict(path='changed.py', summary=description)],
                  changeAreas=[dict(title='Authentication', paths=['changed.py'], changes=[description])],
                  relationships=[{**edge, endpoint: reference}, edge])
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content=json.dumps(output))))
    raw = 'diff --git a/changed.py b/changed.py\n--- a/changed.py\n+++ b/changed.py\n@@ -1 +1 @@\n-old\n+new\n'
    result = await scan.scan_pr_changes(llm, request(rawDiff=raw, changedFiles=['changed.py']),
        rag_client=SimpleNamespace(query_code_graph=base, query_review_graph=proposed))
    assert result['status'] == result['semanticStatus'] == 'complete'
    assert result['graph']['coverage']['diagnostics'] == []
    assert dict(path=reference, reason='not_repository_file_path') in result['graph']['coverage']['omissions']
    assert description in result['summary'] and result['files'][0]['summary'] == description
    assert len(result['omittedRelationships']) == len(result['relationships']) == 1
    assert result['omittedRelationships'][0][endpoint] == reference
    assert result['relationships'][0]['target'] == 'dependency.py'
    assert not any(q['target'] == reference for q in queries if q['pattern'] == 'file_summary')
    llm.ainvoke.assert_awaited_once()


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
    assert result['graph']['coverage']['status'] == 'unavailable'
    assert result['status'] == 'complete'


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
    assert result['semanticStatus'] == 'complete' and result['diagnostics'] == []
    assert result['scanReceipts'][0]['hostGroupedPaths'] == ['a.py']


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


@pytest.mark.parametrize('case', ['removed', 'renamed', 'module-only', 'duplicate', 'context-only',
    'addition-only', 'missing-ranges', 'foreign-origin', 'wrong-relation', 'near-name', 'missing-line',
    'incomplete-base', 'incomplete-proposed', 'retained', 'added-file', 'deleted-file'])
def test_removed_import_reference_requires_complete_snapshots_and_changed_base_origin(case):
    source, target = 'src/search.ts', '#api/authorize.js'
    item = dict(path=source, changeType='modified', changedRanges=[dict(base=[9, 1], head=[9, 0])])
    if case == 'renamed': item.update(changeType='renamed', oldPath='src/old-search.ts')
    if case == 'addition-only': item['changedRanges'][0]['base'][1] = 0
    if case == 'missing-ranges': item.pop('changedRanges')
    if case in {'added-file', 'deleted-file'}: item['changeType'] = case.split('-')[0]
    graph = ChangeGraph(request(), [item])
    graph.completed.update((source, snapshot) for snapshot in ('base', 'proposed'))
    if case.startswith('incomplete-'): graph.completed.remove((source, case.removeprefix('incomplete-')))
    fact = dict(evidenceId='removed-import', relation='imports', target=target + '::requirePerm',
        origin=dict(path=item.get('oldPath', source), line=9))
    if case == 'module-only': fact['target'] = target
    if case == 'context-only': fact['origin']['line'] = 8
    if case == 'missing-line': fact['origin'].pop('line')
    if case == 'foreign-origin': fact['origin']['path'] = 'other.ts'
    if case == 'wrong-relation': fact['relation'] = 'calls'
    if case == 'near-name': fact['target'] = target + '.extra::requirePerm'
    graph.relations['base'][fact['evidenceId']] = fact
    if case == 'duplicate': graph.relations['base']['duplicate'] = {**fact, 'evidenceId': 'duplicate'}
    if case == 'retained': graph.relations['proposed']['retained'] = {**fact, 'evidenceId': 'retained'}
    result = graph.removed_import_reference(source, target)
    if case in {'removed', 'renamed', 'module-only', 'duplicate'}:
        assert result == dict(kind='module', resolution='unresolved', snapshot='base',
            evidenceIds=['duplicate', 'removed-import'] if case == 'duplicate' else ['removed-import'])
        assert graph.import_reference(source, target) is None
    else:
        assert result is None


@pytest.mark.asyncio
async def test_scan_preserves_removed_import_from_modified_file_as_historical_module(monkeypatch):
    source, target = 'src/search.ts', '#api/authorize.js'
    fact = dict(evidenceId='removed-import', relation='imports', target=target + '::requirePerm',
        source='requirePerm', origin=dict(path=source, line=9), relatedPaths=[source])
    async def query(which, **kw):
        assert kw['target'] != target, 'Historical module spelling must not become a repository path query'
        return dict(snapshot=snapshot(which), results=([unit(source, 'search', 1, 20)]
            if kw['pattern'] == 'file_summary' else [fact] if which == 'base' else []))
    async def base(**kw): return await query('base', **kw)
    async def proposed(**kw): return await query('proposed', **kw)
    response = dict(summary='Removes the search authorization check.',
        files=[dict(path=source, summary='Removes the authorization import and check.')],
        changeAreas=[dict(title='Search', paths=[source], changes=['Removes authorization.'])],
        relationships=[dict(source=source, target=target, relationship='removes import',
            evidence='The diff removes the requirePerm import.')])
    raw = (f'diff --git a/{source} b/{source}\n--- a/{source}\n+++ b/{source}\n'
           '@@ -8,3 +8,2 @@\n context\n-import { requirePerm } from "#api/authorize.js";\n context\n')
    monkeypatch.setattr(scan, 'supports_structured_output', lambda _: False)
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content=json.dumps(response))))
    result = await scan.scan_pr_changes(llm, request(rawDiff=raw, changedFiles=[source]),
        rag_client=SimpleNamespace(query_code_graph=base, query_review_graph=proposed))
    assert result['status'] == result['graph']['coverage']['status'] == 'complete', result['diagnostics']
    assert not result['diagnostics'] and len(result['relationships']) == 1
    edge = result['relationships'][0]
    assert edge['source'] == source and edge['target'] == target and edge['relationship'] == 'removes import'
    assert edge['targetReference'] == dict(kind='module', resolution='unresolved', snapshot='base', evidenceIds=['removed-import'])
    assert edge['provenance'] == 'model_inferred' and 'Removed import' in edge['evidence']
    assert not any(node.get('path') == target for node in result['graph']['nodes'])


@pytest.mark.parametrize('deleted', [False, True])
@pytest.mark.parametrize('case', ['canonical', 'literal', 'foreign-origin', 'foreign-source',
    'wrong-snapshot', 'wrong-relation', 'near-target', 'empty'])
def test_directory_reference_requires_exact_source_and_snapshot_declaration(deleted, case):
    source, target = 'i18n/wrangler.jsonc', 'i18n/dist'
    graph = ChangeGraph(request(), [dict(path=source, changeType='deleted' if deleted else 'modified')])
    which = 'base' if deleted else 'proposed'
    fact = dict(evidenceId='directory', relation='declares-directory', source=source, target=target,
        origin=dict(path=source, line=1), attributes=dict(targetSpecifier='./dist'))
    if case == 'foreign-origin': fact['origin']['path'] = 'other/wrangler.jsonc'
    if case == 'foreign-source': fact['source'] = 'other/wrangler.jsonc'
    if case == 'wrong-relation': fact['relation'] = 'calls'
    if case == 'near-target': fact['target'] += '/extra'
    graph.relations[('proposed' if deleted else 'base') if case == 'wrong-snapshot' else which]['directory'] = fact
    result = graph.directory_reference(source, './dist' if case == 'literal' else '' if case == 'empty' else target)
    if case in {'canonical', 'literal'}:
        assert result == dict(kind='directory', resolution='unresolved', snapshot=which, evidenceIds=['directory'])
    else:
        assert result is None


@pytest.mark.asyncio
@pytest.mark.parametrize('collision', [False, True])
async def test_scan_retains_declared_directory_without_file_existence_or_collision_binding(monkeypatch, collision):
    source, target = 'i18n/wrangler.jsonc', 'i18n/dist'
    paths = [source, target] if collision else [source]
    fact = dict(evidenceId='directory', relation='declares-directory', source=source, target=target,
        origin=dict(path=source, line=1), attributes=dict(targetSpecifier='./dist', targetResolutionProven='false'),
        relatedPaths=[source], sourceUnit=None, targetUnit=None)
    async def query(which, **kw):
        if not collision: assert kw['target'] != target, 'Declared directory must not be acquired as a source file'
        return dict(snapshot=snapshot(which), results=([dict(unit(kw['target'], kw['target'], 1, 20), kind='file')]
            if kw['pattern'] == 'file_summary' else [fact] if which == 'proposed' and kw['target'] == source else []))
    async def base(**kw): return await query('base', **kw)
    async def proposed(**kw): return await query('proposed', **kw)
    response = dict(summary='Changes assets deployment.',
        files=[dict(path=p, summary='Changes deployment configuration.') for p in paths],
        changeAreas=[dict(title='Assets', paths=paths, changes=['Configures the assets directory.'])],
        relationships=[dict(source=source, target=target, relationship='serves-assets', evidence='assets.directory is ./dist.')])
    raw = ''.join(f'diff --git a/{p} b/{p}\n--- a/{p}\n+++ b/{p}\n@@ -9 +9 @@\n-old\n+new\n' for p in paths)
    monkeypatch.setattr(scan, 'supports_structured_output', lambda _: False)
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content=json.dumps(response))))
    result = await scan.scan_pr_changes(llm, request(rawDiff=raw, changedFiles=paths),
        rag_client=SimpleNamespace(query_code_graph=base, query_review_graph=proposed))
    assert result['status'] == result['graph']['coverage']['status'] == 'complete', result['diagnostics']
    assert result['relationships'][0]['targetReference'] == dict(kind='directory', resolution='unresolved', snapshot='proposed', evidenceIds=['directory'])
    directory_edges = [e for e in result['graph']['edges'] if e['relationship'] == 'declares-directory']
    assert len(directory_edges) == 1
    nodes = {node['id']: node for node in result['graph']['nodes']}
    for observation in directory_edges[0]['observations']:
        endpoint = nodes[observation['target']]
        assert endpoint['resolution'] == 'unresolved' and not endpoint.get('path')


@pytest.mark.parametrize('deleted', [False, True])
@pytest.mark.parametrize('requested,expected', [
    ('../plugins/types.js', True), ('./nested/../types.js', True), ('././types.js', True),
    ('../plugins/./types.js', True), ('../plugins/types.ts', True), ('src/plugins/types.js', True),
    ('../types.js', False), ('./types.jsx', False), ('./types', False), ('../../plugins/types.js', False),
])
def test_equivalent_relative_import_spelling_uses_existing_bound_target(deleted, requested, expected):
    source, target = 'src/plugins/hooks.ts', 'src/plugins/types.ts'
    graph = ChangeGraph(request(), [dict(path=source, changeType='deleted' if deleted else 'modified')])
    which = 'base' if deleted else 'proposed'
    fact = dict(evidenceId='import-proof', relation='resolves-import', origin=dict(path=source),
        attributes=dict(specifier='./types.js'), relatedPaths=[source, target])
    graph.relations[which]['import-proof'] = fact
    result = graph.resolve_import_path(source, requested)
    assert result == (dict(path=target, snapshot=which, evidenceIds=['import-proof']) if expected else None)
    if expected:
        graph.relations[which]['ambiguous'] = {**fact, 'evidenceId': 'ambiguous', 'relatedPaths': [source, 'src/plugins/other.ts']}
        assert graph.resolve_import_path(source, requested) is None


@pytest.mark.asyncio
async def test_scan_normalizes_equivalent_relative_import_before_repository_path_queries(monkeypatch):
    source, target, requested = 'src/plugins/hooks.ts', 'src/plugins/types.ts', '../plugins/types.js'
    fact = {**relation('import-proof', unit(source, 'hooks'), unit(target, 'types')),
        'relation': 'resolves-import', 'attributes': {'specifier': './types.js'}}
    async def query(which, **kw):
        assert kw['target'] != requested, 'Relative spelling must resolve before repository-root graph acquisition'
        return dict(snapshot=snapshot(which), results=([unit(kw['target'], kw['target'])]
            if kw['pattern'] == 'file_summary' else [fact] if which == 'proposed' else []))
    async def base(**kw): return await query('base', **kw)
    async def proposed(**kw): return await query('proposed', **kw)
    response = dict(summary='Extends hook types.', files=[dict(path=source, summary='Adds hook types.')],
        changeAreas=[dict(title='Hooks', paths=[source], changes=['Adds hook types.'])],
        relationships=[dict(source=source, target=requested, relationship='imports', evidence='Adds types to the existing import.')])
    raw = f'diff --git a/{source} b/{source}\n--- a/{source}\n+++ b/{source}\n@@ -12 +12 @@\n-old\n+new\n'
    monkeypatch.setattr(scan, 'supports_structured_output', lambda _: False)
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content=json.dumps(response))))
    result = await scan.scan_pr_changes(llm, request(rawDiff=raw, changedFiles=[source]),
        rag_client=SimpleNamespace(query_code_graph=base, query_review_graph=proposed))
    assert result['status'] == result['graph']['coverage']['status'] == 'complete', result['diagnostics']
    assert len(result['relationships']) == 1 and result['relationships'][0]['target'] == target
    assert requested in result['relationships'][0]['evidence'] and 'import-proof' in result['relationships'][0]['evidence']
    assert result['relationships'][0]['provenance'] == 'model_inferred'


@pytest.mark.asyncio
@pytest.mark.parametrize('availability', ['disabled', 'no-client', 'no-binding', 'unsupported', 'mixed'])
@pytest.mark.parametrize('target', ['virtual:unknown/module', '@uncovered/framework', 'generated/unknown.code'])
async def test_optional_graph_absence_omits_unproved_links_without_losing_reviews(monkeypatch, availability, target):
    paths = ['src/service.uncovered', 'src/consumer.uncovered']
    calls = []
    async def query(which, **kw):
        calls.append(kw)
        assert availability in {'unsupported', 'mixed'}, 'Absent graph must not be queried'
        values = []
        if availability == 'mixed' and kw['target'] == paths[0]:
            values = ([unit(paths[0], 'service', 1, 2)] if kw['pattern'] == 'file_summary' else
                      [relation('known', unit(paths[0], 'service', 1, 2), unit('known.py', 'helper'), line=1)])
        return dict(snapshot=snapshot(which), results=values, fileCoverage=dict(state='unknown'))
    async def base(**kw): return await query('base', **kw)
    async def proposed(**kw): return await query('proposed', **kw)
    raw = ''.join(f'diff --git a/{p} b/{p}\n--- a/{p}\n+++ b/{p}\n@@ -1 +1 @@\n-old\n+new\n' for p in paths)
    req = request(rawDiff=raw, changedFiles=paths, ragEnabled=availability != 'disabled')
    if availability == 'no-binding':
        req.ragBaseGenerationManifestSha256 = None
        req.ragReviewGenerationManifestSha256 = None
    client = None if availability == 'no-client' else SimpleNamespace(query_code_graph=base, query_review_graph=proposed)
    payload = dict(summary='Changes both implementations.',
        files={p: dict(summary='Handles the new input.', changes=['Rejects empty input.']) for p in paths},
        relationships=[dict(source=paths[0], target=target, relationship='uses', evidence='Unverified inferred target.')])
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content=json.dumps(payload))))
    monkeypatch.setattr(scan, 'supports_structured_output', lambda _: False)
    result = await scan.scan_pr_changes(llm, req, rag_client=client)
    assert result['status'] == result['semanticStatus'] == 'complete'
    assert not result['diagnostics'] and not result['relationships']
    assert len(result['omittedRelationships']) == 1 and result['omittedRelationships'][0]['target'] == target
    assert {f['path'] for f in result['files'] if f['scanStatus'] == 'scanned'} == set(paths)
    assert all(f['summary'] == 'Handles the new input.' for f in result['files'])
    coverage = result['graph']['coverage']
    assert coverage['status'] == ('partial' if availability == 'mixed' else 'unavailable')
    assert not coverage['diagnostics']
    if availability == 'mixed':
        assert result['graph']['edges'], 'Available graph evidence must remain usable'
    else:
        assert result['graph']['nodes'] == result['graph']['edges'] == []
    context = scan.change_summary_context(result)
    assert 'omittedRelationshipCount' in context and 'Unverified inferred target.' not in context
    assert 'Missing optional graph coverage is not evidence' in context
    llm.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_graph_does_not_hide_a_missing_file_description(monkeypatch):
    paths = ['one.uncovered', 'two.uncovered']
    raw = ''.join(f'diff --git a/{p} b/{p}\n--- a/{p}\n+++ b/{p}\n@@ -1 +1 @@\n-old\n+new\n' for p in paths)
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content=json.dumps(dict(
        summary='Only one file described.', files={paths[0]: dict(summary='Adds validation.')})))))
    monkeypatch.setattr(scan, 'supports_structured_output', lambda _: False)
    result = await scan.scan_pr_changes(llm, request(rawDiff=raw, changedFiles=paths), rag_client=None)
    assert result['status'] == result['semanticStatus'] == 'degraded'
    assert result['graph']['coverage']['status'] == 'unavailable'
    assert any(paths[1] in reason for reason in result['diagnostics'])
    assert result['files'][1]['scanStatus'] == 'degraded'
