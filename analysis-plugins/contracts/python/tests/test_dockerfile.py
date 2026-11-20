from pathlib import Path

import pytest

from codecrow_plugins import FileArtifact, PluginCatalog, PluginRuntime, ProjectSelector, RepositoryFacts


@pytest.fixture
def catalog():
    return PluginCatalog.discover(Path(__file__).resolve().parents[3])


@pytest.mark.parametrize('path', ['Dockerfile', 'Dockerfile.prod', 'app.Dockerfile',
    'app/Dockerfile', 'app/Dockerfile.prod', 'app/build.Dockerfile'])
def test_literal_images_are_source_bound_without_language_or_local_resolution(catalog, path):
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(revision='head', paths=(path,)))
    assert 'dockerfile' in capabilities.repository_plugins
    facts, diagnostics = PluginRuntime(catalog).graph_facts(FileArtifact(path,
        '# build\nFROM node:22-slim AS base\nFROM base AS deps\nFROM deps AS build\nFROM node:22-slim\n'), capabilities)
    assert not diagnostics and len(facts) == 2
    assert {f.line for f in facts} == {2, 5}
    for fact in facts:
        assert fact.path == fact.source == path and fact.target == 'node:22-slim'
        assert fact.kind == 'docker-base-image' and fact.relation == 'configures-module'
        assert fact.contributing_plugin_ids == ('dockerfile',) and not fact.related_paths
        assert dict(fact.attributes) == {'referenceKind': 'dockerfile-from',
            'targetResolutionProven': 'false', 'targetSpecifier': 'node:22-slim'}


@pytest.mark.parametrize('source,expected', [
    ('  from\t--platform=$BUILDPLATFORM registry.example:5000/team/app@sha256:abcdef AS builder', [(1, 'registry.example:5000/team/app@sha256:abcdef')]),
    ('FROM alpine AS base\nFROM BASE AS next\nFROM next', [(1, 'alpine')]),
    ('ARG BASE=node:22\nFROM ${BASE} AS base\nFROM base\nFROM scratch', []),
    ('FROM alpine\\\n AS base\nFROM base', [(1, 'alpine')]),
    ('FROM \\\n# comment\n\n  alpine:3', [(1, 'alpine:3')]),
    ('# escape=`\nFROM `\n node:22\nRUN echo a`\n FROM fake:1\nFROM alpine', [(2, 'node:22'), (6, 'alpine')]),
    ('# comment\n# escape=`\nFROM alpine\nRUN echo hi \\\n FROM fake:1\nFROM node', [(3, 'alpine'), (6, 'node')]),
    ('FROM alpine\nRUN echo FROM fake:1\n# FROM fake:2\nLABEL base="FROM fake:3"', [(1, 'alpine')]),
    ('FROM alpine\nCOPY --from=build /app /app\nRUN --mount=from=external echo hi', [(1, 'alpine')]),
    ('\ufeffFROM alpine\r\nFROM node:22\r\n', [(1, 'alpine'), (2, 'node:22')]),
])
def test_explicit_from_semantics(catalog, source, expected):
    outcome = catalog.implementation('dockerfile').index_file(FileArtifact('Dockerfile', source))
    assert outcome.status.value == 'handled', outcome
    assert sorted((f.line, f.target) for f in outcome.value) == expected


@pytest.mark.parametrize('instruction', ['RUN', 'COPY', 'ADD', 'ONBUILD RUN'])
@pytest.mark.parametrize('delimiter', ['EOF', "'EOF'", '"EOF"', 'E"O"F'])
def test_from_inside_heredoc_is_never_an_image(catalog, instruction, delimiter):
    source = f'FROM alpine\n{instruction} <<{delimiter} /file\nFROM false:image\nEOF\nFROM node:22\n'
    outcome = catalog.implementation('dockerfile').index_file(FileArtifact('Dockerfile', source))
    assert outcome.status.value == 'handled', outcome
    assert {(f.line, f.target) for f in outcome.value} == {(1, 'alpine'), (5, 'node:22')}


@pytest.mark.parametrize('source,expected', [
    ('RUN <<-EOF\n\tFROM fake\n\tEOF\nFROM alpine', [(4, 'alpine')]),
    ('RUN <<ONE cat && <<"TWO" cat\nFROM fake\nONE\nFROM fake2\nTWO\nFROM alpine', [(6, 'alpine')]),
    ('RUN echo "<<EOF"\nFROM alpine', [(2, 'alpine')]),
    ("RUN echo '<<EOF'\nFROM alpine", [(2, 'alpine')]),
    ('RUN ["echo", "<<EOF"]\nFROM alpine', [(2, 'alpine')]),
    ('RUN echo \\<\\<EOF\nFROM alpine', [(2, 'alpine')]),
])
def test_heredoc_boundaries_and_quoted_operators(catalog, source, expected):
    outcome = catalog.implementation('dockerfile').index_file(FileArtifact('Dockerfile', source))
    assert outcome.status.value == 'handled', outcome
    assert sorted((f.line, f.target) for f in outcome.value) == expected


@pytest.mark.parametrize('source', ['FROM', 'FROM image # inline comment', 'FROM image not AS stage',
    'FROM "image"', 'FROM image\\', 'RUN <<EOF\nFROM fake', '# escape=x\nFROM image'])
def test_unreadable_declarations_have_recoverable_diagnostics(catalog, source):
    outcome = catalog.implementation('dockerfile').index_file(FileArtifact('Dockerfile', source))
    assert outcome.status.value == 'failed' and outcome.diagnostic.recoverable


@pytest.mark.parametrize('path', ['docker-compose.yml', 'package.json', 'Dockerfileish', 'Dockerfile.dockerignore', 'src/code.py'])
def test_unrelated_or_deleted_files_abstain(catalog, path):
    plugin = catalog.implementation('dockerfile')
    assert plugin.index_file(FileArtifact(path, 'FROM image')).status.value == 'abstained'
    assert plugin.index_file(FileArtifact('Dockerfile', 'FROM image', deleted=True)).status.value == 'abstained'
