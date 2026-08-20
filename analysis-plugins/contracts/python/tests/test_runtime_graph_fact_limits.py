from __future__ import annotations

import json
from types import SimpleNamespace

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


def _serialized_facts_bytes(facts: tuple[GraphFact, ...]) -> int:
    return len(json.dumps(
        [dict(fact.as_metadata()) for fact in facts],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8"))


def test_graph_facts_reject_every_overlong_string_location_without_truncating():
    limit = PluginRuntime.MAX_GRAPH_FACT_STRING_LENGTH
    overlong = "x" * (limit + 1)
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
    runtime, capabilities = _runtime({"bounded": (valid, *invalid)})

    facts, diagnostics = runtime.graph_facts(
        FileArtifact("src/example.py", "pass"),
        capabilities,
    )

    assert facts == (valid,)
    assert len(diagnostics) == 1
    assert diagnostics[0].code == "plugin-index-output-limit"
    assert diagnostics[0].plugin_id == "bounded"
    assert diagnostics[0].path == "src/example.py"
    assert diagnostics[0].recoverable is True
    assert "8 fact(s)" in diagnostics[0].message
    assert str(limit) in diagnostics[0].message


def test_graph_fact_byte_budget_is_global_deterministic_and_per_artifact():
    alpha = _fact("alpha", "alpha", target="α" * 32)
    beta = _fact("beta", "beta", target="β" * 32)
    runtime, capabilities = _runtime({
        "first": (alpha,),
        "second": (beta, beta),
    })
    runtime.MAX_GRAPH_FACT_BYTES_PER_ARTIFACT = _serialized_facts_bytes((alpha,))

    first_facts, first_diagnostics = runtime.graph_facts(
        FileArtifact("src/first.py", "pass"),
        capabilities,
    )
    second_facts, second_diagnostics = runtime.graph_facts(
        FileArtifact("src/second.py", "pass"),
        capabilities,
    )

    assert first_facts == second_facts == (alpha,)
    assert _serialized_facts_bytes(first_facts) <= (
        runtime.MAX_GRAPH_FACT_BYTES_PER_ARTIFACT
    )
    assert [diagnostic.plugin_id for diagnostic in first_diagnostics] == ["second"]
    assert [diagnostic.path for diagnostic in first_diagnostics] == ["src/first.py"]
    assert [diagnostic.path for diagnostic in second_diagnostics] == ["src/second.py"]
    assert all(diagnostic.recoverable for diagnostic in first_diagnostics)


def test_graph_fact_byte_budget_preserves_balanced_kind_selection():
    facts = (
        _fact("kind-a", "a-1", target="x" * 32),
        _fact("kind-a", "a-0", target="x" * 32),
        _fact("kind-b", "b-1", target="x" * 32),
        _fact("kind-b", "b-0", target="x" * 32),
    )
    expected = tuple(sorted((facts[1], facts[3])))
    runtime, capabilities = _runtime({"balanced": tuple(reversed(facts))})
    runtime.MAX_GRAPH_FACT_BYTES_PER_ARTIFACT = _serialized_facts_bytes(expected)

    selected, diagnostics = runtime.graph_facts(
        FileArtifact("src/example.py", "pass"),
        capabilities,
    )

    assert selected == expected
    assert {fact.kind for fact in selected} == {"kind-a", "kind-b"}
    assert len(diagnostics) == 1
    assert diagnostics[0].plugin_id == "balanced"
    assert "2 fact(s)" in diagnostics[0].message
