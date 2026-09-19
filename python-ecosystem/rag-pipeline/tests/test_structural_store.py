import os
import json
from types import SimpleNamespace

import pytest

import rag_pipeline.core.structural_store as structural_store_module
from rag_pipeline.core.documents import TextNode
from rag_pipeline.core.coordination import ProjectMutationCoordinator
from rag_pipeline.core.exact_index import ExactIndexPreconditionError
from rag_pipeline.core.index_manager.manager import RAGIndexManager
from rag_pipeline.core.structural_store import (
    STRUCTURAL_STORE_SCHEMA,
    STRUCTURAL_STORE_SCHEMA_REVISION,
    StructuralGenerationStore,
    StructuralGraphReader,
    StructuralGraphWriter,
    build_receipt,
    relation_to_manifest,
    write_receipt,
)


def _node(path: str, qualified_name: str, content: str = "source") -> TextNode:
    return TextNode(
        content,
        {
            "path": path,
            "start_line": 1,
            "end_line": 1,
            "primary_name": qualified_name.rsplit(".", 1)[-1],
            "symbol_qualified_name": qualified_name,
            "language": "python",
        },
    )


def _reader(connection) -> StructuralGraphReader:
    return StructuralGraphReader(
        connection,
        {
            "branch": "main",
            "repository_revision": "revision",
            "generation_manifest_sha256": "a" * 64,
        },
    )


def test_snapshot_distinguishes_base_and_proposed_generation_manifests():
    reader = StructuralGraphReader(None, {
        "branch": "main",
        "repository_revision": "proposed-revision",
        "generation_manifest_sha256": "b" * 64,
        "snapshot_metadata": {
            "kind": "proposed_tree",
            "base_revision": "base-revision",
            "base_collection_target": "sealed-base-target",
            "base_generation_manifest_sha256": "a" * 64,
            "source_revision": "source-revision",
        },
    })

    assert reader.snapshot() == {
        "kind": "proposed_tree",
        "branch": "main",
        "revision": "proposed-revision",
        "generationManifestSha256": "b" * 64,
        "baseRevision": "base-revision",
        "baseCollectionTarget": "sealed-base-target",
        "baseGenerationManifestSha256": "a" * 64,
        "sourceRevision": "source-revision",
    }


def _pending_store(tmp_path):
    store = StructuralGenerationStore(tmp_path / "structural")
    pending = store.pending_paths("generation-target")
    connection = store.initialize(pending)
    return store, pending, connection, StructuralGraphWriter(connection)


class _RecordingConnection:
    def __init__(self, connection):
        self.connection = connection
        self.execute_calls = []
        self.executemany_calls = []

    def execute(self, statement, parameters=()):
        self.execute_calls.append((statement, tuple(parameters)))
        return self.connection.execute(statement, parameters)

    def executemany(self, statement, rows):
        materialized_rows = tuple(rows)
        self.executemany_calls.append((statement, materialized_rows))
        return self.connection.executemany(statement, materialized_rows)

    def reset(self):
        self.execute_calls.clear()
        self.executemany_calls.clear()

    def __getattr__(self, name):
        return getattr(self.connection, name)


def _recording_writer(tmp_path, *, track_mutations=True):
    store = StructuralGenerationStore(tmp_path / "structural")
    pending = store.pending_paths("generation-target")
    connection = store.initialize(pending)
    recording = _RecordingConnection(connection)
    writer = StructuralGraphWriter(recording, track_mutations=track_mutations)
    recording.reset()
    return connection, recording, writer


def _compact_sql(calls):
    return [" ".join(statement.split()).upper() for statement, _ in calls]


def _alias_node(primary_name):
    return TextNode(
        "stable source body",
        {
            "path": "src/service.py",
            "start_line": 4,
            "end_line": 6,
            "primary_name": primary_name,
            "symbol_qualified_name": "package.StableService",
            "symbol_names": [f"{primary_name}Extra"],
            "language": "python",
        },
    )


def _publish_generation(store, target, *, snapshot_kind):
    pending = store.pending_paths(target)
    connection = store.initialize(pending)
    writer = StructuralGraphWriter(connection)
    writer.add_unit(_node("a.py", "a.Foo"))
    receipt = build_receipt(
        connection,
        workspace="workspace",
        project="project",
        branch="main",
        revision="revision",
        source_tree_sha256="b" * 64,
        collection_target=target,
        repository_facts_json="{}",
        plugin_ids=(),
        plugin_fingerprint="sha256:" + "0" * 64,
        plugin_descriptor_fingerprint="sha256:" + "0" * 64,
        plugin_implementation_fingerprint="sha256:" + "0" * 64,
        index_representation_fingerprint="sha256:" + "1" * 64,
        include_patterns=None,
        exclude_patterns=None,
        document_count=1,
        skipped_file_count=0,
        snapshot_metadata={"kind": snapshot_kind},
    )
    writer.seal(receipt)
    connection.close()
    write_receipt(pending.receipt, receipt)
    return store.publish(pending), receipt


def test_repository_generation_discovery_excludes_review_overlays(tmp_path):
    store = StructuralGenerationStore(tmp_path / "structural")
    _, base_receipt = _publish_generation(
        store,
        "base-generation",
        snapshot_kind="repository",
    )
    _publish_generation(
        store,
        "review-generation",
        snapshot_kind="proposed_tree",
    )

    discovered = store.repository_generation_receipts(
        workspace="workspace",
        project="project",
        branch="main",
        revision="revision",
    )

    assert [item["collection_target"] for item in discovered] == [
        base_receipt["collection_target"]
    ]
    assert store.repository_generation_receipts(
        workspace="other-workspace",
        project="project",
        branch="main",
    ) == []


def test_graph_patterns_include_plugin_semantics_and_resolve_exact_names_first(
    tmp_path,
):
    _, _, connection, writer = _pending_store(tmp_path)
    first = writer.add_unit(_node("a.py", "a.Foo"))
    writer.add_unit(_node("b.py", "b.Foo"))
    plugin_relation = writer.add_relation(
        kind="python-call",
        source="a.Foo",
        relation="calls",
        target="service.run",
        path="a.py",
        line=1,
        origin="plugin",
    )
    writer.add_relation(
        kind="CALLS",
        source="b.Foo",
        relation="calls",
        target="other.run",
        path="b.py",
        line=1,
        origin="tree-sitter",
    )
    writer.resolve_relations()
    connection.commit()

    linked = connection.execute(
        "SELECT source_unit_id FROM relations WHERE relation_id = ?",
        (plugin_relation,),
    ).fetchone()
    assert linked["source_unit_id"] == first
    assert [
        (item["source"], item["target"], item["kind"])
        for item in _reader(connection).query_graph(
            "callees_of",
            "a.Foo",
        )["results"]
    ] == [("a.Foo", "service.run", "python-call")]
    connection.close()


def test_named_graph_query_requires_unit_id_when_symbol_is_ambiguous(tmp_path):
    _, _, connection, writer = _pending_store(tmp_path)
    first_id = writer.add_unit(_node("a.py", "Shared.run"))
    writer.add_unit(_node("b.py", "Shared.run"))
    connection.commit()

    reader = _reader(connection)
    ambiguous = reader.query_graph("relations_of", "Shared.run")
    precise = reader.query_graph("relations_of", first_id)

    assert ambiguous["status"] == "ambiguous"
    assert ambiguous["results"] == []
    assert ambiguous["candidateCount"] == 2
    assert ambiguous["candidateResultCount"] == 2
    assert ambiguous["candidatesTruncated"] is False
    assert ambiguous["nextCursor"] is None
    assert "unitId" in ambiguous["hint"]
    assert {
        candidate["path"] for candidate in ambiguous["candidates"]
    } == {"a.py", "b.py"}
    assert precise.get("status") != "ambiguous"
    assert [unit["unitId"] for unit in precise["resolvedUnits"]] == [first_id]
    connection.close()


def test_ambiguous_graph_query_pages_all_candidates_above_internal_cap(tmp_path):
    _, _, connection, writer = _pending_store(tmp_path)
    candidate_ids = {
        writer.add_unit(_node(f"src/shared_{index:02d}.py", "Shared.run"))
        for index in range(25)
    }
    connection.commit()

    reader = _reader(connection)
    first = reader.query_graph("relations_of", "Shared.run")
    second = reader.query_graph(
        "relations_of",
        "Shared.run",
        cursor=first["nextCursor"],
    )
    bounded = reader.query_graph(
        "relations_of",
        "Shared.run",
        max_results=7,
    )

    assert first["status"] == second["status"] == "ambiguous"
    assert first["candidateCount"] == second["candidateCount"] == 25
    assert first["candidateResultCount"] == 20
    assert first["candidatesTruncated"] is True
    assert first["truncated"] is True
    assert first["nextCursor"] == 20
    assert "nextCursor" in first["hint"]
    assert bounded["candidateCount"] == 25
    assert bounded["candidateResultCount"] == 7
    assert bounded["nextCursor"] == 7
    assert second["cursor"] == 20
    assert second["candidateResultCount"] == 5
    assert second["candidatesTruncated"] is True
    assert second["truncated"] is False
    assert second["nextCursor"] is None
    assert {
        candidate["unitId"]
        for page in (first, second)
        for candidate in page["candidates"]
    } == candidate_ids
    connection.close()


def test_relation_query_cursor_pages_without_duplicates_or_gaps(tmp_path):
    _, _, connection, writer = _pending_store(tmp_path)
    root_id = writer.add_unit(_node("src/root.py", "package.Root"))
    relation_ids = []
    for index in range(5):
        target_id = writer.add_unit(
            _node(f"src/target_{index}.py", f"package.Target{index}")
        )
        relation_ids.append(
            writer.add_relation(
                kind="CALLS",
                source="package.Root",
                relation="calls",
                target=f"package.Target{index}",
                path=f"src/root_{index}.py",
                line=index + 1,
                origin="tree-sitter",
                source_unit_id=root_id,
                target_unit_id=target_id,
            )
        )
    connection.commit()

    reader = _reader(connection)
    first = reader.query_graph(
        "relations_of",
        root_id,
        max_results=2,
        cursor=0,
    )
    second = reader.query_graph(
        "relations_of",
        root_id,
        max_results=2,
        cursor=first["nextCursor"],
    )
    third = reader.query_graph(
        "relations_of",
        root_id,
        max_results=2,
        cursor=second["nextCursor"],
    )

    assert [first["cursor"], second["cursor"], third["cursor"]] == [0, 2, 4]
    assert [
        first["nextCursor"],
        second["nextCursor"],
        third["nextCursor"],
    ] == [2, 4, None]
    assert [first["truncated"], second["truncated"], third["truncated"]] == [
        True,
        True,
        False,
    ]
    paged_ids = [
        result["evidenceId"]
        for page in (first, second, third)
        for result in page["results"]
    ]
    assert paged_ids == relation_ids
    assert len(paged_ids) == len(set(paged_ids))
    connection.close()


def test_relations_among_returns_only_the_bounded_induced_subgraph(tmp_path):
    _, _, connection, writer = _pending_store(tmp_path)
    first_id = writer.add_unit(_node("src/first.py", "package.First"))
    second_id = writer.add_unit(_node("src/second.py", "package.Second"))
    outside_id = writer.add_unit(_node("src/outside.py", "package.Outside"))
    retained_id = writer.add_relation(
        kind="CALLS",
        source="package.First",
        relation="calls",
        target="package.Second",
        path="src/first.py",
        line=1,
        origin="tree-sitter",
        source_unit_id=first_id,
        target_unit_id=second_id,
    )
    writer.add_relation(
        kind="CALLS",
        source="package.First",
        relation="calls",
        target="package.Outside",
        path="src/first.py",
        line=2,
        origin="tree-sitter",
        source_unit_id=first_id,
        target_unit_id=outside_id,
    )
    connection.commit()

    result = _reader(connection).relations_among([first_id, second_id])

    assert result["truncated"] is False
    assert [relation["evidenceId"] for relation in result["results"]] == [
        retained_id
    ]
    connection.close()


def test_file_summary_cursor_pages_every_structural_unit(tmp_path):
    _, _, connection, writer = _pending_store(tmp_path)
    unit_ids = [
        writer.add_unit(_node("src/dense.py", f"package.Unit{index}"))
        for index in range(5)
    ]
    connection.commit()

    reader = _reader(connection)
    pages = []
    cursor = 0
    while cursor is not None:
        page = reader.query_graph(
            "file_summary",
            "src/dense.py",
            max_results=2,
            cursor=cursor,
        )
        pages.append(page)
        cursor = page["nextCursor"]

    assert [page["cursor"] for page in pages] == [0, 2, 4]
    assert [page["truncated"] for page in pages] == [True, True, False]
    paged_ids = [
        result["unitId"]
        for page in pages
        for result in page["results"]
    ]
    assert paged_ids == sorted(unit_ids)
    assert len(paged_ids) == len(set(paged_ids))
    connection.close()


def test_graph_query_projects_indexed_endpoint_candidates_before_filtering(
    tmp_path,
):
    _, _, connection, writer = _pending_store(tmp_path)
    target_id = writer.add_unit(_node("target.py", "package.Target"))
    relation_id = writer.add_relation(
        kind="REFERENCES",
        source="package.Caller",
        relation="references",
        target="package.Target",
        path="caller.py",
        line=3,
        origin="tree-sitter",
        target_unit_id=target_id,
    )
    writer.add_relation(
        kind="REFERENCES",
        source="package.Unrelated",
        relation="references",
        target="package.Other",
        path="unrelated.py",
        line=4,
        origin="tree-sitter",
    )
    connection.commit()
    recording = _RecordingConnection(connection)

    results = _reader(recording).query_graph(
        "references_to",
        "package.Target",
    )["results"]

    assert [result["evidenceId"] for result in results] == [relation_id]
    statement, parameters = next(
        (statement, parameters)
        for statement, parameters in recording.execute_calls
        if "WITH candidate_relations" in statement
    )
    plan = connection.execute(
        "EXPLAIN QUERY PLAN " + statement,
        parameters,
    ).fetchall()
    details = [str(row["detail"]) for row in plan]
    assert any("idx_relation_names_lookup" in detail for detail in details)
    assert any("idx_relations_target_unit" in detail for detail in details)
    assert "SCAN relations" not in details
    connection.close()


def test_graph_query_index_prefilter_retains_exact_nocase_semantics(tmp_path):
    _, _, connection, writer = _pending_store(tmp_path)
    exact_id = writer.add_relation(
        kind="CALLS",
        source="Package.Service",
        relation="calls",
        target="expected",
        path="expected.py",
        line=1,
        origin="tree-sitter",
    )
    writer.add_relation(
        kind="CALLS",
        source=" Package.Service ",
        relation="calls",
        target="whitespace-only-normalized-match",
        path="whitespace.py",
        line=1,
        origin="tree-sitter",
    )
    connection.commit()

    results = _reader(connection).query_graph(
        "callees_of",
        "PACKAGE.SERVICE",
    )["results"]

    assert [result["evidenceId"] for result in results] == [exact_id]
    connection.close()


def test_large_selective_resolution_uses_one_bulk_name_projection(
    tmp_path,
    monkeypatch,
):
    _, _, connection, writer = _pending_store(tmp_path)
    target_id = writer.add_unit(_node("target.py", "package.Target"))
    relation_ids = {
        writer.add_relation(
            kind="REFERENCES",
            source=f"caller.{index}",
            relation="references",
            target="package.Target",
            path=f"src/caller_{index}.py",
            line=1,
            origin="plugin",
        )
        for index in range(256)
    }

    def fail_per_endpoint_lookup(*_args, **_kwargs):
        raise AssertionError("bulk resolution must not perform N+1 name lookups")

    monkeypatch.setattr(writer, "_unique_named_unit", fail_per_endpoint_lookup)
    writer.resolve_relations(relation_ids)

    assert connection.execute(
        "SELECT count(*) FROM relations WHERE target_unit_id = ?",
        (target_id,),
    ).fetchone()[0] == 256
    connection.close()


def test_unqualified_graph_fallback_is_delimiter_aware_and_escapes_like(
    tmp_path,
):
    _, _, connection, writer = _pending_store(tmp_path)
    writer.add_relation(
        kind="CALLS",
        source="pkg.get_user",
        relation="calls",
        target="expected",
        path="expected.py",
        line=1,
        origin="tree-sitter",
    )
    writer.add_relation(
        kind="CALLS",
        source="pkg.getXuser",
        relation="calls",
        target="false-positive",
        path="false.py",
        line=1,
        origin="tree-sitter",
    )
    connection.commit()

    results = _reader(connection).query_graph("callees_of", "get_user")[
        "results"
    ]
    assert [(item["source"], item["target"]) for item in results] == [
        ("pkg.get_user", "expected")
    ]
    connection.close()


def test_bidirectional_graph_patterns_accept_repository_path_targets(tmp_path):
    _, _, connection, writer = _pending_store(tmp_path)
    writer.add_unit(_node("src/C.java", "example.C"))
    relation_id = writer.add_relation(
        kind="magento-event-observer",
        source="checkout_submit_all_after",
        relation="observed-by",
        target="Vendor.Module.Observer.Submit",
        path="src/C.java",
        line=7,
        origin="plugin",
        plugin_id="magento",
    )
    connection.commit()

    reader = _reader(connection)
    preloaded_ids = {
        relation["evidenceId"]
        for relation in reader.relations_for_paths(["src/C.java"])["relations"]
    }
    assert relation_id in preloaded_ids
    for pattern in ("relations_of", "framework_relations"):
        results = reader.query_graph(pattern, "src/C.java")["results"]
        assert [result["evidenceId"] for result in results] == [relation_id]
    connection.close()


@pytest.mark.parametrize(
    ("pattern", "target_role", "relation_kind", "relation_semantic"),
    (
        ("callers_of", "target", "php-instance-call-relation", "calls-instance"),
        ("callees_of", "source", "python-call-resolution", "calls-resolved-target"),
        (
            "references_to",
            "target",
            "data-contract-reference",
            "references-json-schema-target",
        ),
        ("references_to", "target", "magento-indexer-dependency", "depends-on-indexer"),
        ("imports_of", "source", "module-resolution", "resolves-import"),
        ("importers_of", "target", "module-resolution", "resolves-import"),
        ("triggers_of", "target", "quarkus-scheduled-method", "runs-on"),
        ("triggered_by", "source", "quarkus-scheduled-method", "runs-on"),
        ("publishers_of", "target", "quarkus-reactive-channel", "produces"),
        ("publishers_of", "target", "magento-event-dispatch", "dispatches-event"),
        ("publishers_of", "target", "magento-message-publisher", "publishes-through"),
        ("listeners_of", "source", "magento-effective-observer", "observed-by"),
        (
            "listeners_of",
            "source",
            "magento-template-event-listener",
            "listens-to-layout-dispatchers",
        ),
        ("handlers_of", "target", "fastapi-exception-handler", "handles"),
        ("endpoints_for", "source", "fastapi-route", "handles"),
        ("consumers_of", "target", "quarkus-reactive-channel", "consumes"),
        ("consumers_of", "target", "magento-message-consumer", "consumes-queue"),
    ),
)
def test_named_graph_patterns_match_emitted_plugin_vocabulary_and_direction(
    tmp_path,
    pattern,
    target_role,
    relation_kind,
    relation_semantic,
):
    _, _, connection, writer = _pending_store(tmp_path)
    source_id = writer.add_unit(_node(
        f"src/{relation_semantic}_source.py",
        f"events.{relation_semantic}.Source",
    ))
    target_id = writer.add_unit(_node(
        f"src/{relation_semantic}_target.py",
        f"events.{relation_semantic}.Target",
    ))
    evidence_id = writer.add_relation(
        kind=relation_kind,
        source=f"events.{relation_semantic}.Source",
        relation=relation_semantic,
        target=f"events.{relation_semantic}.Target",
        path=f"src/{relation_semantic}_source.py",
        line=1,
        origin="plugin",
        source_unit_id=source_id,
        target_unit_id=target_id,
    )
    connection.commit()

    results = _reader(connection).query_graph(
        pattern,
        {"source": source_id, "target": target_id}[target_role],
    )["results"]

    assert [result["evidenceId"] for result in results] == [evidence_id]
    connection.close()


def test_endpoints_for_requires_endpoint_target_or_known_route_fact(tmp_path):
    _, _, connection, writer = _pending_store(tmp_path)
    handler_id = writer.add_unit(_node("src/handler.py", "api.Handler.run"))
    endpoint_id = writer.add_unit(
        _node("src/endpoint.py", "GET /typed"),
        metadata_override={"symbol_kind": "Endpoint"},
    )
    class_id = writer.add_unit(
        _node("src/error.py", "DomainError"),
        metadata_override={"symbol_kind": "Class"},
    )
    typed_endpoint = writer.add_relation(
        kind="plugin-edge",
        source="api.Handler.run",
        relation="handles",
        target="GET /typed",
        path="src/handler.py",
        line=1,
        origin="plugin",
        source_unit_id=handler_id,
        target_unit_id=endpoint_id,
    )
    route_fact = writer.add_relation(
        kind="fastapi-route",
        source="api.Handler.run",
        relation="handles",
        target="GET /raw",
        path="src/handler.py",
        line=2,
        origin="plugin",
        source_unit_id=handler_id,
    )
    writer.add_relation(
        kind="fastapi-exception-handler",
        source="api.Handler.run",
        relation="handles",
        target="DomainError",
        path="src/handler.py",
        line=3,
        origin="plugin",
        source_unit_id=handler_id,
        target_unit_id=class_id,
    )
    writer.add_relation(
        kind="plugin-edge",
        source="api.Handler.run",
        relation="handles",
        target="DomainError",
        path="src/handler.py",
        line=4,
        origin="plugin",
        source_unit_id=handler_id,
        target_unit_id=class_id,
    )
    connection.commit()

    results = _reader(connection).query_graph("endpoints_for", handler_id)["results"]

    assert [result["evidenceId"] for result in results] == [
        typed_endpoint,
        route_fact,
    ]
    connection.close()


def test_relation_endpoint_names_are_normalized_and_indexed_for_delta_lookup(
    tmp_path,
):
    _, _, connection, writer = _pending_store(tmp_path)
    relation_id = writer.add_relation(
        kind="CALLS",
        source=" Package.Service ",
        relation="calls",
        target="PACKAGE.Dependency",
        path="src/service.py",
        line=1,
        origin="plugin",
    )

    assert [
        (row["role"], row["normalized_name"])
        for row in connection.execute(
            "SELECT role, normalized_name FROM relation_names "
            "WHERE relation_id = ? ORDER BY role",
            (relation_id,),
        )
    ] == [
        ("source", "package.service"),
        ("target", "package.dependency"),
    ]
    plan = connection.execute(
        "EXPLAIN QUERY PLAN SELECT relation_id FROM relation_names "
        "WHERE normalized_name = ? AND role = ?",
        ("package.service", "source"),
    ).fetchall()
    assert any(
        "idx_relation_names_lookup" in str(row["detail"])
        for row in plan
    )
    connection.execute(
        "DELETE FROM relations WHERE relation_id = ?", (relation_id,)
    )
    assert connection.execute(
        "SELECT 1 FROM relation_names WHERE relation_id = ?", (relation_id,)
    ).fetchone() is None
    connection.close()


def test_lexical_fallback_treats_like_metacharacters_as_text(tmp_path):
    _, _, connection, writer = _pending_store(tmp_path)
    writer.add_unit(_node("a.py", "a.Foo"))
    connection.commit()

    assert _reader(connection).search_units("%", max_results=10) == []
    connection.close()


def test_add_unit_fresh_insert_skips_duplicate_reads_and_name_cleanup(tmp_path):
    connection, recording, writer = _recording_writer(tmp_path)

    unit_id = writer.add_unit(_node("a.py", "a.Foo"))

    statements = _compact_sql(recording.execute_calls)
    assert statements[0].startswith("INSERT INTO UNITS(")
    assert "ON CONFLICT(UNIT_ID) DO NOTHING" in statements[0]
    assert "RETURNING ROWID" in statements[0]
    assert not any("FROM UNITS WHERE UNIT_ID = ?" in item for item in statements)
    assert not any("DELETE FROM UNIT_NAMES" in item for item in statements)
    assert len(recording.executemany_calls) == 1
    name_statement, name_rows = recording.executemany_calls[0]
    assert "INSERT OR IGNORE INTO unit_names" in name_statement
    assert name_rows == (
        ("foo", "Foo", unit_id),
        ("a.foo", "a.Foo", unit_id),
    )
    assert writer.unit_count == 1
    assert writer._touched_names == {"foo", "a.foo"}
    assert connection.execute("SELECT count(*) FROM units").fetchone()[0] == 1
    if writer.fts_available:
        assert sum(
            "INSERT OR REPLACE INTO UNITS_FTS" in item for item in statements
        ) == 1
        assert connection.execute("SELECT count(*) FROM units_fts").fetchone()[0] == 1
    connection.close()


def test_add_unit_equal_duplicate_does_not_rewrite_names_or_fts(tmp_path):
    connection, recording, writer = _recording_writer(tmp_path)
    node = _node("a.py", "a.Foo")
    unit_id = writer.add_unit(node)
    recording.reset()
    writer._touched_names.clear()

    duplicate_id = writer.add_unit(node)

    statements = _compact_sql(recording.execute_calls)
    assert duplicate_id == unit_id
    assert len(statements) == 2
    assert statements[0].startswith("INSERT INTO UNITS(")
    assert "ON CONFLICT(UNIT_ID) DO NOTHING" in statements[0]
    assert statements[1].startswith("SELECT ROWID, RECORD_TYPE")
    assert not any(item.startswith("UPDATE UNITS SET") for item in statements)
    assert not any("DELETE FROM UNIT_NAMES" in item for item in statements)
    assert not any("UNITS_FTS" in item for item in statements)
    assert recording.executemany_calls == []
    assert writer.unit_count == 1
    assert writer._touched_names == set()
    connection.close()


def test_add_unit_changed_collision_updates_names_fts_and_not_count(tmp_path):
    connection, recording, writer = _recording_writer(tmp_path)
    unit_id = writer.add_unit(_alias_node("ObsoleteAlias"))
    recording.reset()
    writer._touched_names.clear()

    changed_id = writer.add_unit(_alias_node("CurrentAlias"))

    statements = _compact_sql(recording.execute_calls)
    assert changed_id == unit_id
    assert statements[0].startswith("INSERT INTO UNITS(")
    assert statements[1].startswith("SELECT ROWID, RECORD_TYPE")
    assert sum(item.startswith("UPDATE UNITS SET") for item in statements) == 1
    assert sum("DELETE FROM UNIT_NAMES" in item for item in statements) == 1
    assert len(recording.executemany_calls) == 1
    _, name_rows = recording.executemany_calls[0]
    assert name_rows == (
        ("currentalias", "CurrentAlias", unit_id),
        ("currentaliasextra", "CurrentAliasExtra", unit_id),
        ("stableservice", "StableService", unit_id),
        ("package.stableservice", "package.StableService", unit_id),
    )
    assert writer.unit_count == 1
    assert writer._touched_names == {
        "currentalias",
        "currentaliasextra",
        "stableservice",
        "package.stableservice",
    }
    assert [
        row["display_name"]
        for row in connection.execute(
            "SELECT display_name FROM unit_names WHERE unit_id = ? "
            "ORDER BY display_name",
            (unit_id,),
        )
    ] == [
        "CurrentAlias",
        "CurrentAliasExtra",
        "StableService",
        "package.StableService",
    ]
    if writer.fts_available:
        assert sum(
            "INSERT OR REPLACE INTO UNITS_FTS" in item for item in statements
        ) == 1
        assert connection.execute(
            "SELECT count(*) FROM units_fts WHERE units_fts MATCH ?",
            ('"ObsoleteAlias"',),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM units_fts WHERE units_fts MATCH ?",
            ('"CurrentAlias"',),
        ).fetchone()[0] == 1
    connection.close()


def test_unit_fts_upsert_avoids_unindexed_delete_and_replaces_duplicate_terms(
    tmp_path,
):
    _, _, connection, writer = _pending_store(tmp_path)
    if not writer.fts_available:
        pytest.skip("SQLite FTS5 is unavailable")

    delete_plan = connection.execute(
        "EXPLAIN QUERY PLAN DELETE FROM units_fts WHERE unit_id = ?",
        ("unused",),
    ).fetchall()
    assert any(
        "SCAN" in row["detail"].upper()
        and "VIRTUAL TABLE" in row["detail"].upper()
        for row in delete_plan
    )

    def alias_node(primary_name):
        return TextNode(
            "stable source body",
            {
                "path": "src/service.py",
                "start_line": 4,
                "end_line": 6,
                "primary_name": primary_name,
                "symbol_qualified_name": "package.StableService",
                "symbol_names": [f"{primary_name}Extra"],
                "language": "python",
            },
        )

    statements = []
    connection.set_trace_callback(statements.append)
    first_id = writer.add_unit(alias_node("ObsoleteAlias"))
    duplicate_id = writer.add_unit(alias_node("CurrentAlias"))
    connection.set_trace_callback(None)
    connection.commit()

    assert duplicate_id == first_id
    assert not any(
        "DELETE FROM UNITS_FTS" in statement.upper()
        for statement in statements
    )
    assert sum(
        "INSERT OR REPLACE INTO UNITS_FTS" in statement.upper()
        for statement in statements
    ) == 2
    fts_rows = connection.execute(
        "SELECT units.rowid AS unit_rowid, units_fts.rowid AS fts_rowid "
        "FROM units JOIN units_fts ON units_fts.unit_id = units.unit_id "
        "WHERE units.unit_id = ?",
        (first_id,),
    ).fetchall()
    assert len(fts_rows) == 1
    assert fts_rows[0]["fts_rowid"] == fts_rows[0]["unit_rowid"]
    assert connection.execute(
        "SELECT count(*) AS count FROM units_fts WHERE units_fts MATCH ?",
        ('"ObsoleteAlias"',),
    ).fetchone()["count"] == 0
    assert connection.execute(
        "SELECT count(*) AS count FROM units_fts WHERE units_fts MATCH ?",
        ('"CurrentAlias"',),
    ).fetchone()["count"] == 1
    assert [
        row["display_name"]
        for row in connection.execute(
            "SELECT display_name FROM unit_names WHERE unit_id = ? "
            "ORDER BY display_name",
            (first_id,),
        ).fetchall()
    ] == [
        "CurrentAlias",
        "CurrentAliasExtra",
        "StableService",
        "package.StableService",
    ]
    connection.close()


def test_relation_preload_limits_selected_sql_and_reports_total_coverage(tmp_path):
    _, _, connection, writer = _pending_store(tmp_path)
    unit_id = writer.add_unit(_node("a.py", "a.Foo"))
    for index in range(5):
        writer.add_relation(
            kind="CALLS",
            source="a.Foo",
            relation="calls",
            target=f"target{index}",
            path="a.py",
            line=index + 1,
            origin="tree-sitter",
            source_unit_id=unit_id,
        )
    connection.commit()
    statements = []
    connection.set_trace_callback(statements.append)

    result = _reader(connection).relations_for_paths(
        ["a.py"],
        max_relations=2,
    )

    assert len(result["relations"]) == 2
    assert result["coverage"] == {
        "state": "bounded",
        "totalRelations": 5,
        "omittedRelations": 3,
        "preloadedSymbols": 1,
        "omittedSymbols": 0,
    }
    assert any(
        "JOIN relation_paths" in statement and "LIMIT 3" in statement
        for statement in statements
    )
    connection.close()


def test_symbol_truncation_marks_relation_preload_as_bounded(tmp_path):
    _, _, connection, writer = _pending_store(tmp_path)
    for index in range(161):
        writer.add_unit(
            _node("dense.py", f"dense.Symbol{index}", f"source {index}")
        )
    connection.commit()

    coverage = _reader(connection).relations_for_paths(["dense.py"])["coverage"]

    assert coverage["totalRelations"] == 0
    assert coverage["preloadedSymbols"] == 160
    assert coverage["omittedSymbols"] == 1
    assert coverage["state"] == "bounded"
    connection.close()


def test_receipt_persists_stats_and_enforces_database_schema_revision(tmp_path):
    store, pending, connection, writer = _pending_store(tmp_path)
    writer.add_unit(_node("a.py", "a.Foo"))
    receipt = build_receipt(
        connection,
        workspace="workspace",
        project="project",
        branch="main",
        revision="revision",
        source_tree_sha256="b" * 64,
        collection_target=pending.target,
        repository_facts_json="{}",
        plugin_ids=(),
        plugin_fingerprint="sha256:" + "0" * 64,
        plugin_descriptor_fingerprint="sha256:" + "0" * 64,
        plugin_implementation_fingerprint="sha256:" + "0" * 64,
        index_representation_fingerprint="sha256:" + "1" * 64,
        include_patterns=None,
        exclude_patterns=None,
        document_count=3,
        skipped_file_count=2,
    )
    assert receipt["store_schema"] == STRUCTURAL_STORE_SCHEMA
    assert receipt["store_schema_revision"] == STRUCTURAL_STORE_SCHEMA_REVISION
    stats = RAGIndexManager._stats_from_receipt(receipt)
    assert stats.document_count == 3
    assert stats.skipped_file_count == 2

    writer.seal(receipt)
    connection.close()
    write_receipt(pending.receipt, receipt)
    published = store.publish(pending)
    with store.open_bound(
        target=published.target,
        workspace="workspace",
        project="project",
        branch="main",
        revision="revision",
        manifest_sha256=receipt["generation_manifest_sha256"],
    ):
        pass

    incompatible = store.connect(published.database)
    incompatible.execute("PRAGMA user_version = 0")
    incompatible.commit()
    incompatible.close()
    with pytest.raises(ExactIndexPreconditionError, match="incompatible"):
        with store.open_bound(
            target=published.target,
            workspace="workspace",
            project="project",
            branch="main",
            revision="revision",
            manifest_sha256=receipt["generation_manifest_sha256"],
        ):
            pass


def test_receipt_reuses_and_invalidates_canonical_relation_digest_cache(tmp_path):
    _, pending, connection, writer = _pending_store(tmp_path)
    writer.add_relation(
        kind="CALLS",
        source="a.Foo",
        relation="calls",
        target="b.Bar",
        path="a.py",
        line=1,
        origin="plugin",
    )

    def receipt():
        return build_receipt(
            connection,
            workspace="workspace",
            project="project",
            branch="main",
            revision="revision",
            source_tree_sha256="b" * 64,
            collection_target=pending.target,
            repository_facts_json="{}",
            plugin_ids=(),
            plugin_fingerprint="sha256:" + "0" * 64,
            plugin_descriptor_fingerprint="sha256:" + "0" * 64,
            plugin_implementation_fingerprint="sha256:" + "0" * 64,
            index_representation_fingerprint="sha256:" + "1" * 64,
            include_patterns=None,
            exclude_patterns=None,
            document_count=1,
            skipped_file_count=0,
        )

    first = receipt()
    assert connection.execute(
        "SELECT count(*) FROM relation_manifest_cache"
    ).fetchone()[0] == 1
    assert receipt()["generation_members_sha256"] == first[
        "generation_members_sha256"
    ]

    connection.execute("UPDATE relations SET target = 'c.Baz'")
    assert connection.execute(
        "SELECT count(*) FROM relation_manifest_cache"
    ).fetchone()[0] == 0
    changed = receipt()
    assert changed["generation_members_sha256"] != first[
        "generation_members_sha256"
    ]
    assert connection.execute(
        "SELECT count(*) FROM relation_manifest_cache"
    ).fetchone()[0] == 1
    connection.close()


def test_pending_cleanup_skips_a_generation_with_a_live_owner(tmp_path):
    store, pending, connection, _ = _pending_store(tmp_path)
    connection.close()
    ownership = store.acquire_pending_ownership(pending)
    old = 1
    os.utime(pending.directory, (old, old))

    assert store.cleanup_pending(max_age_seconds=0) == 0
    assert pending.directory.is_dir()

    store.release_pending_ownership(ownership)
    assert store.cleanup_pending(max_age_seconds=0) == 1
    assert not pending.directory.exists()


def test_review_generation_cleanup_is_ttl_bound_and_never_deletes_branch_index(
    tmp_path,
):
    store = StructuralGenerationStore(tmp_path / "structural")
    review_paths, review_receipt = _publish_generation(
        store,
        "cc_review_g_test",
        snapshot_kind="proposed_tree",
    )
    branch_paths, _ = _publish_generation(
        store,
        "branch-generation",
        snapshot_kind="target_head",
    )
    old = 1
    os.utime(review_paths.directory, (old, old))
    os.utime(branch_paths.directory, (old, old))

    candidates = store.expired_review_generation_receipts(
        max_age_seconds=60,
    )
    assert [item["collection_target"] for item in candidates] == [
        "cc_review_g_test",
    ]

    # A successful graph read refreshes only the request-scoped generation's
    # access time and cancels the pending expiry.
    with store.open_bound(
        target=review_paths.target,
        workspace="workspace",
        project="project",
        branch="main",
        revision="revision",
        manifest_sha256=review_receipt["generation_manifest_sha256"],
    ):
        pass
    assert store.expired_review_generation_receipts(
        max_age_seconds=60,
    ) == []

    os.utime(review_paths.directory, (old, old))
    manager = RAGIndexManager.__new__(RAGIndexManager)
    manager.store = store
    manager.config = SimpleNamespace(review_generation_ttl_seconds=60)
    manager._mutation_coordinator = ProjectMutationCoordinator(
        "redis://unused",
        enabled=False,
    )

    assert manager.cleanup_expired_review_generations() == 1
    assert not review_paths.directory.exists()
    assert branch_paths.directory.exists()


def test_review_generation_cleanup_retains_an_active_exact_reader(tmp_path):
    store = StructuralGenerationStore(tmp_path / "structural")
    review_paths, review_receipt = _publish_generation(
        store,
        "cc_review_g_active",
        snapshot_kind="proposed_tree",
    )
    manager = RAGIndexManager.__new__(RAGIndexManager)
    manager.store = store
    manager.config = SimpleNamespace(review_generation_ttl_seconds=60)
    manager._mutation_coordinator = ProjectMutationCoordinator(
        "redis://unused",
        enabled=False,
    )

    with store.open_bound(
        target=review_paths.target,
        workspace="workspace",
        project="project",
        branch="main",
        revision="revision",
        manifest_sha256=review_receipt["generation_manifest_sha256"],
    ):
        # Force the access timestamp past the TTL while the shared reader lock
        # remains held.  The janitor must retain the generation, not race the
        # in-flight SQLite query.
        os.utime(review_paths.directory, (1, 1))
        assert manager.cleanup_expired_review_generations() == 0
        assert review_paths.directory.is_dir()

    os.utime(review_paths.directory, (1, 1))
    assert manager.cleanup_expired_review_generations() == 1
    assert not review_paths.directory.exists()


def test_exact_generation_deletion_retains_an_active_reader(tmp_path):
    store = StructuralGenerationStore(tmp_path / "structural")
    paths, receipt = _publish_generation(
        store,
        "branch-generation",
        snapshot_kind="target_head",
    )
    delete_arguments = {
        "workspace": "workspace",
        "project": "project",
        "branch": "main",
        "revision": "revision",
        "manifest_sha256": receipt["generation_manifest_sha256"],
    }

    with store.open_bound(
        target=paths.target,
        workspace="workspace",
        project="project",
        branch="main",
        revision="revision",
        manifest_sha256=receipt["generation_manifest_sha256"],
    ):
        assert store.delete(paths.target, **delete_arguments) is False
        assert paths.directory.is_dir()

    assert store.delete(paths.target, **delete_arguments) is True
    assert not paths.directory.exists()


def test_file_hierarchy_and_path_scoped_resolution_keep_cross_file_calls_ambiguous(
    tmp_path,
):
    _, _, connection, writer = _pending_store(tmp_path)
    file_id = writer.add_file(TextNode(
        "class Service:\n    def run(self): pass\n",
        {"path": "src/service.py", "language": "python"},
    ))
    class_id = writer.add_unit(_node("src/service.py", "Service"))
    method_id = writer.add_unit(_node("src/service.py", "Service.run"))
    writer.add_file_containment(
        file_id,
        class_id,
        _node("src/service.py", "Service"),
    )
    writer.add_unit(_node("src/other.py", "Other.run"))
    writer.add_symbol(SimpleNamespace(
        path="src/service.py",
        line=1,
        qualified_name="package.Service",
        kind="class",
        parents=(),
        methods=(),
        constructor_types=(),
        attributes=(),
    ))
    contains_id = writer.add_relation(
        kind="CONTAINS",
        source="Service",
        relation="contains",
        target="Service.run",
        path="src/service.py",
        line=1,
        origin="tree-sitter",
    )
    ambiguous_call_id = writer.add_relation(
        kind="CALLS",
        source="Service.run",
        relation="calls",
        target="run",
        path="src/service.py",
        line=1,
        origin="tree-sitter",
        source_unit_id=method_id,
    )
    registration_id = writer.add_relation(
        kind="magento-module-registration",
        source="src/service.py",
        relation="registers-module",
        target="Package_Service",
        path="src/service.py",
        line=1,
        origin="plugin",
    )

    writer.resolve_relations()
    connection.commit()

    file_row = connection.execute(
        "SELECT record_type, kind FROM units WHERE unit_id = ?",
        (file_id,),
    ).fetchone()
    assert dict(file_row) == {"record_type": "structural_file", "kind": "file"}
    file_edge = connection.execute(
        "SELECT source_unit_id, target_unit_id FROM relations "
        "WHERE origin = 'structural-index'",
    ).fetchone()
    assert tuple(file_edge) == (file_id, class_id)
    contains = connection.execute(
        "SELECT source_unit_id, target_unit_id FROM relations WHERE relation_id = ?",
        (contains_id,),
    ).fetchone()
    assert tuple(contains) == (class_id, method_id)
    ambiguous = connection.execute(
        "SELECT target_unit_id FROM relations WHERE relation_id = ?",
        (ambiguous_call_id,),
    ).fetchone()
    assert ambiguous["target_unit_id"] is None
    registration = connection.execute(
        "SELECT source_unit_id FROM relations WHERE relation_id = ?",
        (registration_id,),
    ).fetchone()
    assert registration["source_unit_id"] == file_id
    connection.close()


def test_file_containment_keeps_distinct_units_with_identical_display_identity(
    tmp_path,
):
    _, _, connection, writer = _pending_store(tmp_path)
    file_id = writer.add_file(TextNode(
        "fallback source",
        {"path": "src/fallback.txt", "language": "text"},
    ))
    fallback_nodes = tuple(
        TextNode(
            content,
            {
                "path": "src/fallback.txt",
                "start_line": 1,
                "end_line": 1,
                "primary_name": "fallback.txt",
                "language": "text",
            },
        )
        for content in ("first fallback", "second fallback", "third fallback")
    )
    child_ids = tuple(writer.add_unit(node) for node in fallback_nodes)
    for child_id, node in zip(child_ids, fallback_nodes, strict=True):
        writer.add_file_containment(file_id, child_id, node)
    connection.commit()

    relations = connection.execute(
        "SELECT relation_id, source, target, target_unit_id FROM relations "
        "WHERE source_unit_id = ? AND kind = 'CONTAINS' "
        "AND origin = 'structural-index' ORDER BY relation_id",
        (file_id,),
    ).fetchall()
    assert len(set(child_ids)) == 3
    assert len(relations) == 3
    assert {row["target_unit_id"] for row in relations} == set(child_ids)
    assert {row["source"] for row in relations} == {"src/fallback.txt"}
    assert {row["target"] for row in relations} == {"fallback.txt"}
    connection.close()


def test_plugin_containment_uses_concrete_symbol_and_context_identity(tmp_path):
    _, _, connection, writer = _pending_store(tmp_path)
    file_id = writer.add_file(TextNode(
        "plugin source",
        {"path": "src/plugin.php", "language": "php"},
    ))
    symbol_ids = tuple(
        writer.add_symbol(
            SimpleNamespace(
                path="src/plugin.php",
                line=1,
                qualified_name="Vendor\\Thing",
                kind=kind,
                parents=(),
                methods=(),
                constructor_types=(),
                attributes=(),
            ),
            plugin_id="php",
        )
        for kind in ("class", "interface")
    )
    context_ids = tuple(
        writer.add_context(SimpleNamespace(
            path="src/plugin.php",
            plugin_id="framework",
            kind="context",
            content=content,
            attributes=(),
        ))
        for content in ("first context", "other context")
    )
    connection.commit()

    relations = connection.execute(
        "SELECT target, target_unit_id FROM relations "
        "WHERE source_unit_id = ? AND kind = 'CONTAINS' "
        "AND origin = 'plugin' ORDER BY relation_id",
        (file_id,),
    ).fetchall()
    expected_ids = {*symbol_ids, *context_ids}
    assert len(expected_ids) == 4
    assert len(relations) == 4
    assert {row["target_unit_id"] for row in relations} == expected_ids
    assert {row["target"] for row in relations} == {
        "Vendor\\Thing",
        "framework:context",
    }
    connection.close()


def test_php_method_graph_fact_projects_exact_method_endpoints(tmp_path):
    _, _, connection, writer = _pending_store(tmp_path)
    caller_id = writer.add_unit(TextNode(
        "function export() {}",
        {
            "path": "src/Caller.php",
            "start_line": 1,
            "end_line": 1,
            "primary_name": "export",
            "symbol_qualified_name": "Caller.export",
            "parent_class": "Caller",
            "namespace": "Vendor",
            "language": "php",
        },
    ))
    target_id = writer.add_unit(TextNode(
        "function fetch() {}",
        {
            "path": "src/Base.php",
            "start_line": 1,
            "end_line": 1,
            "primary_name": "fetch",
            "symbol_qualified_name": "Base.fetch",
            "parent_class": "Base",
            "namespace": "Vendor",
            "language": "php",
        },
    ))
    writer.add_unit(TextNode(
        "function fetch() {}",
        {
            "path": "src/OtherBase.php",
            "start_line": 1,
            "end_line": 1,
            "primary_name": "fetch",
            "symbol_qualified_name": "Base.fetch",
            "parent_class": "Base",
            "namespace": "Other",
            "language": "php",
        },
    ))
    relation_id = writer.add_graph_fact(
        SimpleNamespace(
            kind="php-instance-call-relation",
            source="Vendor\\Caller",
            relation="calls-instance",
            target="Vendor\\Child",
            path="src/Caller.php",
            line=12,
            related_paths=("src/Base.php",),
            attributes=(
                ("callerMethod", "export"),
                ("targetMethod", "fetch"),
                ("targetMethodDeclared", "true"),
                ("targetMethodDeclaredOn", "Vendor\\Base"),
            ),
        ),
        plugin_id="php",
    )
    unproven_id = writer.add_graph_fact(
        SimpleNamespace(
            kind="php-intra-class-call-relation",
            source="Vendor\\Caller",
            relation="calls-instance",
            target="Vendor\\Caller",
            path="src/Caller.php",
            line=13,
            related_paths=(),
            attributes=(
                ("callerMethod", "export"),
                ("targetMethod", "missing"),
                ("targetMethodDeclared", "false"),
            ),
        ),
        plugin_id="php",
    )

    writer.resolve_relations()
    connection.commit()

    row = connection.execute(
        "SELECT * FROM relations WHERE relation_id = ?",
        (relation_id,),
    ).fetchone()
    assert row["source"] == "Vendor\\Caller::export"
    assert row["target"] == "Vendor\\Base::fetch"
    assert row["source_unit_id"] == caller_id
    assert row["target_unit_id"] == target_id
    assert json.loads(row["attributes_json"])["declaringTarget"] == "Vendor\\Child"
    unproven = connection.execute(
        "SELECT * FROM relations WHERE relation_id = ?",
        (unproven_id,),
    ).fetchone()
    assert unproven["source"] == "Vendor\\Caller::export"
    assert unproven["target"] == "Vendor\\Caller::missing"
    assert unproven["source"] != unproven["target"]
    assert unproven["source_unit_id"] == caller_id
    assert unproven["target_unit_id"] is None
    assert json.loads(unproven["attributes_json"])[
        "targetResolutionProven"
    ] is False
    connection.close()


def test_graph_fact_preserves_all_contributing_plugins_on_one_relation(tmp_path):
    _, _, connection, writer = _pending_store(tmp_path)
    relation_id = writer.add_graph_fact(
        SimpleNamespace(
            kind="framework-binding",
            source="source",
            relation="binds",
            target="target",
            path="src/config.xml",
            line=4,
            related_paths=(),
            attributes=(),
            contributing_plugin_ids=("framework", "xml"),
        ),
        plugin_id=None,
    )
    # Re-adding the same semantic fact from a packet contributes provenance
    # without creating a duplicate edge.
    duplicate_id = writer.add_relation(
        kind="framework-binding",
        source="source",
        relation="binds",
        target="target",
        path="src/config.xml",
        line=4,
        origin="plugin",
        plugin_id="repository-plugin",
    )
    connection.commit()

    assert duplicate_id == relation_id
    assert connection.execute(
        "SELECT count(*) AS count FROM relations"
    ).fetchone()["count"] == 1
    assert [
        row["plugin_id"]
        for row in connection.execute(
            "SELECT plugin_id FROM relation_plugins WHERE relation_id = ? "
            "ORDER BY plugin_id",
            (relation_id,),
        ).fetchall()
    ] == ["framework", "repository-plugin", "xml"]
    row = connection.execute(
        "SELECT * FROM relations WHERE relation_id = ?",
        (relation_id,),
    ).fetchone()
    assert row["plugin_id"] is None
    assert relation_to_manifest(connection, row)["origin"]["plugins"] == [
        "framework",
        "repository-plugin",
        "xml",
    ]
    connection.close()


def test_relation_insert_skips_redundant_contributor_reconciliation(tmp_path):
    _, _, connection, writer = _pending_store(tmp_path)

    def add_relation(target, *, plugin_id=None, plugin_ids=()):
        return writer.add_relation(
            kind="framework-binding",
            source="source",
            relation="binds",
            target=target,
            path="src/config.xml",
            line=4,
            origin="plugin",
            plugin_id=plugin_id,
            plugin_ids=plugin_ids,
        )

    ordinary_statements = []
    connection.set_trace_callback(ordinary_statements.append)
    single_id = add_relation("single", plugin_id="alpha")
    multiple_id = add_relation(
        "multiple",
        plugin_ids=("gamma", "beta"),
    )
    connection.set_trace_callback(None)

    ordinary_sql = "\n".join(ordinary_statements).upper()
    assert "SELECT PLUGIN_ID FROM RELATION_PLUGINS" not in ordinary_sql
    assert "UPDATE RELATIONS SET PLUGIN_ID" not in ordinary_sql
    assert connection.execute(
        "SELECT plugin_id FROM relations WHERE relation_id = ?",
        (single_id,),
    ).fetchone()["plugin_id"] == "alpha"
    assert connection.execute(
        "SELECT plugin_id FROM relations WHERE relation_id = ?",
        (multiple_id,),
    ).fetchone()["plugin_id"] is None
    assert [
        row["plugin_id"]
        for row in connection.execute(
            "SELECT plugin_id FROM relation_plugins WHERE relation_id = ? "
            "ORDER BY plugin_id",
            (multiple_id,),
        ).fetchall()
    ] == ["beta", "gamma"]

    conflict_statements = []
    connection.set_trace_callback(conflict_statements.append)
    assert add_relation("single", plugin_id="beta") == single_id
    connection.set_trace_callback(None)
    conflict_sql = "\n".join(conflict_statements).upper()
    assert "SELECT PLUGIN_ID FROM RELATION_PLUGINS" in conflict_sql
    assert "UPDATE RELATIONS SET PLUGIN_ID" in conflict_sql
    assert connection.execute(
        "SELECT plugin_id FROM relations WHERE relation_id = ?",
        (single_id,),
    ).fetchone()["plugin_id"] is None
    assert [
        row["plugin_id"]
        for row in connection.execute(
            "SELECT plugin_id FROM relation_plugins WHERE relation_id = ? "
            "ORDER BY plugin_id",
            (single_id,),
        ).fetchall()
    ] == ["alpha", "beta"]
    connection.close()


def test_symbol_preserves_contributing_plugins_on_metadata_and_relations(tmp_path):
    _, _, connection, writer = _pending_store(tmp_path)
    writer.add_file(TextNode(
        "class Service(Base):\n    pass\n",
        {"path": "src/service.py", "language": "python"},
    ))
    unit_id = writer.add_symbol(
        SimpleNamespace(
            path="src/service.py",
            line=1,
            qualified_name="package.Service",
            kind="class",
            parents=("package.Base",),
            methods=(),
            constructor_types=("package.Dependency",),
            attributes=(),
        ),
        plugin_ids=("framework", "python"),
    )
    connection.commit()

    unit = connection.execute(
        "SELECT metadata_json FROM units WHERE unit_id = ?",
        (unit_id,),
    ).fetchone()
    metadata = json.loads(unit["metadata_json"])
    assert metadata["plugin_id"] is None
    assert metadata["plugin_ids"] == ["framework", "python"]

    relations = connection.execute(
        "SELECT relation_id, kind, plugin_id FROM relations "
        "WHERE origin = 'plugin' AND "
        "(source_unit_id = ? OR target_unit_id = ?) ORDER BY kind",
        (unit_id, unit_id),
    ).fetchall()
    assert [row["kind"] for row in relations] == [
        "CONSTRUCTOR_DEPENDENCY",
        "CONTAINS",
        "INHERITS",
    ]
    assert all(row["plugin_id"] is None for row in relations)
    for relation in relations:
        assert [
            row["plugin_id"]
            for row in connection.execute(
                "SELECT plugin_id FROM relation_plugins "
                "WHERE relation_id = ? ORDER BY plugin_id",
                (relation["relation_id"],),
            ).fetchall()
        ] == ["framework", "python"]
    connection.close()


def test_bound_generation_clone_is_private_unsealed_and_can_be_resealed(tmp_path):
    store = StructuralGenerationStore(tmp_path / "structural")
    source_paths, source_receipt = _publish_generation(
        store,
        "base-generation",
        snapshot_kind="target_head",
    )

    pending_before = set(store.pending_root.iterdir())
    with pytest.raises(ExactIndexPreconditionError, match="changed|match"):
        store.clone_bound_to_pending(
            source_target=source_paths.target,
            target="unreachable-review-generation",
            workspace="workspace",
            project="project",
            branch="main",
            revision="revision",
            manifest_sha256="f" * 64,
        )
    assert set(store.pending_root.iterdir()) == pending_before

    pending, connection, base_receipt, ownership = (
        store.clone_bound_to_pending(
            source_target=source_paths.target,
            target="review-generation",
            workspace="workspace",
            project="project",
            branch="main",
            revision="revision",
            manifest_sha256=source_receipt["generation_manifest_sha256"],
        )
    )
    try:
        assert pending.directory.parent == store.pending_root
        assert base_receipt == source_receipt
        assert connection.execute(
            "SELECT count(*) FROM generation"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT qualified_name FROM units"
        ).fetchone()["qualified_name"] == "a.Foo"

        clone_writer = StructuralGraphWriter(connection)
        clone_writer.refresh_counts()
        clone_writer.add_unit(_node("b.py", "b.Bar"))
        review_receipt = build_receipt(
            connection,
            workspace="workspace",
            project="project",
            branch="main",
            revision="review-revision",
            source_tree_sha256="c" * 64,
            collection_target=pending.target,
            repository_facts_json="{}",
            plugin_ids=(),
            plugin_fingerprint="sha256:" + "0" * 64,
            plugin_descriptor_fingerprint="sha256:" + "0" * 64,
            plugin_implementation_fingerprint="sha256:" + "0" * 64,
            index_representation_fingerprint="sha256:" + "1" * 64,
            include_patterns=None,
            exclude_patterns=None,
            document_count=2,
            skipped_file_count=0,
            snapshot_metadata={
                "kind": "proposed_tree",
                "baseGenerationManifestSha256": source_receipt[
                    "generation_manifest_sha256"
                ],
            },
        )
        clone_writer.seal(review_receipt)
        connection.close()
        connection = None
        write_receipt(pending.receipt, review_receipt)
    finally:
        if connection is not None:
            connection.close()
        store.release_pending_ownership(ownership)

    review_paths = store.publish(pending)
    with store.open_bound(
        target=review_paths.target,
        workspace="workspace",
        project="project",
        branch="main",
        revision="review-revision",
        manifest_sha256=review_receipt["generation_manifest_sha256"],
    ) as (review_connection, sealed_receipt):
        assert sealed_receipt == review_receipt
        assert review_connection.execute(
            "SELECT count(*) FROM units"
        ).fetchone()[0] == 2

    # Mutating and sealing the cloned database did not alter the immutable base.
    with store.open_bound(
        target=source_paths.target,
        workspace="workspace",
        project="project",
        branch="main",
        revision="revision",
        manifest_sha256=source_receipt["generation_manifest_sha256"],
    ) as (source_connection, _):
        assert [
            row["qualified_name"]
            for row in source_connection.execute(
                "SELECT qualified_name FROM units ORDER BY qualified_name"
            )
        ] == ["a.Foo"]


def test_bound_generation_backup_finishes_before_destination_wal_is_enabled(
    tmp_path,
    monkeypatch,
):
    observations = []
    original_connect = structural_store_module.sqlite3.connect

    class TrackingConnection(structural_store_module.sqlite3.Connection):
        def backup(self, target, *args, **kwargs):
            database_path = target.execute(
                "PRAGMA database_list"
            ).fetchone()[2]
            journal_mode_before = target.execute(
                "PRAGMA journal_mode"
            ).fetchone()[0]
            result = super().backup(target, *args, **kwargs)
            wal_path = database_path + "-wal"
            observations.append(
                {
                    "journal_mode_before": journal_mode_before,
                    "database_bytes_after": os.path.getsize(database_path),
                    "wal_bytes_after": (
                        os.path.getsize(wal_path)
                        if os.path.exists(wal_path)
                        else 0
                    ),
                }
            )
            return result

    def tracking_connect(*args, **kwargs):
        kwargs["factory"] = TrackingConnection
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(
        structural_store_module.sqlite3,
        "connect",
        tracking_connect,
    )
    store = StructuralGenerationStore(tmp_path / "structural")
    source_paths, source_receipt = _publish_generation(
        store,
        "backup-base-generation",
        snapshot_kind="target_head",
    )

    pending, connection, _, ownership = store.clone_bound_to_pending(
        source_target=source_paths.target,
        target="backup-review-generation",
        workspace="workspace",
        project="project",
        branch="main",
        revision="revision",
        manifest_sha256=source_receipt["generation_manifest_sha256"],
    )
    try:
        assert observations == [
            {
                "journal_mode_before": "delete",
                "database_bytes_after": source_paths.database.stat().st_size,
                "wal_bytes_after": 0,
            }
        ]
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        connection.close()
        store.release_pending_ownership(ownership)
        store.remove_pending(pending)


def test_remove_paths_expands_non_packet_dependencies_and_cleans_fts(tmp_path):
    _, _, connection, base_writer = _pending_store(tmp_path)
    unit_ids = {
        path: base_writer.add_unit(_node(path, qualified_name))
        for path, qualified_name in (
            ("src/a.py", "package.A"),
            ("src/b.py", "package.B"),
            ("src/c.py", "package.C"),
        )
    }
    cross_file_relation = base_writer.add_relation(
        kind="CALLS",
        source="package.B",
        relation="calls",
        target="package.A",
        path="src/b.py",
        line=3,
        origin="plugin",
        related_paths=("src/a.py",),
        source_unit_id=unit_ids["src/b.py"],
        target_unit_id=unit_ids["src/a.py"],
    )
    packet_relation = base_writer.add_relation(
        kind="repository-packet",
        source="package.C",
        relation="summarizes",
        target="package.A",
        path="src/c.py",
        line=5,
        origin="plugin",
        related_paths=("src/a.py",),
        source_unit_id=unit_ids["src/c.py"],
        target_unit_id=unit_ids["src/a.py"],
        attributes={"packetKind": "repository"},
    )
    connection.commit()

    writer = StructuralGraphWriter(connection, track_mutations=True)
    writer.refresh_counts()
    affected_paths, old_document_paths = writer.remove_paths(("src/a.py",))
    connection.commit()

    assert affected_paths == ("src/a.py", "src/b.py")
    assert old_document_paths == frozenset({"src/a.py", "src/b.py"})
    assert [
        row["relation_id"]
        for row in connection.execute(
            "SELECT relation_id FROM relations ORDER BY relation_id"
        )
    ] == [packet_relation]
    assert connection.execute(
        "SELECT relation_id FROM relations WHERE relation_id = ?",
        (cross_file_relation,),
    ).fetchone() is None
    assert [
        row["path"]
        for row in connection.execute("SELECT path FROM units ORDER BY path")
    ] == ["src/c.py"]
    if writer.fts_available:
        assert [
            row["unit_id"]
            for row in connection.execute(
                "SELECT unit_id FROM units_fts ORDER BY unit_id"
            )
        ] == [unit_ids["src/c.py"]]
        assert connection.execute(
            "SELECT count(*) FROM units_fts "
            "LEFT JOIN units ON units.rowid = units_fts.rowid "
            "WHERE units.rowid IS NULL"
        ).fetchone()[0] == 0
    connection.close()


def test_remove_paths_interrupts_in_flight_sql_on_cancellation(
    tmp_path,
    monkeypatch,
):
    _, _, connection, writer = _pending_store(tmp_path)
    cancellation_checks = 0

    class ExpectedCancellation(InterruptedError):
        pass

    def cancellation_check():
        nonlocal cancellation_checks
        cancellation_checks += 1
        if cancellation_checks > 1:
            raise ExpectedCancellation("cancelled during graph mutation")

    def run_long_query(_paths):
        connection.execute(
            "WITH RECURSIVE counter(value) AS ("
            "SELECT 1 UNION ALL SELECT value + 1 FROM counter "
            "WHERE value < 1000000) SELECT sum(value) FROM counter"
        ).fetchone()
        return (), frozenset()

    monkeypatch.setattr(writer, "_remove_paths", run_long_query)
    try:
        with pytest.raises(
            ExpectedCancellation,
            match="cancelled during graph mutation",
        ):
            writer.remove_paths(
                ("src/example.py",),
                cancellation_check=cancellation_check,
            )

        assert cancellation_checks > 1
        # The progress handler is request-scoped and must not poison later SQL.
        assert connection.execute("SELECT 1").fetchone()[0] == 1
    finally:
        connection.close()


def test_repository_output_removal_keeps_file_facts_and_drops_global_rows(
    tmp_path,
):
    _, _, connection, base_writer = _pending_store(tmp_path)
    file_id = base_writer.add_file(TextNode(
        "class Service: pass\n",
        {"path": "src/service.py", "language": "python"},
    ))
    source_id = base_writer.add_unit(_node("src/service.py", "package.Service"))
    local_context_id = base_writer.add_unit(
        TextNode(
            "file-local plugin output",
            {
                "path": "src/service.py",
                "primary_name": "local-context",
                "language": "plugin",
            },
        ),
        record_type="plugin_context",
    )
    file_relation = base_writer.add_relation(
        kind="file-fact",
        source="package.Service",
        relation="uses",
        target="local-context",
        path="src/service.py",
        line=1,
        origin="plugin",
        source_unit_id=source_id,
        target_unit_id=local_context_id,
    )
    global_symbol_id = base_writer.add_symbol(
        SimpleNamespace(
            path="src/service.py",
            line=1,
            qualified_name="package.GlobalService",
            kind="class",
            parents=(),
            methods=(),
            constructor_types=(),
            attributes=(),
        ),
        plugin_id="framework",
    )
    repository_state_id = base_writer.add_unit(
        TextNode(
            "repository state",
            {
                "path": ".codecrow/repository-state.json",
                "primary_name": "repository-state",
                "language": "json",
            },
        ),
        record_type="repository_state",
    )
    architecture_context_id = base_writer.add_context(SimpleNamespace(
        path="src/service.py",
        plugin_id="framework",
        kind="architecture",
        content="architecture output",
        attributes=(),
    ))
    packet_relation = base_writer.add_relation(
        kind="repository-packet",
        source="package.Service",
        relation="belongs-to",
        target="repository",
        path="src/service.py",
        line=1,
        origin="plugin",
        source_unit_id=source_id,
        attributes={"packetKind": "architecture"},
    )
    base_writer.add_snapshot(SimpleNamespace(
        plugin_id="framework",
        kind="repository",
        content="snapshot",
    ))
    connection.commit()

    writer = StructuralGraphWriter(connection, track_mutations=True)
    writer.refresh_counts()
    writer.remove_repository_analysis_outputs()
    connection.commit()

    remaining_units = {
        row["unit_id"]: row["record_type"]
        for row in connection.execute(
            "SELECT unit_id, record_type FROM units"
        )
    }
    assert remaining_units == {
        file_id: "structural_file",
        source_id: "source_unit",
        local_context_id: "plugin_context",
    }
    assert not {
        global_symbol_id,
        repository_state_id,
        architecture_context_id,
    } & remaining_units.keys()
    assert [
        row["relation_id"]
        for row in connection.execute(
            "SELECT relation_id FROM relations ORDER BY relation_id"
        )
    ] == [file_relation]
    assert connection.execute(
        "SELECT 1 FROM relations WHERE relation_id = ?",
        (packet_relation,),
    ).fetchone() is None
    assert connection.execute(
        "SELECT count(*) FROM repository_snapshots"
    ).fetchone()[0] == 0
    if writer.fts_available:
        assert connection.execute(
            "SELECT count(*) FROM units_fts"
        ).fetchone()[0] == len(remaining_units)
        assert connection.execute(
            "SELECT count(*) FROM units_fts "
            "LEFT JOIN units ON units.rowid = units_fts.rowid "
            "WHERE units.rowid IS NULL"
        ).fetchone()[0] == 0
    connection.close()


def test_repository_output_reconciliation_preserves_equal_relation_cache(tmp_path):
    _, pending, connection, writer = _pending_store(tmp_path)
    symbol = SimpleNamespace(
        path="src/service.py",
        line=1,
        qualified_name="package.GlobalService",
        kind="class",
        parents=(),
        methods=(),
        constructor_types=(),
        attributes=(),
    )
    writer.begin_repository_analysis_reconciliation()
    symbol_id = writer.add_symbol(symbol, plugin_id="framework")
    relation_id = writer.add_relation(
        kind="repository-packet",
        source="package.GlobalService",
        relation="belongs-to",
        target="repository",
        path="src/service.py",
        line=1,
        origin="plugin",
        plugin_id="framework",
        source_unit_id=symbol_id,
        attributes={"packetKind": "architecture"},
    )
    snapshot = SimpleNamespace(
        plugin_id="framework",
        kind="repository",
        content="stable snapshot",
    )
    writer.add_snapshot(snapshot)
    writer.reconcile_repository_analysis_outputs()
    build_receipt(
        connection,
        workspace="workspace",
        project="project",
        branch="main",
        revision="revision",
        source_tree_sha256="b" * 64,
        collection_target=pending.target,
        repository_facts_json="{}",
        plugin_ids=("framework",),
        plugin_fingerprint="sha256:" + "0" * 64,
        plugin_descriptor_fingerprint="sha256:" + "0" * 64,
        plugin_implementation_fingerprint="sha256:" + "0" * 64,
        index_representation_fingerprint="sha256:" + "1" * 64,
        include_patterns=None,
        exclude_patterns=None,
        document_count=1,
        skipped_file_count=0,
    )
    cached_member = connection.execute(
        "SELECT member_json FROM relation_manifest_cache WHERE relation_id = ?",
        (relation_id,),
    ).fetchone()[0]

    delta_writer = StructuralGraphWriter(connection, track_mutations=True)
    delta_writer.refresh_counts()
    delta_writer.begin_repository_analysis_reconciliation()
    assert delta_writer.add_symbol(symbol, plugin_id="framework") == symbol_id
    assert delta_writer.add_relation(
        kind="repository-packet",
        source="package.GlobalService",
        relation="belongs-to",
        target="repository",
        path="src/service.py",
        line=1,
        origin="plugin",
        plugin_id="framework",
        source_unit_id=symbol_id,
        attributes={"packetKind": "architecture"},
    ) == relation_id
    delta_writer.add_snapshot(snapshot)
    delta_writer.reconcile_repository_analysis_outputs()

    assert connection.execute(
        "SELECT member_json FROM relation_manifest_cache WHERE relation_id = ?",
        (relation_id,),
    ).fetchone()[0] == cached_member
    assert connection.execute(
        "SELECT count(*) FROM relation_plugins WHERE relation_id = ?",
        (relation_id,),
    ).fetchone()[0] == 1

    delta_writer.begin_repository_analysis_reconciliation()
    delta_writer.add_symbol(symbol, plugin_id="framework")
    delta_writer.add_relation(
        kind="repository-packet",
        source="package.GlobalService",
        relation="belongs-to",
        target="repository",
        path="src/service.py",
        line=1,
        origin="plugin",
        plugin_ids=("framework", "magento"),
        source_unit_id=symbol_id,
        attributes={"packetKind": "architecture"},
    )
    delta_writer.add_snapshot(snapshot)
    delta_writer.reconcile_repository_analysis_outputs()
    assert {
        row[0]
        for row in connection.execute(
            "SELECT plugin_id FROM relation_plugins WHERE relation_id = ?",
            (relation_id,),
        )
    } == {"framework", "magento"}
    assert connection.execute(
        "SELECT 1 FROM relation_manifest_cache WHERE relation_id = ?",
        (relation_id,),
    ).fetchone() is None
    connection.close()


def test_repository_output_reconciliation_removes_obsolete_rows_and_rolls_back(
    tmp_path,
):
    _, _, connection, writer = _pending_store(tmp_path)
    obsolete_symbol = SimpleNamespace(
        path="src/obsolete.py",
        line=1,
        qualified_name="package.Obsolete",
        kind="class",
        parents=(),
        methods=(),
        constructor_types=(),
        attributes=(),
    )
    writer.begin_repository_analysis_reconciliation()
    obsolete_id = writer.add_symbol(obsolete_symbol, plugin_id="framework")
    obsolete_relation = writer.add_relation(
        kind="repository-packet",
        source="package.Obsolete",
        relation="belongs-to",
        target="repository",
        path="src/obsolete.py",
        line=1,
        origin="plugin",
        plugin_id="framework",
        source_unit_id=obsolete_id,
        attributes={"packetKind": "architecture"},
    )
    writer.add_snapshot(SimpleNamespace(
        plugin_id="framework",
        kind="obsolete",
        content="obsolete snapshot",
    ))
    writer.reconcile_repository_analysis_outputs()

    delta_writer = StructuralGraphWriter(connection, track_mutations=True)
    delta_writer.refresh_counts()
    delta_writer.begin_repository_analysis_reconciliation()
    delta_writer.reconcile_repository_analysis_outputs()
    assert connection.execute(
        "SELECT 1 FROM units WHERE unit_id = ?", (obsolete_id,)
    ).fetchone() is None
    assert connection.execute(
        "SELECT 1 FROM relations WHERE relation_id = ?", (obsolete_relation,)
    ).fetchone() is None
    assert connection.execute(
        "SELECT count(*) FROM repository_snapshots"
    ).fetchone()[0] == 0

    delta_writer.begin_repository_analysis_reconciliation()
    transient_id = delta_writer.add_symbol(
        obsolete_symbol,
        plugin_id="framework",
    )
    delta_writer.abort_repository_analysis_reconciliation()
    assert transient_id == obsolete_id
    assert connection.execute(
        "SELECT 1 FROM units WHERE unit_id = ?", (transient_id,)
    ).fetchone() is None
    connection.close()


def test_repository_reconciliation_preserves_shared_file_contributors(tmp_path):
    _, _, connection, writer = _pending_store(tmp_path)
    relation_kwargs = {
        "kind": "repository-packet",
        "source": "package.Service",
        "relation": "belongs-to",
        "target": "repository",
        "path": "src/service.py",
        "line": 1,
        "origin": "plugin",
        "attributes": {"packetKind": "architecture"},
    }
    relation_id = writer.add_relation(
        **relation_kwargs,
        plugin_id="file-parser",
    )
    writer.begin_repository_analysis_reconciliation()
    assert writer.add_relation(
        **relation_kwargs,
        plugin_id="framework",
    ) == relation_id
    writer.reconcile_repository_analysis_outputs()
    assert {
        row[0]
        for row in connection.execute(
            "SELECT plugin_id FROM relation_plugins WHERE relation_id = ?",
            (relation_id,),
        )
    } == {"file-parser", "framework"}

    delta_writer = StructuralGraphWriter(connection, track_mutations=True)
    delta_writer.refresh_counts()
    delta_writer.begin_repository_analysis_reconciliation()
    delta_writer.reconcile_repository_analysis_outputs()

    assert connection.execute(
        "SELECT 1 FROM relations WHERE relation_id = ?",
        (relation_id,),
    ).fetchone() is not None
    assert {
        row[0]
        for row in connection.execute(
            "SELECT plugin_id FROM relation_plugins WHERE relation_id = ?",
            (relation_id,),
        )
    } == {"file-parser"}
    assert {
        tuple(row)
        for row in connection.execute(
            "SELECT plugin_id, scope FROM relation_plugin_scopes "
            "WHERE relation_id = ?",
            (relation_id,),
        )
    } == {("file-parser", "file")}
    connection.close()


def test_deferred_file_relation_ownership_matches_immediate_output(tmp_path):
    table_orders = {
        "relations": "relation_id",
        "relation_plugins": "relation_id, plugin_id",
        "relation_scopes": "relation_id, scope",
        "relation_plugin_scopes": "relation_id, plugin_id, scope",
        "relation_paths": "relation_id, path",
        "relation_names": "relation_id, role, normalized_name",
    }

    def build_output(root, *, deferred):
        _, _, connection, _ = _pending_store(root)
        writer = StructuralGraphWriter(
            connection,
            defer_file_relation_ownership=deferred,
        )
        relation_kwargs = {
            "kind": "plugin-call",
            "source": "package.Caller",
            "relation": "calls",
            "target": "package.Service",
            "path": "src/caller.py",
            "line": 12,
            "origin": "plugin",
            "attributes": {"dispatch": "virtual"},
            "related_paths": ("src/service.py",),
        }
        relation_id = writer.add_relation(
            **relation_kwargs,
            plugin_id="parser-a",
        )
        assert writer.add_relation(
            **relation_kwargs,
            plugin_id="parser-b",
        ) == relation_id
        writer.add_relation(
            kind="REFERENCES",
            source="package.Caller",
            relation="references",
            target="package.Value",
            path="src/caller.py",
            line=13,
            origin="tree-sitter",
        )

        if deferred:
            assert connection.execute(
                "SELECT count(*) FROM relation_scopes"
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT count(*) FROM relation_plugin_scopes"
            ).fetchone()[0] == 0
        writer.flush_deferred_file_relation_ownership()
        output = {
            table: [
                tuple(row)
                for row in connection.execute(
                    f"SELECT * FROM {table} ORDER BY {order}"
                )
            ]
            for table, order in table_orders.items()
        }
        connection.close()
        return output

    assert build_output(
        tmp_path / "deferred",
        deferred=True,
    ) == build_output(
        tmp_path / "immediate",
        deferred=False,
    )


def test_deferred_file_ownership_survives_repository_reconciliation(tmp_path):
    _, _, connection, _ = _pending_store(tmp_path)
    writer = StructuralGraphWriter(
        connection,
        defer_file_relation_ownership=True,
    )
    relation_kwargs = {
        "kind": "repository-packet",
        "source": "package.Service",
        "relation": "belongs-to",
        "target": "repository",
        "path": "src/service.py",
        "line": 1,
        "origin": "plugin",
        "attributes": {"packetKind": "architecture"},
    }
    relation_id = writer.add_relation(
        **relation_kwargs,
        plugin_id="file-parser",
    )
    assert connection.execute(
        "SELECT 1 FROM relation_scopes WHERE relation_id = ?",
        (relation_id,),
    ).fetchone() is None

    writer.flush_deferred_file_relation_ownership()
    writer.begin_repository_analysis_reconciliation()
    assert writer.add_relation(
        **relation_kwargs,
        plugin_id="framework",
    ) == relation_id
    writer.reconcile_repository_analysis_outputs()

    assert {
        row[0]
        for row in connection.execute(
            "SELECT scope FROM relation_scopes WHERE relation_id = ?",
            (relation_id,),
        )
    } == {"file", "repository"}
    assert {
        tuple(row)
        for row in connection.execute(
            "SELECT plugin_id, scope FROM relation_plugin_scopes "
            "WHERE relation_id = ?",
            (relation_id,),
        )
    } == {
        ("file-parser", "file"),
        ("framework", "repository"),
    }

    delta_writer = StructuralGraphWriter(connection, track_mutations=True)
    delta_writer.refresh_counts()
    delta_writer.begin_repository_analysis_reconciliation()
    delta_writer.reconcile_repository_analysis_outputs()

    assert connection.execute(
        "SELECT 1 FROM relations WHERE relation_id = ?",
        (relation_id,),
    ).fetchone() is not None
    assert {
        row[0]
        for row in connection.execute(
            "SELECT scope FROM relation_scopes WHERE relation_id = ?",
            (relation_id,),
        )
    } == {"file"}
    assert {
        row[0]
        for row in connection.execute(
            "SELECT plugin_id FROM relation_plugins WHERE relation_id = ?",
            (relation_id,),
        )
    } == {"file-parser"}
    assert {
        tuple(row)
        for row in connection.execute(
            "SELECT plugin_id, scope FROM relation_plugin_scopes "
            "WHERE relation_id = ?",
            (relation_id,),
        )
    } == {("file-parser", "file")}
    connection.close()


def test_default_and_delta_writers_record_file_ownership_immediately(tmp_path):
    _, _, connection, writer = _pending_store(tmp_path)
    default_relation = writer.add_relation(
        kind="REFERENCES",
        source="package.Caller",
        relation="references",
        target="package.Service",
        path="src/caller.py",
        line=1,
        origin="plugin",
        plugin_id="default-parser",
    )
    assert connection.execute(
        "SELECT scope FROM relation_scopes WHERE relation_id = ?",
        (default_relation,),
    ).fetchone()[0] == "file"
    assert tuple(connection.execute(
        "SELECT plugin_id, scope FROM relation_plugin_scopes "
        "WHERE relation_id = ?",
        (default_relation,),
    ).fetchone()) == ("default-parser", "file")

    delta_writer = StructuralGraphWriter(connection, track_mutations=True)
    delta_writer.refresh_counts()
    delta_relation = delta_writer.add_relation(
        kind="REFERENCES",
        source="package.OtherCaller",
        relation="references",
        target="package.Service",
        path="src/other.py",
        line=2,
        origin="plugin",
        plugin_id="delta-parser",
    )
    assert connection.execute(
        "SELECT scope FROM relation_scopes WHERE relation_id = ?",
        (delta_relation,),
    ).fetchone()[0] == "file"
    assert tuple(connection.execute(
        "SELECT plugin_id, scope FROM relation_plugin_scopes "
        "WHERE relation_id = ?",
        (delta_relation,),
    ).fetchone()) == ("delta-parser", "file")

    delta_writer.flush_deferred_file_relation_ownership()
    assert connection.execute(
        "SELECT count(*) FROM relation_scopes"
    ).fetchone()[0] == 2
    assert connection.execute(
        "SELECT count(*) FROM relation_plugin_scopes"
    ).fetchone()[0] == 2
    connection.close()


def test_repository_state_removal_cleans_fts_row(tmp_path):
    _, _, connection, writer = _pending_store(tmp_path)
    state_id = writer.add_unit(
        TextNode(
            "repository state",
            {
                "path": "__analysis_state__/repository-facts.state",
                "primary_name": "repository-facts",
                "language": "repository-state",
            },
        ),
        record_type="repository_state",
    )
    state_rowid = connection.execute(
        "SELECT rowid FROM units WHERE unit_id = ?", (state_id,)
    ).fetchone()[0]

    writer.remove_repository_state_output()

    assert connection.execute(
        "SELECT 1 FROM units WHERE unit_id = ?", (state_id,)
    ).fetchone() is None
    if writer.fts_available:
        assert connection.execute(
            "SELECT 1 FROM units_fts WHERE rowid = ?", (state_rowid,)
        ).fetchone() is None
    connection.close()


def test_touched_name_resolution_invalidates_only_changed_candidate_sets(tmp_path):
    _, _, connection, base_writer = _pending_store(tmp_path)
    original_foo_id = base_writer.add_unit(_node("src/foo.py", "package.Foo"))
    bar_id = base_writer.add_unit(_node("src/bar.py", "package.Bar"))
    foo_relation = base_writer.add_relation(
        kind="REFERENCES",
        source="caller",
        relation="references",
        target="package.Foo",
        path="src/caller.py",
        line=2,
        origin="tree-sitter",
    )
    bar_relation = base_writer.add_relation(
        kind="REFERENCES",
        source="caller",
        relation="references",
        target="package.Bar",
        path="src/caller.py",
        line=3,
        origin="tree-sitter",
    )
    base_writer.resolve_relations()
    connection.commit()
    assert connection.execute(
        "SELECT target_unit_id FROM relations WHERE relation_id = ?",
        (foo_relation,),
    ).fetchone()[0] == original_foo_id
    assert connection.execute(
        "SELECT target_unit_id FROM relations WHERE relation_id = ?",
        (bar_relation,),
    ).fetchone()[0] == bar_id

    writer = StructuralGraphWriter(connection, track_mutations=True)
    writer.refresh_counts()
    duplicate_foo_id = writer.add_unit(
        _node("src/alternate.py", "package.Foo", "alternate source")
    )
    # A null endpoint is a sentinel: selective resolution must leave this
    # unrelated relation untouched even though it could otherwise be resolved.
    connection.execute(
        "UPDATE relations SET target_unit_id = NULL WHERE relation_id = ?",
        (bar_relation,),
    )
    writer.resolve_touched_relations()

    assert duplicate_foo_id != original_foo_id
    assert connection.execute(
        "SELECT target_unit_id FROM relations WHERE relation_id = ?",
        (foo_relation,),
    ).fetchone()[0] is None
    assert connection.execute(
        "SELECT target_unit_id FROM relations WHERE relation_id = ?",
        (bar_relation,),
    ).fetchone()[0] is None

    # Removing the competing name restores the original unique resolution.
    assert writer.remove_paths(("src/alternate.py",))[0] == (
        "src/alternate.py",
    )
    writer.resolve_touched_relations()
    assert connection.execute(
        "SELECT target_unit_id FROM relations WHERE relation_id = ?",
        (foo_relation,),
    ).fetchone()[0] == original_foo_id
    assert connection.execute(
        "SELECT target_unit_id FROM relations WHERE relation_id = ?",
        (bar_relation,),
    ).fetchone()[0] is None
    connection.close()
