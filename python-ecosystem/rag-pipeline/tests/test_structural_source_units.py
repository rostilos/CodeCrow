"""Structural ingestion keeps source ownership independent of embedding sizes."""
from pathlib import Path
import json

import pytest

from codecrow_plugins import PluginCatalog, PluginRuntime, ProjectSelector, RepositoryFacts
from rag_pipeline.core.documents import Document
from rag_pipeline.core.index_manager.file_indexer import FileIndexer
from rag_pipeline.core.splitter import ASTCodeSplitter
from rag_pipeline.core.structural_store import StructuralGenerationStore, StructuralGraphReader, StructuralGraphWriter


PLUGINS_ROOT = Path(__file__).resolve().parents[3] / "analysis-plugins"


def test_resolved_import_path_provides_source_without_fabricating_a_member_endpoint(tmp_path):
    from rag_pipeline.core.review_graph.source import _bounded_source_windows
    from tests.test_structural_store import _node, _pending_store, _reader
    _, _, connection, writer = _pending_store(tmp_path)
    try:
        target_id = writer.add_unit(_node('dependency.py', 'dependency.py', "def actual():\n    return 7\n"))
        relation_id = writer.add_relation(kind='python-call-resolution', source='caller::dynamic',
            relation='calls-resolved-target', target='dependency::dynamic', path='caller.py',
            line=3, origin='plugin', related_paths=('dependency.py',))
        writer.resolve_relations()
        reader = _reader(connection)
        relation = reader.get_relation(relation_id)
        assert relation['targetUnit'] is None
        windows, coverage = _bounded_source_windows(reader, [], [relation], changed_paths=['caller.py'],
            max_source_windows=1, max_source_characters=1000)
        assert len(windows) == 1 and windows[0]['unitId'] == target_id
        assert windows[0]['path'] == 'dependency.py' and 'return 7' in windows[0]['content']
        assert windows[0]['relationEvidenceIds'] == [relation_id]
        assert coverage['state'] == 'complete'
        assert reader.get_relation(relation_id)['targetUnit'] is None
    finally:
        connection.close()


def test_large_class_and_method_retain_exact_source_graph_ownership_and_tail_relations(tmp_path):
    pytest.importorskip("tree_sitter_python")
    path = "src/service.py"
    method = '    def execute(self, value):\n' + ''.join(
        f'        local_{index} = value\n' for index in range(800)
    ) + '        return validate_tail(value)'
    owner = 'class Service(BaseService):\n' + method
    source = owner + '\n'
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(revision="revision", paths=(path,)))
    splitter = ASTCodeSplitter(plugin_runtime=runtime)
    store = StructuralGenerationStore(tmp_path / "structural")
    connection = store.initialize(store.pending_paths("structural-test"))
    try:
        writer = StructuralGraphWriter(connection)
        skipped = set()
        indexer = FileIndexer(None, splitter, runtime)
        assert indexer.index_document(Document(source, {"path": path, "language": "python"}),
            writer=writer, dispositions={}, capabilities=capabilities,
            skipped_paths=skipped, check_cancelled=lambda: None) == 1
        writer.resolve_relations()
        assert not skipped
        rows = connection.execute("SELECT unit_id, name, content, metadata_json FROM units WHERE record_type='source_unit'").fetchall()
        class_row = next(row for row in rows if row['name'] == 'Service')
        method_row = next(row for row in rows if row['name'] == 'execute')
        assert len(class_row['content']) > 8_000
        assert class_row['content'] == owner
        assert method_row['content'] == method.lstrip()
        metadata = json.loads(method_row['metadata_json'])
        assert metadata['parent_class'] == 'Service'
        assert 'validate_tail' in metadata['calls']
        assert 'execute' in json.loads(class_row['metadata_json'])['methods']
        assert all(not json.loads(row['metadata_json']).get('is_fragment') for row in rows)
        for row in (class_row, method_row):
            assert connection.execute("SELECT count(*) FROM relations WHERE kind='CONTAINS' AND target_unit_id=?",
                                      (row['unit_id'],)).fetchone()[0] >= 1
        assert connection.execute("SELECT count(*) FROM relations WHERE kind='CALLS' AND source_unit_id=? AND target='validate_tail'",
                                  (method_row['unit_id'],)).fetchone()[0] == 1
        reader = StructuralGraphReader(connection, {"branch":"main", "repository_revision":"revision",
                                                   "generation_manifest_sha256":"a"*64})
        evidence = reader.get_unit(method_row['unit_id'])
        assert evidence['sourceEvidence'] is True
        assert evidence['unit']['content'] == method.lstrip()
        assert reader.search_units('validate_tail')
    finally:
        connection.close()


def test_fallback_file_is_one_complete_searchable_owned_source_unit(tmp_path):
    path = 'assets/unknown.source'
    source = '界' * 35_001 + '\nlast_contract_marker\n'
    store = StructuralGenerationStore(tmp_path / 'structural')
    connection = store.initialize(store.pending_paths('fallback-test'))
    try:
        writer = StructuralGraphWriter(connection)
        indexer = FileIndexer(None, ASTCodeSplitter(), None)
        skipped = set()
        assert indexer.index_document(Document(source, {'path':path, 'language':'text'}),
            writer=writer, dispositions={}, capabilities=None,
            skipped_paths=skipped, check_cancelled=lambda: None) == 1
        rows = connection.execute("SELECT unit_id, content FROM units WHERE record_type='source_unit'").fetchall()
        assert len(rows) == 1 and rows[0]['content'].encode('utf-8') == source.encode('utf-8')
        assert connection.execute("SELECT count(*) FROM relations WHERE kind='CONTAINS' AND target_unit_id=?",
                                  (rows[0]['unit_id'],)).fetchone()[0] == 1
        reader = StructuralGraphReader(connection, {"branch":"main", "repository_revision":"revision",
                                                   "generation_manifest_sha256":"a"*64})
        assert reader.search_units('last_contract_marker')
    finally:
        connection.close()


def test_swift_fallback_retains_source_without_inventing_semantic_relations(tmp_path):
    path = 'Shared/Strings.swift'
    source = 'import Foundation\nstruct Strings {\n    static func message() -> String { return localize("key") }\n}\n'
    catalog = PluginCatalog.discover(PLUGINS_ROOT)
    runtime = PluginRuntime(catalog)
    capabilities = ProjectSelector(catalog.registry).select(RepositoryFacts(revision='revision', paths=(path,)))
    assert 'swift' not in capabilities.repository_plugins
    store = StructuralGenerationStore(tmp_path / 'structural')
    connection = store.initialize(store.pending_paths('swift-fallback'))
    try:
        indexer = FileIndexer(None, ASTCodeSplitter(plugin_runtime=runtime), runtime)
        skipped = set()
        indexer.index_document(Document(source, {'path': path, 'language': 'swift'}),
            writer=StructuralGraphWriter(connection), dispositions={}, capabilities=capabilities,
            skipped_paths=skipped, check_cancelled=lambda: None)
        rows = connection.execute("SELECT kind,content,metadata_json FROM units WHERE record_type='source_unit'").fetchall()
        assert len(rows) == 1 and rows[0]['kind'] == 'fallback'
        assert rows[0]['content'] == source and not skipped
        assert json.loads(rows[0]['metadata_json'])['content_type'] == 'fallback'
        assert {row[0] for row in connection.execute('SELECT kind FROM relations')} == {'CONTAINS'}
    finally:
        connection.close()
