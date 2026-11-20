from pathlib import Path

import pytest

from codecrow_plugins import FileArtifact, PluginCatalog, PluginRuntime, ProjectSelector, RepositoryFacts


@pytest.fixture
def catalog():
    return PluginCatalog.discover(Path(__file__).resolve().parents[3])


@pytest.mark.parametrize('extension', ['yml', 'yaml'])
@pytest.mark.parametrize('reference', ['actions/checkout@v4', 'owner/repo/sub/action@main', 'owner/repo@' + 'a' * 40])
def test_workflow_action_is_external_with_pin_and_source_without_language_dependency(catalog, extension, reference):
    path = '.github/workflows/ci.' + extension
    content = f'on: push\njobs:\n  build:\n    steps:\n      - uses: {reference}\n'
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(revision='head', paths=(path,)))
    assert 'github-actions' in capabilities.repository_plugins
    facts, diagnostics = PluginRuntime(catalog).graph_facts(FileArtifact(path, content), capabilities)
    assert not diagnostics and len(facts) == 1
    fact, = facts
    assert fact.kind == 'github-action-reference' and fact.relation == 'configures-module'
    assert fact.source == fact.path == path and fact.target == reference.split('@')[0] and fact.line == 5
    assert not fact.related_paths and fact.contributing_plugin_ids == ('github-actions',)
    assert dict(fact.attributes) == dict(gitRef=reference.split('@')[1], referenceKind='action', targetResolutionProven='false',
        targetSpecifier=reference, yamlPointer='/jobs/build/steps/0/uses')


def test_reusable_workflow_and_action_steps_are_distinct_declarations(catalog):
    source = '''jobs:
  reuse:
    uses: owner/repo/.github/workflows/build.yml@release/next
  run:
    steps:
      - uses: owner/repo@v1
      - uses: owner/repo@v2
'''
    result = catalog.implementation('github-actions').index_file(FileArtifact('.github/workflows/ci.yml', source))
    assert result.status.value == 'handled' and len(result.value) == 3
    assert {(f.target, dict(f.attributes)['gitRef'], dict(f.attributes)['referenceKind'], f.line) for f in result.value} == {
        ('owner/repo/.github/workflows/build.yml', 'release/next', 'reusable-workflow', 3),
        ('owner/repo', 'v1', 'action', 6), ('owner/repo', 'v2', 'action', 7)}


def test_scalar_alias_retains_usage_line_without_evaluating_expressions(catalog):
    source = '''env:
  ACTION: &action owner/repo@v1
jobs:
  build:
    steps:
      - uses: *action
'''
    result = catalog.implementation('github-actions').index_file(FileArtifact('.github/workflows/ci.yml', source))
    assert result.status.value == 'handled'
    assert len(result.value) == 1 and result.value[0].line == 6


@pytest.mark.parametrize('reference', ['./.github/actions/local', '$/local',
    'owner/repo', 'owner/repo@', 'repo@main', '${{ inputs.action }}', 'owner/repo@${{ inputs.ref }}',
    'owner/repo@branch@other', 'https://host/owner/repo@main', 'owner/../repo@main', 'true', '[]'])
def test_unsupported_or_dynamic_references_do_not_invent_module_bindings(catalog, reference):
    source = 'jobs:\n  build:\n    steps:\n      - uses: ' + reference + '\n'
    result = catalog.implementation('github-actions').index_file(FileArtifact('.github/workflows/ci.yml', source))
    assert result.status.value == 'handled' and not result.value


@pytest.mark.parametrize('source', [
    'env: {uses: "owner/not-a-step@main"}\n',
    'jobs:\n  build:\n    steps:\n      - run: "uses: owner/not-a-step@main"\n',
    'jobs:\n  build:\n    steps:\n      - run: echo hello\n        uses: owner/invalid-step@main\n',
    '# jobs: {build: {steps: [{uses: owner/comment@main}]}}\n',
])
def test_only_workflow_uses_positions_contribute(catalog, source):
    result = catalog.implementation('github-actions').index_file(FileArtifact('.github/workflows/ci.yml', source))
    assert result.status.value == 'handled' and not result.value


@pytest.mark.parametrize('source', ['[not-a-workflow]', 'jobs: [',
    'jobs: {build: {steps: [{uses: "a/b@main", uses: "c/d@main"}]}}',
    'jobs: {build: {steps: [{"<<": {uses: "a/b@main"}}]}}'])
def test_malformed_or_ambiguous_workflow_is_a_recoverable_diagnostic(catalog, source):
    result = catalog.implementation('github-actions').index_file(FileArtifact('.github/workflows/ci.yml', source))
    assert result.status.value == 'failed' and result.diagnostic.recoverable


@pytest.mark.parametrize('path', ['action.yml', 'example.yml', '.github/workflows/nested/ci.yml',
                                  'examples/.github/workflows/ci.yml'])
def test_unrelated_files_and_deleted_workflows_abstain(catalog, path):
    plugin = catalog.implementation('github-actions')
    assert plugin.index_file(FileArtifact(path, 'jobs: {}')).status.value == 'abstained'
    assert plugin.index_file(FileArtifact('.github/workflows/ci.yml', 'jobs: {}', deleted=True)).status.value == 'abstained'


@pytest.mark.parametrize('image', ['postgres:17', 'ghcr.io/owner/image:tag',
                                  'registry.example:5000/team/image@sha256:' + 'a' * 64, 'redis'])
@pytest.mark.parametrize('form,kind,pointer,line', [
    ('    container: {image}\n', 'job-container', '/jobs/build/container', 3),
    ('    container:\n      image: {image}\n', 'job-container', '/jobs/build/container/image', 4),
    ('    services:\n      db:\n        image: {image}\n', 'service-container', '/jobs/build/services/db/image', 5),
    ('    steps:\n      - uses: docker://{image}\n', 'container-action', '/jobs/build/steps/0/uses', 4),
])
def test_literal_container_images_retain_source_and_external_identity(catalog, image, form, kind, pointer, line):
    path = '.github/workflows/ci.yml'
    source = 'jobs:\n  build:\n' + form.format(image=image)
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(revision='head', paths=(path,)))
    facts, diagnostics = PluginRuntime(catalog).graph_facts(FileArtifact(path, source), capabilities)
    assert not diagnostics and len(facts) == 1
    fact, = facts
    assert fact.kind == 'github-container-image' and fact.relation == 'configures-module'
    assert fact.source == fact.path == path and fact.target == image and fact.line == line
    assert not fact.related_paths and fact.contributing_plugin_ids == ('github-actions',)
    assert dict(fact.attributes) == dict(referenceKind=kind, targetResolutionProven='false',
        targetSpecifier=('docker://' if kind == 'container-action' else '') + image, yamlPointer=pointer)


@pytest.mark.parametrize('image', ['""', 'true', '[]', '{}', '"${{ matrix.image }}"',
                                  '"postgres:${{ inputs.version }}"', '"invalid image"'])
def test_nonliteral_or_empty_images_do_not_invent_bindings(catalog, image):
    source = 'jobs:\n  build:\n    container:\n      image: ' + image + '\n    services:\n      db:\n        image: ' + image + '\n'
    result = catalog.implementation('github-actions').index_file(FileArtifact('.github/workflows/ci.yml', source))
    assert result.status.value == 'handled' and not result.value


def test_container_alias_and_escaped_keys_keep_declaration_origins(catalog):
    source = '''env:
  IMAGE: &image postgres:17
jobs:
  build/job:
    container: *image
    services:
      db~service:
        image: *image
'''
    result = catalog.implementation('github-actions').index_file(FileArtifact('.github/workflows/ci.yml', source))
    assert result.status.value == 'handled' and len(result.value) == 2
    assert {(f.line, dict(f.attributes)['yamlPointer']) for f in result.value} == {
        (5, '/jobs/build~1job/container'), (8, '/jobs/build~1job/services/db~0service/image')}


def test_unrelated_image_fields_and_docker_workflow_uses_do_not_contribute(catalog):
    source = '''env: {image: postgres:17}
jobs:
  reuse:
    uses: docker://postgres:17
  build:
    steps:
      - run: 'echo image: postgres:17'
        image: postgres:17
      - uses: owner/action@v1
        with: {image: postgres:17}
'''
    result = catalog.implementation('github-actions').index_file(FileArtifact('.github/workflows/ci.yml', source))
    assert result.status.value == 'handled' and len(result.value) == 1
    assert result.value[0].kind == 'github-action-reference'
