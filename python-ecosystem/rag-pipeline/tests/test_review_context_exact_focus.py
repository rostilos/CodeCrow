"""Exact graph handles must resolve through the actual SQLite-backed reader."""
from rag_pipeline.core.documents import TextNode
from rag_pipeline.core.review_graph.context import minimal_review_context
from rag_pipeline.core.structural_store import (
    StructuralGenerationStore, StructuralGraphReader, StructuralGraphWriter,
)


def node(path, name, line=1):
    return TextNode(f"def {name.rsplit('.', 1)[-1]}(): return 1\n", {
        "path": path, "start_line": line, "end_line": line,
        "primary_name": name.rsplit(".", 1)[-1], "symbol_qualified_name": name,
        "language": "python",
    })


def test_minimal_context_resolves_exact_id_before_dense_anchor_and_fuzzy_name_search(tmp_path):
    store = StructuralGenerationStore(tmp_path / "graph")
    connection = store.initialize(store.pending_paths("fixture"))
    try:
        writer = StructuralGraphWriter(connection)
        for index in range(30):
            writer.add_unit(node("changed.py", f"changed.item{index}", index + 1))
        selected = writer.add_unit(node("one/worker.py", "one.process"))
        other = writer.add_unit(node("two/worker.py", "two.process"))
        dependency = writer.add_unit(node("dependency.py", "dependency.persist"))
        relation = writer.add_relation(kind="CALLS", source="one.process", relation="calls", target="dependency.persist",
                                       source_unit_id=selected, target_unit_id=dependency,
                                       path="one/worker.py", line=1, origin="ast", related_paths=["dependency.py"])
        reader = StructuralGraphReader(connection, {
            "branch": "main", "repository_revision": "fixture", "generation_manifest_sha256": "a" * 64,
        })
        # This is the real mismatch: opaque IDs have no name/path search match.
        assert reader.search_units(selected, max_results=25) == []
        response = minimal_review_context(reader, question="", focus_paths=["changed.py"],
                                          focus_symbols=[selected], max_relations=25,
                                          detail_level="standard", include_source=False)
        assert response["nodes"][0]["unitId"] == selected
        assert other not in {value["unitId"] for value in response["nodes"]}
        assert relation in {value["evidenceId"] for value in response["edges"]}
        assert response["sourceWindows"] == []
        assert response["coverage"]["truncated"] is True
        # Names remain supported when no exact unit identity exists.
        by_name = minimal_review_context(reader, question="", focus_paths=[], focus_symbols=["one.process"],
                                         detail_level="standard", include_source=False)
        assert selected in {value["unitId"] for value in by_name["nodes"]}
    finally:
        connection.close()
