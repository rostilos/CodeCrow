"""Real long-IIFE failure shape: exact storage, bounded observations and paging."""
from contextlib import contextmanager
from pathlib import Path
import json
from types import SimpleNamespace

import pytest

from rag_pipeline.core.review_context import ProposedTreeReviewContextService
from rag_pipeline.core.review_graph.budget import bound_graph_response
from rag_pipeline.core.structural_store import StructuralGenerationStore, StructuralGraphReader, StructuralGraphWriter
from tests.test_review_graph_tools import _Reader, _relation, _unit


def _service(monkeypatch):
    # Exact parser-emitted target from the measured Discourse vendored Sinon.
    expression = json.loads((Path(__file__).parent / "fixtures/sinon-long-call.json").read_text())["target"]
    root = _unit("root", "vendor/assets/javascripts/sinon-1.7.1.js", content="invoke();\n")
    target = _unit("anonymous", "src/related.js", content=expression)
    relation = _relation("a" * 64, root, target)
    relation.update(target=expression, kind="javascript-call")
    for endpoint in ("sourceUnit", "targetUnit"):
        relation[endpoint] = {key: value for key, value in relation[endpoint].items()
                              if key not in {"content", "contentSha256"}}
    reader = _Reader([root, target], [relation])
    reader.get_relation = lambda evidence_id: relation if evidence_id == relation["evidenceId"] else None
    generation = SimpleNamespace(
        changed_paths=[root["path"]], deleted_paths=[],
        receipt={"snapshot_metadata": {"base_revision": "base", "source_revision": "source"}},
        target_source_tree_sha256="target", overlay_sha256="overlay",
        proposed_source_tree_sha256="proposed", representation_identity={},
        collection_target="sealed", cache_hit=True,
    )
    service = ProposedTreeReviewContextService(None)
    opened = []

    @contextmanager
    def session(**binding):
        opened.append(binding)
        yield SimpleNamespace(reader=reader, generation=generation)

    monkeypatch.setattr(service, "open_read_session", session)
    return service, root, target, relation, opened


def _bounded(response, budget):
    encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
    assert len(encoded.encode()) <= budget * 4
    assert response["coverage"]["serializedCharacters"] == len(encoded)
    assert response["coverage"]["serializedBytes"] == len(encoded.encode())
    wire = json.dumps(response, ensure_ascii=False, indent=2).encode()
    assert len(wire) <= budget * 4
    assert response["coverage"]["modelObservationBytes"] == len(wire)


@pytest.mark.parametrize("budget", [512, 4000])
@pytest.mark.parametrize("operation", ["review_context", "minimal_review_context",
                                        "query_review_graph", "review_impact_radius",
                                        "traverse_review_graph", "get_review_structural_unit"])
def test_every_model_graph_operation_bounds_the_complete_response(monkeypatch, operation, budget):
    service, root, target, relation, opened = _service(monkeypatch)
    controls = {
        "review_context": {"question": "root", "focus_symbols": ["anonymous"]},
        "minimal_review_context": {"question": "root"},
        "query_review_graph": {"pattern": "relations_of", "target": root["unitId"]},
        "review_impact_radius": {"targets": [root["unitId"]]},
        "traverse_review_graph": {"start": root["unitId"]},
        "get_review_structural_unit": {"unit_id": target["unitId"]},
    }[operation]
    response = getattr(service, operation)(focus_paths=[root["path"]],
                                         token_budget=budget, workspace="tenant-a", **controls)
    _bounded(response, budget)
    assert opened == [{"workspace": "tenant-a"}]
    assert len(relation["target"]) == 130885  # The sealed reader was not changed.


def test_oversized_relation_reference_recovers_exact_expression_by_id(monkeypatch):
    service, root, _, relation, _ = _service(monkeypatch)
    original = service.query_review_graph(focus_paths=[root["path"]], target=root["unitId"])
    assert original["results"][0]["target"] == relation["target"]
    response = service.query_review_graph(focus_paths=[root["path"]], target=root["unitId"], token_budget=4000)
    reference = response["continuations"][0]
    assert reference["arguments"]["evidenceId"] == relation["evidenceId"]
    assert reference["arguments"]["field"] == "target"
    assert all("target" not in item for item in response["results"])
    content = []
    offset = 0
    while offset is not None:
        page = service.query_review_graph(focus_paths=[], evidence_id=relation["evidenceId"],
                                         field="target", offset=offset, max_characters=60000,
                                         token_budget=4000)
        _bounded(page, 4000)
        assert page["content"]
        assert page["contentWindow"]["offset"] == offset
        content.append(page["content"])
        next_offset = page["contentWindow"]["nextOffset"]
        assert next_offset is None or next_offset > offset
        offset = next_offset
    assert "".join(content) == relation["target"]
    missing = service.query_review_graph(focus_paths=[], evidence_id="relation:other-tenant", token_budget=4000)
    assert missing["status"] == "unavailable"
    with pytest.raises(ValueError, match="Relation field"):
        service.query_review_graph(focus_paths=[], evidence_id=relation["evidenceId"], field="sql")


def test_none_budget_keeps_exact_facts_and_small_budget_preserves_source_offsets():
    response = {"status": "ready", "snapshot": {"kind": "proposed_tree"},
                "unit": {"unitId": "unit:source", "content": '"漢字\\' * 10000,
                         "contentWindow": {"offset": 40, "endOffset": 50040,
                                           "totalCharacters": 60000, "nextOffset": 50040}}}
    assert bound_graph_response(response, None) is response
    bounded = bound_graph_response(response, 512)
    _bounded(bounded, 512)
    page = bounded["unit"]["contentWindow"]
    assert page["nextOffset"] == 40 + len(bounded["unit"]["content"])
    assert response["unit"]["contentWindow"]["nextOffset"] == 50040


def test_relation_lookup_uses_bound_database_and_preserves_exact_attributes(tmp_path):
    connections = []
    try:
        readers = []
        for tenant in ("a", "b"):
            store = StructuralGenerationStore(tmp_path / tenant)
            connection = store.initialize(store.pending_paths("generation"))
            connections.append(connection)
            readers.append(StructuralGraphReader(connection, {
                "branch": "main", "repository_revision": "head",
                "generation_manifest_sha256": tenant * 64,
            }))
        attributes = {"contract": '漢字"\\\n' * 2000}
        writer = StructuralGraphWriter(connections[0])
        evidence_id = writer.add_relation(kind="javascript-call", source="src/a.js",
                                          relation="calls", target="function () {}",
                                          path="src/a.js", line=1, origin="plugin",
                                          attributes=attributes)
        assert readers[0].get_relation(evidence_id)["attributes"] == attributes
        assert readers[1].get_relation(evidence_id) is None
        assert readers[0].get_relation("' OR 1=1 --") is None
        from rag_pipeline.core.review_graph.budget import relation_field_page
        offset, parts = 0, []
        while offset is not None:
            page = bound_graph_response(relation_field_page(
                readers[0], evidence_id, "attributes", offset, 60000), 512)
            _bounded(page, 512)
            assert page["encoding"] == "json"
            assert page["content"]
            parts.append(page["content"])
            offset = page["contentWindow"]["nextOffset"]
        assert json.loads("".join(parts)) == attributes
    finally:
        for connection in connections:
            connection.close()


def test_large_metadata_keeps_advancing_exact_unit_page_and_literal_labels():
    text = '漢字"\\' * 10000
    metadata = {f"tag{i}": "x" * 100 for i in range(1000)}
    response = {"status": "ready", "snapshot": metadata,
                "unit": {"unitId": "unit:source", "attributes": metadata, "content": text,
                         "contentWindow": {"offset": 10, "endOffset": 10 + len(text),
                                           "totalCharacters": 10 + len(text), "nextOffset": None}}}
    bounded = bound_graph_response(response, 512)
    _bounded(bounded, 512)
    assert bounded["unit"]["content"]
    assert bounded["unit"]["unitId"] == "unit:source"
    assert bounded["unit"]["contentWindow"]["nextOffset"] == 10 + len(bounded["unit"]["content"])
    relation = {"status": "ready", "results": [{"evidenceId": "relation:" + "a" * 64,
                   "target": "x" * 1500}], "cursor": 0, "nextCursor": None}
    displayed = bound_graph_response(relation, 4000)
    _bounded(displayed, 4000)
    assert displayed["continuations"][0]["arguments"]["field"] == "target"


def test_truncated_one_line_source_has_exact_unit_continuation():
    response = {"status": "ready", "sourceWindows": [{"unitId": "unit:source", "path": "a.js",
                "startLine": 1, "endLine": 1, "content": "x" * 10000, "truncated": False}]}
    bounded = bound_graph_response(response, 512)
    _bounded(bounded, 512)
    assert bounded["sourceWindows"][0]["content"]
    continuation = next(ref for ref in bounded["continuations"] if ref["tool"] == "getStructuralUnit")
    assert continuation["arguments"]["unitId"] == "unit:source"
    assert continuation["arguments"]["offset"] == len(bounded["sourceWindows"][0]["content"])


def test_giant_query_metadata_does_not_skip_unshown_result_ids():
    response = {"status": "ready", "cursor": 10, "nextCursor": 12,
                "results": [{"unitId": f"unit:{i}", "metadata": {str(j): "x" * 100 for j in range(100)}} for i in range(2)]}
    bounded = bound_graph_response(response, 512)
    _bounded(bounded, 512)
    assert bounded["results"] == [{"unitId": "unit:0"}]
    assert bounded["nextCursor"] == 11


@pytest.mark.parametrize("previous_omissions", [0, 7, "7"])
def test_final_coverage_metadata_does_not_discard_source_that_fits(previous_omissions):
    fixture = json.loads((Path(__file__).parent / "fixtures/minimal-context-metadata-cliff.json").read_text())
    response = fixture["response"]
    before = json.dumps(response, sort_keys=True)
    if previous_omissions:
        response["coverage"].update(
            omittedNodes=previous_omissions, omittedRelations=previous_omissions,
        )
        before = json.dumps(response, sort_keys=True)

    bounded = bound_graph_response(response, fixture["provenance"]["token_budget"])

    _bounded(bounded, 5000)
    assert bounded["sourceWindows"] == response["sourceWindows"]
    assert sum(len(window["content"]) for window in bounded["sourceWindows"]) == 5972
    assert "response_envelope" not in bounded["coverage"]["budgetOmissions"]
    assert bounded["snapshot"] == response["snapshot"]
    assert bounded["focusPaths"] == response["focusPaths"]
    assert bounded["focusSymbols"] == response["focusSymbols"]
    for name, key in (("Nodes", "nodes"), ("Relations", "edges")):
        coverage = bounded["coverage"]
        assert 0 < len(bounded[key]) <= len(response[key]) <= 20
        assert coverage["returned" + name] == len(bounded[key])
        assert coverage["omitted" + name] == int(previous_omissions) + len(response[key]) - len(bounded[key])
    source = bounded["coverage"]["source"]
    assert len(bounded["sourceWindows"]) == source["returnedSourceUnits"] == 8
    assert source["returnedSourceCharacters"] == 5972 <= source["maxSourceCharacters"]
    assert json.dumps(response, sort_keys=True) == before

    # Repacking must not add the same omissions on every size measurement.
    repacked = bound_graph_response(bounded, 5000)
    _bounded(repacked, 5000)
    for name in ("omittedNodes", "omittedRelations"):
        assert repacked["coverage"][name] == bounded["coverage"][name]
    assert repacked["sourceWindows"] == bounded["sourceWindows"]


def test_unused_coverage_count_is_not_coerced_without_its_response_list():
    response = {"status": "ready", "coverage": {"returnedRoots": "unavailable"}}
    bounded = bound_graph_response(response, 512)
    _bounded(bounded, 512)
    assert bounded["coverage"]["returnedRoots"] == "unavailable"
