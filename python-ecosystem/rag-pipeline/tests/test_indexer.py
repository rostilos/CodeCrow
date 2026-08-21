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
from rag_pipeline.core.index_manager.point_operations import PointWriteResult
from rag_pipeline.models.config import IndexStats


def _mock_config(**overrides):
    cfg = MagicMock()
    cfg.max_files_per_index = 0
    cfg.max_chunks_per_index = 0
    cfg.architecture_finalization_timeout_seconds = 600
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
    point_ops.client = MagicMock()
    branch_mgr.get_branch_point_count.return_value = 1
    branch_mgr.stream_copy_points_to_collection.return_value = 0
    splitter.split_documents_resilient.side_effect = (
        lambda documents, capabilities=None: (
            splitter.split_documents(
                documents,
                capabilities=capabilities,
            ),
            (),
        )
    )

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
        progress_events = []
        result = indexer.index_repository(
            "/repo", "ws", "proj", "main", "abc123", "alias1",
            progress_callback=progress_events.append,
        )

        coll_mgr.delete_collection.assert_called_with("pending")
        assert result.document_count == 0
        assert [event["stage"] for event in progress_events] == [
            "preparing", "scanning",
        ]
        assert progress_events[-1]["total"] == 0

    def test_progress_callback_failure_does_not_fail_indexing(self):
        config = _mock_config()
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()
        loader.iter_repository_files.return_value = iter([])
        coll_mgr.create_pending_collection.return_value = "pending"
        coll_mgr.alias_exists.return_value = False
        coll_mgr.collection_exists.return_value = False
        coll_mgr.resolve_alias.return_value = None
        stats_mgr.get_branch_stats.return_value = IndexStats(
            namespace="ws__proj__main", document_count=0, chunk_count=0,
            last_updated="2024-01-01", workspace="ws", project="proj", branch="main"
        )
        indexer = RepositoryIndexer(
            config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader,
        )

        result = indexer.index_repository(
            "/repo", "ws", "proj", "main", "abc123", "alias1",
            progress_callback=lambda _event: (_ for _ in ()).throw(
                RuntimeError("event sink unavailable")
            ),
        )

        assert result.document_count == 0

    def test_architecture_only_batch_is_ingested_and_reports_completion(
        self,
        tmp_path,
        monkeypatch,
    ):
        from codecrow_plugins import (
            FileDisposition,
            PluginDiagnostic,
            ProjectCapabilities,
            RepositoryAnalysis,
        )
        from rag_pipeline.core.index_manager import indexer as indexer_module

        monkeypatch.setattr(indexer_module, "DOCUMENT_BATCH_SIZE", 1)

        config = _mock_config()
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()
        paths = [
            Path("source.php"),
            Path("etc/di.xml"),
            Path("generated/code/Proxy.php"),
        ]
        loader.iter_repository_files.return_value = iter(paths)
        documents_by_path = {
            "source.php": SimpleNamespace(
                text="<?php class Source {}",
                metadata={"path": "source.php"},
            ),
            "etc/di.xml": SimpleNamespace(
                text="<config/>",
                metadata={"path": "etc/di.xml"},
            ),
        }
        loader.load_file_batch.side_effect = lambda batch, *_args, **_kwargs: [
            documents_by_path[Path(path).as_posix()]
            for path in batch
        ]
        splitter.split_documents.return_value = [MagicMock()]
        coll_mgr.create_pending_collection.return_value = "pending"
        coll_mgr.alias_exists.return_value = False
        coll_mgr.collection_exists.return_value = False
        coll_mgr.resolve_alias.return_value = None
        point_ops.client.get_collection.return_value = SimpleNamespace(points_count=2)
        point_ops.process_and_upsert_chunks.return_value = (1, 0)
        branch_mgr.get_branch_point_count.return_value = 2
        stats_mgr.store_metadata.return_value = None
        selector = MagicMock()
        selector.select.return_value = ProjectCapabilities(
            repository_plugins=("php", "magento"),
            file_plugins={}, detection_evidence={}, unavailable_capabilities=(),
            fingerprint="sha256:" + "0" * 64,
        )
        handle = MagicMock(active=True)
        handle.finish.return_value = (
            RepositoryAnalysis(),
            (PluginDiagnostic(
                code="plugin-repository-finalization-timeout",
                message="Magento architecture exceeded its time budget",
                plugin_id="magento",
                recoverable=True,
            ),),
        )
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

        progress_events = []
        indexer.index_repository(
            str(tmp_path), "ws", "proj", "main", "abc", "alias",
            progress_callback=progress_events.append,
        )

        ingested_paths = [
            artifact.path
            for invocation in handle.ingest.call_args_list
            for artifact in invocation.args[0]
        ]
        assert ingested_paths == ["source.php", "etc/di.xml"]
        loaded_paths = [
            invocation.args[0]
            for invocation in loader.load_file_batch.call_args_list
        ]
        assert loaded_paths == [[Path("source.php")], [Path("etc/di.xml")]]
        semantic_documents = splitter.split_documents.call_args.args[0]
        assert [document.metadata["path"] for document in semantic_documents] == ["source.php"]
        batch_events = [
            event for event in progress_events
            if event["stage"] == "indexing" and "completedBatches" in event
        ]
        assert [event["completedBatches"] for event in batch_events] == [1, 2]
        assert batch_events[-1]["architectureOnlyFiles"] == 1
        assert batch_events[-1]["estimatedRemainingMs"] == 0
        assert batch_events[-1]["remainingEstimateScope"] == "file_batches"
        assert any(
            event.get("architectureStatus") == "degraded"
            and event.get("degraded") is True
            for event in progress_events
        )

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
        temp_info.points_count = 2
        point_ops.client.get_collection.return_value = temp_info
        branch_mgr.get_branch_point_count.return_value = 2

        indexer = RepositoryIndexer(config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader)
        result = indexer.index_repository("/repo", "ws", "proj", "main", "abc123", "alias1")

        assert result.workspace == "ws"
        assert result.project == "proj"
        assert result.branch == "main"
        stats_mgr.store_metadata.assert_called_once()

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
        temp_info.points_count = 3
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
        temp_info.points_count = 1
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
            points_count=1
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
            points_count=1
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

    def test_rejected_vector_point_is_skipped_and_valid_index_is_published(self):
        config = _mock_config()
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()
        loader.iter_repository_files.return_value = iter(["a.php"])
        loader.load_file_batch.return_value = [
            SimpleNamespace(text="<?php", metadata={"path": "a.php"})
        ]
        splitter.split_documents.return_value = [MagicMock(), MagicMock()]
        point_ops.process_and_upsert_chunks.return_value = (1, 1)
        point_ops.client.get_collection.return_value = SimpleNamespace(
            points_count=1
        )
        branch_mgr.get_branch_point_count.return_value = 1
        coll_mgr.create_pending_collection.return_value = "pending"
        coll_mgr.alias_exists.return_value = True
        coll_mgr.collection_exists.return_value = True
        coll_mgr.resolve_alias.return_value = "active"

        indexer = RepositoryIndexer(config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader)

        result = indexer.index_repository(
            "/repo", "ws", "proj", "main", "abc123", "alias1"
        )

        assert result.chunk_count == 1
        assert result.skipped_chunk_count == 1
        coll_mgr.atomic_assign_aliases.assert_called_once_with({"alias1": "pending"})
        stats_mgr.store_metadata.assert_called_once()

    def test_repository_architecture_is_streamed_and_indexed_as_context(self, tmp_path):
        from codecrow_plugins import (
            ArchitecturePacket,
            FileDisposition,
            GraphFact,
            PluginDiagnostic,
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
        point_ops.client.get_collection.return_value = SimpleNamespace(points_count=3)
        branch_mgr.get_branch_point_count.return_value = 3
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
            (PluginDiagnostic(
                code="magento-invalid-xml",
                message="Cannot parse etc/invalid.xml",
                plugin_id="magento",
                path="etc/invalid.xml",
                recoverable=True,
            ),),
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
        assert result.skipped_file_count == 1


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

        coll_mgr.read_alias_targets.return_value = {"alias1": "active"}

        indexer = RepositoryIndexer(config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader)
        old_targets = indexer._perform_atomic_swap(
            "alias1", "pending", ["alias1"]
        )

        coll_mgr.atomic_assign_aliases.assert_called_once_with({"alias1": "pending"})
        assert old_targets == {"alias1": "active"}
        coll_mgr.delete_collection.assert_not_called()

    def test_first_activation_has_no_rollback_target(self):
        config = _mock_config()
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()

        coll_mgr.read_alias_targets.return_value = {"alias1": None}

        indexer = RepositoryIndexer(config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader)
        old_targets = indexer._perform_atomic_swap(
            "alias1", "pending", ["alias1"]
        )

        assert old_targets == {"alias1": None}
        coll_mgr.atomic_assign_aliases.assert_called_once_with({"alias1": "pending"})

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
        coll_mgr.read_alias_targets.return_value = {"alias1": "active"}
        point_ops.client.get_collection.return_value = SimpleNamespace(points_count=1)
        stats_mgr.store_metadata.side_effect = RuntimeError("metadata unavailable")

        indexer = RepositoryIndexer(config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader)
        with pytest.raises(RuntimeError, match="metadata unavailable"):
            indexer.index_repository("/repo", "ws", "proj", "main", "abc123", "alias1")

        assert coll_mgr.atomic_assign_aliases.call_args_list == [
            call({"alias1": "pending"}),
            call({"alias1": "active"}),
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
        point_ops.prepare_chunks_for_embedding.side_effect = (
            lambda nodes, *_: [
                (f"point-{index}", node) for index, node in enumerate(nodes)
            ]
        )
        point_ops.embed_and_create_points.side_effect = (
            lambda chunk_data, **_kwargs: [
                SimpleNamespace(id=point_id) for point_id, _ in chunk_data
            ]
        )
        point_ops.upsert_points.side_effect = (
            lambda _collection, points: (len(points), 0)
        )
        point_ops.upsert_points_detailed.side_effect = (
            lambda _collection, points: PointWriteResult(
                successful=len(points)
            )
        )
        splitter.split_documents_resilient.side_effect = (
            lambda documents, capabilities=None: (
                splitter.split_documents(
                    documents,
                    capabilities=capabilities,
                ),
                (),
            )
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

        ops.splitter.split_documents_resilient.assert_called_once()
        ops.point_ops.upsert_points_detailed.assert_called_once()
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
        ops.point_ops.upsert_points_detailed.assert_called_once_with("coll", [])

    def test_delete_files(self):
        ops = self._make_file_ops()

        result = ops.delete_files(
            file_paths=["src/Del.java"],
            workspace="ws",
            project="proj",
            branch="main",
            collection_name="coll",
        )
        ops.point_ops.upsert_points_detailed.assert_called_once_with("coll", [])
        ops.client.delete.assert_not_called()

    def test_rejected_replacement_point_is_quarantined(self):
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
        ops.point_ops.upsert_points_detailed.side_effect = None
        ops.point_ops.upsert_points_detailed.return_value = PointWriteResult(
            skipped_points=(
                PointStruct(
                    id=new_id,
                    vector=[0.0, 1.0],
                    payload={"path": "new.php", "branch": "main"},
                ),
            ),
        )

        successful = ops._replace_points(
            [node],
            [old_record],
            "coll",
            "ws",
            "project",
            "main",
        )

        assert successful == 0
        ops.client.upsert.assert_not_called()
        deleted = ops.client.delete.call_args.kwargs["points_selector"]
        assert [str(point_id) for point_id in deleted.points] == [old_id]

    def test_replace_checks_mutation_lease_after_embedding_before_write(self):
        from qdrant_client.models import PointStruct

        ops = self._make_file_ops()
        node = MagicMock()
        point_id = str(uuid.uuid4())
        ops.point_ops.prepare_chunks_for_embedding.side_effect = None
        ops.point_ops.prepare_chunks_for_embedding.return_value = [(point_id, node)]
        ops.point_ops.embed_and_create_points.side_effect = None
        ops.point_ops.embed_and_create_points.return_value = [PointStruct(
            id=point_id,
            vector=[0.0, 1.0],
            payload={"path": "new.php", "branch": "main"},
        )]
        guard = MagicMock(side_effect=RuntimeError("lease lost"))

        with pytest.raises(RuntimeError, match="lease lost"):
            ops._replace_points(
                [node],
                [],
                "coll",
                "ws",
                "project",
                "main",
                guard,
            )

        ops.point_ops.upsert_points_detailed.assert_not_called()
        ops.client.upsert.assert_not_called()
        ops.client.delete.assert_not_called()
