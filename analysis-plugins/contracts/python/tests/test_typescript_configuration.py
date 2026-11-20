import json
from pathlib import Path
import posixpath

import pytest

from codecrow_plugins import FileArtifact, PluginCatalog, PluginRuntime, ProjectSelector, RepositoryFacts


@pytest.fixture
def catalog():
    return PluginCatalog.discover(Path(__file__).resolve().parents[3])


@pytest.mark.parametrize('path', ['tsconfig.json', 'packages/admin/tsconfig.json', 'config/tsconfig.browser.json'])
def test_local_types_are_source_bound_configuration_facts(catalog, path):
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(revision='head', paths=(path,)))
    assert 'typescript' in capabilities.repository_plugins
    content = '''\ufeff{
      // Leave strings and their comment-like text intact.
      "extends": "./base.json",
      "description": "https://example.test/*literal*/ //text,]",
      "compilerOptions": { /* local declarations only */
        "types": ["vite/client", "node", "escaped\\u002fname",],
      },
      "nested": {"compilerOptions": {"types": ["not-local"]}},
    }'''
    facts, diagnostics = PluginRuntime(catalog).graph_facts(FileArtifact(path, content), capabilities)
    assert not diagnostics
    assert len(facts) == 3 and {fact.target for fact in facts} == {'vite/client', 'node', 'escaped/name'}
    assert all(fact.source == fact.path == path and fact.relation == 'configures-module'
               and fact.kind == 'typescript-configured-type' and not fact.related_paths for fact in facts)
    assert {dict(fact.attributes)['jsonPointer'] for fact in facts} == {
        '/compilerOptions/types/0', '/compilerOptions/types/1', '/compilerOptions/types/2'}
    assert all(fact.contributing_plugin_ids == ('typescript',) for fact in facts)


@pytest.mark.parametrize('path', ['tsconfig.json', 'packages/core/tsconfig.json', 'config/tsconfig.browser.json'])
def test_local_file_patterns_preserve_declarations_without_resolving_files(catalog, path):
    declarations = {
        'include': ['src/**/*.ts', '../shared/*.ts', './src', r'src\**\*.tsx', '/outside/*.ts', r'C:\outside\*.ts'],
        'exclude': ['src/auth/providers/*-admin.tsx', 'node_modules', ''],
    }
    content = json.dumps({**declarations, 'extends': './base.json',
        'compilerOptions': {'types': ['node']},
        'typeAcquisition': {'include': ['not-a-file']}, 'nested': {'exclude': ['not-local']}})
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(revision='head', paths=(path,)))
    facts, diagnostics = PluginRuntime(catalog).graph_facts(FileArtifact(path, content), capabilities)
    assert not diagnostics
    assert [f.target for f in facts if f.relation == 'configures-module'] == ['node']
    patterns = [f for f in facts if f.relation == 'declares-file-pattern']
    assert len(patterns) == 8
    for fact in patterns:
        attributes = dict(fact.attributes)
        option, index = attributes['jsonPointer'].strip('/').split('/')
        literal = declarations[option][int(index)]
        assert attributes == dict(jsonPointer=f'/{option}/{index}', option=option, targetSpecifier=literal)
        assert fact.source == fact.path == path and fact.kind == 'typescript-file-pattern'
        assert not fact.related_paths and fact.contributing_plugin_ids == ('typescript',)
    targets = {dict(f.attributes)['targetSpecifier']: f.target for f in patterns}
    assert targets['src/auth/providers/*-admin.tsx'] == posixpath.join(posixpath.dirname(path), 'src/auth/providers/*-admin.tsx')
    assert targets['../shared/*.ts'] == posixpath.normpath(posixpath.join(posixpath.dirname(path), '../shared/*.ts'))
    assert targets['./src'] == posixpath.join(posixpath.dirname(path), 'src')
    assert targets[r'src\**\*.tsx'] == posixpath.join(posixpath.dirname(path), 'src/**/*.tsx')
    assert targets['/outside/*.ts'] == '/outside/*.ts'
    assert targets[r'C:\outside\*.ts'] == 'C:/outside/*.ts'


@pytest.mark.parametrize('content', [
    '{}', '{"extends":"./base.json"}', '{"compilerOptions":{"types":[]}}',
    '{"compilerOptions":{"typeRoots":["./types"],"lib":["DOM"]}}',
    '{"types":["not-compiler-types"]}', '{"nested":{"compilerOptions":{"types":["not-local"]}}}',
    '{"include":[],"exclude":[]}', '{"typeAcquisition":{"include":["jquery"],"exclude":["jest"]}}',
    '{"nested":{"include":["not-local"],"exclude":["not-local"]}}',
])
def test_other_fields_and_inherited_types_are_not_guessed(catalog, content):
    outcome = catalog.implementation('typescript').index_file(FileArtifact('tsconfig.json', content))
    assert outcome.status.value == 'handled' and outcome.value == ()


@pytest.mark.parametrize('content', [
    '[]', '{broken', '{"compilerOptions":null}', '{"compilerOptions":{"types":"vite/client"}}',
    '{"compilerOptions":{"types":["vite/client",42]}}',
    '{"compilerOptions":{"types":["vite/client"]}} /* unfinished',
    '{"compilerOptions":{"types":["unterminated]}}',
    '{"include":null}', '{"exclude":"src"}', '{"exclude":["src",null]}',
])
def test_malformed_configuration_is_an_observable_optional_failure(catalog, content):
    outcome = catalog.implementation('typescript').index_file(FileArtifact('tsconfig.json', content))
    assert outcome.status.value == 'failed' and outcome.value is None
    assert outcome.diagnostic.recoverable and outcome.diagnostic.path == 'tsconfig.json'


def test_unrelated_and_deleted_configs_do_not_contribute_types(catalog):
    plugin = catalog.implementation('typescript')
    content = '{"compilerOptions":{"types":["vite/client"]}}'
    assert plugin.index_file(FileArtifact('package.json', content)).status.value == 'abstained'
    assert plugin.index_file(FileArtifact('tsconfig.json', content, deleted=True)).status.value == 'abstained'
    outcome = plugin.index_file(FileArtifact('tsconfig.json', '{"compilerOptions":{"types":["old"],"types":["new"]}}'))
    assert [fact.target for fact in outcome.value] == ['new']
