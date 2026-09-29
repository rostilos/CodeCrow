from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from pathlib import Path

import pytest

from codecrow_plugins import (
    Capability,
    DetectionRules,
    FileArtifact,
    GraphFact,
    PluginCatalog,
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
    kinds: dict[str, PluginKind] | None = None,
) -> tuple[PluginRuntime, ProjectCapabilities]:
    descriptors = {
        plugin_id: PluginDescriptor(
            id=plugin_id,
            kind=(kinds or {}).get(plugin_id, PluginKind.DOMAIN),
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
        file_plugins={
            "src/example.py": tuple(
                plugin_id for plugin_id, descriptor in descriptors.items()
                if descriptor.kind is PluginKind.LANGUAGE
            ),
        },
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


def test_graph_facts_preserve_long_strings_in_every_fact_location():
    long_value = "x" * 4_097
    expected = (
        _fact(long_value, "kind"),
        _fact("source", long_value),
        _fact("relation", "source", relation=long_value),
        _fact("target", "source", target=long_value),
        _fact("path", "source", path=long_value),
        _fact("attribute-key", "source", attributes=((long_value, "value"),)),
        _fact("attribute-value", "source", attributes=(("key", long_value),)),
        _fact("related-path", "source", related_paths=(long_value,)),
        replace(_fact("provenance", "source"), contributing_plugin_ids=(long_value,)),
    )
    runtime, capabilities = _runtime({"complete": tuple(reversed(expected))})

    facts, diagnostics = runtime.graph_facts(
        FileArtifact("src/example.py", "pass"), capabilities,
    )

    assert facts == tuple(sorted(expected))
    assert diagnostics == ()
    assert all("complete" in fact.contributing_plugin_ids for fact in facts)
    assert next(fact for fact in facts if fact.kind == "provenance").contributing_plugin_ids == (
        "complete", long_value,
    )


def test_graph_facts_preserve_payload_larger_than_previous_serialized_byte_limit():
    # Each string is below the former string limit; together the distinct facts
    # exceed 16 MiB. Neither payload admission nor provenance merging may drop one.
    payload = "α" * 3_000
    expected = tuple(
        _fact("route", f"controller-{index:04d}", target=payload)
        for index in range(3_000)
    )
    assert len(json.dumps(
        [dict(fact.as_metadata()) for fact in expected],
        ensure_ascii=False,
    ).encode("utf-8")) > 16_777_216
    runtime, capabilities = _runtime({
        "first": expected,
        "second": (expected[-1],),
    })

    facts, diagnostics = runtime.graph_facts(
        FileArtifact("src/example.py", "pass"), capabilities,
    )

    assert facts == expected
    assert facts[-1].contributing_plugin_ids == ("first", "second")
    assert diagnostics == ()


def test_graph_facts_keep_language_and_framework_evidence_beyond_previous_counts():
    language = tuple(_fact("language", f"symbol-{index:05d}") for index in range(5_001))
    framework = tuple(_fact("framework", f"route-{index:05d}") for index in range(2_001))
    shared = _fact("shared", "symbol")
    runtime, capabilities = _runtime(
        {"language": (*language, shared), "framework": (*framework, shared)},
        {"language": PluginKind.LANGUAGE, "framework": PluginKind.FRAMEWORK},
    )

    facts, diagnostics = runtime.graph_facts(
        FileArtifact("src/example.py", "pass"), capabilities,
    )

    assert facts == tuple(sorted((*language, *framework, shared)))
    assert len(facts) == 7_003
    assert facts[-1].contributing_plugin_ids == ("framework", "language")
    assert diagnostics == ()


def test_javascript_long_call_expression_is_retained_exactly():
    catalog = PluginCatalog.discover(Path(__file__).resolve().parents[3])
    runtime = PluginRuntime(catalog)
    path = "app/code/Shop/Module/view/frontend/web/js/lib/library.js"
    expression = '(function () { const value = "' + ("payload" * 1_000) + '"; return value; })'
    artifact = FileArtifact(path, f"const result = {expression}();")
    expected = catalog.implementation("javascript").index_file(artifact).value
    long_call = next(fact for fact in expected if len(fact.target) > 4_096)
    assert long_call.target == expression
    capabilities = ProjectCapabilities(
        repository_plugins=("javascript",),
        file_plugins={path: ("javascript",)},
        fingerprint="sha256:" + "0" * 64,
    )

    facts, diagnostics = runtime.graph_facts(artifact, capabilities)

    assert facts == expected
    assert long_call in facts
    assert all(fact.contributing_plugin_ids == ("javascript",) for fact in facts)
    assert diagnostics == ()


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

    from codecrow_plugins.scope import rebase_fact

    rebased = rebase_fact(fact, "services/orders")

    assert rebased.path == "services/orders/routes.py"
    assert rebased.related_paths == ("services/orders/handlers.py",)
    assert rebased.contributing_plugin_ids == ("django",)


def test_malformed_graph_contribution_keeps_other_plugin_evidence():
    valid = _fact("valid", "source")
    runtime, capabilities = _runtime({
        "broken": (object(),),
        "working": (valid,),
    })
    facts, diagnostics = runtime.graph_facts(
        FileArtifact("src/example.py", "pass"), capabilities,
    )
    assert facts == (valid,)
    assert [(item.code, item.plugin_id, item.recoverable) for item in diagnostics] == [
        ("plugin-index-invalid-result", "broken", True),
    ]
