import pytest

from rag_pipeline.core.documents import TextNode
from rag_pipeline.utils.utils import LANGUAGE_MAP
from tests.test_structural_store import _node, _pending_store, _reader


@pytest.mark.parametrize('pattern', ['importers_of', 'references_to', 'relations_of'])
@pytest.mark.parametrize('target', ['ghost/core/core/server/services/xmlrpc.js', 'ghost\\core\\core\\server\\services\\xmlrpc.js', *('missing' + suffix for suffix in LANGUAGE_MAP)])
def test_absent_exact_path_never_resolves_unrelated_suffix_units(tmp_path, pattern, target):
    _, _, connection, writer = _pending_store(tmp_path)
    suffix = '.' + target.rsplit('.', 1)[-1]
    for path in ['.github/scripts/bump-version' + suffix, '.github/scripts/clean' + suffix]:
        writer.add_unit(TextNode('source', {'path': path, 'primary_name': path.rsplit('/', 1)[-1], 'start_line': 1, 'end_line': 1}))
    connection.commit()
    result = _reader(connection).query_graph(pattern, target)
    assert result.get('status') != 'ambiguous'
    assert result['results'] == []
    assert result['resolvedUnits'] == []
    assert not result.get('candidates')


def test_deleted_exact_path_still_returns_real_dangling_import(tmp_path):
    _, _, connection, writer = _pending_store(tmp_path)
    for path in ['one.js', 'two.js']:
        writer.add_unit(TextNode('source', {'path': path, 'primary_name': path, 'start_line': 1, 'end_line': 1}))
    target = 'ghost/core/core/server/services/xmlrpc.js'
    writer.add_relation(kind='IMPORTS', source='one.js', relation='imports', target=target, path='one.js', line=1, origin='tree-sitter')
    writer.resolve_relations()
    connection.commit()
    result = _reader(connection).query_graph('importers_of', target)
    assert result.get('status') != 'ambiguous'
    assert [row['target'] for row in result['results']] == [target]
    assert result['resolvedUnits'] == []


@pytest.mark.parametrize('name', ['Shared.config', 'Shared.conf', 'Parser.Json', 'Header.hpp'])
def test_exact_qualified_symbol_beats_filename_suffix_heuristic(tmp_path, name):
    _, _, connection, writer = _pending_store(tmp_path)
    identity = writer.add_unit(_node('src/definitions.py', name))
    writer.add_unit(_node('src/other.py', 'Other.' + name.rsplit('.', 1)[-1]))
    connection.commit()
    result = _reader(connection).query_graph('relations_of', name)
    assert result.get('status') != 'ambiguous'
    assert [unit['unitId'] for unit in result['resolvedUnits']] == [identity]


def test_actual_qualified_symbol_ambiguity_beats_filename_suffix_heuristic(tmp_path):
    _, _, connection, writer = _pending_store(tmp_path)
    for path in ['a.py', 'b.py']:
        writer.add_unit(_node(path, 'Parser.Json'))
    connection.commit()
    result = _reader(connection).query_graph('relations_of', 'Parser.Json')
    assert result['status'] == 'ambiguous'
    assert result['candidateCount'] == 2
