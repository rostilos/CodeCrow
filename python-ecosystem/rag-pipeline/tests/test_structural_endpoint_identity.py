"""Endpoint identity regressions from a captured proposed-tree Go graph."""
import json
import sqlite3

import pytest

from rag_pipeline.core.documents import TextNode
from rag_pipeline.core.structural_store import StructuralGraphWriter
from rag_pipeline.core.structural_graph import resolution
from tests.test_structural_store import _node, _pending_store, _reader


def _reference(writer, target, *, path="caller.go", explicit=None, related_paths=()):
    return writer.add_relation(
        kind="CALLS", source="Caller", relation="calls", target=target,
        path=path, line=1, origin="tree-sitter", target_unit_id=explicit, related_paths=related_paths,
    )


def _resolve(writer, relation_ids, mode, monkeypatch):
    if mode == "full":
        writer.resolve_relations()
    else:
        if mode == "bulk_selective":
            monkeypatch.setattr(resolution, "_BULK_RELATION_RESOLUTION_THRESHOLD", 1)
        writer.resolve_relations(relation_ids)


def _target(connection, relation_id):
    return connection.execute(
        "SELECT target_unit_id FROM relations WHERE relation_id = ?",
        (relation_id,),
    ).fetchone()[0]


@pytest.mark.parametrize("mode", ["full", "selective", "bulk_selective"])
def test_captured_builtin_spelling_does_not_bind_unrelated_receiver_method(tmp_path, monkeypatch, mode):
    _, _, connection, writer = _pending_store(tmp_path)
    # Actual captured source/target identity and extraction shape: a bare int64
    # conversion/type reference was bound to the sole casefolded method Int64.
    target_id = writer.add_unit(TextNode(
        'func (j *Json) Int64() (int64, error) {\n\treturn 0, errors.New("invalid value type")\n}',
        {"path": "pkg/components/simplejson/simplejson_go11.go", "language": "go",
         "primary_name": "Int64", "node_type": "method", "start_line": 66,
         "end_line": 78, "namespace": "package simplejson", "symbol_names": ["Int64"]},
    ))
    caller = TextNode(
        'func TestIntegrationAnonDeviceService_localCacheSafety(t *testing.T) {\n'
        '\tassert.Equal(t, int64(0), stats["stats.anonymous.device.ui.count"].(int64))\n}',
        {"path": "pkg/services/anonymous/anonimpl/impl_test.go", "language": "go",
         "primary_name": "TestIntegrationAnonDeviceService_localCacheSafety",
         "node_type": "function", "start_line": 149, "end_line": 179,
         "calls": ["int64"], "referenced_types": ["int64"]},
    )
    source_id = writer.add_unit(caller)
    writer.add_ast_relations(source_id, caller)
    ids = [row[0] for row in connection.execute("SELECT relation_id FROM relations")]
    _resolve(writer, ids, mode, monkeypatch)
    rows = connection.execute("SELECT * FROM relations ORDER BY kind").fetchall()
    assert {row["kind"] for row in rows} == {"CALLS", "REFERENCES"}
    assert all(row["target"] == "int64" and row["source_unit_id"] == source_id for row in rows)
    assert all(row["target_unit_id"] is None for row in rows)
    # The logical fact remains discoverable; the unrelated receiver is not an endpoint.
    observed = _reader(connection).query_graph("callees_of", source_id)["results"]
    assert any(row["target"] == "int64" for row in observed)
    assert all(row.get("targetUnit") is None for row in observed)
    connection.close()


@pytest.mark.parametrize("mode", ["full", "selective", "bulk_selective"])
def test_exact_aliases_disambiguate_case_and_preserve_declared_case_collisions(tmp_path, monkeypatch, mode):
    _, _, connection, writer = _pending_store(tmp_path)
    upper = writer.add_unit(_node("upper.py", "pkg.Foo"))
    lower = writer.add_unit(_node("lower.py", "pkg.foo"))
    aliases = _node("aliases.py", "pkg.Owner")
    aliases.metadata.update(symbol_names=["Alias", "alias"], architecture_identifiers=["ExactToken"])
    aliased = writer.add_unit(aliases)
    # This schema retains one display spelling per folded name/unit; both exact
    # source-provided aliases must remain eligible even if one display row wins.
    assert connection.execute("SELECT count(*) FROM unit_names WHERE unit_id=? AND normalized_name='alias'", (aliased,)).fetchone()[0] == 1
    expected = {"pkg.Foo": upper, "pkg.foo": lower, "pkg.FOO": None,
                "Alias": aliased, "alias": aliased, "ALIAS": None,
                "ExactToken": aliased, "exacttoken": None}
    ids = {name: _reference(writer, name, related_paths=("aliases.py",) if "." not in name else ())
           for name in expected}
    _resolve(writer, ids.values(), mode, monkeypatch)
    assert {name: _target(connection, rid) for name, rid in ids.items()} == expected
    connection.close()


@pytest.mark.parametrize("mode", ["full", "selective", "bulk_selective"])
def test_exact_shadow_name_and_qualified_receiver_survive_without_builtin_blacklist(tmp_path, monkeypatch, mode):
    _, _, connection, writer = _pending_store(tmp_path)
    # This is an exact user declaration, not permission to infer lexical scope.
    shadow = writer.add_unit(_node("shadow.go", "int64"))
    method = writer.add_unit(_node("receiver.go", "Json.Int64"))
    qualified = _reference(writer, "Json.Int64")
    same_spelling = _reference(writer, "int64", path="shadow.go")
    explicit = _reference(writer, "external spelling", explicit=method)
    short = _reference(writer, "unknownReceiver.Int64")
    _resolve(writer, [qualified, same_spelling, explicit, short], mode, monkeypatch)
    assert _target(connection, qualified) == method
    assert _target(connection, same_spelling) == shadow
    assert _target(connection, explicit) == method
    # A differently qualified receiver is not the same declaration.
    assert _target(connection, short) is None
    connection.close()


def test_clone_case_only_add_delete_rename_keeps_base_immutable(tmp_path):
    _, _, base, writer = _pending_store(tmp_path)
    original = writer.add_unit(_node("old.go", "pkg.Int64"))
    lower_ref = _reference(writer, "pkg.int64")
    upper_ref = _reference(writer, "pkg.Int64")
    writer.resolve_relations()
    base.commit()
    before = [tuple(row) for row in base.execute("SELECT * FROM relations ORDER BY relation_id")]
    clone = sqlite3.connect(tmp_path / "clone.sqlite3")
    clone.row_factory = sqlite3.Row
    base.backup(clone)
    delta = StructuralGraphWriter(clone, track_mutations=True)
    delta.refresh_counts()
    added = delta.add_unit(_node("added.go", "pkg.int64"))
    delta.resolve_touched_relations()
    assert _target(clone, lower_ref) == added
    assert _target(clone, upper_ref) == original
    delta.remove_paths(("added.go",))
    delta.resolve_touched_relations()
    assert _target(clone, lower_ref) is None
    delta.remove_paths(("old.go",))
    renamed = delta.add_unit(_node("renamed.go", "pkg.Int64"))
    delta.resolve_touched_relations()
    assert _target(clone, upper_ref) == renamed
    assert _target(clone, lower_ref) is None
    assert [tuple(row) for row in base.execute("SELECT * FROM relations ORDER BY relation_id")] == before
    assert _target(base, upper_ref) == original and _target(base, lower_ref) is None
    clone.close()
    base.close()


@pytest.mark.parametrize("mode", ["full", "selective", "bulk_selective"])
def test_alias_metadata_is_decoded_at_most_once_per_unit_per_resolution(tmp_path, monkeypatch, mode):
    _, _, connection, writer = _pending_store(tmp_path)
    node = _node("target.py", "pkg.Target")
    node.metadata["symbol_names"] = [f"Alias{index}" for index in range(40)]
    target = writer.add_unit(node)
    ids = [_reference(writer, name, related_paths=("target.py",)) for name in node.metadata["symbol_names"]]
    calls = []
    original = json.loads
    def counted(value, *args, **kwargs):
        if isinstance(value, str) and '"symbol_names"' in value:
            calls.append(value)
        return original(value, *args, **kwargs)
    monkeypatch.setattr(resolution.json, "loads", counted)
    _resolve(writer, ids, mode, monkeypatch)
    assert all(_target(connection, relation_id) == target for relation_id in ids)
    assert len(calls) <= 1
    connection.close()


@pytest.mark.parametrize("mode", ["full", "selective", "bulk_selective"])
def test_captured_python_builtins_and_external_types_do_not_bind_unrelated_typescript(tmp_path, monkeypatch, mode):
    _, _, connection, writer = _pending_store(tmp_path)
    for path, name in [("static/rangeMap.tsx", "RangeMap.min"), ("static/api.tsx", "Request"),
                       ("unrelated.py", "another")]:
        target = _node(path, name)
        target.metadata['language'] = 'typescript' if path.endswith('.tsx') else 'python'
        writer.add_unit(target)
    local = writer.add_unit(_node('caller.py', 'local'))
    imported = writer.add_unit(_node('dependency.ts', 'imported'))
    ids = {name: _reference(writer, name, path='caller.py')
           for name in ('min', 'Request', 'another', 'missing.min', 'local')}
    ids['imported'] = _reference(writer, 'imported', path='caller.js', related_paths=('dependency.ts',))
    _resolve(writer, ids.values(), mode, monkeypatch)
    assert {name: _target(connection, value) for name, value in ids.items()} == {
        'min': None, 'Request': None, 'another': None, 'missing.min': None,
        'local': local, 'imported': imported,
    }
    connection.close()
