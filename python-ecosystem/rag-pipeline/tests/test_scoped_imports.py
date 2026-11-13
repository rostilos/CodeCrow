"""Import declarations retain location/ownership without a chunk cross-product."""
import json

import pytest

from rag_pipeline.core.documents import Document, TextNode
from rag_pipeline.core.splitter import ASTCodeSplitter
from rag_pipeline.core.structural_store import (
    StructuralGenerationStore, StructuralGraphWriter, StructuralGraphReader,
)


def _reader(connection):
    return StructuralGraphReader(connection, {
        "branch": "main", "repository_revision": "revision",
        "generation_manifest_sha256": "a" * 64,
    })


def _graph(tmp_path, source, path):
    document = Document(text=source, metadata={"path": path})
    nodes = ASTCodeSplitter(parser_threshold=1).split_documents([document])
    store = StructuralGenerationStore(tmp_path)
    connection = store.initialize(store.pending_paths("imports"))
    writer = StructuralGraphWriter(connection)
    file_id = writer.add_file(document)
    ids = {}
    for node in nodes:
        unit_id = writer.add_unit(node)
        ids[node.metadata.get("primary_name")] = unit_id
        writer.add_file_containment(file_id, unit_id, node)
        writer.add_ast_relations(unit_id, node)
    writer.resolve_relations()
    return connection, writer, nodes, ids, file_id


def test_java_imports_are_stored_once_and_available_from_each_method(tmp_path):
    source = "\n".join([
        *(f"import example.Type{i};" for i in range(5)),
        "class Example {",
        *(f"  void method{i}() {{}}" for i in range(10)), "}",
    ])
    connection, writer, nodes, ids, file_id = _graph(tmp_path, source, "Example.java")
    try:
        rows = connection.execute("SELECT * FROM relations WHERE kind='IMPORTS'").fetchall()
        assert len(rows) == 5
        assert {row["source_unit_id"] for row in rows} == {file_id}
        assert sorted(row["line"] for row in rows) == list(range(1, 6))
        # The public parse inventory remains complete.
        assert set().union(*(set(node.metadata.get("imports", ())) for node in nodes)) == {
            f"example.Type{i}" for i in range(5)
        }
        reader = _reader(connection)
        for name, unit_id in ids.items():
            if name and name.startswith("method"):
                result = reader.query_graph("imports_of", unit_id)
                assert {row["target"] for row in result["results"]} == {
                    f"example.Type{i}" for i in range(5)
                }
                assert all(row["sourceUnit"]["unitId"] == file_id for row in result["results"])
        assert len(reader.relations_for_paths(["Example.java"])["relations"]) >= 5
        target_id = writer.add_unit(TextNode("class Type0 {}", {
            "path": "Type0.java", "primary_name": "Type0",
            "symbol_qualified_name": "example.Type0", "start_line": 1, "end_line": 1,
        }))
        writer.resolve_relations()
        assert len(reader.relations_among([file_id, target_id])["results"]) == 1
        from rag_pipeline.core.review_graph.walk import _walk
        from rag_pipeline.core.review_graph.impact import _weighted_impact

        walked = _walk(reader, [reader.get_unit(file_id)["unit"]], strategy="bfs",
                       direction="outgoing", relation_kinds=["IMPORTS"], max_depth=1, max_results=20)
        assert target_id in walked["visited"]
        impact = _weighted_impact(reader, [reader.get_unit(target_id)["unit"]], max_depth=1, max_results=20)
        # The import impacts its actual file owner, without claiming each
        # method uses Type0 merely because it is imported by the file.
        assert {unit_id for unit_id, _ in impact["impacted"]} == {file_id}
    finally:
        connection.close()


def test_python_local_nested_and_repeated_imports_keep_actual_owner(tmp_path):
    source = (
        "import os\nimport os\ndef first():\n    import json\n"
        "    def nested():\n        import decimal\n        return decimal.Decimal(1)\n"
        "    return json.dumps({})\ndef second():\n    import sys\n    return sys.version\n"
    )
    connection, _, _, ids, file_id = _graph(tmp_path, source, "example.py")
    try:
        rows = connection.execute("SELECT * FROM relations WHERE kind='IMPORTS' ORDER BY line").fetchall()
        assert [(row["target"], row["line"], row["source_unit_id"]) for row in rows] == [
            ("import os", 1, file_id), ("import os", 2, file_id),
            ("import json", 4, ids["first"]), ("import decimal", 6, ids["nested"]),
            ("import sys", 10, ids["second"]),
        ]
        reader = _reader(connection)
        first = reader.query_graph("imports_of", ids["first"])["results"]
        assert {row["target"] for row in first} == {"import os", "import json"}
        assert {row["target"] for row in reader.query_graph("imports_of", ids["second"])["results"]} == {
            "import os", "import sys",
        }
        assert all(row["sourceUnit"]["unitId"] == file_id
                   for row in reader.query_graph("importers_of", "import os")["results"])
        assert all(json.loads(row["attributes_json"])["importScope"] == "unit" for row in rows[2:])
    finally:
        connection.close()


def test_import_only_source_is_retained_and_fallback_inventory_is_not_dropped(tmp_path):
    source = "import os\nfrom pathlib import Path\n"
    connection, writer, nodes, _, file_id = _graph(tmp_path, source, "imports.py")
    try:
        assert any(node.text == source for node in nodes)
        assert connection.execute("SELECT count(*) FROM relations WHERE kind='IMPORTS'").fetchone()[0] == 2
        assert connection.execute("SELECT DISTINCT source_unit_id FROM relations WHERE kind='IMPORTS'").fetchone()[0] == file_id
        fallback = TextNode("unknown source", {"path": "unknown.lang", "imports": ["kept"]})
        fallback_id = writer.add_unit(fallback)
        writer.add_ast_relations(fallback_id, fallback)
        assert connection.execute("SELECT target FROM relations WHERE source_unit_id=?", (fallback_id,)).fetchone()[0] == "kept"
    finally:
        connection.close()


def test_repeated_imports_on_one_line_keep_distinct_source_spans(tmp_path):
    source = "import os; import os\n"
    connection, _, _, _, file_id = _graph(tmp_path, source, "imports.py")
    try:
        rows = connection.execute("SELECT * FROM relations WHERE kind='IMPORTS'").fetchall()
        assert len(rows) == 2
        assert {row["source_unit_id"] for row in rows} == {file_id}
        assert {json.loads(row["attributes_json"])["startByte"] for row in rows} == {0, 11}
    finally:
        connection.close()


def test_graph_phase_logs_correlated_failure_without_hiding_the_error(tmp_path, caplog):
    from rag_pipeline.core.structural_graph.shared import _timed_graph_phase

    store = StructuralGenerationStore(tmp_path)
    pending = store.pending_paths("measured-generation")
    connection = store.initialize(pending)
    try:
        with caplog.at_level("INFO"), pytest.raises(RuntimeError, match="original"):
            with _timed_graph_phase(connection, "receipt", target="measured-generation"):
                raise RuntimeError("original")
        assert "target=measured-generation" in caplog.text
        assert f"generation={pending.directory.name}" in caplog.text
        assert "phase=receipt outcome=failed" in caplog.text
        assert "elapsed_ms=" in caplog.text and "database_bytes=" in caplog.text
    finally:
        connection.close()


def test_delta_frontier_observes_cancellation_between_short_index_probes(tmp_path):
    store = StructuralGenerationStore(tmp_path)
    connection = store.initialize(store.pending_paths("cancel"))
    writer = StructuralGraphWriter(connection)
    checks = 0

    def cancelled():
        nonlocal checks
        checks += 1
        if checks == 5:
            raise InterruptedError("cancel short probes")

    try:
        for index in range(20):
            writer.add_relation(kind="CALLS", source=f"{index + 1}.py", relation="calls",
                                target=f"{index}.py", path=f"{index + 1}.py", line=1,
                                origin="plugin", related_paths=(f"{index}.py",))
        with pytest.raises(InterruptedError, match="cancel short probes"):
            writer.remove_paths(["0.py"], cancellation_check=cancelled)
        assert checks == 5
        assert connection.execute("SELECT count(*) FROM relations").fetchone()[0] == 20
        assert connection.execute("SELECT 1").fetchone()[0] == 1
    finally:
        connection.close()


@pytest.mark.parametrize("directory", ["src/a_b", "src/a%b", "src/界"])
def test_delta_directory_paths_are_literal_and_dependency_closure_is_transitive(tmp_path, directory):
    store = StructuralGenerationStore(tmp_path)
    connection = store.initialize(store.pending_paths("delta"))
    writer = StructuralGraphWriter(connection, track_mutations=True)
    paths = [directory + "/a.py", "src/axb/unrelated.py", "caller.py", "outer.py", "packet.py"]
    try:
        for path in paths:
            writer.add_unit(TextNode("source", {"path": path, "primary_name": path}))
        for origin, target, attributes in [
            ("caller.py", paths[0], {}), ("outer.py", "caller.py", {}),
            ("packet.py", paths[0], {"packetKind": "repository"}),
        ]:
            writer.add_relation(kind="CALLS", source=origin, relation="calls", target=target,
                                path=origin, line=1, origin="plugin", related_paths=(target,),
                                attributes=attributes)
        statements = []
        connection.set_trace_callback(statements.append)
        affected, _ = writer.remove_paths([directory])
        connection.set_trace_callback(None)
        assert set(affected) == {directory, paths[0], "caller.py", "outer.py"}
        assert {row[0] for row in connection.execute("SELECT path FROM units")} == {
            "src/axb/unrelated.py", "packet.py",
        }
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        expansion = next(sql for sql in statements if "SELECT DISTINCT path FROM units WHERE path >=" in sql)
        closure = next(sql for sql in statements if sql.startswith("SELECT DISTINCT relations.path FROM relation_paths"))
        assert any("SEARCH units USING COVERING INDEX idx_units_path" in row[3]
                   for row in connection.execute("EXPLAIN QUERY PLAN " + expansion))
        assert any("SEARCH relation_paths USING COVERING INDEX idx_relation_paths_path" in row[3]
                   for row in connection.execute("EXPLAIN QUERY PLAN " + closure))
    finally:
        connection.close()
