"""
Tests for rag_pipeline.core.index_manager components —
CollectionManager, BranchManager, PointOperations, StatsManager, RAGIndexManager.
"""
import pytest
import uuid
from unittest.mock import patch, MagicMock, PropertyMock
from datetime import datetime


# ─────────────────────────────────────────────────────────────
# CollectionManager
# ─────────────────────────────────────────────────────────────
class TestCollectionManager:

    def _make(self, client=None, dim=768):
        from rag_pipeline.core.index_manager.collection_manager import CollectionManager
        return CollectionManager(client or MagicMock(), dim)

    def test_init(self):
        mock_client = MagicMock()
        cm = self._make(mock_client, 1024)
        assert cm.client is mock_client
        assert cm.embedding_dim == 1024

    def test_ensure_collection_exists_creates_new(self):
        cm = self._make()
        cm.client.get_collections.return_value.collections = []
        cm.alias_exists = MagicMock(return_value=False)

        cm.ensure_collection_exists("test_coll")
        cm.client.create_collection.assert_called_once()

    def test_ensure_collection_exists_already_exists(self):
        cm = self._make()
        mock_coll = MagicMock()
        mock_coll.name = "test_coll"
        cm.client.get_collections.return_value.collections = [mock_coll]
        cm.alias_exists = MagicMock(return_value=False)

        cm.ensure_collection_exists("test_coll")
        cm.client.create_collection.assert_not_called()

    def test_ensure_collection_exists_is_alias(self):
        cm = self._make()
        cm.alias_exists = MagicMock(return_value=True)

        cm.ensure_collection_exists("alias_name")
        cm.client.create_collection.assert_not_called()

    def test_create_versioned_collection(self):
        cm = self._make()
        cm.client.create_collection = MagicMock()

        name = cm.create_versioned_collection("base_name")
        assert name.startswith("base_name_v")
        cm.client.create_collection.assert_called_once()

    def test_delete_collection(self):
        cm = self._make()
        result = cm.delete_collection("test_coll")
        assert result is True
        cm.client.delete_collection.assert_called_once_with("test_coll")

    def test_delete_collection_failure(self):
        cm = self._make()
        cm.client.delete_collection.side_effect = Exception("fail")
        result = cm.delete_collection("test_coll")
        assert result is False

    def test_collection_exists_true(self):
        cm = self._make()
        mock_coll = MagicMock()
        mock_coll.name = "my_coll"
        cm.client.get_collections.return_value.collections = [mock_coll]
        cm.alias_exists = MagicMock(return_value=False)
        assert cm.collection_exists("my_coll") is True

    def test_collection_exists_via_alias(self):
        cm = self._make()
        cm.client.get_collections.return_value.collections = []
        cm.alias_exists = MagicMock(return_value=True)
        assert cm.collection_exists("alias_name") is True

    def test_collection_exists_false(self):
        cm = self._make()
        cm.client.get_collections.return_value.collections = []
        cm.alias_exists = MagicMock(return_value=False)
        assert cm.collection_exists("nonexistent") is False

    def test_get_collection_names(self):
        cm = self._make()
        c1, c2 = MagicMock(), MagicMock()
        c1.name = "coll_a"
        c2.name = "coll_b"
        cm.client.get_collections.return_value.collections = [c1, c2]
        assert cm.get_collection_names() == ["coll_a", "coll_b"]

    def test_payload_indexes_are_ensured_independently_for_old_collections(self):
        cm = self._make()
        cm.client.create_payload_index.side_effect = [
            RuntimeError("path already exists"),
            None,
            None,
        ]

        cm._ensure_payload_indexes("coll")

        assert [
            call.kwargs["field_name"]
            for call in cm.client.create_payload_index.call_args_list
        ] == ["path", "branch", "snapshot_sha"]


# ─────────────────────────────────────────────────────────────
# BranchManager
# ─────────────────────────────────────────────────────────────
class TestBranchManager:

    def _make(self, client=None):
        from rag_pipeline.core.index_manager.branch_manager import BranchManager
        return BranchManager(client or MagicMock())

    def test_init(self):
        mock_client = MagicMock()
        bm = self._make(mock_client)
        assert bm.client is mock_client

    def test_delete_branch_points_success(self):
        bm = self._make()
        result = bm.delete_branch_points("coll", "feature/xyz")
        assert result is True
        bm.client.delete.assert_called_once()

    def test_delete_branch_points_failure(self):
        bm = self._make()
        bm.client.delete.side_effect = Exception("fail")
        result = bm.delete_branch_points("coll", "feature/xyz")
        assert result is False

    def test_get_branch_point_count(self):
        bm = self._make()
        bm.client.count.return_value.count = 42
        count = bm.get_branch_point_count("coll", "main")
        assert count == 42

    def test_get_branch_point_count_error(self):
        bm = self._make()
        bm.client.count.side_effect = Exception("fail")
        count = bm.get_branch_point_count("coll", "main")
        assert count == 0

    def test_revision_count_is_bound_to_branch_and_commit(self):
        bm = self._make()
        bm.client.count.return_value.count = 17

        assert bm.get_revision_point_count("coll", "main", "a" * 40) == 17

        count_filter = bm.client.count.call_args.kwargs["count_filter"]
        assert [condition.key for condition in count_filter.must] == [
            "branch",
            "snapshot_sha",
        ]

    def test_delete_revision_does_not_delete_whole_branch(self):
        bm = self._make()

        assert bm.delete_revision_points("coll", "main", "a" * 40) is True

        selector = bm.client.delete.call_args.kwargs["points_selector"]
        assert [condition.key for condition in selector.must] == [
            "branch",
            "snapshot_sha",
        ]
        assert bm.client.delete.call_args.kwargs["wait"] is True


# ─────────────────────────────────────────────────────────────
# PointOperations
# ─────────────────────────────────────────────────────────────
class TestPointOperations:

    def _make(self, client=None, embed_model=None, batch_size=50):
        from rag_pipeline.core.index_manager.point_operations import PointOperations
        return PointOperations(
            client or MagicMock(),
            embed_model or MagicMock(),
            batch_size=batch_size,
        )

    def test_init(self):
        mock_client = MagicMock()
        mock_embed = MagicMock()
        po = self._make(mock_client, mock_embed, 25)
        assert po.client is mock_client
        assert po.embed_model is mock_embed
        assert po.batch_size == 25

    def test_generate_point_id_deterministic(self):
        from rag_pipeline.core.index_manager.point_operations import PointOperations
        id1 = PointOperations.generate_point_id("ws", "proj", "main", "a.py", 0)
        id2 = PointOperations.generate_point_id("ws", "proj", "main", "a.py", 0)
        assert id1 == id2

    def test_generate_point_id_different_for_different_input(self):
        from rag_pipeline.core.index_manager.point_operations import PointOperations
        id1 = PointOperations.generate_point_id("ws", "proj", "main", "a.py", 0)
        id2 = PointOperations.generate_point_id("ws", "proj", "main", "b.py", 0)
        assert id1 != id2

    def test_generate_point_id_is_uuid(self):
        from rag_pipeline.core.index_manager.point_operations import PointOperations
        result = PointOperations.generate_point_id("ws", "proj", "main", "a.py", 0)
        # Should be a valid UUID string
        uuid.UUID(result)

    def test_generate_point_id_is_revision_safe(self):
        from rag_pipeline.core.index_manager.point_operations import PointOperations

        first = PointOperations.generate_point_id(
            "ws", "proj", "main", "a.py", 0,
            revision="a" * 40,
            content_digest="1" * 64,
        )
        second = PointOperations.generate_point_id(
            "ws", "proj", "main", "a.py", 0,
            revision="b" * 40,
            content_digest="1" * 64,
        )

        assert first != second

    def test_prepare_chunks_for_embedding(self):
        po = self._make()

        mock_chunk = MagicMock()
        mock_chunk.metadata = {"path": "src/main.py"}
        mock_chunk.text = "def hello(): pass"

        result = po.prepare_chunks_for_embedding(
            [mock_chunk], "ws", "proj", "main"
        )
        assert len(result) == 1
        point_id, chunk = result[0]
        assert isinstance(point_id, str)
        assert chunk is mock_chunk

    def test_prepare_chunks_records_exact_content_identity(self):
        from rag_pipeline.core.index_manager.point_operations import PointOperations

        po = self._make()
        mock_chunk = MagicMock()
        mock_chunk.metadata = {"path": "src/main.py", "commit": "a" * 40}
        mock_chunk.text = "def hello(): pass"

        point_id, chunk = po.prepare_chunks_for_embedding(
            [mock_chunk], "ws", "proj", "main"
        )[0]

        assert chunk.metadata["snapshot_sha"] == "a" * 40
        assert len(chunk.metadata["content_digest"]) == 64
        assert chunk.metadata["context_identity_version"] == 1
        assert point_id != PointOperations.generate_point_id(
            "ws", "proj", "main", "src/main.py", 0
        )

    def test_embed_and_create_points_empty(self):
        po = self._make()
        result = po.embed_and_create_points([])
        assert result == []

    def test_embed_and_create_points(self):
        mock_embed = MagicMock()
        mock_embed.get_text_embedding_batch.return_value = [[0.1, 0.2, 0.3]]
        po = self._make(embed_model=mock_embed)

        mock_chunk = MagicMock()
        mock_chunk.text = "def hello(): pass"
        mock_chunk.metadata = {"path": "a.py"}

        points = po.embed_and_create_points([("point-id-1", mock_chunk)])
        assert len(points) == 1
        assert points[0].id == "point-id-1"
        assert points[0].vector == [0.1, 0.2, 0.3]


# ─────────────────────────────────────────────────────────────
# StatsManager
# ─────────────────────────────────────────────────────────────
class TestStatsManager:

    def _make(self, client=None, prefix="rag"):
        from rag_pipeline.core.index_manager.stats_manager import StatsManager
        return StatsManager(client or MagicMock(), prefix)

    def test_init(self):
        mock_client = MagicMock()
        sm = self._make(mock_client, "rag")
        assert sm.client is mock_client
        assert sm.collection_prefix == "rag"

    def test_get_branch_stats(self):
        sm = self._make()
        sm.client.count.return_value.count = 42

        stats = sm.get_branch_stats("ws", "proj", "main", "rag_ws__proj")
        assert stats.chunk_count == 42
        assert stats.workspace == "ws"
        assert stats.project == "proj"
        assert stats.branch == "main"

    def test_get_branch_stats_error(self):
        sm = self._make()
        sm.client.count.side_effect = Exception("fail")

        stats = sm.get_branch_stats("ws", "proj", "main", "rag_ws__proj")
        assert stats.chunk_count == 0

    def test_get_project_stats(self):
        sm = self._make()
        sm.client.get_collection.return_value.points_count = 100

        stats = sm.get_project_stats("ws", "proj", "rag_ws__proj")
        assert stats.chunk_count == 100

    def test_get_project_stats_error(self):
        sm = self._make()
        sm.client.get_collection.side_effect = Exception("fail")

        stats = sm.get_project_stats("ws", "proj", "rag_ws__proj")
        assert stats.chunk_count == 0

    def test_list_all_indices(self):
        sm = self._make(prefix="rag")

        c1 = MagicMock()
        c1.name = "rag_workspace1__project1"
        sm.client.get_collections.return_value.collections = [c1]
        sm.client.get_collection.return_value.points_count = 50

        indices = sm.list_all_indices(alias_checker=lambda x: False)
        assert len(indices) == 1
        assert indices[0].workspace == "workspace1"
        assert indices[0].project == "project1"


# ─────────────────────────────────────────────────────────────
# RAGIndexManager
# ─────────────────────────────────────────────────────────────
class TestRAGIndexManager:

    def _mock_config(self):
        mock_config = MagicMock()
        mock_config.qdrant_url = "http://localhost:6333"
        mock_config.qdrant_api_key = None
        mock_config.embedding_dim = 768
        mock_config.qdrant_collection_prefix = "rag"
        mock_config.chunk_size = 1500
        mock_config.chunk_overlap = 200
        return mock_config

    def _make_embed_mock(self):
        """Create a mock that passes LlamaIndex's isinstance(embed_model, BaseEmbedding) check."""
        from llama_index.core.base.embeddings.base import BaseEmbedding
        mock_embed = MagicMock(spec=BaseEmbedding)
        return mock_embed

    @patch("rag_pipeline.core.index_manager.manager.create_embedding_model")
    @patch("rag_pipeline.core.index_manager.manager.get_embedding_model_info")
    @patch("rag_pipeline.core.index_manager.manager.QdrantClient")
    def test_init(self, MockQdrant, mock_info, mock_create):
        from rag_pipeline.core.index_manager.manager import RAGIndexManager

        mock_info.return_value = {"provider": "ollama", "type": "local", "model": "nomic", "embedding_dim": 768}
        mock_embed = self._make_embed_mock()
        mock_create.return_value = mock_embed

        mgr = RAGIndexManager(self._mock_config())
        assert mgr.qdrant_client is not None
        assert mgr.embed_model is mock_embed

    @patch("rag_pipeline.core.index_manager.manager.create_embedding_model")
    @patch("rag_pipeline.core.index_manager.manager.get_embedding_model_info")
    @patch("rag_pipeline.core.index_manager.manager.QdrantClient")
    def test_get_project_collection_name(self, MockQdrant, mock_info, mock_create):
        from rag_pipeline.core.index_manager.manager import RAGIndexManager

        mock_info.return_value = {"provider": "ollama", "type": "local", "model": "nomic", "embedding_dim": 768}
        mock_create.return_value = self._make_embed_mock()

        mgr = RAGIndexManager(self._mock_config())
        name = mgr._get_project_collection_name("workspace", "project")
        assert name.startswith("rag_")
        assert "workspace" in name

    @patch("rag_pipeline.core.index_manager.manager.create_embedding_model")
    @patch("rag_pipeline.core.index_manager.manager.get_embedding_model_info")
    @patch("rag_pipeline.core.index_manager.manager.QdrantClient")
    def test_delete_branch_delegates(self, MockQdrant, mock_info, mock_create):
        from rag_pipeline.core.index_manager.manager import RAGIndexManager

        mock_info.return_value = {"provider": "ollama", "type": "local", "model": "nomic", "embedding_dim": 768}
        mock_create.return_value = self._make_embed_mock()

        mgr = RAGIndexManager(self._mock_config())
        mgr._collection_manager = MagicMock()
        mgr._collection_manager.collection_exists.return_value = True
        mgr._branch_manager = MagicMock()
        mgr._branch_manager.delete_branch_points.return_value = True

        result = mgr.delete_branch("workspace", "project", "feature/xyz")
        assert result is True

    @patch("rag_pipeline.core.index_manager.manager.create_embedding_model")
    @patch("rag_pipeline.core.index_manager.manager.get_embedding_model_info")
    @patch("rag_pipeline.core.index_manager.manager.QdrantClient")
    def test_get_branch_point_count(self, MockQdrant, mock_info, mock_create):
        from rag_pipeline.core.index_manager.manager import RAGIndexManager

        mock_info.return_value = {"provider": "ollama", "type": "local", "model": "nomic", "embedding_dim": 768}
        mock_create.return_value = self._make_embed_mock()

        mgr = RAGIndexManager(self._mock_config())
        mgr._collection_manager = MagicMock()
        mgr._collection_manager.collection_exists.return_value = True
        mgr._collection_manager.alias_exists.return_value = False
        mgr._branch_manager = MagicMock()
        mgr._branch_manager.get_branch_point_count.return_value = 42

        count = mgr.get_branch_point_count("workspace", "project", "main")
        assert count == 42
