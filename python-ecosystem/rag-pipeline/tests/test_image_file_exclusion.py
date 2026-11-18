import sqlite3
from pathlib import Path
from unittest.mock import patch

from codecrow_plugins.file_scope import EXCLUDED_EXTENSIONS
from rag_pipeline.core.documents import TextNode
from rag_pipeline.core.loader import DocumentLoader
from rag_pipeline.models.config import RAGConfig
from rag_pipeline.core.structural_store import StructuralGraphWriter, StructuralGraphReader
from rag_pipeline.core.structural_graph.schema import _SCHEMA_SQL


def test_image_extensions_never_reach_loader_reads_even_with_include_all(tmp_path, monkeypatch):
    from codecrow_plugins import file_scope
    monkeypatch.setattr(file_scope, 'EXCLUDED_EXTENSIONS', EXCLUDED_EXTENSIONS | {'futureasset'})
    paths = [Path('asset.' + extension.upper()) for extension in file_scope.EXCLUDED_EXTENSIONS]
    for path in paths:
        (tmp_path / path).write_text('<svg>IMAGE_PAYLOAD</svg>')
    (tmp_path / 'source.py').write_text('image = "asset.png"')
    loader = DocumentLoader(RAGConfig(excluded_patterns=[]))
    assert list(loader.iter_repository_files(tmp_path, extra_include_patterns=['**'])) == [Path('source.py')]
    with patch('rag_pipeline.core.loader.read_repository_file_bytes', side_effect=AssertionError('Image read')):
        assert loader.load_file_batch(paths, tmp_path, 'w', 'p', 'main', 'sha') == []


def test_image_units_and_relationships_are_not_persisted_and_legacy_reads_are_excluded():
    connection = sqlite3.connect(':memory:')
    connection.row_factory = sqlite3.Row
    connection.executescript(_SCHEMA_SQL)
    writer = StructuralGraphWriter(connection)
    node = TextNode(text='<svg>IMAGE_BYTES</svg>', metadata={'path': 'logo.SVG', 'start_line': 1, 'end_line': 1})
    assert writer.add_file(node) == writer.add_unit(node) == ''
    assert writer.add_relation(kind='imports', source='source.py', relation='imports', target='logo.SVG',
                               path='source.py', line=1, origin='test') == ''
    assert connection.execute('SELECT COUNT(*) FROM units').fetchone()[0] == 0
    assert connection.execute('SELECT COUNT(*) FROM relations').fetchone()[0] == 0
    source = TextNode(text='source', metadata={'path': 'source.py', 'start_line': 1, 'end_line': 1})
    unit_id = writer.add_unit(source)
    connection.execute('UPDATE units SET path = ?, content = ? WHERE unit_id = ?', ('legacy.svg', 'IMAGE_BYTES', unit_id))
    reader = StructuralGraphReader(connection, {})
    assert reader.get_unit(unit_id) is None
    assert reader.query_graph('file_search', 'legacy.svg')['status'] == 'excluded'
