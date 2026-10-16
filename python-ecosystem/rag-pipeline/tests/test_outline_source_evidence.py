"""Captured Cal11059 outlines must stay navigation, never literal source."""
import hashlib
import json
from pathlib import Path

import pytest

from rag_pipeline.core.documents import Document, TextNode
from rag_pipeline.core.index_manager.file_indexer import FileIndexer
from rag_pipeline.core.review_evidence import _bounded_source_window, _source_detail_for_manifest
from rag_pipeline.core.review_graph_tools import get_review_structural_unit, minimal_review_context
from rag_pipeline.core.splitter import ASTCodeSplitter
from rag_pipeline.core.structural_store import StructuralGenerationStore, StructuralGraphReader, StructuralGraphWriter


FIXTURE = json.loads((Path(__file__).parent / 'fixtures/cal11059-outline-source.json').read_text())


@pytest.fixture
def graph(tmp_path):
    store = StructuralGenerationStore(tmp_path / 'structural')
    connection = store.initialize(store.pending_paths('outline-source'))
    writer = StructuralGraphWriter(connection)
    reader = StructuralGraphReader(connection, {
        'branch': 'main',
        'repository_revision': FIXTURE['provenance']['public_proposed_commit'],
        'generation_manifest_sha256': FIXTURE['provenance']['generation_manifest_sha256'],
    })
    yield connection, writer, reader
    connection.close()


def _add_captured_unit(writer, unit):
    unit_id = writer.add_unit(TextNode(text=unit['content'], metadata=unit['metadata']))
    assert unit_id == unit['unit_id']
    return unit_id


def test_captured_outline_stays_navigable_but_only_literal_declarations_are_source(graph):
    connection, writer, reader = graph
    assert hashlib.sha256(FIXTURE['source'].encode()).hexdigest() == FIXTURE['provenance']['head_source_sha256']
    for unit in FIXTURE['units']:
        _add_captured_unit(writer, unit)
    outline = next(unit for unit in FIXTURE['units'] if unit['kind'] == 'simplified')
    assert outline['content'] not in FIXTURE['source']
    assert outline['end_line'] == 57 and len(outline['content'].splitlines()) == 19

    result = get_review_structural_unit(reader, unit_id=outline['unit_id'])
    assert result['status'] == 'ready'
    assert result['sourceEvidence'] is False
    assert result['unit']['kind'] == 'simplified'
    assert 'content' not in result['unit'] and 'contentWindow' not in result['unit']
    assert outline['unit_id'] in {unit['unitId'] for unit in reader.search_units('callback.ts')}

    context = minimal_review_context(reader, focus_paths=[FIXTURE['provenance']['file']],
                                     question='OAuth callback', include_source=True, detail_level='standard')
    assert outline['unit_id'] in {node['unitId'] for node in context['nodes']}
    assert len(context['sourceWindows']) == 2
    assert not any(node.get('sourceEvidenceId') for node in context['nodes'] if node['unitId'] == outline['unit_id'])
    for window in context['sourceWindows']:
        assert window['unitId'] != outline['unit_id']
        lines = FIXTURE['source'].splitlines()
        assert window['content'] in '\n'.join(lines[window['startLine'] - 1:window['endLine']])

    # Existing same-file source ordering should bypass the encompassing outline.
    selected = _source_detail_for_manifest(reader, {'path': outline['path'], 'startLine': 22})
    assert selected['sourceEvidence'] is True
    assert selected['unit']['kind'] == 'function'
    assert selected['unit']['content'] in FIXTURE['source']
    assert connection.execute('SELECT content FROM units WHERE unit_id=?', (outline['unit_id'],)).fetchone()[0] == outline['content']


@pytest.mark.parametrize(('kind', 'metadata_json', 'source_evidence'), [
    ('simplified', '{}', False),
    ('source_unit', '{"content_type":"simplified_code"}', False),
    ('simplified', '{invalid legacy metadata', False),
    ('simplified', '[]', False),
    ('function', '', True),
    ('function', '{invalid legacy metadata', True),
    ('function', 'null', True),
])
def test_current_and_legacy_metadata_classification_is_optional(graph, kind, metadata_json, source_evidence):
    connection, writer, reader = graph
    # A marker in genuine source is not a reason to classify it as an outline.
    content = 'def example():\n    # Code for: an ordinary source comment\n    return 1\n'
    unit_id = writer.add_unit(TextNode(text=content, metadata={'path': 'example.py', 'node_type': 'function'}))
    connection.execute('UPDATE units SET kind=?, metadata_json=? WHERE unit_id=?', (kind, metadata_json, unit_id))
    result = reader.get_unit(unit_id)
    assert result['sourceEvidence'] is source_evidence
    if source_evidence:
        assert result['unit']['content'] == content
    else:
        assert 'content' not in result['unit']


def test_outline_only_context_remains_usable_without_cross_file_source_fallback(graph):
    _, writer, reader = graph
    outline = next(unit for unit in FIXTURE['units'] if unit['kind'] == 'simplified')
    _add_captured_unit(writer, outline)
    writer.add_unit(TextNode(text='def unrelated(): return True', metadata={'path': 'other.py', 'node_type': 'function'}))
    selected = _source_detail_for_manifest(reader, {'path': outline['path'], 'startLine': 1})
    assert selected is None
    context = minimal_review_context(reader, focus_paths=[outline['path']], question='callback', include_source=True)
    assert context['nodes'] and context['sourceWindows'] == []
    assert not any(node.get('sourceEvidenceId') for node in context['nodes'])
    # A custom resolver retaining navigation content must not bypass the authority flag.
    assert _bounded_source_window({'sourceEvidence': False, 'unit': {
        'unitId': outline['unit_id'], 'path': outline['path'], 'content': outline['content'],
    }}, remaining_characters=8000, changed_paths=set(), relation_evidence_ids=[]) is None


@pytest.mark.parametrize('record_type', ['source_unit', 'plugin_context'])
def test_literal_fallback_and_plugin_source_remain_available(graph, record_type):
    _, writer, reader = graph
    content = '// Code for: actual source comment\nexport const configured = true;\n'
    unit_id = writer.add_unit(TextNode(text=content, metadata={
        'path': 'config.ts', 'content_type': 'fallback', 'start_line': 1, 'end_line': 3,
    }), record_type=record_type)
    detail = reader.get_unit(unit_id)
    assert detail['sourceEvidence'] is True and detail['unit']['content'] == content
    window = _bounded_source_window(detail, remaining_characters=8000, changed_paths=set(), relation_evidence_ids=[])
    assert window['content'] == content and window['startLine'] == 1


def test_future_ingestion_preserves_outline_navigation_and_exact_source(graph):
    pytest.importorskip('tree_sitter_typescript')
    _, writer, reader = graph
    path = FIXTURE['provenance']['file']
    skipped = set()
    indexed = FileIndexer(None, ASTCodeSplitter(), None).index_document(
        Document(FIXTURE['source'], {'path': path, 'language': 'typescript'}), writer=writer,
        dispositions={}, capabilities=None, skipped_paths=skipped, check_cancelled=lambda: None,
    )
    assert indexed == 1 and not skipped
    units = reader.search_units('callback.ts', max_results=100)
    outlines = [unit for unit in units if unit['kind'] == 'simplified']
    assert outlines and all(not reader.get_unit(unit['unitId'])['sourceEvidence'] for unit in outlines)
    source_units = [reader.get_unit(unit['unitId']) for unit in units if unit['kind'] in {'function', 'interface'}]
    assert source_units and all(unit['sourceEvidence'] for unit in source_units)
    assert all(unit['unit']['content'] in FIXTURE['source'] for unit in source_units)
