"""Focused coverage for proposed-tree graph tools adapted from code-review-graph."""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager

import pytest

from rag_pipeline.core.review_context import (
    LayeredReviewGraphReader,
    ProposedTreeGeneration,
    ProposedTreeReadSession,
    ProposedTreeReviewContextService,
)
from rag_pipeline.core.review_graph_tools import (
    _canonical_relation_semantic,
    get_review_structural_unit,
    minimal_review_context,
    query_review_graph,
    review_impact_radius,
    traverse_review_graph,
)


def _unit(name, path, *, content=None, record_type="source_unit"):
    unit = {
        "unitId": f"unit:{name}",
        "path": path,
        "kind": "function",
        "name": name,
        "qualifiedName": f"example.{name}",
        "startLine": 1,
        "endLine": 4,
        "language": "python",
        "recordType": record_type,
    }
    if content is not None:
        unit["content"] = content
        unit["contentSha256"] = name * 64
    return unit


def _relation(
    index,
    source,
    target,
    *,
    kind="CALLS",
    relation=None,
    attributes=None,
):
    return {
        "evidenceId": f"relation:{index}",
        "kind": kind,
        "source": source["qualifiedName"],
        "relation": relation or kind.casefold(),
        "target": target["qualifiedName"],
        "origin": {
            "path": source["path"],
            "line": 2,
            "extractor": "plugin" if attributes else "ast",
            "plugins": ["framework-fixture"] if attributes else [],
        },
        "sourceUnit": source,
        "targetUnit": target,
        "relatedPaths": [source["path"], target["path"]],
        "attributes": attributes or {},
    }


class _Reader:
    def __init__(self, units, relations):
        self.units = {unit["unitId"]: dict(unit) for unit in units}
        self.relations = list(relations)

    @staticmethod
    def snapshot():
        return {
            "kind": "proposed_tree",
            "revision": "source",
            "baseRevision": "base",
        }

    def get_unit(self, unit_id):
        unit = self.units.get(unit_id)
        if unit is None:
            return None
        return {
            "snapshot": self.snapshot(),
            "unit": dict(unit),
            "sourceEvidence": "content" in unit,
        }

    def source_detail_for_manifest(self, unit):
        return self.get_unit(unit.get("unitId"))

    def search_units(self, query, *, max_results):
        folded = query.casefold()
        matches = [
            unit
            for unit in self.units.values()
            if folded in " ".join((
                unit["name"], unit["qualifiedName"], unit["path"],
            )).casefold()
        ]
        return [dict(unit) for unit in matches[:max_results]]

    def relations_for_paths(self, paths, *, max_relations):
        paths = set(paths)
        symbols = [
            dict(unit) for unit in self.units.values() if unit["path"] in paths
        ]
        matching = [
            relation
            for relation in self.relations
            if paths.intersection(relation["relatedPaths"])
        ]
        return {
            "snapshot": self.snapshot(),
            "anchors": [
                {
                    "path": path,
                    "symbols": [
                        unit for unit in symbols if unit["path"] == path
                    ],
                    "omittedSymbols": 0,
                }
                for path in paths
            ],
            "relations": matching[:max_relations],
            "coverage": {
                "state": "complete" if len(matching) <= max_relations else "bounded",
                "omittedRelations": max(0, len(matching) - max_relations),
                "omittedSymbols": 0,
            },
        }

    def query_graph(self, pattern, target, *, max_results, cursor=0):
        if pattern == "file_summary":
            results = [unit for unit in self.units.values() if unit["path"] == target]
        elif pattern in {"symbol_search", "symbols"}:
            results = self.search_units(target, max_results=max_results + 1)
        elif pattern == "relations_of":
            results = [
                relation
                for relation in self.relations
                if target in {
                    relation["sourceUnit"]["unitId"],
                    relation["targetUnit"]["unitId"],
                }
            ]
        elif pattern == "callers_of":
            matching_ids = {
                unit["unitId"]
                for unit in self.search_units(target, max_results=100)
            }
            results = [
                relation
                for relation in self.relations
                if relation["kind"] == "CALLS"
                and relation["targetUnit"]["unitId"] in matching_ids
            ]
        else:
            return {"status": "error", "results": []}
        selected = results[cursor:cursor + max_results]
        next_cursor = cursor + len(selected)
        truncated = next_cursor < len(results)
        return {
            "snapshot": self.snapshot(),
            "pattern": pattern,
            "target": target,
            "cursor": cursor,
            "nextCursor": next_cursor if truncated else None,
            "results": [dict(item) for item in selected],
            "truncated": truncated,
        }


def test_minimal_context_keeps_plugin_facts_and_exact_bounded_source():
    changed = _unit(
        "changed",
        "src/changed.py",
        content="def changed():\n    return framework_target()\n",
    )
    target = _unit(
        "framework_target",
        "src/framework.py",
        content="def framework_target():\n    return 42\n",
    )
    plugin_relation = _relation(
        1,
        changed,
        target,
        kind="FRAMEWORK_BINDS",
        attributes={"binding": "route", "route": "/items"},
    )
    reader = _Reader([changed, target], [plugin_relation])

    result = minimal_review_context(
        reader,
        question="Review changed framework route binding",
        focus_paths=["src/changed.py"],
        focus_symbols=["framework_target"],
        changed_paths=["src/changed.py"],
        max_relations=10,
        detail_level="standard",
        include_source=True,
        max_source_windows=1,
        max_source_characters=24,
    )

    assert result["operation"] == "minimal_review_context"
    assert result["edges"][0]["attributes"] == {
        "binding": "route",
        "route": "/items",
    }
    assert result["edges"][0]["origin"]["plugins"] == ["framework-fixture"]
    assert result["sourceWindows"][0]["content"] == "def framework_target():\n"
    assert result["sourceWindows"][0]["changedFile"] is False
    assert result["sourceWindows"][0]["truncated"] is True


def test_minimal_context_reports_reader_truncation_and_source_omission():
    focus = _unit(
        "focus",
        "src/focus.py",
        content="def focus():\n    return 1\n",
    )
    targets = [
        _unit(
            f"target_{index}",
            f"src/target_{index}.py",
            content=f"def target_{index}():\n    return {index}\n",
        )
        for index in range(6)
    ]

    class ReaderWithUnpreloadedRelations(_Reader):
        def relations_for_paths(self, paths, *, max_relations):
            result = super().relations_for_paths(
                paths,
                max_relations=max_relations,
            )
            result["relations"] = []
            result["coverage"] = {
                "state": "complete",
                "omittedRelations": 0,
                "omittedSymbols": 0,
            }
            return result

    reader = ReaderWithUnpreloadedRelations(
        [focus, *targets],
        [
            _relation(index, focus, target)
            for index, target in enumerate(targets, 1)
        ],
    )

    result = minimal_review_context(
        reader,
        question="Review focus behavior",
        focus_paths=["src/focus.py"],
        changed_paths=["src/focus.py"],
        max_relations=4,
        include_source=True,
        max_source_windows=1,
        max_source_characters=200,
    )

    assert result["coverage"]["state"] == "bounded"
    assert result["coverage"]["truncated"] is True
    assert set(result["coverage"]["partialReasons"]) == {
        "graph_relation_limit",
        "source_window_limit",
    }
    assert result["coverage"]["source"] == {
        "state": "bounded",
        "truncated": True,
        "availableSourceUnits": 3,
        "returnedSourceUnits": 1,
        "omittedSourceUnits": 2,
        "truncatedSourceWindows": 0,
        "omittedUnitIds": [
            "unit:target_1",
            "unit:focus",
        ],
        "maxSourceWindows": 1,
        "maxSourceCharacters": 200,
        "returnedSourceCharacters": len(result["sourceWindows"][0]["content"]),
    }
    assert result["continuations"] == [{
        "tool": "queryCodeGraph",
        "arguments": {
            "pattern": "relations_of",
            "target": "unit:focus",
            "cursor": 2,
            "maxResults": 4,
            "detailLevel": "minimal",
        },
    }]


def test_minimal_context_reserves_budget_for_a_real_second_hop():
    focus = _unit("focus", "src/focus.py")
    direct = _unit("direct", "src/direct.py")
    second_hop = _unit("second_hop", "src/second.py")
    tail = _unit("tail", "src/tail.py")
    reader = _Reader(
        [focus, direct, second_hop, tail],
        [
            _relation(1, focus, direct),
            _relation(2, direct, second_hop),
            _relation(3, second_hop, tail),
        ],
    )

    result = minimal_review_context(
        reader,
        question="Review changed",
        focus_paths=["src/focus.py"],
        max_relations=6,
        detail_level="standard",
    )

    edges = {edge["evidenceId"]: edge for edge in result["edges"]}
    assert edges["relation:1"]["depth"] == 0
    assert edges["relation:2"]["depth"] == 2
    assert result["coverage"]["depthReached"] == 2
    assert result["coverage"]["relationsByDepth"] == {"0": 1, "2": 1}


@pytest.mark.parametrize(
    ("relation", "expected"),
    (
        ({"kind": "python-call", "relation": "calls"}, "CALLS"),
        ({"kind": "fastapi-dependency", "relation": "depends-on"}, "DEPENDS_ON"),
        ({"kind": "python-import", "relation": "imports"}, "IMPORTS"),
        ({"kind": "java-import", "relation": "imports-from"}, "IMPORTS_FROM"),
        ({"kind": "typescript-reference", "relation": "uses"}, "REFERENCES"),
        ({"kind": "php-instance-call-relation", "relation": "calls-instance"}, "CALLS"),
        ({"kind": "python-call-resolution", "relation": "calls-resolved-target"}, "CALLS"),
        ({"kind": "module-resolution", "relation": "resolves-import"}, "IMPORTS"),
        (
            {
                "kind": "data-contract-reference",
                "relation": "references-json-schema-target",
            },
            "REFERENCES",
        ),
        ({"kind": "framework-binds", "relation": "binds"}, "FRAMEWORK_BINDS"),
    ),
)
def test_canonical_relation_semantic_uses_recognized_portable_relation(
    relation,
    expected,
):
    assert _canonical_relation_semantic(relation) == expected


def test_plugin_specific_call_kind_scores_and_filters_as_calls():
    changed = _unit("changed", "src/changed.py")
    caller = _unit("caller", "src/caller.py")
    relation = _relation(
        1,
        caller,
        changed,
        kind="python-call",
        relation="calls",
    )
    reader = _Reader([changed, caller], [relation])

    impact = review_impact_radius(
        reader,
        targets=["unit:changed"],
        changed_paths=["src/changed.py"],
        max_depth=1,
        max_results=10,
        detail_level="minimal",
    )
    traversal = traverse_review_graph(
        reader,
        start="unit:caller",
        strategy="bfs",
        direction="outgoing",
        relation_kinds=["call"],
        max_depth=1,
        max_results=10,
    )
    plugin_kind_traversal = traverse_review_graph(
        reader,
        start="unit:caller",
        strategy="bfs",
        direction="outgoing",
        relation_kinds=["python-call"],
        max_depth=1,
        max_results=10,
    )

    assert impact["impactScores"] == {"unit:caller": 0.6}
    assert [node["name"] for node in traversal["nodes"]] == ["caller", "changed"]
    assert [edge["kind"] for edge in traversal["edges"]] == ["python-call"]
    assert [node["name"] for node in plugin_kind_traversal["nodes"]] == [
        "caller",
        "changed",
    ]


def test_impact_radius_follows_dependents_tests_and_custom_plugin_relations():
    changed = _unit("changed", "src/changed.py")
    caller = _unit("caller", "src/caller.py")
    second_caller = _unit("second_caller", "src/second_caller.py")
    callee = _unit("callee", "src/callee.py")
    test = _unit("test_changed", "tests/test_changed.py")
    framework = _unit("framework_consumer", "src/framework.py")
    relations = [
        _relation(1, caller, changed),
        _relation(2, second_caller, caller),
        _relation(3, changed, callee),
        _relation(4, changed, test, kind="TESTED_BY"),
        _relation(
            5,
            framework,
            changed,
            kind="FRAMEWORK_BINDS",
            attributes={"binding": "subscriber"},
        ),
        # The direct import path scores .3. The two-hop CALLS path scores .36,
        # so best-score relaxation must replace the earlier connection.
        _relation(6, second_caller, changed, kind="IMPORTS"),
    ]

    result = review_impact_radius(
        _Reader(
            [changed, caller, second_caller, callee, test, framework],
            relations,
        ),
        targets=["src/changed.py"],
        changed_paths=["src/changed.py"],
        max_depth=2,
        max_results=20,
        detail_level="minimal",
    )

    depths = {node["name"]: node["depth"] for node in result["nodes"]}
    assert depths == {
        "changed": 0,
        "caller": 1,
        "second_caller": 2,
        "test_changed": 1,
        "framework_consumer": 1,
    }
    assert "callee" not in depths
    assert result["impactedFiles"] == [
        "src/caller.py",
        "src/framework.py",
        "src/second_caller.py",
        "tests/test_changed.py",
    ]
    assert {edge["kind"] for edge in result["edges"]} == {
        "CALLS",
        "IMPORTS",
        "TESTED_BY",
        "FRAMEWORK_BINDS",
    }
    assert {edge["evidenceId"] for edge in result["edges"]} == {
        "relation:1",
        "relation:2",
        "relation:4",
        "relation:5",
        # This lower-scoring direct edge is not the winning connection for
        # second_caller, but it remains part of the induced returned subgraph.
        "relation:6",
    }
    assert result["impactScores"] == {
        "unit:caller": 0.6,
        "unit:test_changed": 0.42,
        "unit:second_caller": 0.36,
        "unit:framework_consumer": 0.3,
    }
    caller_connection = next(
        item for item in result["connections"] if item["unitId"] == "unit:caller"
    )
    assert caller_connection == {
        "unitId": "unit:caller",
        "fromUnitId": "unit:changed",
        "evidenceId": "relation:1",
        "kind": "CALLS",
        "direction": "incoming",
        "edgeWeight": 1.0,
        "depthDecay": 0.6,
        "depth": 1,
        "impactScore": 0.6,
    }
    second_connection = next(
        item
        for item in result["connections"]
        if item["unitId"] == "unit:second_caller"
    )
    assert second_connection["evidenceId"] == "relation:2"
    assert second_connection["depth"] == 2
    assert second_connection["impactScore"] == 0.36


def test_deleted_changed_target_is_absent_from_layered_traversal():
    caller = _unit("caller", "src/caller.py")
    deleted = _unit("deleted", "src/changed.py")
    stale_relation = _relation(1, caller, deleted)
    reader = LayeredReviewGraphReader(
        _Reader([caller, deleted], [stale_relation]),
        _Reader([], []),
        ["src/changed.py"],
    )

    result = traverse_review_graph(
        reader,
        start="unit:caller",
        direction="outgoing",
        max_depth=1,
        max_results=10,
    )

    assert [node["unitId"] for node in result["nodes"]] == ["unit:caller"]
    assert result["edges"] == []
    assert result["frontier"] == []


def test_renamed_changed_target_is_absent_from_layered_impact():
    caller = _unit("caller", "src/caller.py")
    target_head = _unit("service", "src/changed.py")
    proposed = _unit("service_v2", "src/changed.py")
    # Keep the opaque ID stable to prove reconciliation is based on the exact
    # proposed symbol identity, not merely an ID/path or a fuzzy search hit.
    proposed["unitId"] = target_head["unitId"]
    stale_relation = _relation(1, caller, target_head)
    reader = LayeredReviewGraphReader(
        _Reader([caller, target_head], [stale_relation]),
        _Reader([proposed], []),
        ["src/changed.py"],
    )

    result = review_impact_radius(
        reader,
        targets=[target_head["unitId"]],
        max_depth=1,
        max_results=10,
    )

    assert [node["unitId"] for node in result["nodes"]] == [
        target_head["unitId"],
    ]
    assert result["nodes"][0]["name"] == "service_v2"
    assert result["edges"] == []
    assert result["connections"] == []
    assert result["impactScores"] == {}


def test_unchanged_symbol_identity_rebinds_base_relation_to_overlay_unit():
    caller = _unit("caller", "src/caller.py")
    target_head = _unit("service", "src/changed.py")
    proposed = _unit("service", "src/changed.py")
    proposed["unitId"] = "unit:service-proposed"
    reader = LayeredReviewGraphReader(
        _Reader([caller, target_head], [_relation(1, caller, target_head)]),
        _Reader([proposed], []),
        ["src/changed.py"],
    )

    result = traverse_review_graph(
        reader,
        start="unit:caller",
        direction="outgoing",
        max_depth=1,
        max_results=10,
    )

    assert [node["unitId"] for node in result["nodes"]] == [
        "unit:caller",
        "unit:service-proposed",
    ]
    assert result["edges"][0]["targetUnitId"] == "unit:service-proposed"


def test_exact_full_impact_roots_do_not_report_root_truncation():
    roots = [
        _unit(f"root_{index:03d}", "src/many.py")
        for index in range(100)
    ]

    result = review_impact_radius(
        _Reader(roots, []),
        targets=["src/many.py"],
        max_depth=1,
        max_results=100,
    )

    assert len(result["roots"]) == 100
    assert result["coverage"]["state"] == "complete"
    assert "root_result_limit" not in result["coverage"]["partialReasons"]


def test_impact_seeds_every_resolved_root_and_caps_only_returned_output():
    roots = [_unit(f"root_{index}", "src/many.py") for index in range(3)]
    callers = [
        _unit(f"caller_{index}", f"src/caller_{index}.py")
        for index in range(6)
    ]
    relations = [
        _relation(index + 1, caller, roots[index % len(roots)])
        for index, caller in enumerate(callers)
    ]

    result = review_impact_radius(
        _Reader([*roots, *callers], relations),
        targets=["src/many.py"],
        max_depth=1,
        max_results=1,
        detail_level="minimal",
    )

    assert result["coverage"]["resolvedRoots"] == 3
    assert result["coverage"]["returnedRoots"] == 1
    assert result["coverage"]["totalDiscovered"] == 6
    assert result["coverage"]["totalScored"] == 6
    assert result["coverage"]["returnedImpacted"] == 1
    assert len(result["roots"]) == 1
    assert len(result["impactScores"]) == 1
    assert {"result_limit", "root_response_limit"}.issubset(
        result["coverage"]["partialReasons"]
    )


def test_impact_keeps_referenced_root_and_winning_edge_when_roots_are_bounded():
    first_root = _unit("first_root", "src/many.py")
    referenced_root = _unit("referenced_root", "src/many.py")
    caller = _unit("caller", "src/caller.py")
    relation = _relation(1, caller, referenced_root)

    result = review_impact_radius(
        _Reader([first_root, referenced_root, caller], [relation]),
        targets=["src/many.py"],
        max_depth=1,
        max_results=1,
        detail_level="minimal",
    )

    assert [root["unitId"] for root in result["roots"]] == [
        referenced_root["unitId"]
    ]
    returned_unit_ids = {node["unitId"] for node in result["nodes"]}
    returned_evidence_ids = {edge["evidenceId"] for edge in result["edges"]}
    assert result["connections"] == [{
        "unitId": caller["unitId"],
        "fromUnitId": referenced_root["unitId"],
        "evidenceId": relation["evidenceId"],
        "kind": "CALLS",
        "direction": "incoming",
        "edgeWeight": 1.0,
        "depthDecay": 0.6,
        "depth": 1,
        "impactScore": 0.6,
    }]
    assert result["connections"][0]["fromUnitId"] in returned_unit_ids
    assert result["connections"][0]["evidenceId"] in returned_evidence_ids
    assert result["coverage"]["omittedRoots"] == 1
    assert result["coverage"]["omittedConnectionRelations"] == 0


def test_impact_fetches_induced_edges_between_max_depth_nodes():
    changed = _unit("changed", "src/changed.py")
    first = _unit("first", "src/first.py")
    second = _unit("second", "src/second.py")
    relations = [
        _relation(1, first, changed),
        _relation(2, second, changed),
        _relation(3, first, second, kind="REFERENCES"),
    ]

    result = review_impact_radius(
        _Reader([changed, first, second], relations),
        targets=[changed["unitId"]],
        max_depth=1,
        max_results=10,
        detail_level="minimal",
    )

    assert {edge["evidenceId"] for edge in result["edges"]} == {
        "relation:1",
        "relation:2",
        "relation:3",
    }
    assert result["coverage"]["inducedRelationsFound"] == 3


def test_traversal_supports_bfs_dfs_direction_and_relation_filter():
    a = _unit("a", "src/a.py")
    b = _unit("b", "src/b.py")
    c = _unit("c", "src/c.py")
    d = _unit("d", "src/d.py")
    e = _unit("e", "src/e.py")
    relations = [
        _relation(1, a, b),
        _relation(2, a, c),
        _relation(3, b, d),
        _relation(4, c, e),
        _relation(5, e, a, kind="REFERENCES"),
    ]
    reader = _Reader([a, b, c, d, e], relations)

    bfs = traverse_review_graph(
        reader,
        start="unit:a",
        strategy="bfs",
        direction="outgoing",
        relation_kinds=["CALLS"],
        max_depth=2,
        max_results=20,
    )
    dfs = traverse_review_graph(
        reader,
        start="unit:a",
        strategy="dfs",
        direction="outgoing",
        relation_kinds=["CALLS"],
        max_depth=2,
        max_results=20,
    )

    assert [node["name"] for node in bfs["nodes"]] == ["a", "b", "c", "d", "e"]
    assert [node["name"] for node in dfs["nodes"]] == ["a", "b", "d", "c", "e"]
    assert {edge["kind"] for edge in bfs["edges"]} == {"CALLS"}
    assert {item["reason"] for item in bfs["frontier"]} == {"depth_limit"}


def test_named_root_reuses_exact_query_resolution_before_fuzzy_search():
    exact = _unit("exact", "src/exact.py")
    exact["qualifiedName"] = "service.Service.run"
    decoy = _unit("decoy", "src/decoy.py")
    decoy["qualifiedName"] = "service.Service.runner"

    class ExactResolutionReader(_Reader):
        def __init__(self):
            super().__init__([exact, decoy], [])
            self.fuzzy_searches = 0

        def search_units(self, query, *, max_results):
            self.fuzzy_searches += 1
            return [dict(decoy)]

        def query_graph(self, pattern, target, *, max_results, cursor=0):
            if pattern == "relations_of" and target == "Service.run":
                return {
                    "snapshot": self.snapshot(),
                    "pattern": pattern,
                    "target": target,
                    "resolvedUnits": [dict(exact)],
                    "results": [],
                    "cursor": cursor,
                    "nextCursor": None,
                    "truncated": False,
                }
            return super().query_graph(
                pattern,
                target,
                max_results=max_results,
                cursor=cursor,
            )

    reader = ExactResolutionReader()
    result = traverse_review_graph(
        reader,
        start="Service.run",
        direction="both",
        max_depth=1,
        max_results=10,
    )
    query = query_review_graph(
        reader,
        pattern="relations_of",
        target="Service.run",
        detail_level="minimal",
    )

    assert [node["unitId"] for node in result["nodes"]] == [exact["unitId"]]
    assert [unit["unitId"] for unit in query["resolvedUnits"]] == [exact["unitId"]]
    assert reader.fuzzy_searches == 0


def test_traversal_token_budget_is_bounded_and_returns_actionable_frontier():
    root = _unit(
        "root_with_a_descriptive_name",
        "src/root.py",
        content="def root_with_a_descriptive_name():\n    return None\n",
    )
    neighbors = [
        _unit(
            f"neighbor_with_a_descriptive_name_{index:02d}",
            f"src/neighbor_{index:02d}.py",
            content=f"def neighbor_{index:02d}():\n    return {index}\n",
        )
        for index in range(12)
    ]
    reader = _Reader(
        [root, *neighbors],
        [
            _relation(index + 1, root, neighbor)
            for index, neighbor in enumerate(neighbors)
        ],
    )
    token_budget = 512

    result = traverse_review_graph(
        reader,
        start=root["unitId"],
        strategy="bfs",
        direction="outgoing",
        max_depth=2,
        max_results=100,
        token_budget=token_budget,
        include_source=True,
        max_source_windows=1,
        max_source_characters=64,
    )
    repeated = traverse_review_graph(
        reader,
        start=root["unitId"],
        strategy="bfs",
        direction="outgoing",
        max_depth=2,
        max_results=100,
        token_budget=token_budget,
        include_source=True,
        max_source_windows=1,
        max_source_characters=64,
    )

    coverage = result["coverage"]
    serialized_characters = len(json.dumps(
        result,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ))
    assert coverage["tokenBudget"] == token_budget
    assert coverage["serializedCharacters"] == serialized_characters
    assert coverage["estimatedTokens"] == (serialized_characters + 3) // 4
    assert coverage["estimatedTokens"] <= token_budget
    assert coverage["serializedCharacters"] <= token_budget * 4
    assert coverage["truncated"] is True
    assert "token_budget" in coverage["partialReasons"]
    assert coverage["returnedNodes"] + coverage["omittedNodes"] == coverage[
        "discoveredNodes"
    ]
    assert coverage["returnedRelations"] + coverage["omittedRelations"] == coverage[
        "discoveredRelations"
    ]
    assert 1 <= coverage["returnedNodes"] < len(neighbors) + 1
    assert coverage["sourceIncluded"] is True
    assert coverage["source"]["returnedSourceUnits"] <= 1
    token_frontier = [
        item for item in result["frontier"] if item["reason"] == "token_budget"
    ]
    assert token_frontier
    assert all(item.get("unitId") for item in token_frontier)

    def expected_continuation(item):
        return {
            "tool": "traverseCodeGraph",
            "arguments": {
                "start": item["unitId"],
                "strategy": "bfs",
                "direction": "outgoing",
                "maxDepth": max(0, 2 - int(item.get("depth") or 0)),
                "maxResults": 100,
                "tokenBudget": token_budget,
                "detailLevel": "standard",
                "includeSource": True,
                "maxSourceWindows": 1,
                "maxSourceCharacters": 64,
            },
        }

    traversal_continuations = [
        (item, item["continuation"])
        for item in token_frontier
        if item.get("continuation")
    ]
    for item, continuation in traversal_continuations:
        assert continuation == expected_continuation(item)

    if not traversal_continuations:
        probe_sizes = []
        for frontier_index, item in enumerate(result["frontier"]):
            if item.get("reason") != "token_budget":
                continue
            probe = json.loads(json.dumps(result))
            probe["frontier"][frontier_index]["continuation"] = (
                expected_continuation(item)
            )
            for _iteration in range(8):
                probe_characters = len(json.dumps(
                    probe,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ))
                probe["coverage"]["serializedCharacters"] = probe_characters
                probe["coverage"]["estimatedTokens"] = (
                    probe_characters + 3
                ) // 4
            probe_sizes.append(probe_characters)
        assert probe_sizes
        assert min(probe_sizes) > token_budget * 4
    assert {
        item["unitId"] for item in token_frontier
    }.issubset({unit["unitId"] for unit in (root, *neighbors)})
    assert repeated == result


def test_traversal_counts_each_token_omitted_relation_once():
    units = [_unit(f"node_{index}", f"src/node_{index}.py") for index in range(6)]
    relations = [
        _relation(
            index,
            units[index - 1],
            units[index],
            attributes={"payload": "x" * 900},
        )
        for index in range(1, len(units))
    ]

    result = traverse_review_graph(
        _Reader(units, relations),
        start=units[0]["unitId"],
        direction="outgoing",
        max_depth=6,
        max_results=20,
        token_budget=512,
    )

    coverage = result["coverage"]
    assert coverage["discoveredRelations"] == len(relations)
    assert coverage["returnedRelations"] + coverage["omittedRelations"] == len(
        relations
    )


def test_result_limited_traversal_does_not_claim_token_omission():
    root = _unit("root", "src/root.py")
    neighbor = _unit("neighbor", "src/neighbor.py")

    result = traverse_review_graph(
        _Reader([root, neighbor], [_relation(1, root, neighbor)]),
        start=root["unitId"],
        direction="outgoing",
        max_depth=2,
        max_results=1,
        token_budget=512,
    )

    assert "result_limit" in result["coverage"]["partialReasons"]
    assert "token_budget" not in result["coverage"]["partialReasons"]


def test_traversal_bounds_long_echoed_controls_and_ambiguous_roots():
    unresolved = traverse_review_graph(
        _Reader([], []),
        start="x" * 1000,
        relation_kinds=[("K" * 10_000) + str(index) for index in range(50)],
        token_budget=512,
    )
    assert unresolved["coverage"]["serializedCharacters"] <= 512 * 4
    assert unresolved["coverage"]["estimatedTokens"] <= 512

    first = _unit("first", "src/first.py")
    second = _unit("second", "src/second.py")

    class AmbiguousReader(_Reader):
        def query_graph(self, pattern, target, *, max_results, cursor=0):
            if pattern == "relations_of" and target == "Shared.run":
                return {
                    "status": "ambiguous",
                    "error": "multiple units",
                    "candidates": [first, second],
                    "candidateCount": 2,
                    "candidatesTruncated": False,
                    "results": [],
                }
            return super().query_graph(
                pattern,
                target,
                max_results=max_results,
                cursor=cursor,
            )

    reader = AmbiguousReader([first, second], [])
    traversal = traverse_review_graph(reader, start="Shared.run")
    impact = review_impact_radius(reader, targets=["Shared.run"])
    for response in (traversal, impact):
        assert response["status"] == "ambiguous"
        assert response["candidateCount"] == 2
        assert {candidate["unitId"] for candidate in response["candidates"]} == {
            first["unitId"],
            second["unitId"],
        }


def test_exact_query_and_unit_keep_standard_plugin_payload_and_proposed_snapshot():
    source = _unit("source", "src/source.py", content="def source():\n    pass\n")
    target = _unit("target", "src/target.py")
    relation = _relation(
        1,
        source,
        target,
        kind="FRAMEWORK_BINDS",
        attributes={"scope": "request"},
    )
    reader = _Reader([source, target], [relation])

    query = query_review_graph(
        reader,
        pattern="relations_of",
        target="unit:source",
        max_results=5,
        detail_level="standard",
    )
    unit = get_review_structural_unit(reader, unit_id="unit:source")

    assert query["results"][0] == relation
    assert query["snapshot"]["kind"] == "proposed_tree"
    assert unit["unit"]["content"] == "def source():\n    pass\n"
    assert unit["sourceEvidence"] is True
    assert unit["snapshot"]["kind"] == "proposed_tree"


def test_ambiguous_query_returns_stable_coverage_and_exact_id_retry_candidates():
    first = _unit("duplicate_one", "src/one.py")
    second = _unit("duplicate_two", "src/two.py")

    class AmbiguousReader(_Reader):
        def query_graph(self, pattern, target, *, max_results, cursor=0):
            candidates = [first, second]
            selected = candidates[cursor:cursor + max_results]
            next_cursor = cursor + len(selected)
            truncated = next_cursor < len(candidates)
            return {
                "status": "ambiguous",
                "pattern": pattern,
                "target": target,
                "cursor": cursor,
                "nextCursor": next_cursor if truncated else None,
                "results": [],
                "truncated": truncated,
                "error": "Target resolves to multiple structural units",
                "candidates": selected,
                "candidateCount": 2,
                "candidateResultCount": len(selected),
                "candidatesTruncated": truncated,
                "hint": "Retry with one candidate unitId",
            }

    reader = AmbiguousReader([first, second], [])
    first_page = query_review_graph(
        reader,
        pattern="callers_of",
        target="duplicate",
        max_results=1,
        include_source=True,
    )
    second_page = query_review_graph(
        reader,
        pattern="callers_of",
        target="duplicate",
        max_results=1,
        cursor=first_page["nextCursor"],
        include_source=True,
    )

    assert first_page["status"] == second_page["status"] == "ambiguous"
    assert first_page["nextCursor"] == 1
    assert first_page["truncated"] is True
    assert second_page["nextCursor"] is None
    assert second_page["truncated"] is False
    assert [
        first_page["candidates"][0]["unitId"],
        second_page["candidates"][0]["unitId"],
    ] == [first["unitId"], second["unitId"]]
    for result in (first_page, second_page):
        assert result["results"] == []
        assert result["sourceWindows"] == []
        assert result["coverage"] == {
            "state": "bounded",
            "truncated": True,
            "partialReasons": ["ambiguous_target"],
            "maxResults": 1,
            "returnedResults": 0,
            "sourceIncluded": True,
            "source": {
                "state": "unavailable",
                "truncated": False,
                "availableSourceUnits": 0,
                "returnedSourceUnits": 0,
                "omittedSourceUnits": 0,
                "truncatedSourceWindows": 0,
            },
        }


def test_exact_unit_content_window_continues_without_changing_full_digest():
    content = "0123456789abcdef"
    source = _unit("windowed", "src/windowed.py", content=content)
    source["contentSha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
    reader = _Reader([source], [])

    first = get_review_structural_unit(
        reader,
        unit_id=source["unitId"],
        offset=3,
        max_characters=5,
    )
    second = get_review_structural_unit(
        reader,
        unit_id=source["unitId"],
        offset=first["unit"]["contentWindow"]["nextOffset"],
        max_characters=20,
    )
    eof = get_review_structural_unit(
        reader,
        unit_id=source["unitId"],
        offset=len(content),
        max_characters=5,
    )

    assert first["unit"]["content"] == "34567"
    assert first["unit"]["contentSha256"] == source["contentSha256"]
    assert first["unit"]["contentWindow"] == {
        "offset": 3,
        "endOffset": 8,
        "totalCharacters": len(content),
        "truncated": True,
        "nextOffset": 8,
    }
    assert second["unit"]["content"] == content[8:]
    assert second["unit"]["contentSha256"] == source["contentSha256"]
    assert second["unit"]["contentWindow"] == {
        "offset": 8,
        "endOffset": len(content),
        "totalCharacters": len(content),
        "truncated": False,
        "nextOffset": None,
    }
    assert first["unit"]["content"] + second["unit"]["content"] == content[3:]
    assert eof["unit"]["content"] == ""
    assert eof["unit"]["contentWindow"] == {
        "offset": len(content),
        "endOffset": len(content),
        "totalCharacters": len(content),
        "truncated": False,
        "nextOffset": None,
    }


def test_large_plugin_context_is_bounded_by_default_with_exact_continuation():
    content = "architecture-context\n" * 26_215
    context = _unit(
        "architecture",
        "src/architecture.context",
        content=content,
        record_type="plugin_context",
    )
    context["contentSha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()

    result = get_review_structural_unit(
        _Reader([context], []),
        unit_id=context["unitId"],
    )

    returned = result["unit"]["content"]
    window = result["unit"]["contentWindow"]
    assert 0 < len(returned) < len(content)
    assert result["unit"]["contentSha256"] == context["contentSha256"]
    assert window == {
        "offset": 0,
        "endOffset": len(returned),
        "totalCharacters": len(content),
        "truncated": True,
        "nextOffset": len(returned),
    }


def test_every_service_operation_uses_the_shared_read_session():
    changed = _unit("changed", "src/changed.py", content="def changed():\n    pass\n")
    precise = _unit("precise", "src/precise.py")
    reader = _Reader([changed, precise], [])
    generation = ProposedTreeGeneration(
        collection_target="review-generation",
        receipt={"generation_manifest_sha256": "a" * 64},
        target_source_tree_sha256="b" * 64,
        overlay_sha256="c" * 64,
        proposed_source_tree_sha256="d" * 64,
        representation_identity="sha256:" + "e" * 64,
        changed_paths=("src/changed.py",),
        deleted_paths=(),
        cache_hit=True,
    )
    service = ProposedTreeReviewContextService(index_manager=None)
    opened = []

    @contextmanager
    def open_session(**arguments):
        opened.append(arguments)
        yield ProposedTreeReadSession(reader=reader, generation=generation)

    service.open_read_session = open_session
    binding = {
        "target_repo_path": "/tmp/target",
        "review_overlay_path": "/tmp/overlay",
        "workspace": "workspace",
        "project": "project",
        "target_branch": "main",
        "base_revision": "base",
        "source_revision": "source",
        "base_collection_target": "base-generation",
        "base_generation_manifest_sha256": "f" * 64,
        "review_collection_target": "review-generation",
        "review_generation_manifest_sha256": "a" * 64,
        "focus_paths": ["src/changed.py"],
    }

    service.minimal_review_context(**binding, question="Review changed")
    impact = service.review_impact_radius(
        **binding,
        targets=["unit:precise"],
    )
    service.traverse_review_graph(**binding, start="unit:changed")
    service.query_review_graph(
        **binding,
        pattern="file_summary",
        target="src/changed.py",
    )
    service.get_review_structural_unit(**binding, unit_id="unit:changed")

    assert len(opened) == 5
    assert all(arguments["source_revision"] == "source" for arguments in opened)
    assert all(arguments["base_revision"] == "base" for arguments in opened)
    assert [root["unitId"] for root in impact["roots"]] == ["unit:precise"]
