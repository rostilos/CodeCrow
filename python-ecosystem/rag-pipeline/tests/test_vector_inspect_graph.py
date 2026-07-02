from rag_pipeline.api.routers.inspect import _build_graph, _relation_lookup_names


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
