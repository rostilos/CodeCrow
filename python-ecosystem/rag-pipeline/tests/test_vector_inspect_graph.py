from types import SimpleNamespace

from rag_pipeline.api.routers.inspect import (
    _architecture_lookup_paths,
    _build_graph,
    _dependency_neighbor_filters,
    _relation_lookup_names,
    _to_graph_node,
)
from rag_pipeline.api.models import VectorInspectFilters


def _node(
    node_id,
    title,
    *,
    primary=None,
    semantic=None,
    branch="main",
    path=None,
    kind="class",
    metadata=None,
    namespace=None,
    full_path=None,
):
    return {
        "id": node_id,
        "title": title,
        "kind": kind,
        "group": branch,
        "branch": branch,
        "path": path or f"src/{title}.java",
        "language": "java",
        "primaryName": primary or title,
        "semanticNames": semantic or [primary or title],
        "namespace": namespace,
        "fullPath": full_path,
        "metadata": metadata or {},
        "virtual": False,
    }


def _edge_kinds(edges):
    return {edge["kind"] for edge in edges}


def test_build_graph_emits_typed_dependency_edges():
    source = _node(
        "controller",
        "Controller",
        metadata={
            "imports": ["org.example.Service"],
            "calls": ["run"],
            "referenced_types": ["Service"],
            "extends": ["BaseController"],
            "implements": ["Closeable"],
        },
    )
    service = _node(
        "service",
        "Service",
        namespace="org.example",
        metadata={"methods": ["run"]},
    )
    base = _node("base", "BaseController")
    iface = _node("closeable", "Closeable", kind="interface")

    _, edges = _build_graph(
        [source, service, base, iface],
        max_edges=80,
        max_virtual_nodes=20,
    )

    assert {"imports", "calls", "referenced_type", "extends", "implements"} <= _edge_kinds(edges)
    assert any(edge["source"] == "controller" and edge["target"] == "service" and edge["kind"] == "imports" for edge in edges)
    assert any(edge["source"] == "controller" and edge["target"] == "service" and edge["kind"] == "calls" for edge in edges)
    assert any(edge["source"] == "controller" and edge["target"] == "base" and edge["kind"] == "extends" for edge in edges)
    assert any(edge["source"] == "controller" and edge["target"] == "closeable" and edge["kind"] == "implements" for edge in edges)


def test_build_graph_adds_bounded_external_relation_nodes_for_unmatched_imports():
    source = _node(
        "source",
        "Source",
        metadata={
            "imports": ["java.util.concurrent.CompletableFuture"],
            "referenced_types": ["MissingType"],
            "calls": ["missingMethod"],
        },
    )

    nodes, edges = _build_graph([source], max_edges=20, max_virtual_nodes=10)

    virtual_nodes = [node for node in nodes if node.get("virtual")]
    assert any(node["kind"] == "import" and node["title"] == "CompletableFuture" for node in virtual_nodes)
    assert any(node["kind"] == "external_type" and node["title"] == "MissingType" for node in virtual_nodes)
    assert "calls" not in _edge_kinds(edges)
    assert {"imports", "referenced_type"} <= _edge_kinds(edges)


def test_build_graph_skips_high_fanout_common_relation_matches():
    source = _node("source", "Source", metadata={"calls": ["render"]})
    targets = [
        _node(f"target-{index}", f"Target{index}", metadata={"methods": ["render"]})
        for index in range(48)
    ]

    _, edges = _build_graph([source, *targets], max_edges=120, max_virtual_nodes=20)

    assert not [
        edge
        for edge in edges
        if edge["source"] == "source" and edge["kind"] == "calls"
    ]


def test_relation_lookup_names_collects_dependency_tokens_by_branch():
    source = _node(
        "source",
        "Source",
        metadata={
            "imports": ["org.example.Service"],
            "calls": ["run"],
            "referenced_types": ["Worker"],
        },
    )

    names = _relation_lookup_names([source], max_names=20)

    assert "main" in names
    assert {"org.example.Service", "Service", "run", "Worker"} <= set(names["main"])


def test_dependency_neighbor_filters_preserve_language_and_pr_scope():
    source = _node(
        "source",
        "Source",
        metadata={"calls": ["run"]},
    )
    filters = VectorInspectFilters(
        branches=["main"],
        languages=["php"],
        pr_number=42,
        include_pr=False,
    )

    neighbor_filters = list(_dependency_neighbor_filters([source], filters))

    assert neighbor_filters
    for neighbor_filter in neighbor_filters:
        conditions = {condition.key: condition for condition in neighbor_filter.must}
        assert conditions["branch"].match.value == "main"
        assert conditions["language"].match.value == "php"
        assert conditions["pr_number"].match.value == 42
        assert any(
            condition.key == "pr" and condition.match.value is True
            for condition in neighbor_filter.must_not
        )


def test_relation_lookup_names_and_paths_include_plugin_fact_boundaries():
    architecture = _node(
        "architecture",
        "magento: magento-di",
        kind="architecture_context",
        path="__analysis_architecture__/magento/context",
        metadata={
            "architecture_source_path": "app/code/Acme/etc/di.xml",
            "architecture_paths": ["app/code/Acme/Api/Contract.php"],
            "plugin_graph_facts": [{
                "source": "Acme\\Api\\Contract",
                "relation": "resolves-to",
                "target": "Acme\\Model\\Implementation",
                "path": "app/code/Acme/etc/di.xml",
                "related_paths": ["app/code/Acme/Model/Implementation.php"],
            }],
        },
    )

    names = _relation_lookup_names([architecture], max_names=20)
    paths = _architecture_lookup_paths([architecture], max_paths=20)

    assert {"Acme\\Api\\Contract", "Contract", "Acme\\Model\\Implementation", "Implementation"} <= set(names["main"])
    assert paths["main"] == [
        "app/code/Acme/etc/di.xml",
        "app/code/Acme/Api/Contract.php",
        "app/code/Acme/Model/Implementation.php",
    ]


def test_build_graph_connects_architecture_facts_to_code_and_evidence_files():
    evidence_path = "app/code/Acme/etc/di.xml"
    architecture = _node(
        "architecture",
        "magento: magento-di",
        kind="architecture_context",
        path="__analysis_architecture__/magento/context",
        metadata={
            "architecture_plugin": "magento",
            "architecture_kind": "magento-di",
            "architecture_source_path": evidence_path,
            "architecture_paths": [
                evidence_path,
                "app/code/Acme/Api/Contract.php",
                "app/code/Acme/Model/Implementation.php",
            ],
            "plugin_graph_facts": [{
                "kind": "magento-preference",
                "source": "Acme\\Api\\Contract",
                "relation": "resolves-to",
                "target": "Acme\\Model\\Implementation",
                "path": evidence_path,
                "line": 7,
                "related_paths": [
                    "app/code/Acme/Api/Contract.php",
                    "app/code/Acme/Model/Implementation.php",
                ],
            }],
        },
    )
    contract = _node(
        "contract",
        "Contract",
        primary="Contract",
        semantic=["Acme\\Api\\Contract"],
        kind="interface",
        path="app/code/Acme/Api/Contract.php",
    )
    implementation = _node(
        "implementation",
        "Implementation",
        primary="Implementation",
        semantic=["Acme\\Model\\Implementation"],
        path="app/code/Acme/Model/Implementation.php",
    )

    nodes, edges = _build_graph(
        [architecture, contract, implementation],
        max_edges=80,
        max_virtual_nodes=20,
    )

    metadata_edges = [edge for edge in edges if edge["kind"] == "metadata_reference"]
    assert any(edge["source"] == "architecture" and edge["target"] == "contract" for edge in metadata_edges)
    assert any(edge["source"] == "architecture" and edge["target"] == "implementation" for edge in metadata_edges)
    evidence_node = next(
        node for node in nodes
        if node.get("virtual") and node.get("path") == evidence_path
    )
    assert any(
        edge["source"] == "architecture" and edge["target"] == evidence_node["id"]
        for edge in metadata_edges
    )
    assert any("resolves-to" in token for edge in metadata_edges for token in edge.get("tokens", []))


def test_build_graph_does_not_duplicate_plugin_fact_edges_from_semantic_chunks():
    semantic_chunk = _node(
        "semantic-chunk",
        "Consumer",
        metadata={
            "plugin_graph_facts": [{
                "source": "Consumer",
                "relation": "uses",
                "target": "Service",
                "path": "src/Consumer.java",
                "related_paths": ["src/Service.java"],
            }],
        },
    )
    service = _node("service", "Service", path="src/Service.java")

    _, edges = _build_graph(
        [semantic_chunk, service],
        max_edges=40,
        max_virtual_nodes=10,
    )

    assert "metadata_reference" not in _edge_kinds(edges)


def test_architecture_points_are_labeled_and_expose_invalidation_metadata():
    point = SimpleNamespace(
        id="point-id",
        payload={
            "branch": "main",
            "pr": True,
            "pr_number": 42,
            "path": "__analysis_architecture__/magento/hash.context",
            "architecture_context": True,
            "architecture_plugin": "magento",
            "architecture_kind": "magento-di",
            "architecture_source_path": "app/etc/di.xml",
            "architecture_group": "group-id",
            "architecture_paths": [
                "app/etc/di.xml",
                "app/code/Acme/Model/Cart.php",
            ],
            "plugin_graph_facts": [{"relation": "resolves-to"}],
            "text": "Deterministic repository architecture context",
        },
    )

    node = _to_graph_node(point, detail=True)
    graph_node = _to_graph_node(point, detail=False)

    assert node["kind"] == "architecture_context"
    assert node["title"].startswith("magento: magento-di")
    assert node["metadata"]["architecture_group"] == "group-id"
    assert node["metadata"]["architecture_paths"] == [
        "app/etc/di.xml",
        "app/code/Acme/Model/Cart.php",
    ]
    assert graph_node["metadata"]["plugin_graph_facts"] == [{"relation": "resolves-to"}]
