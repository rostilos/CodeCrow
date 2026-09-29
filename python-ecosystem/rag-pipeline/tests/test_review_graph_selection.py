"""Complete response parity and serialization work for graph evidence selection."""
import hashlib
import json
from pathlib import Path

import pytest

from rag_pipeline.core.review_graph import selection
from rag_pipeline.core.review_graph_tools import traverse_review_graph
from .test_review_graph_tools import _Reader, _relation, _unit


def _graph_fixture():
    units = [
        _unit(
            f'Node{index}_Δ"', f"src/component_{index}.py",
            content=(f'def operation_{index}():\n    return "escaped \\" and λ"\n' * 35),
        )
        for index in range(28)
    ]
    relations = [
        _relation(
            f"{index}_{offset}", units[index], units[(index + offset) % len(units)],
            attributes={"reason": f"dependency {index}→{offset}", "flags": [True, False]},
        )
        for index in range(len(units)) for offset in (1, 3, 5)
    ]
    return _Reader(units, relations), units, relations


_ORACLE = json.loads((Path(__file__).parent / "fixtures/review_graph_response_oracle.json").read_text())


@pytest.mark.parametrize("case", _ORACLE)
def test_complete_traversal_response_matches_pre_refactor_oracle(case):
    reader, units, _ = _graph_fixture()
    response = traverse_review_graph(
        reader, start=units[0]["unitId"], max_depth=5, max_results=28,
        changed_paths=[units[0]["path"]], **case["arguments"],
    )
    encoded = json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(encoded.encode()).hexdigest() == case["responseSha256"]
    assert response["coverage"]["serializedCharacters"] == len(encoded)


def test_graph_selection_serializes_each_fact_at_most_once(monkeypatch):
    _, units, relations = _graph_fixture()
    visited = {unit["unitId"]: (unit, index) for index, unit in enumerate(units)}
    edges = {edge["evidenceId"]: (edge, 1) for edge in relations}
    original = selection._json_char_length
    calls = []

    def counted(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(selection, "_json_char_length", counted)
    selected = selection.select_graph_evidence(
        visited, edges, detail_level="standard", character_budget=1_000_000,
    )
    assert len(selected.units) == len(units)
    assert len(selected.relations) == len(relations)
    assert len(calls) == 1 + len(units) + len(relations)
    assert selected.serialized_characters == original({
        "nodes": selected.nodes, "edges": selected.edges,
    })
