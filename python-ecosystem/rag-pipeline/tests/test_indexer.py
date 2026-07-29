"""
Tests for rag_pipeline.core.index_manager.indexer — RepositoryIndexer + FileOperations.

Covers:
- estimate_repository_size (small repo, sampled large repo)
- index_repository (full flow, limits, atomic swap, errors)
- _perform_atomic_swap (normal, migration from direct collection)
- FileOperations.update_files
- FileOperations.delete_files
"""
import gc
import uuid
import pytest
from unittest.mock import patch, MagicMock, PropertyMock, call
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from rag_pipeline.core.index_manager.indexer import (
    RepositoryIndexer, FileOperations,
    DOCUMENT_BATCH_SIZE, INSERT_BATCH_SIZE,
)
from rag_pipeline.core.repository_overlay import (
    IncrementalIndexPreconditionError,
)
from rag_pipeline.models.config import IndexStats


@pytest.fixture(autouse=True)
def _verified_source_tree(monkeypatch):
    source = SimpleNamespace(
        commit="abc123",
        tree_sha256="c" * 64,
        git_commit_verified=False,
        file_sha256_by_path={},
    )
    monkeypatch.setattr(
        "rag_pipeline.core.index_manager.indexer.verify_repository_source_tree",
        lambda *_args, **_kwargs: source,
    )
    monkeypatch.setattr(
        "rag_pipeline.core.index_manager.indexer."
        "require_repository_source_tree_unchanged",
        lambda *_args, **_kwargs: None,
    )


def _mock_config(**overrides):
    cfg = MagicMock()
    cfg.max_files_per_index = 0
    cfg.max_chunks_per_index = 0
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _mock_components():
    """Return mocked sub-components for RepositoryIndexer."""
    coll_mgr = MagicMock()
    branch_mgr = MagicMock()
    point_ops = MagicMock()
    stats_mgr = MagicMock()
    splitter = MagicMock()
    loader = MagicMock()

    # Default: point_ops returns success
    point_ops.process_and_upsert_chunks.return_value = (1, 0)
    point_ops.write_repository_generation_manifest.return_value = {
        "generation_schema": "codecrow.repository-index-generation",
        "generation_member_count": 1,
        "generation_members_sha256": "a" * 64,
        "generation_manifest_sha256": "b" * 64,
    }
    point_ops.client = MagicMock()
    branch_mgr.get_branch_point_count.return_value = 2
    branch_mgr.stream_copy_points_to_collection.return_value = 0

    return coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader


# ─────────────────────────────────────────────────────────────
# estimate_repository_size
# ─────────────────────────────────────────────────────────────
class TestEstimateRepositorySize:

    def test_empty_repo(self):
        config = _mock_config()
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()
        loader.iter_repository_files.return_value = iter([])

        indexer = RepositoryIndexer(config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader)
        fc, cc = indexer.estimate_repository_size("/repo")
        assert fc == 0
        assert cc == 0

    def test_small_repo_exact_count(self):
        config = _mock_config()
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()

        fake_files = [f"file{i}.py" for i in range(10)]
        loader.iter_repository_files.return_value = iter(fake_files)

        # Simulate loader returning 2 docs per batch, splitter returning 3 chunks per doc batch
        from llama_index.core.schema import Document as LlamaDoc
        mock_docs = [MagicMock() for _ in range(2)]
        loader.load_file_batch.return_value = mock_docs
        mock_chunks = [MagicMock() for _ in range(3)]
        splitter.split_documents.return_value = mock_chunks

        indexer = RepositoryIndexer(config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader)
        fc, cc = indexer.estimate_repository_size("/repo")
        assert fc == 10
        assert cc > 0

    def test_large_repo_sampling(self):
        config = _mock_config()
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()

        # More than SAMPLE_SIZE=100 files
        fake_files = [f"file{i}.py" for i in range(200)]
        loader.iter_repository_files.return_value = iter(fake_files)

        loader.load_file_batch.return_value = [
            SimpleNamespace(text="a = 1", metadata={"path": "a.py"})
        ]
        splitter.split_documents.return_value = [MagicMock(), MagicMock()]

        indexer = RepositoryIndexer(config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader)
        fc, cc = indexer.estimate_repository_size("/repo")
        assert fc == 200
        assert cc > 0  # Estimated from sampling


# ─────────────────────────────────────────────────────────────
# index_repository
# ─────────────────────────────────────────────────────────────
class TestIndexRepository:

    def test_empty_repo_returns_stats(self):
        config = _mock_config()
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()
        loader.iter_repository_files.return_value = iter([])

        coll_mgr.create_pending_collection.return_value = "pending"
        coll_mgr.alias_exists.return_value = False
        coll_mgr.collection_exists.return_value = False
        coll_mgr.resolve_alias.return_value = None

        mock_stats = IndexStats(
            namespace="ws__proj__main", document_count=0, chunk_count=0,
            last_updated="2024-01-01", workspace="ws", project="proj", branch="main"
        )
        stats_mgr.get_branch_stats.return_value = mock_stats

        indexer = RepositoryIndexer(config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader)
        result = indexer.index_repository("/repo", "ws", "proj", "main", "abc123", "alias1")

        coll_mgr.delete_collection.assert_called_with("pending")
        assert result.document_count == 0

    def test_architecture_files_are_ingested_while_generated_files_are_not_loaded(self, tmp_path):
        from codecrow_plugins import FileDisposition, ProjectCapabilities, RepositoryAnalysis

        config = _mock_config()
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()
        paths = [
            Path("source.php"),
            Path("etc/di.xml"),
            Path("generated/code/Proxy.php"),
        ]
        loader.iter_repository_files.return_value = iter(paths)
        loader.load_file_batch.return_value = [
            SimpleNamespace(text="<?php class Source {}", metadata={"path": "source.php"}),
            SimpleNamespace(text="<config/>", metadata={"path": "etc/di.xml"}),
        ]
        splitter.split_documents.return_value = [MagicMock()]
        coll_mgr.create_pending_collection.return_value = "pending"
        coll_mgr.alias_exists.return_value = False
        coll_mgr.collection_exists.return_value = False
        coll_mgr.resolve_alias.return_value = None
        point_ops.client.get_collection.return_value = SimpleNamespace(points_count=3)
        point_ops.process_and_upsert_chunks.return_value = (1, 0)
        branch_mgr.get_branch_point_count.return_value = 3
        stats_mgr.store_metadata.return_value = None
        selector = MagicMock()
        selector.select.return_value = ProjectCapabilities(
            repository_plugins=("php", "magento"),
            file_plugins={}, detection_evidence={}, unavailable_capabilities=(),
            fingerprint="sha256:" + "0" * 64,
        )
        handle = MagicMock(active=True)
        handle.finish.return_value = (RepositoryAnalysis(), ())
        runtime = MagicMock()
        runtime.start_repository_analysis.return_value = handle
        runtime.file_disposition.side_effect = lambda path, _capabilities: {
            "source.php": FileDisposition.FULL,
            "etc/di.xml": FileDisposition.ARCHITECTURE_ONLY,
            "generated/code/Proxy.php": FileDisposition.GENERATED,
        }[path]
        indexer = RepositoryIndexer(
            config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader,
            plugin_catalog=MagicMock(), plugin_runtime=runtime, plugin_selector=selector,
        )

        indexer.index_repository(
            str(tmp_path), "ws", "proj", "main", "abc", "alias"
        )

        artifacts = handle.ingest.call_args.args[0]
        assert [artifact.path for artifact in artifacts] == ["etc/di.xml", "source.php"]
        loaded_paths = loader.load_file_batch.call_args.args[0]
        assert loaded_paths == [Path("source.php"), Path("etc/di.xml")]
        semantic_documents = splitter.split_documents.call_args.args[0]
        assert [document.metadata["path"] for document in semantic_documents] == ["source.php"]

    def test_exceeds_file_limit(self):
        config = _mock_config(max_files_per_index=5)
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()

        fake_files = [f"f{i}.py" for i in range(10)]
        loader.iter_repository_files.return_value = iter(fake_files)

        coll_mgr.create_pending_collection.return_value = "pending"
        coll_mgr.alias_exists.return_value = False
        coll_mgr.collection_exists.return_value = False
        coll_mgr.resolve_alias.return_value = None

        indexer = RepositoryIndexer(config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader)

        with pytest.raises(ValueError, match="exceeds file limit"):
            indexer.index_repository("/repo", "ws", "proj", "main", "abc123", "alias1")

        coll_mgr.delete_collection.assert_called_with("pending")

    def test_exceeds_chunk_limit_at_estimation(self):
        config = _mock_config(max_chunks_per_index=10)
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()

        fake_files = [f"f{i}.py" for i in range(5)]
        loader.iter_repository_files.side_effect = lambda *a, **kw: iter(fake_files)

        coll_mgr.create_pending_collection.return_value = "pending"
        coll_mgr.alias_exists.return_value = False
        coll_mgr.collection_exists.return_value = False
        coll_mgr.resolve_alias.return_value = None

        # Make estimation exceed limit: 100 estimated > 10 * 1.2 = 12
        loader.load_file_batch.return_value = [
            SimpleNamespace(text="a = 1", metadata={"path": "a.py"})
        ]
        splitter.split_documents.return_value = [MagicMock() for _ in range(100)]

        indexer = RepositoryIndexer(config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader)

        with pytest.raises(ValueError, match="estimated to exceed chunk limit"):
            indexer.index_repository("/repo", "ws", "proj", "main", "abc123", "alias1")

    def test_successful_indexing(self):
        config = _mock_config()
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()

        fake_files = ["a.py", "b.py"]
        loader.iter_repository_files.return_value = iter(fake_files)

        coll_mgr.create_pending_collection.return_value = "pending"
        coll_mgr.alias_exists.return_value = False
        coll_mgr.collection_exists.return_value = False
        coll_mgr.resolve_alias.return_value = None

        loader.load_file_batch.return_value = [
            SimpleNamespace(text="a = 1", metadata={"path": "a.py"})
        ]
        splitter.split_documents.return_value = [MagicMock(), MagicMock()]
        point_ops.process_and_upsert_chunks.return_value = (2, 0)

        # After indexing, temp collection has points
        temp_info = MagicMock()
        temp_info.points_count = 3
        point_ops.client.get_collection.return_value = temp_info
        branch_mgr.get_branch_point_count.return_value = 3

        indexer = RepositoryIndexer(config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader)
        result = indexer.index_repository("/repo", "ws", "proj", "main", "abc123", "alias1")

        assert result.workspace == "ws"
        assert result.project == "proj"
        assert result.branch == "main"
        stats_mgr.store_metadata.assert_called_once()
        point_ops.write_repository_generation_manifest.assert_called_once_with(
            "pending",
            "ws",
            "proj",
            "main",
            "abc123",
            expected_member_count=2,
            expected_members=[],
            source_tree_sha256="c" * 64,
            index_include_patterns=[],
            index_exclude_patterns=[],
            identity_metadata={
                "index_representation_fingerprint": (
                    indexer.representation_fingerprint
                ),
            },
        )

    def test_generation_manifest_failure_never_swaps_alias(self):
        config = _mock_config()
        (
            coll_mgr,
            branch_mgr,
            point_ops,
            stats_mgr,
            splitter,
            loader,
        ) = _mock_components()
        loader.iter_repository_files.return_value = iter(["a.py"])
        loader.load_file_batch.return_value = [
            SimpleNamespace(text="a = 1", metadata={"path": "a.py"})
        ]
        splitter.split_documents.return_value = [MagicMock()]
        coll_mgr.create_pending_collection.return_value = "pending"
        coll_mgr.alias_exists.return_value = True
        coll_mgr.collection_exists.return_value = True
        coll_mgr.resolve_alias.return_value = "active"
        point_ops.write_repository_generation_manifest.side_effect = (
            RuntimeError("generation membership is incomplete")
        )

        indexer = RepositoryIndexer(
            config,
            coll_mgr,
            branch_mgr,
            point_ops,
            stats_mgr,
            splitter,
            loader,
        )
        with pytest.raises(
            RuntimeError,
            match="generation membership is incomplete",
        ):
            indexer.index_repository(
                "/repo",
                "ws",
                "proj",
                "main",
                "abc123",
                "alias1",
            )

        coll_mgr.atomic_alias_swap.assert_not_called()
        stats_mgr.store_metadata.assert_not_called()
        coll_mgr.delete_collection.assert_called_with("pending")

    def test_preserves_other_branches_only_when_explicitly_enabled(self):
        config = _mock_config()
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()

        loader.iter_repository_files.return_value = iter(["a.py"])

        coll_mgr.create_pending_collection.return_value = "pending"
        coll_mgr.alias_exists.return_value = True
        coll_mgr.collection_exists.return_value = True
        coll_mgr.resolve_alias.return_value = "active"

        loader.load_file_batch.return_value = [
            SimpleNamespace(text="a = 1", metadata={"path": "a.py"})
        ]
        splitter.split_documents.return_value = [MagicMock()]

        temp_info = MagicMock()
        temp_info.points_count = 4
        point_ops.client.get_collection.return_value = temp_info
        branch_mgr.stream_copy_points_to_collection.return_value = 2

        indexer = RepositoryIndexer(config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader)
        indexer.index_repository(
            "/repo",
            "ws",
            "proj",
            "main",
            "abc123",
            "alias1",
            preserve_other_branches=True,
        )

        branch_mgr.stream_copy_points_to_collection.assert_called_once_with(
            "active", "pending", "main", INSERT_BATCH_SIZE
        )

    def test_main_only_index_does_not_copy_other_branches(self):
        config = _mock_config()
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()

        loader.iter_repository_files.return_value = iter(["a.py"])
        coll_mgr.create_pending_collection.return_value = "pending"
        coll_mgr.alias_exists.return_value = True
        coll_mgr.collection_exists.return_value = True
        coll_mgr.resolve_alias.return_value = "active"
        loader.load_file_batch.return_value = [
            SimpleNamespace(text="a = 1", metadata={"path": "a.py"})
        ]
        splitter.split_documents.return_value = [MagicMock()]
        temp_info = MagicMock()
        temp_info.points_count = 2
        point_ops.client.get_collection.return_value = temp_info

        indexer = RepositoryIndexer(
            config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader
        )
        indexer.index_repository(
            "/repo", "ws", "proj", "main", "abc123", "alias1"
        )

        branch_mgr.stream_copy_points_to_collection.assert_not_called()

    def test_point_count_mismatch_never_swaps_alias(self):
        config = _mock_config()
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()
        loader.iter_repository_files.return_value = iter(["a.py"])
        loader.load_file_batch.return_value = [
            SimpleNamespace(text="a = 1", metadata={"path": "a.py"})
        ]
        splitter.split_documents.return_value = [MagicMock(), MagicMock()]
        point_ops.process_and_upsert_chunks.return_value = (2, 0)
        point_ops.client.get_collection.return_value = SimpleNamespace(
            points_count=2
        )
        branch_mgr.get_branch_point_count.return_value = 2
        coll_mgr.create_pending_collection.return_value = "pending"
        coll_mgr.alias_exists.return_value = True
        coll_mgr.collection_exists.return_value = True
        coll_mgr.resolve_alias.return_value = "active"

        indexer = RepositoryIndexer(
            config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader
        )
        with pytest.raises(
            RuntimeError,
            match="Pending collection point count is incomplete",
        ):
            indexer.index_repository(
                "/repo", "ws", "proj", "main", "abc123", "alias1"
            )

        coll_mgr.atomic_alias_swap.assert_not_called()
        stats_mgr.store_metadata.assert_not_called()
        coll_mgr.delete_collection.assert_called_with("pending")

    def test_target_branch_point_count_mismatch_never_swaps_alias(self):
        config = _mock_config()
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()
        loader.iter_repository_files.return_value = iter(["a.py"])
        loader.load_file_batch.return_value = [
            SimpleNamespace(text="a = 1", metadata={"path": "a.py"})
        ]
        splitter.split_documents.return_value = [MagicMock()]
        point_ops.client.get_collection.return_value = SimpleNamespace(
            points_count=2
        )
        branch_mgr.get_branch_point_count.return_value = 0
        coll_mgr.create_pending_collection.return_value = "pending"
        coll_mgr.alias_exists.return_value = True
        coll_mgr.collection_exists.return_value = True
        coll_mgr.resolve_alias.return_value = "active"

        indexer = RepositoryIndexer(
            config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader
        )
        with pytest.raises(
            RuntimeError,
            match="Pending target-branch point count is incomplete",
        ):
            indexer.index_repository(
                "/repo", "ws", "proj", "main", "abc123", "alias1"
            )

        coll_mgr.atomic_alias_swap.assert_not_called()
        stats_mgr.store_metadata.assert_not_called()
        coll_mgr.delete_collection.assert_called_with("pending")

    def test_indexing_failure_cleans_up(self):
        config = _mock_config()
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()

        loader.iter_repository_files.return_value = iter(["a.py"])

        coll_mgr.create_pending_collection.return_value = "pending"
        coll_mgr.alias_exists.return_value = False
        coll_mgr.collection_exists.return_value = False
        coll_mgr.resolve_alias.return_value = None

        loader.load_file_batch.side_effect = RuntimeError("disk error")

        indexer = RepositoryIndexer(config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader)

        with pytest.raises(RuntimeError, match="disk error"):
            indexer.index_repository("/repo", "ws", "proj", "main", "abc123", "alias1")

        coll_mgr.delete_collection.assert_called_with("pending")

    def test_partial_vector_write_never_swaps_alias(self):
        config = _mock_config()
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()
        loader.iter_repository_files.return_value = iter(["a.php"])
        loader.load_file_batch.return_value = [
            SimpleNamespace(text="<?php", metadata={"path": "a.php"})
        ]
        splitter.split_documents.return_value = [MagicMock(), MagicMock()]
        point_ops.process_and_upsert_chunks.return_value = (1, 1)
        coll_mgr.create_pending_collection.return_value = "pending"
        coll_mgr.alias_exists.return_value = True
        coll_mgr.collection_exists.return_value = True
        coll_mgr.resolve_alias.return_value = "active"

        indexer = RepositoryIndexer(config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader)

        with pytest.raises(RuntimeError, match="partial"):
            indexer.index_repository("/repo", "ws", "proj", "main", "abc123", "alias1")

        coll_mgr.atomic_alias_swap.assert_not_called()
        stats_mgr.store_metadata.assert_not_called()

    def test_repository_architecture_is_streamed_and_indexed_as_context(self, tmp_path):
        from codecrow_plugins import (
            ArchitecturePacket,
            FileDisposition,
            GraphFact,
            RepositoryAnalysis,
        )

        config = _mock_config()
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()
        source = tmp_path / "app" / "code" / "Acme" / "Module.php"
        source.parent.mkdir(parents=True)
        source.write_text("<?php class Module {}", encoding="utf-8")
        loader.iter_repository_files.return_value = iter(["app/code/Acme/Module.php"])
        document = SimpleNamespace(
            text="<?php class Module {}",
            metadata={"path": "app/code/Acme/Module.php"},
        )
        loader.load_file_batch.return_value = [document]
        splitter.split_documents.return_value = [MagicMock()]
        point_ops.process_and_upsert_chunks.side_effect = [(1, 0), (2, 0)]
        point_ops.client.get_collection.return_value = SimpleNamespace(points_count=4)
        branch_mgr.get_branch_point_count.return_value = 4
        coll_mgr.create_pending_collection.return_value = "pending"
        coll_mgr.alias_exists.return_value = False
        coll_mgr.collection_exists.return_value = False
        coll_mgr.resolve_alias.return_value = None

        capabilities = SimpleNamespace(
            repository_plugins=("php", "magento"),
            fingerprint="sha256:" + "0" * 64,
            descriptor_fingerprint="sha256:" + "1" * 64,
        )
        selector = MagicMock()
        selector.select.return_value = capabilities
        handle = MagicMock()
        handle.active = True
        handle.finish.return_value = (
            RepositoryAnalysis(packets=(ArchitecturePacket(
                plugin_id="magento",
                kind="magento-object-graph",
                key="global:Acme\\Module",
                paths=("app/code/Acme/Module.php", "app/code/Acme/etc/di.xml"),
                facts=(GraphFact(
                    "magento-object-resolution",
                    "Acme\\Api\\Contract",
                    "resolves-to",
                    "Acme\\Model\\Implementation",
                    "app/code/Acme/etc/di.xml",
                    related_paths=("app/code/Acme/Module.php",),
                ),),
            ),)),
            (),
        )
        runtime = MagicMock()
        runtime.file_disposition.return_value = FileDisposition.FULL
        runtime.start_repository_analysis.return_value = handle

        indexer = RepositoryIndexer(
            config,
            coll_mgr,
            branch_mgr,
            point_ops,
            stats_mgr,
            splitter,
            loader,
            plugin_catalog=MagicMock(),
            plugin_runtime=runtime,
            plugin_selector=selector,
        )
        result = indexer.index_repository(
            str(tmp_path), "ws", "proj", "main", "abc123", "alias1"
        )

        ingested = handle.ingest.call_args.args[0]
        assert [(artifact.path, artifact.content) for artifact in ingested] == [
            ("app/code/Acme/Module.php", "<?php class Module {}"),
        ]
        architecture_nodes = point_ops.process_and_upsert_chunks.call_args_list[1].args[0]
        assert architecture_nodes[0].metadata["architecture_context"] is True
        assert architecture_nodes[0].metadata["architecture_paths"] == [
            "app/code/Acme/Module.php",
            "app/code/Acme/etc/di.xml",
        ]
        assert result.chunk_count == 3


# ─────────────────────────────────────────────────────────────
# architecture storage
# ─────────────────────────────────────────────────────────────
class TestArchitectureStorage:

    def test_compacts_only_within_same_plugin_kind_and_source_path(self):
        from codecrow_plugins import ArchitecturePacket, GraphFact, RepositoryAnalysis

        packets = []
        for index in range(30):
            source_path = "app/code/Acme/Module/etc/di.xml"
            packets.append(ArchitecturePacket(
                plugin_id="magento",
                kind="magento-di",
                key=f"preference:{index:02d}",
                paths=(source_path,),
                facts=(GraphFact(
                    kind="magento-preference",
                    source=f"Acme\\Api\\Contract{index:02d}",
                    relation="resolves-to",
                    target=f"Acme\\Model\\Implementation{index:02d}",
                    path=source_path,
                ),),
            ))
        other_path = "app/code/Other/Module/etc/di.xml"
        packets.append(ArchitecturePacket(
            plugin_id="magento",
            kind="magento-di",
            key="preference:other",
            paths=(other_path,),
            facts=(GraphFact(
                kind="magento-preference",
                source="Other\\Api\\Contract",
                relation="resolves-to",
                target="Other\\Model\\Implementation",
                path=other_path,
            ),),
        ))
        capabilities = SimpleNamespace(
            repository_plugins=("php", "magento"),
            fingerprint="sha256:" + "0" * 64,
            descriptor_fingerprint="sha256:" + "1" * 64,
        )

        nodes = RepositoryIndexer._architecture_nodes(
            RepositoryAnalysis(packets=tuple(sorted(packets))),
            capabilities,
            "ws",
            "project",
            "main",
            "commit",
        )

        assert len(nodes) == 3
        acme_nodes = [
            node for node in nodes
            if node.metadata["architecture_source_path"]
            == "app/code/Acme/Module/etc/di.xml"
        ]
        assert [len(node.metadata["plugin_graph_facts"]) for node in acme_nodes] == [25, 5]
        assert all(len(node.metadata["architecture_keys"]) <= 25 for node in acme_nodes)
        assert nodes[-1].metadata["architecture_source_path"] == other_path

    def test_incremental_group_filter_rebuilds_complete_group(self):
        from codecrow_plugins import ArchitecturePacket, GraphFact, RepositoryAnalysis

        first_path = "app/code/Acme/Module/etc/di.xml"
        second_path = "app/code/Acme/Module/etc/events.xml"
        analysis = RepositoryAnalysis(packets=tuple(sorted((
            ArchitecturePacket(
                plugin_id="magento",
                kind="magento-di",
                key="preference:a",
                paths=(first_path,),
                facts=(GraphFact(
                    "magento-preference", "A", "resolves-to", "B", first_path
                ),),
            ),
            ArchitecturePacket(
                plugin_id="magento",
                kind="magento-event",
                key="event:a",
                paths=(second_path,),
                facts=(GraphFact(
                    "magento-observer", "event", "invokes", "Observer", second_path
                ),),
            ),
        ))))
        capabilities = SimpleNamespace(
            repository_plugins=("php", "magento"),
            fingerprint="sha256:" + "0" * 64,
            descriptor_fingerprint="sha256:" + "1" * 64,
        )

        nodes = RepositoryIndexer._architecture_nodes(
            analysis,
            capabilities,
            "ws",
            "project",
            "main",
            "commit",
            groups={("magento", "magento-di", first_path)},
        )

        assert len(nodes) == 1
        assert nodes[0].metadata["architecture_source_path"] == first_path
        assert nodes[0].metadata["architecture_keys"] == ["preference:a"]


# ─────────────────────────────────────────────────────────────
# _perform_atomic_swap
# ─────────────────────────────────────────────────────────────
class TestPerformAtomicSwap:

    def test_normal_swap(self):
        config = _mock_config()
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()

        coll_mgr.collection_exists.return_value = False
        coll_mgr.alias_exists.return_value = True
        coll_mgr.resolve_alias.return_value = "active"

        indexer = RepositoryIndexer(config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader)
        old_target = indexer._perform_atomic_swap("alias1", "pending", old_alias_exists=True)

        coll_mgr.atomic_alias_swap.assert_called_once()
        assert old_target == "active"
        coll_mgr.delete_collection.assert_not_called()

    def test_first_activation_has_no_rollback_target(self):
        config = _mock_config()
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()

        coll_mgr.resolve_alias.return_value = None

        indexer = RepositoryIndexer(config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader)
        old_target = indexer._perform_atomic_swap("alias1", "pending", old_alias_exists=False)

        assert old_target is None
        coll_mgr.atomic_alias_swap.assert_called_once_with("alias1", "pending", False)

    def test_metadata_failure_rolls_back_before_pending_collection_is_deleted(self):
        config = _mock_config()
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()
        loader.iter_repository_files.return_value = iter(["a.php"])
        loader.load_file_batch.return_value = [
            SimpleNamespace(text="<?php", metadata={"path": "a.php"})
        ]
        splitter.split_documents.return_value = [MagicMock()]
        coll_mgr.create_pending_collection.return_value = "pending"
        coll_mgr.alias_exists.return_value = True
        coll_mgr.resolve_alias.return_value = "active"
        point_ops.client.get_collection.return_value = SimpleNamespace(points_count=2)
        stats_mgr.store_metadata.side_effect = RuntimeError("metadata unavailable")

        indexer = RepositoryIndexer(config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader)
        with pytest.raises(RuntimeError, match="metadata unavailable"):
            indexer.index_repository("/repo", "ws", "proj", "main", "abc123", "alias1")

        assert coll_mgr.atomic_alias_swap.call_args_list == [
            call("alias1", "pending", True),
            call("alias1", "active", True),
        ]
        coll_mgr.delete_collection.assert_called_with("pending")

    def test_direct_collection_is_left_untouched(self):
        config = _mock_config()
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()
        coll_mgr.create_pending_collection.return_value = "pending"
        coll_mgr.alias_exists.return_value = False
        coll_mgr.collection_exists.return_value = True

        indexer = RepositoryIndexer(config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader)
        with pytest.raises(RuntimeError, match="direct collection was left unchanged"):
            indexer.index_repository("/repo", "ws", "proj", "main", "abc123", "alias1")

        coll_mgr.delete_collection.assert_called_once_with("pending")
        coll_mgr.atomic_alias_swap.assert_not_called()


# ─────────────────────────────────────────────────────────────
# FileOperations
# ─────────────────────────────────────────────────────────────
class TestFileOperations:

    def _make_file_ops(self):
        client = MagicMock()
        point_ops = MagicMock()
        coll_mgr = MagicMock()
        stats_mgr = MagicMock()
        splitter = MagicMock()
        loader = MagicMock()

        client.scroll.return_value = ([], None)
        coll_mgr.resolve_collection_target.return_value = "coll"
        point_ops.prepare_chunks_for_embedding.side_effect = (
            lambda nodes, *_: [
                (f"point-{index}", node) for index, node in enumerate(nodes)
            ]
        )
        point_ops.embed_and_create_points.side_effect = (
            lambda chunk_data: [
                SimpleNamespace(id=point_id) for point_id, _ in chunk_data
            ]
        )
        point_ops.upsert_points.side_effect = (
            lambda _collection, points: (len(points), 0)
        )

        stats_mgr.get_project_stats.return_value = MagicMock(spec=IndexStats)
        return FileOperations(client, point_ops, coll_mgr, stats_mgr, splitter, loader)

    def test_update_files_success(self, tmp_path):
        ops = self._make_file_ops()
        source = tmp_path / "src" / "Foo.java"
        source.parent.mkdir(parents=True)
        source.write_text("final class Foo {}\n", encoding="utf-8")
        ops.loader.load_specific_files.return_value = [MagicMock()]
        ops.splitter.split_documents.return_value = [MagicMock(), MagicMock()]
        ops.point_ops.process_and_upsert_chunks.return_value = (2, 0)

        result = ops.update_files(
            file_paths=["src/Foo.java"],
            repo_base=str(tmp_path),
            workspace="ws",
            project="proj",
            branch="main",
            commit="abc",
            collection_name="coll",
        )

        ops.splitter.split_documents.assert_called_once()
        ops.point_ops.upsert_points.assert_called_once()
        ops.client.delete.assert_not_called()

    def test_update_files_no_documents(self, tmp_path):
        ops = self._make_file_ops()
        source = tmp_path / "src" / "Missing.java"
        source.parent.mkdir(parents=True)
        source.write_text("final class Missing {}\n", encoding="utf-8")
        ops.loader.load_specific_files.return_value = []

        result = ops.update_files(
            file_paths=["src/Missing.java"],
            repo_base=str(tmp_path),
            workspace="ws",
            project="proj",
            branch="main",
            commit="abc",
            collection_name="coll",
        )
        ops.splitter.split_documents.assert_not_called()
        ops.point_ops.upsert_points.assert_called_once_with("coll", [])

    def test_delete_files(self):
        ops = self._make_file_ops()

        result = ops.delete_files(
            file_paths=["src/Del.java"],
            workspace="ws",
            project="proj",
            branch="main",
            collection_name="coll",
        )
        ops.point_ops.upsert_points.assert_called_once_with("coll", [])
        ops.client.delete.assert_not_called()

    def test_alias_swap_before_incremental_write_fails_without_mutation(self):
        ops = self._make_file_ops()
        ops.collection_manager.resolve_collection_target.side_effect = [
            "active-before",
            "active-after",
        ]

        with pytest.raises(
            IncrementalIndexPreconditionError,
            match="active collection changed",
        ):
            ops.delete_files(
                file_paths=["src/Del.java"],
                workspace="ws",
                project="proj",
                branch="main",
                collection_name="coll",
                commit="b" * 40,
            )

        ops.point_ops.upsert_points.assert_not_called()
        ops.client.delete.assert_not_called()
        assert {
            call.kwargs["collection_name"]
            for call in ops.client.scroll.call_args_list
        } == {"active-before"}

    def test_sealed_generation_rejects_incremental_change_before_mutation(self):
        ops = self._make_file_ops()
        manifest = SimpleNamespace(
            id="manifest",
            payload={"repository_generation_manifest": True},
        )
        ops.client.scroll.return_value = ([manifest], None)

        with pytest.raises(
            IncrementalIndexPreconditionError,
            match="sealed repository generation",
        ):
            ops.delete_files(
                file_paths=["src/Del.java"],
                workspace="ws",
                project="proj",
                branch="main",
                collection_name="coll",
                commit="b" * 40,
            )

        ops.collection_manager.ensure_collection_exists.assert_not_called()
        ops.loader.load_specific_files.assert_not_called()
        ops.point_ops.upsert_points.assert_not_called()
        ops.client.delete.assert_not_called()

    def test_partial_replacement_restores_previous_points(self):
        from qdrant_client.models import PointStruct

        ops = self._make_file_ops()
        old_id = str(uuid.uuid4())
        new_id = str(uuid.uuid4())
        old_record = SimpleNamespace(
            id=old_id,
            vector=[1.0, 0.0],
            payload={"path": "old.php", "branch": "main"},
        )
        node = MagicMock()
        ops.point_ops.prepare_chunks_for_embedding.side_effect = None
        ops.point_ops.prepare_chunks_for_embedding.return_value = [(new_id, node)]
        ops.point_ops.embed_and_create_points.side_effect = None
        ops.point_ops.embed_and_create_points.return_value = [PointStruct(
            id=new_id,
            vector=[0.0, 1.0],
            payload={"path": "new.php", "branch": "main"},
        )]
        ops.point_ops.upsert_points.side_effect = None
        ops.point_ops.upsert_points.return_value = (0, 1)

        with pytest.raises(RuntimeError, match="write was partial"):
            ops._replace_points(
                [node],
                [old_record],
                "coll",
                "ws",
                "project",
                "main",
            )

        restored = ops.client.upsert.call_args.kwargs["points"]
        assert [str(point.id) for point in restored] == [old_id]
        deleted = ops.client.delete.call_args.kwargs["points_selector"]
        assert [str(point_id) for point_id in deleted.points] == [new_id]
