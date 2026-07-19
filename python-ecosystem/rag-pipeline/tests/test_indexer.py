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
import pytest
from unittest.mock import patch, MagicMock, PropertyMock, call
from datetime import datetime, timezone
from types import SimpleNamespace

from rag_pipeline.core.index_manager.indexer import (
    RepositoryIndexer, FileOperations,
    DOCUMENT_BATCH_SIZE, INSERT_BATCH_SIZE,
)
from rag_pipeline.models.config import IndexStats


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
    point_ops.process_and_upsert_chunks.return_value = (5, 0)
    point_ops.client = MagicMock()

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

        loader.load_file_batch.return_value = [MagicMock()]
        splitter.split_documents.return_value = [MagicMock(), MagicMock()]

        indexer = RepositoryIndexer(config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader)
        fc, cc = indexer.estimate_repository_size("/repo")
        assert fc == 200
        assert cc > 0  # Estimated from sampling


# ─────────────────────────────────────────────────────────────
# index_repository
# ─────────────────────────────────────────────────────────────
class TestIndexRepository:

    def test_retained_revision_indexes_in_place_without_atomic_copy(self, tmp_path):
        config = _mock_config()
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()
        loader.iter_repository_files.return_value = iter(["a.py"])
        loader.load_file_batch.return_value = [MagicMock()]
        splitter.split_documents.return_value = [MagicMock(), MagicMock()]
        coll_mgr.resolve_alias.return_value = "coll_v1"
        branch_mgr.delete_revision_points.return_value = True
        branch_mgr.get_revision_point_count.return_value = 5

        indexer = RepositoryIndexer(
            config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader
        )
        repo = tmp_path / "repo"
        repo.mkdir()
        result = indexer.index_repository(
            str(repo),
            "ws",
            "proj",
            "main",
            "abc123",
            "alias1",
            retain_revisions=True,
        )

        assert result.chunk_count == 5
        coll_mgr.ensure_collection_exists.assert_called_once_with("alias1")
        coll_mgr.ensure_payload_indexes.assert_called_once_with("alias1")
        coll_mgr.create_versioned_collection.assert_not_called()
        coll_mgr.atomic_alias_swap.assert_not_called()
        branch_mgr.delete_revision_points.assert_called_once_with(
            "coll_v1", "main", "abc123"
        )

    def test_failed_retained_revision_is_removed_without_touching_other_revisions(
        self, tmp_path
    ):
        config = _mock_config()
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()
        loader.iter_repository_files.return_value = iter(["a.py"])
        loader.load_file_batch.side_effect = RuntimeError("embedding stopped")
        coll_mgr.resolve_alias.return_value = "coll_v1"
        branch_mgr.delete_revision_points.return_value = True

        indexer = RepositoryIndexer(
            config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader
        )
        repo = tmp_path / "repo"
        repo.mkdir()
        with pytest.raises(RuntimeError, match="embedding stopped"):
            indexer.index_repository(
                str(repo),
                "ws",
                "proj",
                "main",
                "abc123",
                "alias1",
                retain_revisions=True,
            )

        assert branch_mgr.delete_revision_points.call_count == 2
        coll_mgr.atomic_alias_swap.assert_not_called()

    def test_empty_repo_returns_stats(self):
        config = _mock_config()
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()
        loader.iter_repository_files.return_value = iter([])

        coll_mgr.create_versioned_collection.return_value = "coll_v2"
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

        coll_mgr.delete_collection.assert_called_with("coll_v2")
        assert result.document_count == 0

    def test_exceeds_file_limit(self):
        config = _mock_config(max_files_per_index=5)
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()

        fake_files = [f"f{i}.py" for i in range(10)]
        loader.iter_repository_files.return_value = iter(fake_files)

        coll_mgr.create_versioned_collection.return_value = "coll_v2"
        coll_mgr.alias_exists.return_value = False
        coll_mgr.collection_exists.return_value = False
        coll_mgr.resolve_alias.return_value = None

        indexer = RepositoryIndexer(config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader)

        with pytest.raises(ValueError, match="exceeds file limit"):
            indexer.index_repository("/repo", "ws", "proj", "main", "abc123", "alias1")

        coll_mgr.delete_collection.assert_called_with("coll_v2")

    def test_exceeds_chunk_limit_at_estimation(self):
        config = _mock_config(max_chunks_per_index=10)
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()

        fake_files = [f"f{i}.py" for i in range(5)]
        loader.iter_repository_files.side_effect = lambda *a, **kw: iter(fake_files)

        coll_mgr.create_versioned_collection.return_value = "coll_v2"
        coll_mgr.alias_exists.return_value = False
        coll_mgr.collection_exists.return_value = False
        coll_mgr.resolve_alias.return_value = None

        # Make estimation exceed limit: 100 estimated > 10 * 1.2 = 12
        loader.load_file_batch.return_value = [MagicMock()]
        splitter.split_documents.return_value = [MagicMock() for _ in range(100)]

        indexer = RepositoryIndexer(config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader)

        with pytest.raises(ValueError, match="estimated to exceed chunk limit"):
            indexer.index_repository("/repo", "ws", "proj", "main", "abc123", "alias1")

    def test_successful_indexing(self):
        config = _mock_config()
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()

        fake_files = ["a.py", "b.py"]
        loader.iter_repository_files.return_value = iter(fake_files)

        coll_mgr.create_versioned_collection.return_value = "coll_v2"
        coll_mgr.alias_exists.return_value = False
        coll_mgr.collection_exists.return_value = False
        coll_mgr.resolve_alias.return_value = None

        loader.load_file_batch.return_value = [MagicMock()]
        splitter.split_documents.return_value = [MagicMock(), MagicMock()]

        # After indexing, temp collection has points
        temp_info = MagicMock()
        temp_info.points_count = 10
        point_ops.client.get_collection.return_value = temp_info

        indexer = RepositoryIndexer(config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader)
        result = indexer.index_repository("/repo", "ws", "proj", "main", "abc123", "alias1")

        assert result.workspace == "ws"
        assert result.project == "proj"
        assert result.branch == "main"
        stats_mgr.store_metadata.assert_called_once()

    def test_preserves_other_branches(self):
        config = _mock_config()
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()

        loader.iter_repository_files.return_value = iter(["a.py"])

        coll_mgr.create_versioned_collection.return_value = "coll_v2"
        coll_mgr.alias_exists.return_value = True
        coll_mgr.collection_exists.return_value = True
        coll_mgr.resolve_alias.return_value = "coll_v1"

        loader.load_file_batch.return_value = [MagicMock()]
        splitter.split_documents.return_value = [MagicMock()]

        temp_info = MagicMock()
        temp_info.points_count = 5
        point_ops.client.get_collection.return_value = temp_info

        indexer = RepositoryIndexer(config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader)
        indexer.index_repository("/repo", "ws", "proj", "main", "abc123", "alias1")

        branch_mgr.stream_copy_points_to_collection.assert_called_once_with(
            "coll_v1", "coll_v2", "main", INSERT_BATCH_SIZE
        )

    def test_indexing_failure_cleans_up(self):
        config = _mock_config()
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()

        loader.iter_repository_files.return_value = iter(["a.py"])

        coll_mgr.create_versioned_collection.return_value = "coll_v2"
        coll_mgr.alias_exists.return_value = False
        coll_mgr.collection_exists.return_value = False
        coll_mgr.resolve_alias.return_value = None

        loader.load_file_batch.side_effect = RuntimeError("disk error")

        indexer = RepositoryIndexer(config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader)

        with pytest.raises(RuntimeError, match="disk error"):
            indexer.index_repository("/repo", "ws", "proj", "main", "abc123", "alias1")

        coll_mgr.delete_collection.assert_called_with("coll_v2")


# ─────────────────────────────────────────────────────────────
# _perform_atomic_swap
# ─────────────────────────────────────────────────────────────
class TestPerformAtomicSwap:

    def test_normal_swap(self):
        config = _mock_config()
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()

        coll_mgr.collection_exists.return_value = False
        coll_mgr.alias_exists.return_value = True
        coll_mgr.resolve_alias.return_value = "coll_v1"

        indexer = RepositoryIndexer(config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader)
        indexer._perform_atomic_swap("alias1", "coll_v2", old_collection_exists=True)

        coll_mgr.atomic_alias_swap.assert_called_once()
        coll_mgr.delete_collection.assert_called_with("coll_v1")

    def test_migration_from_direct_collection(self):
        config = _mock_config()
        coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader = _mock_components()

        coll_mgr.collection_exists.return_value = True
        coll_mgr.alias_exists.return_value = False
        coll_mgr.resolve_alias.return_value = None

        # First swap fails because alias name is taken by collection
        coll_mgr.atomic_alias_swap.side_effect = [
            Exception("already exists"),
            None,
        ]

        indexer = RepositoryIndexer(config, coll_mgr, branch_mgr, point_ops, stats_mgr, splitter, loader)
        indexer._perform_atomic_swap("alias1", "coll_v2", old_collection_exists=True)

        # Should delete the direct collection then retry
        coll_mgr.delete_collection.assert_called_with("alias1")


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

        stats_mgr.get_project_stats.return_value = MagicMock(spec=IndexStats)
        return FileOperations(client, point_ops, coll_mgr, stats_mgr, splitter, loader)

    def test_update_files_success(self):
        ops = self._make_file_ops()
        ops.loader.load_specific_files.return_value = [MagicMock()]
        ops.splitter.split_documents.return_value = [MagicMock(), MagicMock()]
        ops.point_ops.process_and_upsert_chunks.return_value = (2, 0)

        result = ops.update_files(
            file_paths=["src/Foo.java"],
            repo_base="/repo",
            workspace="ws",
            project="proj",
            branch="main",
            commit="abc",
            collection_name="coll",
        )

        ops.client.delete.assert_called_once()
        ops.splitter.split_documents.assert_called_once()
        ops.point_ops.process_and_upsert_chunks.assert_called_once()

    def test_update_files_no_documents(self):
        ops = self._make_file_ops()
        ops.loader.load_specific_files.return_value = []

        result = ops.update_files(
            file_paths=["src/Missing.java"],
            repo_base="/repo",
            workspace="ws",
            project="proj",
            branch="main",
            commit="abc",
            collection_name="coll",
        )
        ops.splitter.split_documents.assert_not_called()

    def test_delete_files(self):
        ops = self._make_file_ops()

        result = ops.delete_files(
            file_paths=["src/Del.java"],
            workspace="ws",
            project="proj",
            branch="main",
            collection_name="coll",
        )
        ops.client.delete.assert_called_once()
