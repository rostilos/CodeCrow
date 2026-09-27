"""Lossless graph metadata projections; no model-quality or token-cost claims."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from service.review.navigation_context import compact_navigation_result
from service.review.verification_tools import VerificationTools
from service.review.verifier import _observations


GRAPH = json.loads((Path(__file__).parent / "fixtures/review_reconciliation/graph_navigation_repeated_units.json").read_text())["result"]


def expanded(projected):
    definitions = projected.get("unitDefinitions", {})

    def expand(value):
        if isinstance(value, dict):
            if set(value) == {"unitRef", "unitId"} and value["unitRef"] in definitions:
                original = definitions[value["unitRef"]]
                assert original["unitId"] == value["unitId"]
                return deepcopy(original)
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
    assert _observations("queryCodeGraph", first) == _observations("queryCodeGraph", second)
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
    assert expanded(result) == full_result
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
    assert _observations("queryCodeGraph", single) <= _observations("queryCodeGraph", repeated)
    # Genuine metadata differences still advance evidence even with the same ID.
    changed = deepcopy(single)
    changed["results"][0]["sourceUnit"]["startLine"] += 1
    assert _observations("queryCodeGraph", changed) - _observations("queryCodeGraph", repeated)


def test_production_semantic_expansion_matches_independent_roundtrip_decoder():
    from service.review.navigation_context import expand_navigation_result

    projected = compact_navigation_result(deepcopy(GRAPH))
    assert expand_navigation_result(projected) == expanded(projected) == GRAPH
