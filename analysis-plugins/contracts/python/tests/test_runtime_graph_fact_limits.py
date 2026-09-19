from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from codecrow_plugins import (
    Capability,
    DetectionRules,
    FileArtifact,
    GraphFact,
    PluginDescriptor,
    PluginKind,
    PluginOutcome,
    PluginRuntime,
    ProjectCapabilities,
)


class _FactPlugin:
    def __init__(self, facts: tuple[GraphFact, ...]):
        self.facts = facts

    def index_file(self, _artifact: FileArtifact):
        return PluginOutcome.handled(self.facts)


def _runtime(
    contributions: dict[str, tuple[GraphFact, ...]],
) -> tuple[PluginRuntime, ProjectCapabilities]:
    descriptors = {
        plugin_id: PluginDescriptor(
            id=plugin_id,
            kind=PluginKind.DOMAIN,
            requires=(),
            capabilities=(Capability.GRAPH,),
            detection=DetectionRules(),
        )
        for plugin_id in contributions
    }
    implementations = {
        plugin_id: _FactPlugin(facts)
        for plugin_id, facts in contributions.items()
    }
    catalog = SimpleNamespace(
        registry=SimpleNamespace(
            descriptor=lambda plugin_id: descriptors[plugin_id],
        ),
        implementation=lambda plugin_id: implementations[plugin_id],
    )
    capabilities = ProjectCapabilities(
        repository_plugins=tuple(contributions),
        fingerprint="sha256:" + "0" * 64,
    )
    return PluginRuntime(catalog), capabilities


def _fact(
    kind: str,
    source: str,
    *,
    relation: str = "declares",
    target: str = "target",
    path: str = "src/example.py",
    attributes: tuple[tuple[str, str], ...] = (),
    related_paths: tuple[str, ...] = (),
) -> GraphFact:
    return GraphFact(
        kind=kind,
        source=source,
        relation=relation,
        target=target,
        path=path,
        attributes=attributes,
        related_paths=related_paths,
    )


def test_graph_facts_reject_large_strings_in_every_fact_location():
    overlong = "x" * 4_097
    valid = _fact("valid", "kept")
    invalid = (
        _fact(overlong, "kind"),
        _fact("source", overlong),
        _fact("relation", "source", relation=overlong),
        _fact("target", "source", target=overlong),
        _fact("path", "source", path=overlong),
        _fact("attribute-key", "source", attributes=((overlong, "value"),)),
        _fact("attribute-value", "source", attributes=(("key", overlong),)),
        _fact("related-path", "source", related_paths=(overlong,)),
    )
    runtime, capabilities = _runtime({
        "complete": tuple(reversed((valid, *invalid))),
    })

    facts, diagnostics = runtime.graph_facts(
        FileArtifact("src/example.py", "pass"),
        capabilities,
    )

    assert facts == (valid,)
    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.code == "plugin-index-output-limit"
    assert diagnostic.plugin_id == "complete"
    assert diagnostic.path == "src/example.py"
    assert diagnostic.recoverable is True
    assert "8 fact(s)" in diagnostic.message
    assert "4096 characters" in diagnostic.message


def test_graph_facts_bound_serialized_payload_bytes():
    first = _fact("first", "first", target="α" * 32)
    second = _fact("second", "second", target="β" * 32)
    runtime, capabilities = _runtime({"first": (first,), "second": (second,)})
    attributed_first = replace(first, contributing_plugin_ids=("first",))
    runtime.MAX_GRAPH_FACT_BYTES_PER_ARTIFACT = len(json.dumps(
        [dict(attributed_first.as_metadata())],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8"))

    facts, diagnostics = runtime.graph_facts(
        FileArtifact("src/example.py", "pass"),
        capabilities,
    )

    assert facts == (first,)
    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.code == "plugin-index-output-limit"
    assert diagnostic.plugin_id == "second"
    assert diagnostic.path == "src/example.py"
    assert diagnostic.recoverable is True
    assert "1 fact(s)" in diagnostic.message
    assert "byte artifact budget" in diagnostic.message


def test_graph_facts_bound_records_with_diagnostics():
    first = tuple(_fact("first", f"first-{index:03d}") for index in range(125))
    second = tuple(_fact("second", f"second-{index:03d}") for index in range(125))
    runtime, capabilities = _runtime({"first": first, "second": second})
    runtime.MAX_FACTS_PER_FILE = 200

    facts, diagnostics = runtime.graph_facts(
        FileArtifact("src/example.py", "pass"),
        capabilities,
    )

    assert facts == tuple(sorted((*first, *second[:75])))
    assert len(facts) == 200
    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.code == "plugin-index-output-limit"
    assert diagnostic.plugin_id == "second"
    assert diagnostic.path == "src/example.py"
    assert diagnostic.recoverable is True
    assert "50 fact(s)" in diagnostic.message


def test_graph_fact_merge_is_deterministic_across_kinds_and_duplicates():
    facts = (
        _fact("kind-a", "a-1", target="x" * 32),
        _fact("kind-a", "a-0", target="x" * 32),
        _fact("kind-b", "b-1", target="x" * 32),
        _fact("kind-b", "b-0", target="x" * 32),
    )
    expected = tuple(sorted(facts))
    runtime, capabilities = _runtime({
        "complete": (*tuple(reversed(facts)), facts[0]),
    })

    selected, diagnostics = runtime.graph_facts(
        FileArtifact("src/example.py", "pass"),
        capabilities,
    )

    assert selected == expected
    assert {fact.kind for fact in selected} == {"kind-a", "kind-b"}
    assert all(fact.contributing_plugin_ids == ("complete",) for fact in selected)
    assert diagnostics == ()


def test_graph_fact_provenance_is_metadata_not_semantic_identity():
    base = _fact("route", "controller", target="GET /orders")
    first = replace(base, contributing_plugin_ids=("first",))
    second = replace(base, contributing_plugin_ids=("second",))

    assert first == second
    assert hash(first) == hash(second)
    assert dict(first.as_metadata())["contributing_plugin_ids"] == ["first"]

    with pytest.raises(
        ValueError,
        match="graph fact contributing plugin ids must be unique and sorted",
    ):
        replace(base, contributing_plugin_ids=("second", "first"))


def test_graph_facts_merge_all_semantic_duplicate_contributors():
    shared = _fact("route", "controller", target="GET /orders")
    runtime, capabilities = _runtime({
        "second": (shared, shared),
        "first": (shared,),
    })

    facts, diagnostics = runtime.graph_facts(
        FileArtifact("src/example.py", "pass"),
        capabilities,
    )

    assert len(facts) == 1
    assert facts[0] == shared
    assert facts[0].contributing_plugin_ids == ("first", "second")
    assert dict(facts[0].as_metadata())["contributing_plugin_ids"] == [
        "first",
        "second",
    ]
    assert diagnostics == ()


def test_graph_fact_rebase_preserves_contributing_plugins():
    fact = replace(
        _fact(
            "route",
            "controller",
            path="routes.py",
            related_paths=("handlers.py",),
        ),
        contributing_plugin_ids=("django",),
    )

    rebased = PluginRuntime._rebase_fact(fact, "services/orders")

    assert rebased.path == "services/orders/routes.py"
    assert rebased.related_paths == ("services/orders/handlers.py",)
    assert rebased.contributing_plugin_ids == ("django",)
