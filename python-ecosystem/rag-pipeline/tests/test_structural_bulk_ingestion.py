"""Whole-graph equivalence for batched private-generation ingestion."""
import pytest

from rag_pipeline.core.documents import TextNode
from rag_pipeline.core.structural_store import (
    StructuralGenerationStore, StructuralGraphReader, StructuralGraphWriter, build_receipt,
)


def _populate(tmp_path, buffered, bulk_load=False):
    store = StructuralGenerationStore(tmp_path)
    pending = store.pending_paths('target')
    connection = store.initialize(pending, bulk_load=bulk_load)
    writer = StructuralGraphWriter(connection, defer_file_relation_ownership=True,
                                   buffer_file_relations=buffered)
    ids = [writer.add_unit(TextNode(f'def symbol{i}(): return {i}', {
        'path': f'src/{i}.py', 'primary_name': f'symbol{i}',
        'symbol_qualified_name': f'package.symbol{i}', 'start_line': 1, 'end_line': 1,
    })) for i in range(12)]
    kwargs = dict(kind='CALLS', source='package.symbol0', relation='calls',
                  target='package.symbol1', path='src/0.py', line=1, origin='plugin',
                  attributes={'complete': ['a', 'b']}, related_paths=('src/1.py', 'src/0.py'))
    relation_id = writer.add_relation(**kwargs, plugin_id='first')
    writer.add_relation(**kwargs, plugin_id='second', source_unit_id=ids[0])
    # Force several SQL batches before the same edge is emitted again.
    for i in range(650):
        writer.add_relation(kind='REFERENCES', source=f'package.symbol{i % 12}',
                            relation='references', target=f'package.symbol{(i + 1) % 12}',
                            path=f'src/{i % 12}.py', line=i + 2, origin='tree-sitter',
                            source_unit_id=ids[i % 12], target_unit_id=ids[(i + 1) % 12])
    writer.add_relation(**kwargs, plugin_id='third', target_unit_id=ids[1])
    # Conflicting later explicit endpoints must retain the first resolution.
    writer.add_relation(**kwargs, source_unit_id=ids[2], target_unit_id=ids[3])
    assert writer.relation_count == 651
    writer.build_lookup_indexes()
    # Finalizers still use normal reconciliation, including contributor union.
    writer.begin_repository_analysis_reconciliation()
    writer.add_relation(**kwargs, plugin_id='repository')
    writer.add_unit(TextNode('late repository output', {
        'path': 'late.py', 'primary_name': 'LateAlias', 'start_line': 1, 'end_line': 1,
    }))
    writer.reconcile_repository_analysis_outputs()
    writer.resolve_relations()
    receipt = build_receipt(
        connection, workspace='workspace', project='project', branch='main', revision='revision',
        source_tree_sha256='b' * 64, collection_target='target', repository_facts_json='{}',
        plugin_ids=(), plugin_fingerprint='sha256:' + '0' * 64,
        plugin_descriptor_fingerprint='sha256:' + '0' * 64,
        plugin_implementation_fingerprint='sha256:' + '0' * 64,
        index_representation_fingerprint='sha256:' + '0' * 64,
        include_patterns=[], exclude_patterns=[], document_count=12, skipped_file_count=0,
    )
    tables = ('units', 'unit_names', 'relations', 'relation_names', 'relation_plugins',
              'relation_scopes', 'relation_plugin_scopes', 'relation_paths', 'repository_snapshots',
              'relation_manifest_cache')
    observations = {name: sorted(tuple(row) for row in connection.execute(f'SELECT * FROM {name}'))
                    for name in tables}
    assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
    connection.execute("INSERT INTO units_fts(units_fts, rank) VALUES('integrity-check', 1)")
    reader = StructuralGraphReader(connection, receipt)
    observations['search'] = [reader.search_units(value) for value in ('symbol0', 'LateAlias')]
    observations['receipt'] = receipt
    assert connection.execute('SELECT plugin_id FROM relations WHERE relation_id=?',
                              (relation_id,)).fetchone()[0] is None
    writer.seal(receipt)
    connection.close()
    return observations


def test_buffered_ingestion_preserves_complete_graph_receipt_source_and_search(tmp_path):
    baseline = _populate(tmp_path / 'ordinary', False)
    assert baseline == _populate(tmp_path / 'buffered', True)
    assert baseline == _populate(tmp_path / 'bulk', True, bulk_load=True)


def test_buffered_ingestion_uses_bulk_sql_instead_of_per_edge_roundtrips(tmp_path):
    store = StructuralGenerationStore(tmp_path)
    connection = store.initialize(store.pending_paths('target'))
    writer = StructuralGraphWriter(connection, defer_file_relation_ownership=True,
                                   buffer_file_relations=True)
    statements = []
    connection.set_trace_callback(statements.append)
    try:
        for i in range(1025):
            writer.add_relation(kind='CALLS', source=f'source{i}', relation='calls',
                                target=f'target{i}', path='source.py', line=i + 1, origin='plugin')
        writer.flush_deferred_file_relation_ownership()
        assert writer.relation_count == 1025
        writes = [sql for sql in statements if sql.startswith('INSERT INTO relations(')]
        assert len(writes) == 5
        assert connection.execute('SELECT count(*) FROM relation_names').fetchone()[0] == 2050
        assert connection.execute('SELECT count(*) FROM relation_scopes').fetchone()[0] == 1025
    finally:
        connection.close()


def test_bulk_generation_builds_all_query_indexes_before_publication(tmp_path):
    from rag_pipeline.core.structural_graph.schema import _SECONDARY_INDEX_SQL
    store = StructuralGenerationStore(tmp_path)
    connection = store.initialize(store.pending_paths('target'), bulk_load=True)
    writer = StructuralGraphWriter(connection, buffer_file_relations=True)
    try:
        assert connection.execute("SELECT count(*) FROM sqlite_master WHERE type='index' AND sql IS NOT NULL").fetchone()[0] == 0
        assert not writer.fts_available
        writer.add_unit(TextNode('exact source', {
            'path': 'source.py', 'symbol_names': ['Straße', 'STRASSE'], 'start_line': 1, 'end_line': 1,
        }))
        writer.add_relation(kind='CALLS', source='a', relation='calls', target='b',
                            path='source.py', line=1, origin='tree-sitter')
        writer.build_lookup_indexes()
        assert writer.fts_available
        assert connection.execute("SELECT count(*) FROM sqlite_master WHERE type='index' AND sql IS NOT NULL").fetchone()[0] == len(_SECONDARY_INDEX_SQL)
        assert len(connection.execute("SELECT rowid FROM units_fts WHERE units_fts MATCH 'STRASSE'").fetchall()) == 1
        connection.execute("INSERT INTO units_fts(units_fts, rank) VALUES('integrity-check', 1)")
        assert writer.relation_count == 1
        assert connection.execute('SELECT count(*) FROM relation_scopes').fetchone()[0] == 1
        writer.build_lookup_indexes()  # repeated phase completion is harmless
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    finally:
        connection.close()


def test_bulk_file_graph_survives_partial_repository_finalizer_abort(tmp_path):
    store = StructuralGenerationStore(tmp_path)
    connection = store.initialize(store.pending_paths('target'), bulk_load=True)
    writer = StructuralGraphWriter(connection, buffer_file_relations=True)
    try:
        source = writer.add_unit(TextNode('def kept(): return 1', {
            'path': 'src/kept.py', 'primary_name': 'KeptAlias', 'start_line': 1, 'end_line': 1,
        }))
        edge = dict(kind='CALLS', source='KeptAlias', relation='calls', target='external',
                    path='src/kept.py', line=1, origin='plugin', source_unit_id=source)
        relation = writer.add_relation(**edge, plugin_id='file-plugin')
        writer.build_lookup_indexes()
        tables = ('units', 'unit_names', 'relations', 'relation_names', 'relation_plugins',
                  'relation_scopes', 'relation_plugin_scopes', 'relation_paths', 'repository_snapshots')

        def state():
            return {table: sorted(tuple(row) for row in connection.execute(f'SELECT * FROM {table}'))
                    for table in tables}

        before = state()
        writer.begin_repository_analysis_reconciliation()
        writer.add_relation(**edge, plugin_id='repository-plugin')
        writer.add_unit(TextNode('transient repository symbol', {
            'path': 'src/transient.py', 'primary_name': 'TransientAlias', 'start_line': 1, 'end_line': 1,
        }), record_type='plugin_symbol')
        writer.add_relation(kind='CALLS', source='TransientAlias', relation='calls', target='KeptAlias',
                            path='src/transient.py', line=1, origin='plugin', plugin_id='repository-plugin')
        writer.abort_repository_analysis_reconciliation()

        assert state() == before
        assert writer.unit_count == 1 and writer.relation_count == 1
        assert connection.execute('SELECT plugin_id FROM relations WHERE relation_id=?', (relation,)).fetchone()[0] == 'file-plugin'
        assert connection.execute("SELECT rowid FROM units_fts WHERE units_fts MATCH 'TransientAlias'").fetchall() == []
        assert len(connection.execute("SELECT rowid FROM units_fts WHERE units_fts MATCH 'KeptAlias'").fetchall()) == 1
        connection.execute("INSERT INTO units_fts(units_fts, rank) VALUES('integrity-check', 1)")
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []

        # A successful retry still contributes only repository-owned facts.
        writer.begin_repository_analysis_reconciliation()
        writer.add_relation(**edge, plugin_id='repository-plugin')
        writer.reconcile_repository_analysis_outputs()
        assert connection.execute('SELECT plugin_id FROM relations WHERE relation_id=?', (relation,)).fetchone()[0] is None
        assert {row[0] for row in connection.execute('SELECT scope FROM relation_scopes WHERE relation_id=?', (relation,))} == {'file', 'repository'}
    finally:
        connection.close()


@pytest.mark.parametrize("buffered", [False, True])
def test_invalid_optional_plugin_fact_does_not_poison_later_ingestion(tmp_path, caplog, buffered):
    from types import SimpleNamespace
    from codecrow_plugins import GraphFact
    from rag_pipeline.core.index_manager.file_indexer import FileIndexer

    store = StructuralGenerationStore(tmp_path)
    connection = store.initialize(store.pending_paths('target'), bulk_load=buffered)
    writer = StructuralGraphWriter(connection, buffer_file_relations=buffered)
    facts = [GraphFact(kind='CALLS', source='a', relation='calls', target='b',
                       path='source.py', line=line) for line in (1, 2**80)]
    indexer = FileIndexer(
        loader=None,
        splitter=SimpleNamespace(split_documents_resilient=lambda documents, **kwargs: (documents, [])),
        plugin_runtime=SimpleNamespace(graph_facts=lambda *args: (facts, [])),
    )
    try:
        for path in ('source.py', 'next.py'):
            indexer.index_document(TextNode('def a(): return 1', {
                'path': path, 'primary_name': 'a', 'start_line': 1, 'end_line': 1,
            }), writer=writer, dispositions={}, capabilities=object(),
               skipped_paths=set(), check_cancelled=lambda: None)
        writer.build_lookup_indexes()
        assert 'Plugin graph extraction failed open' in caplog.text
        assert connection.execute("SELECT count(*) FROM relations WHERE origin='plugin'").fetchone()[0] == 1
        assert connection.execute("SELECT count(DISTINCT path) FROM units").fetchone()[0] == 2
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    finally:
        connection.close()
