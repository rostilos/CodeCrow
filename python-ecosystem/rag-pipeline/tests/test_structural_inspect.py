"""Relation-completeness coverage for structural repository inspection."""

from contextlib import contextmanager
from unittest.mock import patch

from rag_pipeline.api.models import RepositoryIndexGraphRequest
from rag_pipeline.api.routers.inspect import (
    _build_relation_graph,
    _encode_graph_cursor,
    _graph_cursor,
    _relation_rows_for_filters,
    _relation_rows_for_units,
    repository_index_graph,
    repository_index_overview,
)
from rag_pipeline.core.documents import TextNode
from rag_pipeline.core.structural_store import (
    StructuralGenerationStore,
    StructuralGraphWriter,
)


def _database(tmp_path):
    store = StructuralGenerationStore(tmp_path / "structural")
    pending = store.pending_paths("target")
    connection = store.initialize(pending)
    return connection, StructuralGraphWriter(connection)


def _unit(writer, path, name):
    return writer.add_unit(TextNode(
        "source",
        {
            "path": path,
            "start_line": 1,
            "end_line": 1,
            "primary_name": name,
            "symbol_qualified_name": name,
            "language": "python",
        },
    ))


def test_related_paths_participate_once_in_relation_pages(tmp_path):
    connection, writer = _database(tmp_path)
    selected_id = _unit(writer, "src/target.py", "Target")
    selected = connection.execute(
        "SELECT * FROM units WHERE unit_id = ?",
        (selected_id,),
    ).fetchone()
    relation_id = writer.add_relation(
        kind="framework-binding",
        source="config-entry",
        relation="resolves-to",
        target="Target",
        path="config/framework.xml",
        line=3,
        origin="plugin",
        related_paths=("src/target.py",),
        target_unit_id=selected_id,
    )
    connection.commit()

    rows, total = _relation_rows_for_units(
        connection,
        [selected],
        limit=10,
    )

    assert total == 1
    assert [row["relation_id"] for row in rows] == [relation_id]
    connection.close()


def test_relation_rows_are_offset_pageable_without_silent_truncation(tmp_path):
    connection, writer = _database(tmp_path)
    selected_id = _unit(writer, "src/a.py", "A")
    selected = connection.execute(
        "SELECT * FROM units WHERE unit_id = ?",
        (selected_id,),
    ).fetchone()
    for index in range(5):
        writer.add_relation(
            kind="CALLS",
            source="A",
            relation="calls",
            target=f"target{index}",
            path="src/a.py",
            line=index + 1,
            origin="tree-sitter",
            source_unit_id=selected_id,
        )
    connection.commit()

    first, total = _relation_rows_for_units(
        connection,
        [selected],
        limit=2,
    )
    second, second_total = _relation_rows_for_units(
        connection,
        [selected],
        limit=2,
        offset=2,
    )
    final, final_total = _relation_rows_for_units(
        connection,
        [selected],
        limit=2,
        offset=4,
    )

    assert (total, second_total, final_total) == (5, 5, 5)
    assert len(first) == len(second) == 2
    assert len(final) == 1
    assert len({row["relation_id"] for row in (*first, *second, *final)}) == 5
    connection.close()


def test_extra_node_budget_is_additional_and_self_relations_are_visible(tmp_path):
    connection, writer = _database(tmp_path)
    selected_id = _unit(writer, "src/a.py", "A")
    selected = connection.execute(
        "SELECT * FROM units WHERE unit_id = ?",
        (selected_id,),
    ).fetchone()
    writer.add_relation(
        kind="CALLS",
        source="A",
        relation="calls",
        target="external",
        path="src/a.py",
        line=1,
        origin="tree-sitter",
        source_unit_id=selected_id,
    )
    writer.add_relation(
        kind="recursive-call",
        source="recursive",
        relation="calls",
        target="recursive",
        path="src/a.py",
        line=2,
        origin="plugin",
    )
    connection.commit()
    relations, _ = _relation_rows_for_units(
        connection,
        [selected],
        limit=10,
    )

    nodes, edges = _build_relation_graph(
        connection,
        {"branch": "main"},
        [selected],
        relations,
        max_extra_nodes=2,
    )

    assert len(nodes) == 3
    assert len(edges) == 2
    assert any(edge["source"] == edge["target"] for edge in edges)
    assert {edge["metadata"]["origin"] for edge in edges} == {
        "plugin",
        "tree-sitter",
    }
    assert all(
        isinstance(edge["metadata"]["provenance"], dict)
        for edge in edges
    )
    connection.close()


def test_legacy_compound_cursor_with_unit_page_size_still_decodes():
    cursor = _encode_graph_cursor(160, 1200, unit_page_size=160)

    assert cursor == "u:160:n:160:r:1200"
    assert _graph_cursor(cursor) == (160, 1200, 160)
    assert _graph_cursor("5000") == (5000, 0, None)


def test_graph_endpoint_pages_every_relation_when_the_client_changes_page_size(
    tmp_path,
):
    connection, writer = _database(tmp_path)
    selected_id = _unit(writer, "src/a.py", "A")
    for index in range(20):
        _unit(writer, f"src/file{index:02}.py", f"Unit{index}")
    for index in range(6201):
        writer.add_relation(
            kind="CALLS",
            source="A",
            relation="calls",
            target=f"target{index}",
            path="src/a.py",
            line=index + 1,
            origin="tree-sitter",
            source_unit_id=selected_id,
        )
    connection.commit()

    @contextmanager
    def open_generation(*args, **kwargs):
        yield connection, {"branch": "main", "document_count": 1}

    with patch(
        "rag_pipeline.api.routers.inspect._get_index_manager",
        return_value=object(),
    ), patch(
        "rag_pipeline.api.routers.inspect._open_tenant_generation",
        open_generation,
    ):
        first = repository_index_graph(
            "workspace",
            "project",
            RepositoryIndexGraphRequest(
                collection_target="target",
                limit=20,
                scan_limit=100,
            ),
        )
        second = repository_index_graph(
            "workspace",
            "project",
            RepositoryIndexGraphRequest(
                collection_target="target",
                limit=5000,
                scan_limit=15000,
                cursor=first["nextCursor"],
            ),
        )
        third = repository_index_graph(
            "workspace",
            "project",
            RepositoryIndexGraphRequest(
                collection_target="target",
                limit=5000,
                scan_limit=15000,
                cursor=second["nextCursor"],
            ),
        )

    assert first["selectedRelationRows"] == 1200
    assert first["returnedRelations"] == 1200
    assert first["droppedEndpointRelations"] == 0
    assert first["relationsTruncated"] is True
    assert first["scannedPoints"] == 20
    assert first["nextCursor"] == "u:20:r:1200"
    assert second["selectedRelationRows"] == 5000
    assert second["returnedRelations"] == 5000
    assert second["scannedPoints"] == 1
    assert second["nextCursor"] == "u:21:r:6200"
    assert third["selectedRelationRows"] == 1
    assert third["returnedRelations"] == 1
    assert third["scannedPoints"] == 0
    assert third["nextCursor"] is None
    assert len({
        edge["id"]
        for edge in (*first["edges"], *second["edges"], *third["edges"])
    }) == 6201
    connection.close()


def test_overview_reports_exact_relation_and_file_inventory(tmp_path):
    connection, writer = _database(tmp_path)
    file_id = writer.add_file(TextNode(
        "def run(): pass\n",
        {"path": "src/a.py", "language": "python"},
    ))
    unit_id = _unit(writer, "src/a.py", "run")
    writer.add_relation(
        kind="CALLS",
        source="run",
        relation="calls",
        target="external",
        path="src/a.py",
        line=1,
        origin="tree-sitter",
        source_unit_id=unit_id,
    )
    writer.add_relation(
        kind="framework-binding",
        source="src/a.py",
        relation="binds",
        target="run",
        path="src/a.py",
        line=1,
        origin="plugin",
        plugin_id="framework",
        source_unit_id=file_id,
        target_unit_id=unit_id,
    )
    connection.commit()

    @contextmanager
    def open_generation(*args, **kwargs):
        yield connection, {"branch": "main", "document_count": 1}

    with patch(
        "rag_pipeline.api.routers.inspect._get_index_manager",
        return_value=object(),
    ), patch(
        "rag_pipeline.api.routers.inspect._open_tenant_generation",
        open_generation,
    ):
        overview = repository_index_overview(
            "workspace",
            "project",
            collection_target="target",
            sample_limit=100,
        )

    assert overview["indexedFileCount"] == 1
    assert overview["structuralFileCount"] == 1
    assert overview["totalRelations"] == 2
    assert overview["resolvedRelations"] == 1
    assert overview["partiallyResolvedRelations"] == 1
    assert overview["unresolvedRelations"] == 0
    assert overview["renderableRelations"] == 2
    assert overview["suppressedSelfRelations"] == 0
    assert overview["pluginRelations"] == 1
    assert overview["relationKinds"] == [
        {"value": "CALLS", "count": 1},
        {"value": "framework-binding", "count": 1},
    ]
    assert overview["relationLabels"] == [
        {"value": "binds", "count": 1},
        {"value": "calls", "count": 1},
    ]
    connection.close()


def test_graph_relation_cursor_never_repeats_multi_path_relation_across_unit_pages(
    tmp_path,
):
    connection, writer = _database(tmp_path)
    unit_ids = [
        _unit(writer, f"src/file{index:02d}.py", f"Symbol{index}")
        for index in range(21)
    ]
    relation_id = writer.add_relation(
        kind="framework-binding",
        source="Symbol0",
        relation="binds",
        target="Symbol20",
        path="src/file00.py",
        line=1,
        origin="plugin",
        related_paths=("src/file20.py",),
        source_unit_id=unit_ids[0],
        target_unit_id=unit_ids[20],
    )
    second_relation_id = writer.add_relation(
        kind="CALLS",
        source="Symbol0",
        relation="calls",
        target="external",
        path="src/file00.py",
        line=2,
        origin="tree-sitter",
        source_unit_id=unit_ids[0],
    )
    connection.commit()

    filtered, total = _relation_rows_for_filters(
        connection,
        RepositoryIndexGraphRequest(collection_target="target").filters,
        limit=10,
    )
    assert total == 2
    assert {row["relation_id"] for row in filtered} == {
        relation_id,
        second_relation_id,
    }

    @contextmanager
    def open_generation(*args, **kwargs):
        yield connection, {
            "branch": "main",
            "document_count": 21,
            "relation_count": 2,
        }

    with patch(
        "rag_pipeline.api.routers.inspect._get_index_manager",
        return_value=object(),
    ), patch(
        "rag_pipeline.api.routers.inspect._open_tenant_generation",
        open_generation,
    ):
        first = repository_index_graph(
            "workspace",
            "project",
            RepositoryIndexGraphRequest(
                collection_target="target",
                limit=20,
                scan_limit=100,
            ),
        )
        second = repository_index_graph(
            "workspace",
            "project",
            RepositoryIndexGraphRequest(
                collection_target="target",
                limit=5000,
                scan_limit=15000,
                cursor=first["nextCursor"],
            ),
        )

    assert first["nextCursor"] == "u:20:r:2"
    assert {edge["id"] for edge in first["edges"]} == {
        relation_id,
        second_relation_id,
    }
    assert second["edges"] == []
    assert second["nextCursor"] is None
    connection.close()
