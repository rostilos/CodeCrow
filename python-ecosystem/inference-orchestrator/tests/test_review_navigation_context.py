"""Lossless graph metadata projections; no model-quality or token-cost claims."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from service.review.navigation_context import compact_navigation_result
from service.review.verification_tools import VerificationTools
from service.review.verification_context import VerificationContext
from service.review.verification_state import VerificationState


def context_fingerprint(*results):
    state = VerificationState([], [], {})
    for result in results:
        state.add_evidence("queryCodeGraph", result)
    return VerificationContext(state).fingerprint()


GRAPH = json.loads((Path(__file__).parent / "fixtures/review_reconciliation/graph_navigation_repeated_units.json").read_text())["result"]


def expanded(projected):
    definitions = projected.get("unitDefinitions", {})

    def expand(value):
        if isinstance(value, dict):
            if "unitRef" in value and value["unitRef"] in definitions:
                original = definitions[value["unitRef"]]
                assert original["unitId"] == value["unitId"]
                return {**deepcopy(original), **{key: expand(item) for key, item in value.items() if key != "unitRef"}}
            return {key: expand(item) for key, item in value.items()}
        if isinstance(value, list):
            return [expand(item) for item in value]
        return value

    return {key: expand(value) for key, value in projected.items() if key != "unitDefinitions"}


def size(value):
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


def test_captured_navigation_shape_roundtrips_without_changing_original_or_omitting_edges():
    source = deepcopy(GRAPH)
    before = deepcopy(source)
    source["results"][0]["attributes"]["unknownNavigationFact"] = {"confidence": "derived", "values": [0, False, None, "λ"]}
    before = deepcopy(source)
    projected = compact_navigation_result(source)
    assert source == before
    assert "unitDefinitions" in projected
    assert len(projected["results"]) == len(source["results"]) == 22
    assert expanded(projected) == source
    assert projected["snapshot"] == source["snapshot"]
    assert projected["coverage"] == source["coverage"]
    assert projected["nextCursor"] == source["nextCursor"]
    assert size(projected) < size(source)
    for record in projected["resolvedUnits"]:
        assert record["unitId"] == projected["unitDefinitions"][record["unitRef"]]["unitId"]


def test_reordered_queries_keep_reference_and_semantic_observation_identities():
    first = compact_navigation_result(deepcopy(GRAPH))
    reordered = deepcopy(GRAPH)
    reordered["results"].reverse()
    second = compact_navigation_result(reordered)
    assert first["unitDefinitions"] == second["unitDefinitions"]
    assert context_fingerprint(first) == context_fingerprint(second)
    assert expanded(second) == reordered


def test_same_unit_id_with_different_complete_metadata_is_not_conflated():
    first = deepcopy(GRAPH["resolvedUnits"][0])
    second = {**first, "startLine": 900, "endLine": 920, "unknownPluginFact": "distinct data"}
    value = {"status": "ready", "units": [first, second, first, second, first, second]}
    projected = compact_navigation_result(value)
    assert len(projected["unitDefinitions"]) == 2
    assert projected["units"][0]["unitId"] == projected["units"][1]["unitId"]
    assert projected["units"][0]["unitRef"] != projected["units"][1]["unitRef"]
    assert expanded(projected) == value


def test_pagination_and_partial_coverage_are_preserved():
    value = {**deepcopy(GRAPH), "status": "partial", "nextCursor": 22,
             "coverage": {"state": "partial", "truncated": True, "partialReasons": ["more graph results"],
                          "returnedResults": 22, "frontier": ["unresolved dependency"]}}
    projected = compact_navigation_result(value)
    assert expanded(projected) == value
    assert projected["nextCursor"] == 22
    assert projected["coverage"]["truncated"] is True


def test_existing_reference_envelope_is_not_overwritten():
    value = {**deepcopy(GRAPH), "unitDefinitions": {"external": {"existing": "metadata"}}}
    assert compact_navigation_result(value) is value


def test_single_and_tiny_metadata_records_are_not_expanded_into_larger_envelopes():
    for value in ({"units": [{"unitId": "a", "path": "a.py"}]},
                  {"units": [{"unitId": "a", "path": "a.py"}] * 2}):
        assert compact_navigation_result(value) is value


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["queryCodeGraph", "getMinimalReviewContext", "getImpactRadius", "traverseCodeGraph"])
async def test_only_navigation_observations_use_the_shared_metadata_projection(name):
    full_result = deepcopy(GRAPH)
    tools = VerificationTools(rag_client=None, binding={}, parts=[])
    tools.server = SimpleNamespace(call_tool=AsyncMock(return_value=full_result))
    result = await tools.call(name, {})
    assert "unitDefinitions" in result
    expected = deepcopy(full_result)
    expected["snapshot"] = {"kind": "proposed_tree", "branch": "main"}
    assert restore_handles(expanded(result), tools.navigation) == expected
    assert full_result == GRAPH


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["readReviewFile", "getReviewDiff", "getStructuralUnit"])
async def test_source_and_diff_tool_results_are_never_compacted(name):
    full_result = {**deepcopy(GRAPH), "content": "source content stays exact\n" * 10000}
    tools = VerificationTools(rag_client=None, binding={}, parts=[])
    tools.server = SimpleNamespace(call_tool=AsyncMock(return_value=full_result))
    result = await tools.call(name, {})
    assert result == full_result
    assert "unitDefinitions" not in result
    assert result["content"] == full_result["content"]


def test_single_vs_repeated_unit_response_does_not_manufacture_new_graph_evidence():
    repeated = compact_navigation_result(deepcopy(GRAPH))
    single = {"status": "ready", "results": [deepcopy(GRAPH["results"][0])]}
    assert "unitDefinitions" not in compact_navigation_result(single)
    assert context_fingerprint(single, repeated) == context_fingerprint(repeated)
    # Genuine metadata differences still advance evidence even with the same ID.
    changed = deepcopy(single)
    changed["results"][0]["sourceUnit"]["startLine"] += 1
    assert context_fingerprint(changed, repeated) != context_fingerprint(repeated)


def test_production_semantic_expansion_matches_independent_roundtrip_decoder():
    from service.review.navigation_context import expand_navigation_result

    projected = compact_navigation_result(deepcopy(GRAPH))
    assert expand_navigation_result(projected) == expanded(projected) == GRAPH


def restore_handles(value, navigation):
    """Independent decoder: compare every semantic record with the raw graph."""
    if isinstance(value, str):
        return navigation.targets.get(value, value)
    if isinstance(value, list):
        return [restore_handles(item, navigation) for item in value]
    if isinstance(value, dict):
        return {navigation.targets.get(key, key): restore_handles(item, navigation)
                for key, item in value.items()}
    return value


def test_navigation_handles_preserve_every_graph_fact_and_unknown_plugin_attribute():
    from service.review.navigation_context import NavigationContext

    graph = deepcopy(GRAPH)
    graph["results"][0]["attributes"] = {
        "unknownPluginFact": {"snapshot": {"revision": "semantic revision"},
                              "origin": {"plugin": "meaningful provenance"},
                              "scorePolicy": {"rule": "domain scoring"}},
    }
    graph["snapshot"]["unknownAttestationState"] = "partial"
    graph["scorePolicy"] = {"edgeWeights": {"CALLS": 0.85}}
    graph["frontier"] = [{"unitId": graph["resolvedUnits"][0]["unitId"], "reason": "branch_result_limit",
                          "continuation": {"tool": "queryCodeGraph", "arguments": {
                              "pattern": "relations_of", "target": graph["resolvedUnits"][0]["unitId"], "cursor": 22}}}]
    graph["coverage"] = {"state": "bounded", "omittedRelations": 12}
    before = deepcopy(graph)
    navigation = NavigationContext()
    shown = navigation.project(graph)
    decoded = restore_handles(expanded(shown), navigation)
    expected = deepcopy(graph)
    expected["snapshot"] = {"kind": "proposed_tree", "branch": "main", "unknownAttestationState": "partial"}
    del expected["scorePolicy"]
    assert decoded == expected
    assert graph == before
    handle = shown["frontier"][0]["unitId"]
    assert navigation.resolve_arguments("queryCodeGraph", shown["frontier"][0]["continuation"]["arguments"])["target"] == graph["resolvedUnits"][0]["unitId"]
    assert navigation.resolve_arguments("grepReviewCode", {"query": handle}) == {"query": handle}
    assert navigation.resolve_arguments("readReviewFile", {"path": handle}) == {"path": handle}
    assert size(shown) < size(graph)


@pytest.mark.asyncio
async def test_graph_cache_keeps_raw_ids_and_rebinds_handles_for_each_case():
    first = VerificationTools(rag_client=None, binding={}, parts=[])
    first.server = SimpleNamespace(call_tool=AsyncMock(return_value=deepcopy(GRAPH)))
    initial = await first.call("queryCodeGraph", {"pattern": "callees_of", "target": "getWorkingHours"})
    initial_handle = initial["resolvedUnits"][0]["unitId"]
    second = VerificationTools(rag_client=None, binding={}, parts=[])
    second.cache = first.cache
    second.navigation.project({"status": "ready", "units": [{"unitId": "another-real-unit", "path": "different.py"}]})
    second.server = SimpleNamespace(call_tool=AsyncMock(return_value={"status": "ready", "content": "exact source\n"}))
    repeated = await second.call("queryCodeGraph", {"pattern": "callees_of", "target": "getWorkingHours"})
    second.server.call_tool.assert_not_called()
    new_handle = repeated["resolvedUnits"][0]["unitId"]
    assert new_handle != initial_handle
    await second.call("getStructuralUnit", {"unitId": new_handle})
    second.server.call_tool.assert_awaited_once_with("getStructuralUnit", {"unitId": GRAPH["resolvedUnits"][0]["unitId"]})
    assert next(iter(first.cache.values())) == GRAPH


def test_large_impact_graph_preserves_all_nodes_edges_and_continuation_handles():
    """Comparable 391-record graph shape; architecture regression, not benchmark replay."""
    from service.review.navigation_context import NavigationContext

    units = [{"unitId": f"unit:{index:064x}", "name": f"method{index}", "kind": "method",
              "path": f"pkg/service{index}/handler.go", "startLine": index + 1, "endLine": index + 5}
             for index in range(191)]
    edges = [{"evidenceId": f"relation:{index:064x}", "sourceUnitId": units[index % 191]["unitId"],
              "targetUnitId": units[(index + 1) % 191]["unitId"], "kind": "CALLS",
              "origin": {"path": units[index % 191]["path"], "line": index + 1},
              "attributes": {"unknownContractFact": f"value{index}"}}
             for index in range(200)]
    original = {"status": "ready", "nodes": units, "edges": edges,
                "connections": [{"unitId": edge["targetUnitId"], "fromUnitId": edge["sourceUnitId"],
                                 "evidenceId": edge["evidenceId"]} for edge in edges],
                "coverage": {"state": "bounded", "omittedRelations": 6},
                "frontier": [{"unitId": units[-1]["unitId"], "continuation": {
                    "tool": "queryCodeGraph", "arguments": {"target": units[-1]["unitId"], "cursor": 200}}}]}
    navigation = NavigationContext()
    shown = navigation.project(original)
    assert len(shown["nodes"]) + len(shown["edges"]) == 391
    assert restore_handles(expanded(shown), navigation) == original
    assert size(shown) < size(original) * 0.6
    assert shown["coverage"] == original["coverage"]
    for node in shown["nodes"]:
        resolved = navigation.resolve_arguments("getStructuralUnit", {"unitId": node["unitId"]})
        assert resolved["unitId"] in {unit["unitId"] for unit in units}


def test_unit_traversal_annotations_do_not_duplicate_source_identity():
    base = deepcopy(GRAPH["resolvedUnits"][0])
    graph = {"roots": [{**base, "depth": 0}],
             "nodes": [{**base, "depth": 2, "impactScore": 0.7, "connectionEvidenceId": "relation:a"}],
             "frontier": [{**base, "depth": 3, "reason": "more_relations", "continuation": {"cursor": 8}}]}
    result = compact_navigation_result(graph)
    assert len(result["unitDefinitions"]) == 1
    assert expanded(result) == graph
    from service.review.navigation_context import expand_navigation_result
    assert expand_navigation_result(result) == graph
    assert result["nodes"][0]["impactScore"] == 0.7
    assert result["frontier"][0]["continuation"] == {"cursor": 8}


def test_impact_scores_are_shared_only_when_the_node_already_carries_the_exact_value():
    from service.review.navigation_context import NavigationContext

    original = {"nodes": [{"unitId": "unit:a", "path": "a.go", "depth": 2, "impactScore": 0.7}],
                "impactScores": {"unit:a": 0.7, "unknown-unit": 0.9},
                "connections": [{"unitId": "unit:a", "fromUnitId": "unit:b", "evidenceId": "edge:a",
                                 "direction": "incoming", "depth": 2, "impactScore": 0.7,
                                 "edgeWeight": 0.85, "depthDecay": 0.9,
                                 "attributes": {"edgeWeight": "semantic plugin attribute"}}]}
    navigation = NavigationContext()
    decoded = restore_handles(expanded(navigation.project(original)), navigation)
    assert decoded["impactScores"] == {"unknown-unit": 0.9}
    assert decoded["nodes"] == original["nodes"]
    assert decoded["connections"] == [{"unitId": "unit:a", "fromUnitId": "unit:b", "evidenceId": "edge:a",
                                       "direction": "incoming", "attributes": {"edgeWeight": "semantic plugin attribute"}}]


def test_graph_aliasing_preserves_literal_source_and_unknown_plugin_values():
    from service.review.navigation_context import NavigationContext, expand_navigation_result

    identifier = "unit:" + "a" * 64
    edge = "relation:" + "b" * 64
    literal = {"unknownPluginFact": identifier, "unitId": identifier, "evidenceId": edge,
               "content": identifier, "target": identifier, "impactScores": {identifier: 2},
               "nested": [{"nodes": [{"unitId": identifier, "text": "unit@1"}]}]}
    original = {
        "status": "ready", "target": identifier,
        "nodes": [{"unitId": identifier, "path": "worker.py", "content": identifier,
                   "unknownPluginFact": identifier, "attributes": literal}],
        "edges": [{"evidenceId": edge, "sourceUnitId": identifier, "targetUnitId": identifier,
                   "source": identifier, "target": identifier, "attributes": literal}],
        "connections": [{"unitId": identifier, "fromUnitId": identifier, "evidenceId": edge}],
        "sourceWindows": [{"unitId": identifier, "evidenceId": "source:one", "content": identifier,
                           "selectedUnitIds": [identifier], "relationEvidenceIds": [edge]}],
        "coverage": {"source": {"omittedUnitIds": [identifier]}},
        "continuations": [{"tool": "queryCodeGraph", "arguments": {"target": identifier, "cursor": 2}},
                          {"tool": "grepReviewCode", "arguments": {"query": identifier}}],
        "literalExtension": literal,
    }
    navigation = NavigationContext()
    shown = expand_navigation_result(navigation.project(original))
    handle = shown["nodes"][0]["unitId"]
    edge_handle = shown["edges"][0]["evidenceId"]
    assert handle != identifier and edge_handle != edge
    assert shown["nodes"][0]["content"] == identifier
    assert shown["nodes"][0]["unknownPluginFact"] == identifier
    assert shown["nodes"][0]["attributes"] == literal
    assert shown["edges"][0]["attributes"] == literal
    assert shown["edges"][0]["source"] == shown["edges"][0]["target"] == identifier
    assert shown["literalExtension"] == literal
    assert shown["sourceWindows"][0]["content"] == identifier
    assert shown["sourceWindows"][0]["selectedUnitIds"] == [handle]
    assert shown["sourceWindows"][0]["relationEvidenceIds"] == [edge_handle]
    assert shown["coverage"]["source"]["omittedUnitIds"] == [handle]
    assert shown["connections"][0] == {"unitId": handle, "fromUnitId": handle, "evidenceId": edge_handle}
    assert shown["continuations"][0]["arguments"]["target"] == handle
    assert shown["continuations"][1]["arguments"]["query"] == identifier


def test_semantic_expansion_does_not_dereference_unknown_plugin_subtrees():
    from service.review.navigation_context import expand_navigation_result

    projected = compact_navigation_result(deepcopy(GRAPH))
    reference, definition = next(iter(projected["unitDefinitions"].items()))
    literal = {"unitRef": reference, "unitId": definition["unitId"]}
    projected["pluginExtension"] = literal
    restored = expand_navigation_result(projected)
    assert restored["pluginExtension"] == literal
    assert restored["results"] == GRAPH["results"]
