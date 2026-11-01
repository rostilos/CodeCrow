from pathlib import Path

import pytest

from codecrow_plugins import FileArtifact, PluginCatalog, PluginRuntime, ProjectSelector, RepositoryFacts


@pytest.fixture
def catalog():
    return PluginCatalog.discover(Path(__file__).resolve().parents[3])


@pytest.mark.parametrize('extension', ['js', 'mjs', 'cjs', 'ts', 'mts', 'cts'])
@pytest.mark.parametrize('directory', ['', 'packages/admin/'])
def test_lingui_patterns_keep_source_and_default_root(catalog, extension, directory):
    path = directory + 'lingui.config.' + extension
    annotation = ': LinguiConfig' if extension in {'ts', 'mts', 'cts'} else ''
    source = f'''const config{annotation} = {{
        catalogs: [{{
            include: ["<rootDir>/src/**/*.{{ts,tsx}}"],
            exclude: ["./generated/**"],
            path: "<rootDir>/src/locales/{{locale}}/messages",
        }}],
    }};
    export default config;'''
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(revision='head', paths=tuple(sorted((path, 'package.json')))))
    assert 'localization' in capabilities.repository_plugins
    facts, diagnostics = PluginRuntime(catalog).graph_facts(FileArtifact(path, source), capabilities)
    assert not diagnostics
    patterns = [f for f in facts if f.kind == 'localization-file-pattern']
    assert {f.target for f in patterns} == {
        directory + 'src/**/*.{ts,tsx}', './generated/**', directory + 'src/locales/{locale}/messages'}
    assert {f.line for f in patterns} == {3, 4, 5}
    assert all(f.source == f.path == path and not f.related_paths and
               f.contributing_plugin_ids == ('localization',) for f in patterns)
    assert {dict(f.attributes)['targetSpecifier'] for f in patterns} == {
        '<rootDir>/src/**/*.{ts,tsx}', './generated/**', '<rootDir>/src/locales/{locale}/messages'}


@pytest.mark.parametrize('pattern,expected', [
    ('"catalogs/@lang/messages.po"', {'catalogs/@lang/messages.po'}),
    ('{source: "source/@path", locales: "translated/@lang/@path"}', {'source/@path', 'translated/@lang/@path'}),
])
def test_lunaria_preserves_literal_parameters_without_expansion(catalog, pattern, expected):
    source = '''import {defineConfig as configure} from "@lunariajs/core/config";
    export default configure({files: [{include: ["source/en/messages.po"],
        exclude: ["drafts/**"], pattern: PATTERN, type: "dictionary"}]});'''.replace('PATTERN', pattern)
    outcome = catalog.implementation('localization').index_file(FileArtifact('lunaria.config.ts', source))
    assert outcome.status.value == 'handled'
    assert {f.target for f in outcome.value} == expected | {'source/en/messages.po', 'drafts/**'}
    assert all(not f.related_paths and f.relation == 'declares-file-pattern' for f in outcome.value)


@pytest.mark.parametrize('source', [
    'const unused = {catalogs:[{include:["not-exported/**"]}]};',
    'export default factory({catalogs:[{include:["not-known/**"]}]});',
    'import {defineConfig} from "unrelated"; export default defineConfig({catalogs:[{include:["wrong/**"]}]});',
    'const config={catalogs:[{include:["escaped/**"]}]}; mutate(config); export default config;',
    'const config={catalogs:[{include:["mutated/**"]}]}; config.catalogs=[]; export default config;',
    'export default config; const config={catalogs:[{include:["before-declaration/**"]}]};',
    'export default {catalogs:[{include:["duplicate/**"]}],catalogs:[]};',
    'export default {catalogs:[{include:["spread/**"]}],...other};',
    'export default {catalogs:[{include:["computed/**"],[key]:[]}]};',
    'export default {catalogs:[{include:[`dynamic-${name}`,factory(),"escaped\\x2a"]}]};',
    '/* export default {catalogs:[{include:["comment/**"]}]}; */',
    'module.exports={catalogs:[{include:["cjs/**"]}]};',
])
def test_unknown_exports_and_dynamic_values_do_not_invent_patterns(catalog, source):
    outcome = catalog.implementation('localization').index_file(FileArtifact('lingui.config.ts', source))
    assert outcome.status.value == 'handled' and not outcome.value


@pytest.mark.parametrize('root', ['"custom"', 'resolveRoot()'])
def test_explicit_root_and_process_cwd_are_not_guessed(catalog, root):
    source = 'export default {rootDir: ROOT, catalogs:[{include:["<rootDir>/src/**", "relative/**"]}]};'.replace('ROOT', root)
    outcome = catalog.implementation('localization').index_file(FileArtifact('nested/lingui.config.ts', source))
    assert {f.target for f in outcome.value} == {'<rootDir>/src/**', 'relative/**'}


@pytest.mark.parametrize('module', ['@lingui/cli', '@lingui/conf'])
def test_lingui_imported_wrapper_and_type_assertion(catalog, module):
    source = ('import {defineConfig as configure} from "' + module + '";\n'
              'const config = configure({catalogs:[{include:["src/**"]}]}) satisfies LinguiConfig;\n'
              'export default config;')
    outcome = catalog.implementation('localization').index_file(FileArtifact('lingui.config.ts', source))
    assert outcome.status.value == 'handled' and [f.target for f in outcome.value] == ['src/**']


def test_deleted_unrelated_and_malformed_configuration(catalog):
    plugin = catalog.implementation('localization')
    source = 'export default {catalogs:[{include:["src/**"]}]};'
    for artifact in [FileArtifact('unrelated.ts', source), FileArtifact('lingui.config.ts', source, deleted=True)]:
        assert plugin.index_file(artifact).status.value == 'abstained'
    outcome = plugin.index_file(FileArtifact('lingui.config.ts', 'export default {'))
    assert outcome.status.value == 'failed' and outcome.diagnostic.recoverable
