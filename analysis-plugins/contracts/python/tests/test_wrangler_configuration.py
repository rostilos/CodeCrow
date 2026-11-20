import json
from pathlib import Path

import pytest

from codecrow_plugins import FileArtifact, PluginCatalog, PluginRuntime, ProjectSelector, RepositoryFacts


@pytest.fixture
def catalog():
    return PluginCatalog.discover(Path(__file__).resolve().parents[3])


@pytest.mark.parametrize('suffix,content', [
    ('json', '{"assets":{"directory":"./dist"}}'),
    ('jsonc', '\ufeff{/* comment */ "assets": {"directory": "./dist",}, // comment\n}'),
    ('toml', '[assets]\ndirectory = "./dist"\n'),
])
@pytest.mark.parametrize('parent', ['', 'i18n/'])
def test_assets_directory_is_source_bound_without_language_or_existing_output(catalog, suffix, content, parent):
    path = parent + 'wrangler.' + suffix
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(revision='head', paths=(path,)))
    assert 'wrangler' in capabilities.repository_plugins
    facts, diagnostics = PluginRuntime(catalog).graph_facts(FileArtifact(path, content), capabilities)
    assert not diagnostics and len(facts) == 1
    fact, = facts
    assert fact.source == fact.path == path and fact.target == parent + 'dist'
    assert fact.relation == 'declares-directory' and fact.kind == 'wrangler-assets-directory'
    assert fact.contributing_plugin_ids == ('wrangler',) and not fact.related_paths
    assert fact.line == 1 and dict(fact.attributes) == {
        'configurationPointer': '/assets/directory', 'originScope': 'configuration-file',
        'targetResolutionProven': 'false', 'targetSpecifier': './dist'}


@pytest.mark.parametrize('suffix,content', [
    ('jsonc', '{"assets":{"directory":"./dist"},"env":{"prod/a~b":{"assets":{"directory":"./public"}},"inherited":{}}}'),
    ('toml', '[assets]\ndirectory="./dist"\n[env."prod/a~b".assets]\ndirectory="./public"\n[env.inherited]\n'),
])
def test_named_environment_declarations_remain_separate_without_selecting_or_inheriting(catalog, suffix, content):
    result = catalog.implementation('wrangler').index_file(FileArtifact('app/wrangler.' + suffix, content))
    assert result.status.value == 'handled' and len(result.value) == 2
    assert {(f.target, dict(f.attributes)['configurationPointer']) for f in result.value} == {
        ('app/dist', '/assets/directory'), ('app/public', '/env/prod~1a~0b/assets/directory')}


@pytest.mark.parametrize('directory,expected', [
    ('../public/', 'public'), ('./dist/assets', 'app/dist/assets'), ('./', 'app'),
    ('/external/assets', '/external/assets'), ('C:\\assets', 'C:/assets'),
    ('./assets//images', 'app/assets/images'), ('./assets/*literal*', 'app/assets/*literal*'),
    ('./assets/*not a comment*/', 'app/assets/*not a comment*'),
])
def test_literal_directory_spelling_is_preserved_without_glob_or_environment_expansion(catalog, directory, expected):
    result = catalog.implementation('wrangler').index_file(FileArtifact('app/wrangler.jsonc',
        json.dumps({'assets': {'directory': directory}})))
    assert result.status.value == 'handled'
    fact, = result.value
    assert fact.target == expected and dict(fact.attributes)['targetSpecifier'] == directory


@pytest.mark.parametrize('source', ['{}', '{"assets":{"binding":"ASSETS"}}',
    '{"vars":{"assets":{"directory":"./not-assets"}}}',
    '{"note":"\\\"assets\\\":{\\\"directory\\\":\\\"./not-assets\\\"}"}',
    '{/* "assets":{"directory":"./not-assets"} */}',
])
def test_only_explicit_assets_directory_positions_contribute(catalog, source):
    result = catalog.implementation('wrangler').index_file(FileArtifact('wrangler.jsonc', source))
    assert result.status.value == 'handled' and not result.value


@pytest.mark.parametrize('path,source', [
    ('wrangler.jsonc', '['), ('wrangler.json', '[]'), ('wrangler.toml', '[assets'),
    ('wrangler.json', '{"assets":false}'), ('wrangler.json', '{"assets":{"directory":42}}'),
    ('wrangler.json', '{"assets":{"directory":""}}'), ('wrangler.json', '{"env":[]}'),
    ('wrangler.json', '{"env":{"bad":false}}'),
])
def test_malformed_configuration_has_a_recoverable_diagnostic(catalog, path, source):
    result = catalog.implementation('wrangler').index_file(FileArtifact(path, source))
    assert result.status.value == 'failed' and result.diagnostic.recoverable


@pytest.mark.parametrize('path', ['package.json', 'wrangler.config.js', '.wrangler/deploy/config.json'])
def test_unrelated_and_deleted_configurations_abstain(catalog, path):
    plugin = catalog.implementation('wrangler')
    assert plugin.index_file(FileArtifact(path, '{}')).status.value == 'abstained'
    assert plugin.index_file(FileArtifact('wrangler.jsonc', '{}', deleted=True)).status.value == 'abstained'
