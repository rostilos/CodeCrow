from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from llama_index.core.schema import TextNode
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from codecrow_plugins import FileArtifact, PluginRuntime, ProjectSelector, build_repository_facts
from codecrow_plugins.bootstrap import discover_builtin_plugins
from rag_pipeline.core.index_manager.collection_manager import CollectionManager
from rag_pipeline.core.index_manager.indexer import FileOperations, RepositoryIndexer
from rag_pipeline.core.index_manager.point_operations import PointOperations
from rag_pipeline.core.loader import DocumentLoader
from rag_pipeline.core.repository_overlay import (
    IncrementalIndexPreconditionError,
    load_repository_facts,
    load_repository_snapshots,
    scroll_branch_points,
)


class _NoSemanticEmbedding:
    def get_text_embedding_batch(self, _texts):
        raise AssertionError("architecture-only Magento changes must not request embeddings")


class _StaticEmbedding:
    def get_text_embedding_batch(self, texts):
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


def _write_repository(
    root: Path,
    implementation: str,
    service_dependency: str = "Acme\\Checkout\\Api\\CartInterface",
    proxy_type: str | None = None,
) -> None:
    proxy_configuration = (
        f"""
              <type name="Acme\\Checkout\\Model\\Service">
                <arguments>
                  <argument name="cart" xsi:type="object">
                    {proxy_type}
                  </argument>
                </arguments>
              </type>
        """
        if proxy_type is not None
        else ""
    )
    files = {
        "composer.json": """{
            "name": "acme/magento-project",
            "require": {"magento/framework": "*"}
        }""",
        "app/etc/config.php": "<?php return ['modules' => ['Acme_Checkout' => 1]];",
        "app/code/Acme/Checkout/registration.php": """<?php
            use Magento\\Framework\\Component\\ComponentRegistrar;
            ComponentRegistrar::register(
                ComponentRegistrar::MODULE,
                'Acme_Checkout',
                __DIR__
            );
        """,
        "app/code/Acme/Checkout/etc/module.xml": """
            <config><module name="Acme_Checkout" /></config>
        """,
        "app/code/Acme/Checkout/etc/di.xml": f"""
            <config xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
              <preference for="Acme\\Checkout\\Api\\CartInterface"
                          type="{implementation}" />
              {proxy_configuration}
            </config>
        """,
        "app/code/Acme/Checkout/Api/CartInterface.php": """<?php
            namespace Acme\\Checkout\\Api;
            interface CartInterface { public function save(); }
        """,
        "app/code/Acme/Checkout/Model/Cart.php": """<?php
            namespace Acme\\Checkout\\Model;
            class Cart implements \\Acme\\Checkout\\Api\\CartInterface {
                public function save() {}
            }
        """,
        "app/code/Acme/Checkout/Model/AlternativeCart.php": """<?php
            namespace Acme\\Checkout\\Model;
            class AlternativeCart implements \\Acme\\Checkout\\Api\\CartInterface {
                public function save() {}
            }
        """,
        "app/code/Acme/Checkout/Model/Service.php": """<?php
            namespace Acme\\Checkout\\Model;
            class Service {
                public function __construct(
                    \\"""
        + service_dependency
        + """ $dependency
                ) {}
            }
        """,
    }
    for relative_path, content in files.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _analyze_repository(root: Path, revision: str):
    catalog = discover_builtin_plugins()
    runtime = PluginRuntime(catalog)
    facts = _repository_facts(root, revision, catalog)
    capabilities = ProjectSelector(catalog.registry).select(
        facts
    )
    handle = runtime.start_repository_analysis(capabilities, revision)
    handle.ingest(tuple(
        FileArtifact(path.as_posix(), (root / path).read_text(encoding="utf-8"))
        for path in (Path(value) for value in facts.paths)
    ))
    analysis, diagnostics = handle.finish()
    assert diagnostics == ()
    assert "magento" in capabilities.repository_plugins
    return catalog, runtime, capabilities, analysis


def _repository_facts(root: Path, revision: str, catalog):
    paths = tuple(sorted(
        path.relative_to(root) for path in root.rglob("*") if path.is_file()
    ))
    return build_repository_facts(root, revision, paths, catalog.registry)


def test_incremental_di_change_restores_snapshot_and_replaces_effective_graph(tmp_path):
    original = "Acme\\Checkout\\Model\\Cart"
    replacement = "Acme\\Checkout\\Model\\AlternativeCart"
    _write_repository(tmp_path, original)
    catalog, runtime, capabilities, analysis = _analyze_repository(tmp_path, "base")

    client = QdrantClient(":memory:")
    collection = "repository"
    client.create_collection(
        collection_name=collection,
        vectors_config=VectorParams(size=4, distance=Distance.COSINE),
    )
    point_ops = PointOperations(
        client,
        _NoSemanticEmbedding(),
        batch_size=50,
        embedding_dim=4,
    )
    implementation_fingerprint = catalog.implementation_fingerprint(
        capabilities.repository_plugins
    )
    initial_nodes = [
        *RepositoryIndexer._architecture_nodes(
            analysis,
            capabilities,
            "ws",
            "project",
            "main",
            "base",
            implementation_fingerprint,
        ),
        *RepositoryIndexer._repository_context_nodes(
            analysis,
            capabilities,
            "ws",
            "project",
            "main",
            "base",
            implementation_fingerprint,
        ),
        *RepositoryIndexer._snapshot_nodes(
            analysis,
            capabilities,
            "ws",
            "project",
            "main",
            "base",
            implementation_fingerprint,
        ),
        *RepositoryIndexer._repository_facts_nodes(
            _repository_facts(tmp_path, "base", catalog),
            capabilities,
            "ws",
            "project",
            "main",
            "base",
            implementation_fingerprint,
        ),
    ]
    successful, failed = point_ops.process_and_upsert_chunks(
        initial_nodes, collection, "ws", "project", "main"
    )
    assert successful == len(initial_nodes)
    assert failed == 0

    _write_repository(tmp_path, replacement)
    loader = DocumentLoader(SimpleNamespace(
        excluded_patterns=(),
        max_file_size_bytes=1_000_000,
    ))
    splitter = MagicMock()
    stats = MagicMock()
    operations = FileOperations(
        client,
        point_ops,
        CollectionManager(client, 4),
        stats,
        splitter,
        loader,
        plugin_catalog=catalog,
        plugin_runtime=runtime,
        plugin_selector=ProjectSelector(catalog.registry),
    )
    di_path = "app/code/Acme/Checkout/etc/di.xml"

    operations.update_files(
        [di_path],
        str(tmp_path),
        "ws",
        "project",
        "main",
        "changed",
        collection,
    )

    splitter.split_documents.assert_not_called()
    (
        snapshots,
        plugin_ids,
        fingerprint,
        descriptor_fingerprint,
        stored_implementation_fingerprint,
    ) = load_repository_snapshots(
        client, collection, "main"
    )
    assert snapshots
    assert plugin_ids == capabilities.repository_plugins
    assert fingerprint != capabilities.fingerprint
    assert descriptor_fingerprint == capabilities.descriptor_fingerprint
    assert stored_implementation_fingerprint == implementation_fingerprint

    architecture_points = scroll_branch_points(client, collection, "main")
    facts = [
        fact
        for point in architecture_points
        for fact in (point.payload or {}).get("plugin_graph_facts", ())
        if fact.get("source") == "Acme\\Checkout\\Api\\CartInterface"
        and fact.get("relation") == "resolves-to"
    ]
    assert any(fact["target"] == replacement for fact in facts)
    assert not any(fact["target"] == original for fact in facts)

    operations.delete_files(
        [di_path],
        "ws",
        "project",
        "main",
        collection,
        commit="deleted",
    )
    after_delete = scroll_branch_points(client, collection, "main")
    deleted_facts = [
        fact
        for point in after_delete
        for fact in (point.payload or {}).get("plugin_graph_facts", ())
        if fact.get("source") == "Acme\\Checkout\\Api\\CartInterface"
        and fact.get("relation") == "resolves-to"
    ]
    assert deleted_facts == []


def test_incremental_php_change_adds_and_removes_generated_factory_graph(tmp_path):
    implementation = "Acme\\Checkout\\Model\\Cart"
    factory_type = "Acme\\Checkout\\Model\\CartFactory"
    service_path = "app/code/Acme/Checkout/Model/Service.php"
    _write_repository(tmp_path, implementation)
    catalog, runtime, capabilities, analysis = _analyze_repository(tmp_path, "base")

    client = QdrantClient(":memory:")
    collection = "repository"
    client.create_collection(
        collection_name=collection,
        vectors_config=VectorParams(size=4, distance=Distance.COSINE),
    )
    point_ops = PointOperations(
        client,
        _NoSemanticEmbedding(),
        batch_size=50,
        embedding_dim=4,
    )
    implementation_fingerprint = catalog.implementation_fingerprint(
        capabilities.repository_plugins
    )
    initial_nodes = [
        *RepositoryIndexer._architecture_nodes(
            analysis,
            capabilities,
            "ws",
            "project",
            "main",
            "base",
            implementation_fingerprint,
        ),
        *RepositoryIndexer._repository_context_nodes(
            analysis,
            capabilities,
            "ws",
            "project",
            "main",
            "base",
            implementation_fingerprint,
        ),
        *RepositoryIndexer._snapshot_nodes(
            analysis,
            capabilities,
            "ws",
            "project",
            "main",
            "base",
            implementation_fingerprint,
        ),
        *RepositoryIndexer._repository_facts_nodes(
            _repository_facts(tmp_path, "base", catalog),
            capabilities,
            "ws",
            "project",
            "main",
            "base",
            implementation_fingerprint,
        ),
    ]
    successful, failed = point_ops.process_and_upsert_chunks(
        initial_nodes, collection, "ws", "project", "main"
    )
    assert successful == len(initial_nodes)
    assert failed == 0

    def factory_facts():
        return [
            fact
            for point in scroll_branch_points(client, collection, "main")
            for fact in (point.payload or {}).get("plugin_graph_facts", ())
            if fact.get("kind", "").startswith("magento-generated-factory")
        ]

    assert factory_facts() == []

    loader = DocumentLoader(SimpleNamespace(
        excluded_patterns=(),
        max_file_size_bytes=1_000_000,
    ))
    splitter = MagicMock()
    splitter.split_documents.return_value = []
    operations = FileOperations(
        client,
        point_ops,
        CollectionManager(client, 4),
        MagicMock(),
        splitter,
        loader,
        plugin_catalog=catalog,
        plugin_runtime=runtime,
        plugin_selector=ProjectSelector(catalog.registry),
    )

    _write_repository(tmp_path, implementation, factory_type)
    operations.update_files(
        [service_path],
        str(tmp_path),
        "ws",
        "project",
        "main",
        "factory-added",
        collection,
    )

    added = {
        (
            fact["kind"],
            fact["source"],
            fact["relation"],
            fact["target"],
        )
        for fact in factory_facts()
    }
    assert added == {
        (
            "magento-generated-factory",
            "Acme\\Checkout\\Model\\Service",
            "uses-generated-factory-for",
            "Acme\\Checkout\\Model\\Cart",
        ),
        (
            "magento-generated-factory-resolution",
            "Acme\\Checkout\\Model\\Service",
            "creates-via-generated-factory",
            "Acme\\Checkout\\Model\\Cart",
        ),
    }

    _write_repository(tmp_path, implementation)
    operations.update_files(
        [service_path],
        str(tmp_path),
        "ws",
        "project",
        "main",
        "factory-removed",
        collection,
    )

    assert factory_facts() == []
    assert splitter.split_documents.call_count == 2


def test_incremental_di_change_adds_and_removes_generated_proxy_graph(tmp_path):
    implementation = "Acme\\Checkout\\Model\\Cart"
    proxy_type = "Acme\\Checkout\\Model\\Cart\\Proxy"
    di_path = "app/code/Acme/Checkout/etc/di.xml"
    _write_repository(tmp_path, implementation)
    catalog, runtime, capabilities, analysis = _analyze_repository(tmp_path, "base")

    client = QdrantClient(":memory:")
    collection = "repository"
    client.create_collection(
        collection_name=collection,
        vectors_config=VectorParams(size=4, distance=Distance.COSINE),
    )
    point_ops = PointOperations(
        client,
        _NoSemanticEmbedding(),
        batch_size=50,
        embedding_dim=4,
    )
    implementation_fingerprint = catalog.implementation_fingerprint(
        capabilities.repository_plugins
    )
    initial_nodes = [
        *RepositoryIndexer._architecture_nodes(
            analysis,
            capabilities,
            "ws",
            "project",
            "main",
            "base",
            implementation_fingerprint,
        ),
        *RepositoryIndexer._repository_context_nodes(
            analysis,
            capabilities,
            "ws",
            "project",
            "main",
            "base",
            implementation_fingerprint,
        ),
        *RepositoryIndexer._snapshot_nodes(
            analysis,
            capabilities,
            "ws",
            "project",
            "main",
            "base",
            implementation_fingerprint,
        ),
        *RepositoryIndexer._repository_facts_nodes(
            _repository_facts(tmp_path, "base", catalog),
            capabilities,
            "ws",
            "project",
            "main",
            "base",
            implementation_fingerprint,
        ),
    ]
    successful, failed = point_ops.process_and_upsert_chunks(
        initial_nodes, collection, "ws", "project", "main"
    )
    assert successful == len(initial_nodes)
    assert failed == 0

    def proxy_facts():
        return [
            fact
            for point in scroll_branch_points(client, collection, "main")
            for fact in (point.payload or {}).get("plugin_graph_facts", ())
            if fact.get("kind", "").startswith("magento-generated-proxy")
        ]

    assert proxy_facts() == []

    loader = DocumentLoader(SimpleNamespace(
        excluded_patterns=(),
        max_file_size_bytes=1_000_000,
    ))
    splitter = MagicMock()
    operations = FileOperations(
        client,
        point_ops,
        CollectionManager(client, 4),
        MagicMock(),
        splitter,
        loader,
        plugin_catalog=catalog,
        plugin_runtime=runtime,
        plugin_selector=ProjectSelector(catalog.registry),
    )

    _write_repository(tmp_path, implementation, proxy_type=proxy_type)
    operations.update_files(
        [di_path],
        str(tmp_path),
        "ws",
        "project",
        "main",
        "proxy-added",
        collection,
    )

    added = {
        (
            fact["kind"],
            fact["source"],
            fact["relation"],
            fact["target"],
        )
        for fact in proxy_facts()
    }
    assert added == {
        (
            "magento-generated-proxy",
            "Acme\\Checkout\\Model\\Service",
            "injects-generated-proxy-for",
            "Acme\\Checkout\\Model\\Cart",
        ),
        (
            "magento-generated-proxy-resolution",
            "Acme\\Checkout\\Model\\Service",
            "lazy-loads-via-generated-proxy",
            "Acme\\Checkout\\Model\\Cart",
        ),
    }

    _write_repository(tmp_path, implementation)
    operations.update_files(
        [di_path],
        str(tmp_path),
        "ws",
        "project",
        "main",
        "proxy-removed",
        collection,
    )

    assert proxy_facts() == []
    splitter.split_documents.assert_not_called()


def test_snapshot_loader_recovers_capabilities_from_semantic_points():
    client = MagicMock()
    client.scroll.side_effect = [
        ([], None),
        ([], None),
        ([
            SimpleNamespace(payload={
                "branch": "main",
                "plugin_ids": ["java", "spring"],
                "plugin_fingerprint": "sha256:abc",
                "plugin_descriptor_fingerprint": "sha256:" + "1" * 64,
                "plugin_implementation_fingerprint": "sha256:" + "2" * 64,
            }),
        ], None),
    ]

    snapshots, plugin_ids, fingerprint, descriptor_fingerprint, implementation_fingerprint = load_repository_snapshots(
        client,
        "repository",
        "main",
    )

    assert snapshots == ()
    assert plugin_ids == ("java", "spring")
    assert fingerprint == "sha256:abc"
    assert descriptor_fingerprint == "sha256:" + "1" * 64
    assert implementation_fingerprint == "sha256:" + "2" * 64


def test_incremental_update_rejects_missing_repository_analysis_snapshots(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        "rag_pipeline.core.index_manager.indexer."
        "require_compatible_branch_representation",
        lambda *_args, **_kwargs: None,
    )
    _write_repository(tmp_path, "Acme\\Checkout\\Model\\Cart")
    catalog, runtime, capabilities, _analysis = _analyze_repository(
        tmp_path,
        "base",
    )
    implementation_fingerprint = catalog.implementation_fingerprint(
        capabilities.repository_plugins
    )

    from rag_pipeline.core import repository_overlay

    monkeypatch.setattr(
        repository_overlay,
        "load_repository_facts",
        lambda *_args: (
            _repository_facts(tmp_path, "base", catalog),
            capabilities.repository_plugins,
            capabilities.fingerprint,
            capabilities.descriptor_fingerprint,
            implementation_fingerprint,
        ),
    )
    monkeypatch.setattr(
        repository_overlay,
        "load_repository_snapshots",
        lambda *_args: (
            (),
            capabilities.repository_plugins,
            capabilities.fingerprint,
            capabilities.descriptor_fingerprint,
            implementation_fingerprint,
        ),
    )
    point_ops = MagicMock()
    operations = FileOperations(
        MagicMock(),
        point_ops,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        plugin_catalog=catalog,
        plugin_runtime=runtime,
        plugin_selector=ProjectSelector(catalog.registry),
    )

    with pytest.raises(
        RuntimeError,
        match="missing repository-analysis snapshots for magento, php",
    ):
        operations.delete_files(
            ["app/code/Acme/Checkout/etc/di.xml"],
            "ws",
            "project",
            "main",
            "repository",
            commit="changed",
        )

    point_ops.prepare_chunks_for_embedding.assert_not_called()


def test_generic_change_set_updates_and_deletes_through_one_generation(tmp_path):
    catalog = discover_builtin_plugins()
    runtime = PluginRuntime(catalog)
    selector = ProjectSelector(catalog.registry)
    _write = lambda path, content: (
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True),
        (tmp_path / path).write_text(content, encoding="utf-8"),
    )
    _write("docs/a.md", "old a")
    _write("docs/b.md", "old b")
    facts = build_repository_facts(
        tmp_path,
        "base",
        ("docs/a.md", "docs/b.md"),
        catalog.registry,
    )
    capabilities = selector.select(facts)
    implementation_fingerprint = catalog.implementation_fingerprint(
        capabilities.repository_plugins
    )

    client = QdrantClient(":memory:")
    collection = "repository"
    client.create_collection(
        collection_name=collection,
        vectors_config=VectorParams(size=4, distance=Distance.COSINE),
    )
    point_ops = PointOperations(
        client,
        _StaticEmbedding(),
        batch_size=50,
        embedding_dim=4,
    )
    initial_nodes = [
        TextNode(
            text="old a",
            metadata={
                "path": "docs/a.md",
                "branch": "main",
                "commit": "base",
            },
        ),
        TextNode(
            text="old b",
            metadata={
                "path": "docs/b.md",
                "branch": "main",
                "commit": "base",
            },
        ),
        *RepositoryIndexer._repository_facts_nodes(
            facts,
            capabilities,
            "ws",
            "project",
            "main",
            "base",
            implementation_fingerprint,
        ),
    ]
    for node in initial_nodes[:2]:
        node.metadata.update(initial_nodes[-1].metadata | {
            "path": node.metadata["path"],
            "language": "markdown",
            "filetype": "md",
        })
        node.metadata.pop("repository_facts_state", None)
        node.metadata.pop("facts_part", None)
        node.metadata.pop("facts_parts", None)
        node.metadata.pop("facts_content_sha256", None)
    successful, failed = point_ops.process_and_upsert_chunks(
        initial_nodes,
        collection,
        "ws",
        "project",
        "main",
    )
    assert successful == len(initial_nodes)
    assert failed == 0

    _write("docs/a.md", "new a")
    (tmp_path / "docs/b.md").unlink()
    splitter = MagicMock()
    splitter.split_documents.side_effect = lambda documents, capabilities: [
        TextNode(
            text=document.text,
            metadata=dict(document.metadata),
        )
        for document in documents
    ]
    operations = FileOperations(
        client,
        point_ops,
        CollectionManager(client, 4),
        MagicMock(),
        splitter,
        DocumentLoader(SimpleNamespace(
            excluded_patterns=(),
            max_file_size_bytes=1_000_000,
        )),
        plugin_catalog=catalog,
        plugin_runtime=runtime,
        plugin_selector=selector,
    )

    operations.apply_changes(
        ["docs/a.md"],
        ["docs/b.md"],
        str(tmp_path),
        "ws",
        "project",
        "main",
        "changed",
        collection,
    )

    points = scroll_branch_points(client, collection, "main")
    semantic = {
        point.payload["path"]: point.payload["text"]
        for point in points
        if not (point.payload or {}).get("repository_facts_state")
    }
    assert semantic == {"docs/a.md": "new a"}
    stored_facts, plugin_ids, *_identity = load_repository_facts(
        client,
        collection,
        "main",
    )
    assert stored_facts.revision == "changed"
    assert stored_facts.paths == ("docs/a.md",)
    assert plugin_ids == ()


def test_plugin_selection_transition_fails_before_qdrant_mutation(tmp_path):
    catalog = discover_builtin_plugins()
    runtime = PluginRuntime(catalog)
    selector = ProjectSelector(catalog.registry)
    (tmp_path / "README.md").write_text("plain project", encoding="utf-8")
    facts = build_repository_facts(
        tmp_path,
        "base",
        ("README.md",),
        catalog.registry,
    )
    capabilities = selector.select(facts)
    implementation_fingerprint = catalog.implementation_fingerprint(
        capabilities.repository_plugins
    )
    client = QdrantClient(":memory:")
    collection = "repository"
    client.create_collection(
        collection_name=collection,
        vectors_config=VectorParams(size=4, distance=Distance.COSINE),
    )
    point_ops = PointOperations(
        client,
        _StaticEmbedding(),
        batch_size=50,
        embedding_dim=4,
    )
    state_nodes = RepositoryIndexer._repository_facts_nodes(
        facts,
        capabilities,
        "ws",
        "project",
        "main",
        "base",
        implementation_fingerprint,
    )
    point_ops.process_and_upsert_chunks(
        state_nodes,
        collection,
        "ws",
        "project",
        "main",
    )
    before = {
        str(point.id): dict(point.payload or {})
        for point in scroll_branch_points(client, collection, "main")
    }
    (tmp_path / "composer.json").write_text(
        '{"require":{"magento/framework":"*"}}',
        encoding="utf-8",
    )
    operations = FileOperations(
        client,
        point_ops,
        CollectionManager(client, 4),
        MagicMock(),
        MagicMock(),
        DocumentLoader(SimpleNamespace(
            excluded_patterns=(),
            max_file_size_bytes=1_000_000,
        )),
        plugin_catalog=catalog,
        plugin_runtime=runtime,
        plugin_selector=selector,
    )

    with pytest.raises(
        IncrementalIndexPreconditionError,
        match="alter repository plugin selection",
    ):
        operations.apply_changes(
            ["composer.json"],
            [],
            str(tmp_path),
            "ws",
            "project",
            "main",
            "changed",
            collection,
        )

    after = {
        str(point.id): dict(point.payload or {})
        for point in scroll_branch_points(client, collection, "main")
    }
    assert after == before
    point_ops.client.get_collection(collection)


def test_plugin_deactivation_from_mixed_update_and_delete_requires_full_reindex(
    tmp_path,
):
    catalog = discover_builtin_plugins()
    runtime = PluginRuntime(catalog)
    selector = ProjectSelector(catalog.registry)
    (tmp_path / "app.py").write_text(
        "from fastapi import FastAPI\n",
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text(
        "fastapi\n",
        encoding="utf-8",
    )
    facts = build_repository_facts(
        tmp_path,
        "base",
        ("app.py", "requirements.txt"),
        catalog.registry,
    )
    capabilities = selector.select(facts)
    assert "fastapi" in capabilities.repository_plugins

    client = QdrantClient(":memory:")
    collection = "repository"
    client.create_collection(
        collection_name=collection,
        vectors_config=VectorParams(size=4, distance=Distance.COSINE),
    )
    point_ops = PointOperations(
        client,
        _StaticEmbedding(),
        embedding_dim=4,
    )
    state_nodes = RepositoryIndexer._repository_facts_nodes(
        facts,
        capabilities,
        "ws",
        "project",
        "main",
        "base",
        catalog.implementation_fingerprint(capabilities.repository_plugins),
    )
    point_ops.process_and_upsert_chunks(
        state_nodes,
        collection,
        "ws",
        "project",
        "main",
    )
    before = {
        str(point.id): dict(point.payload or {})
        for point in scroll_branch_points(client, collection, "main")
    }

    (tmp_path / "app.py").write_text("print('plain')\n", encoding="utf-8")
    (tmp_path / "requirements.txt").unlink()
    operations = FileOperations(
        client,
        point_ops,
        CollectionManager(client, 4),
        MagicMock(),
        MagicMock(),
        DocumentLoader(SimpleNamespace(
            excluded_patterns=(),
            max_file_size_bytes=1_000_000,
        )),
        plugin_catalog=catalog,
        plugin_runtime=runtime,
        plugin_selector=selector,
    )

    with pytest.raises(
        IncrementalIndexPreconditionError,
        match="alter repository plugin selection",
    ):
        operations.apply_changes(
            ["app.py"],
            ["requirements.txt"],
            str(tmp_path),
            "ws",
            "project",
            "main",
            "changed",
            collection,
        )

    after = {
        str(point.id): dict(point.payload or {})
        for point in scroll_branch_points(client, collection, "main")
    }
    assert after == before


def test_corrupt_repository_facts_fail_closed_with_reindex_precondition(tmp_path):
    catalog = discover_builtin_plugins()
    selector = ProjectSelector(catalog.registry)
    facts = build_repository_facts(
        tmp_path,
        "base",
        (),
        catalog.registry,
    )
    capabilities = selector.select(facts)
    client = QdrantClient(":memory:")
    collection = "repository"
    client.create_collection(
        collection_name=collection,
        vectors_config=VectorParams(size=4, distance=Distance.COSINE),
    )
    point_ops = PointOperations(
        client,
        _StaticEmbedding(),
        embedding_dim=4,
    )
    nodes = RepositoryIndexer._repository_facts_nodes(
        facts,
        capabilities,
        "ws",
        "project",
        "main",
        "base",
        catalog.implementation_fingerprint(()),
    )
    point_ops.process_and_upsert_chunks(
        nodes,
        collection,
        "ws",
        "project",
        "main",
    )
    records = scroll_branch_points(client, collection, "main")
    client.set_payload(
        collection_name=collection,
        points=[records[0].id],
        payload={"text": "tampered"},
    )

    with pytest.raises(
        IncrementalIndexPreconditionError,
        match="integrity validation.*fully reindex",
    ):
        load_repository_facts(client, collection, "main")


def test_python_boundary_rejects_missing_changed_file_before_mutation(tmp_path):
    catalog = discover_builtin_plugins()
    selector = ProjectSelector(catalog.registry)
    facts = build_repository_facts(
        tmp_path,
        "base",
        (),
        catalog.registry,
    )
    capabilities = selector.select(facts)
    client = QdrantClient(":memory:")
    collection = "repository"
    client.create_collection(
        collection_name=collection,
        vectors_config=VectorParams(size=4, distance=Distance.COSINE),
    )
    point_ops = PointOperations(
        client,
        _StaticEmbedding(),
        embedding_dim=4,
    )
    nodes = RepositoryIndexer._repository_facts_nodes(
        facts,
        capabilities,
        "ws",
        "project",
        "main",
        "base",
        catalog.implementation_fingerprint(()),
    )
    point_ops.process_and_upsert_chunks(
        nodes,
        collection,
        "ws",
        "project",
        "main",
    )
    operations = FileOperations(
        client,
        point_ops,
        CollectionManager(client, 4),
        MagicMock(),
        MagicMock(),
        DocumentLoader(SimpleNamespace(
            excluded_patterns=(),
            max_file_size_bytes=1_000_000,
        )),
        plugin_catalog=catalog,
        plugin_runtime=PluginRuntime(catalog),
        plugin_selector=selector,
    )

    with pytest.raises(RuntimeError, match="missing from the pinned checkout"):
        operations.apply_changes(
            ["missing.py"],
            [],
            str(tmp_path),
            "ws",
            "project",
            "main",
            "changed",
            collection,
        )


def test_framework_change_set_updates_relation_and_deletes_related_source_together(
    tmp_path,
):
    original = "Acme\\Checkout\\Model\\Cart"
    replacement = "Acme\\Checkout\\Model\\AlternativeCart"
    deleted_path = "app/code/Acme/Checkout/Model/Service.php"
    di_path = "app/code/Acme/Checkout/etc/di.xml"
    _write_repository(tmp_path, original)
    catalog, runtime, capabilities, analysis = _analyze_repository(
        tmp_path,
        "base",
    )
    facts = _repository_facts(tmp_path, "base", catalog)
    implementation_fingerprint = catalog.implementation_fingerprint(
        capabilities.repository_plugins
    )
    client = QdrantClient(":memory:")
    collection = "repository"
    client.create_collection(
        collection_name=collection,
        vectors_config=VectorParams(size=4, distance=Distance.COSINE),
    )
    point_ops = PointOperations(
        client,
        _NoSemanticEmbedding(),
        embedding_dim=4,
    )
    nodes = [
        *RepositoryIndexer._architecture_nodes(
            analysis,
            capabilities,
            "ws",
            "project",
            "main",
            "base",
            implementation_fingerprint,
        ),
        *RepositoryIndexer._repository_context_nodes(
            analysis,
            capabilities,
            "ws",
            "project",
            "main",
            "base",
            implementation_fingerprint,
        ),
        *RepositoryIndexer._snapshot_nodes(
            analysis,
            capabilities,
            "ws",
            "project",
            "main",
            "base",
            implementation_fingerprint,
        ),
        *RepositoryIndexer._repository_facts_nodes(
            facts,
            capabilities,
            "ws",
            "project",
            "main",
            "base",
            implementation_fingerprint,
        ),
    ]
    point_ops.process_and_upsert_chunks(
        nodes,
        collection,
        "ws",
        "project",
        "main",
    )

    _write_repository(tmp_path, replacement)
    (tmp_path / deleted_path).unlink()
    splitter = MagicMock()
    operations = FileOperations(
        client,
        point_ops,
        CollectionManager(client, 4),
        MagicMock(),
        splitter,
        DocumentLoader(SimpleNamespace(
            excluded_patterns=(),
            max_file_size_bytes=1_000_000,
        )),
        plugin_catalog=catalog,
        plugin_runtime=runtime,
        plugin_selector=ProjectSelector(catalog.registry),
    )

    operations.apply_changes(
        [di_path],
        [deleted_path],
        str(tmp_path),
        "ws",
        "project",
        "main",
        "changed",
        collection,
    )

    graph_facts = [
        fact
        for point in scroll_branch_points(client, collection, "main")
        for fact in (point.payload or {}).get("plugin_graph_facts", ())
    ]
    preferences = [
        fact
        for fact in graph_facts
        if fact.get("source") == "Acme\\Checkout\\Api\\CartInterface"
        and fact.get("relation") == "resolves-to"
    ]
    assert {fact["target"] for fact in preferences} == {replacement}
    assert not any(fact.get("path") == deleted_path for fact in graph_facts)
    stored_facts, *_identity = load_repository_facts(
        client,
        collection,
        "main",
    )
    assert deleted_path not in stored_facts.paths
    splitter.split_documents.assert_not_called()
