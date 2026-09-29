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
