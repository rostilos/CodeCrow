"""Physical storage fixtures; these are not review quality/cost benchmarks."""
import sqlite3

import pytest

from rag_pipeline.core.documents import TextNode
from rag_pipeline.core.structural_graph.schema import _SCHEMA_SQL, _FTS_SCHEMA_SQL
from rag_pipeline.core.structural_graph.projections import relations_to_manifests
from rag_pipeline.core.structural_store import (
    StructuralGenerationStore,
    StructuralGraphReader,
    StructuralGraphWriter,
    build_receipt,
    write_receipt,
)


# Recreate the already-supported self-contained FTS/rowid layout from the
# unchanged logical tables. This exercises pre-refactor generation reads and
# incremental writes without shipping a binary database fixture.
def _open_layout(tmp_path, *, compact):
    store = StructuralGenerationStore(tmp_path)
    pending = store.pending_paths("target")
    connection = store.initialize(pending)
    if not compact:
        connection.close()
        pending.database.unlink()
        connection = store.connect(pending.database)
        legacy_schema = _SCHEMA_SQL.replace(
            ") WITHOUT ROWID;", ");"
        ).replace(
            ",\n    search_names TEXT NOT NULL DEFAULT ''", ""
        )
        connection.executescript(legacy_schema)
        fts_schema = _FTS_SCHEMA_SQL[_FTS_SCHEMA_SQL.index("CREATE VIRTUAL TABLE"):]
        fts_schema = fts_schema.replace(
            "    content = 'unit_search_content',\n    content_rowid = 'rowid',\n", ""
        )
        connection.executescript(fts_schema)
        connection.execute("PRAGMA user_version = 7")
    return store, pending, connection, StructuralGraphWriter(connection)


def _node(index, *, aliases=()):
    return TextNode(
        "\n".join(f"value_{index}_{line} = calculate_{index}(value_{line})" for line in range(35)),
        {
            "path": f"src/unit_{index}.py",
            "start_line": 1,
            "end_line": 35,
            "symbol_qualified_name": f"package.Unit{index}",
            "primary_name": f"Unit{index}",
            "symbol_names": list(aliases),
            "language": "python",
        },
    )


def _receipt(connection):
    return build_receipt(
        connection,
        workspace="workspace", project="project", branch="main", revision="revision",
        source_tree_sha256="b" * 64, collection_target="target",
        repository_facts_json="{}", plugin_ids=["neutral-plugin"],
        plugin_fingerprint="sha256:" + "1" * 64,
        plugin_descriptor_fingerprint="sha256:" + "2" * 64,
        plugin_implementation_fingerprint="sha256:" + "3" * 64,
        index_representation_fingerprint="sha256:" + "4" * 64,
        include_patterns=[], exclude_patterns=[], document_count=80, skipped_file_count=0,
    )


def _populate(writer):
    ids = [writer.add_unit(_node(index)) for index in range(80)]
    for index in range(800):
        source = index % len(ids)
        target = (source + 1) % len(ids)
        writer.add_relation(
            kind="CALLS", source=f"package.Unit{source}", relation="calls",
            target=f"package.Unit{target}", path=f"src/unit_{source}.py",
            line=index + 1, origin="plugin", plugin_id="neutral-plugin",
            source_unit_id=ids[source], target_unit_id=ids[target],
            related_paths=[f"src/unit_{target}.py"],
        )
    return ids


def test_compact_layout_preserves_all_facts_and_receipts_with_less_storage(tmp_path):
    observations = []
    for compact in (False, True):
        store, pending, connection, writer = _open_layout(
            tmp_path / str(compact), compact=compact,
        )
        ids = _populate(writer)
        receipt = _receipt(connection)
        reader = StructuralGraphReader(connection, receipt)
        facts = reader.relations_among(ids)
        source = [reader.get_unit(unit_id) for unit_id in ids]
        search = reader.search_units("calculate_17", max_results=100)
        connection.execute("INSERT INTO units_fts(units_fts, rank) VALUES('integrity-check', 1)")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        writer.seal(receipt)
        write_receipt(pending.receipt, receipt)
        connection.close()
        observations.append((receipt, facts, source, search, pending.database.stat().st_size))
        store.publish(pending)
        with store.open_bound(
            target="target", workspace="workspace", project="project", branch="main",
            revision="revision", manifest_sha256=receipt["generation_manifest_sha256"],
        ) as (bound, _):
            assert StructuralGraphReader(bound, receipt).relations_among(ids) == facts
    assert observations[0][:-1] == observations[1][:-1]
    assert observations[1][-1] < observations[0][-1] * 0.85


@pytest.mark.parametrize("compact", [False, True])
def test_alias_replacement_and_path_deletion_keep_fts_consistent(tmp_path, compact):
    _, _, connection, writer = _open_layout(tmp_path, compact=compact)
    try:
        unit_id = writer.add_unit(_node(1, aliases=["ObsoleteAlias", "Straße", "STRASSE"]))
        connection.execute("INSERT INTO units_fts(units_fts, rank) VALUES('integrity-check', 1)")
        assert writer.add_unit(_node(1, aliases=["CurrentAlias"])) == unit_id
        connection.execute("INSERT INTO units_fts(units_fts, rank) VALUES('integrity-check', 1)")
        assert connection.execute(
            "SELECT rowid FROM units_fts WHERE units_fts MATCH 'ObsoleteAlias'"
        ).fetchall() == []
        assert len(connection.execute(
            "SELECT rowid FROM units_fts WHERE units_fts MATCH 'CurrentAlias'"
        ).fetchall()) == 1
        writer.remove_paths(["src/unit_1.py"])
        connection.execute("INSERT INTO units_fts(units_fts, rank) VALUES('integrity-check', 1)")
        assert connection.execute("SELECT count(*) FROM units").fetchone()[0] == 0
    finally:
        connection.close()


def test_batch_edge_projection_reuses_endpoints_and_does_not_fetch_source(tmp_path):
    _, _, connection, writer = _open_layout(tmp_path, compact=True)
    try:
        _populate(writer)
        rows = connection.execute("SELECT * FROM relations ORDER BY relation_id LIMIT 100").fetchall()
        statements = []
        connection.set_trace_callback(statements.append)
        projected = relations_to_manifests(connection, rows)
        connection.set_trace_callback(None)
        assert len(projected) == 100
        assert [row["evidenceId"] for row in projected] == [row["relation_id"] for row in rows]
        reads = [statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]
        assert len(reads) == 3  # endpoints, paths and contributors, independent of edge count
        assert all("SELECT * FROM UNITS" not in statement.upper() for statement in reads)
        assert all(row["sourceUnit"] and row["targetUnit"] for row in projected)
    finally:
        connection.close()


@pytest.mark.parametrize("compact", [False, True])
def test_existing_generation_clone_preserves_layout_and_base_bytes(tmp_path, compact):
    import hashlib
    from rag_pipeline.core.index_manager.publication import pending_generation

    store, pending, connection, writer = _open_layout(tmp_path, compact=compact)
    unit_id = writer.add_unit(_node(1, aliases=["BaseAlias"]))
    receipt = _receipt(connection)
    writer.seal(receipt)
    write_receipt(pending.receipt, receipt)
    connection.close()
    sealed = store.publish(pending)
    original_digest = hashlib.sha256(sealed.database.read_bytes()).hexdigest()
    with pending_generation(store, "clone", base_binding={
        "source_target": "target", "workspace": "workspace", "project": "project",
        "branch": "main", "revision": "revision",
        "manifest_sha256": receipt["generation_manifest_sha256"],
    }) as build:
        clone_writer = StructuralGraphWriter(build.connection, track_mutations=True)
        clone_writer.refresh_counts()
        assert clone_writer.add_unit(_node(1, aliases=["DeltaAlias"])) == unit_id
        build.connection.execute("INSERT INTO units_fts(units_fts, rank) VALUES('integrity-check', 1)")
        assert not build.connection.execute(
            "SELECT rowid FROM units_fts WHERE units_fts MATCH 'BaseAlias'"
        ).fetchall()
        assert len(build.connection.execute(
            "SELECT rowid FROM units_fts WHERE units_fts MATCH 'DeltaAlias'"
        ).fetchall()) == 1
        has_duplicate_content = build.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'units_fts_content'"
        ).fetchone() is not None
        assert has_duplicate_content is not compact
    assert hashlib.sha256(sealed.database.read_bytes()).hexdigest() == original_digest
    assert list(store.pending_root.iterdir()) == []
