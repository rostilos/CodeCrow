"""
Tests for rag_pipeline.core.index_manager components —
CollectionManager, BranchManager, PointOperations, StatsManager, RAGIndexManager.
"""
import pytest
import uuid
from httpx import Headers
from qdrant_client.http.exceptions import (
    ResponseHandlingException,
    UnexpectedResponse,
)
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

    def test_ensure_collection_exists_accepts_concurrent_create(self):
        cm = self._make()
        created_collection = MagicMock()
        created_collection.name = "test_coll"
        missing = MagicMock()
        missing.collections = []
        present = MagicMock()
        present.collections = [created_collection]
        cm.client.get_collections.side_effect = [missing, present]
        cm.client.create_collection.side_effect = UnexpectedResponse(
            409,
            "Conflict",
            b'{"status":{"error":"collection already exists"}}',
            Headers(),
        )
        cm.alias_exists = MagicMock(return_value=False)
        cm._ensure_payload_indexes = MagicMock()

        cm.ensure_collection_exists("test_coll")

        cm._ensure_payload_indexes.assert_called_once_with("test_coll")

    def test_ensure_collection_exists_does_not_hide_other_conflicts(self):
        cm = self._make()
        missing = MagicMock()
        missing.collections = []
        cm.client.get_collections.side_effect = [missing, missing]
        conflict = UnexpectedResponse(
            409,
            "Conflict",
            b'{"status":{"error":"unrelated conflict"}}',
            Headers(),
        )
        cm.client.create_collection.side_effect = conflict
        cm.alias_exists = MagicMock(return_value=False)

        with pytest.raises(UnexpectedResponse) as exc_info:
            cm.ensure_collection_exists("test_coll")

        assert exc_info.value is conflict

    def test_ensure_collection_exists_is_alias(self):
        cm = self._make()
        cm.alias_exists = MagicMock(return_value=True)

        cm.ensure_collection_exists("alias_name")
        cm.client.create_collection.assert_not_called()

    def test_create_pending_collection(self):
        cm = self._make()
        cm.client.create_collection = MagicMock()

        name = cm.create_pending_collection("base_name")
        assert name.startswith("base_name_pending_")
        cm.client.create_collection.assert_called_once()

    def test_create_pending_collection_uses_unique_names(self):
        cm = self._make()
        cm._ensure_payload_indexes = MagicMock()

        first = cm.create_pending_collection("base_name")
        second = cm.create_pending_collection("base_name")

        assert first != second

    def test_atomic_assign_aliases_replaces_all_requested_aliases_in_one_call(self):
        cm = self._make()
        old = MagicMock()
        old.alias_name = "codecrow_ws__project"
        old.collection_name = "old-primary"
        cm.client.get_aliases.return_value.aliases = [old]

        cm.atomic_assign_aliases({
            "codecrow_ws__project": "new-generation",
            "codecrow_ws__project__develop": "new-generation",
        })

        cm.client.update_collection_aliases.assert_called_once()
        operations = cm.client.update_collection_aliases.call_args.kwargs[
            "change_aliases_operations"
        ]
        assert len(operations) == 3

    def test_payload_index_failure_does_not_skip_remaining_indexes(
        self,
        caplog,
    ):
        cm = self._make()
        cm.client.create_payload_index.side_effect = [
            RuntimeError("path index already exists"),
            *([True] * 12),
        ]

        cm._ensure_payload_indexes("test_coll")

        assert cm.client.create_payload_index.call_count == 13
        warnings = [
            record for record in caplog.records
            if record.levelname == "WARNING"
        ]
        assert len(warnings) == 1
        assert "failed for 1 field(s)" in warnings[0].getMessage()

    def test_payload_index_failures_emit_one_aggregate_warning(self, caplog):
        cm = self._make()
        cm.client.create_payload_index.side_effect = RuntimeError("unsupported")

        assert cm._ensure_payload_indexes("test_coll") is False

        assert cm.client.create_payload_index.call_count == 13
        warnings = [
            record for record in caplog.records
            if record.levelname == "WARNING"
        ]
        assert len(warnings) == 1
        assert "failed for 13 field(s)" in warnings[0].getMessage()

    def test_payload_index_repair_defers_when_schema_inspection_times_out(self):
        cm = self._make()
        cm.client.get_collection.side_effect = ResponseHandlingException(
            TimeoutError("timed out")
        )

        assert cm._ensure_payload_indexes("test_coll") is False
        cm.client.create_payload_index.assert_not_called()

    def test_payload_index_repair_stops_after_transport_failure(self):
        cm = self._make()
        cm.client.create_payload_index.side_effect = (
            ResponseHandlingException(TimeoutError("timed out"))
        )

        assert cm._ensure_payload_indexes("test_coll") is False
        assert cm.client.create_payload_index.call_count == 1

    def test_existing_collection_repairs_payload_indexes_once(self):
        cm = self._make()
        collection = MagicMock(name="test_coll")
        collection.name = "test_coll"
        cm.client.get_collections.return_value.collections = [collection]
        cm.alias_exists = MagicMock(return_value=False)

        cm.ensure_collection_exists("test_coll")
        cm.ensure_collection_exists("test_coll")

        assert cm.client.create_payload_index.call_count == 13

    def test_payload_index_repair_only_creates_missing_schemas(self):
        from qdrant_client.models import PayloadSchemaType

        cm = self._make()
        existing = MagicMock()
        existing.data_type = PayloadSchemaType.KEYWORD
        cm.client.get_collection.return_value.payload_schema = {
            "workspace": existing,
        }

        cm._ensure_payload_indexes("test_coll")

        fields = {
            call.kwargs["field_name"]
            for call in cm.client.create_payload_index.call_args_list
        }
        assert "workspace" not in fields
        assert "project" in fields

    def test_pr_payload_indexes_use_filter_compatible_schemas(self):
        from qdrant_client.models import PayloadSchemaType

        cm = self._make()
        cm._ensure_payload_indexes("test_coll")
        schemas = {
            call.kwargs["field_name"]: call.kwargs["field_schema"]
            for call in cm.client.create_payload_index.call_args_list
        }

        assert schemas["workspace"] is PayloadSchemaType.KEYWORD
        assert schemas["project"] is PayloadSchemaType.KEYWORD
        assert schemas["pr"] is PayloadSchemaType.BOOL
        assert schemas["pr_number"] is PayloadSchemaType.INTEGER

    def test_delete_collection(self):
        cm = self._make()
        result = cm.delete_collection("test_coll")
        assert result is True
        cm.client.delete_collection.assert_called_once_with("test_coll")

    def test_delete_then_recreate_repairs_payload_indexes_again(self):
        cm = self._make()
        cm._payload_indexes_ensured.add("test_coll")

        assert cm.delete_collection("test_coll") is True

        collection = MagicMock()
        collection.name = "test_coll"
        cm.client.get_collections.return_value.collections = [collection]
        cm.alias_exists = MagicMock(return_value=False)
        cm.ensure_collection_exists("test_coll")

        assert cm.client.create_payload_index.call_count == 13

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
        assert points[0].vector == pytest.approx([0.1, 0.2, 0.3])


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

    @pytest.fixture(autouse=True)
    def avoid_network_tokenizer_download(self, monkeypatch):
        # Constructing LlamaIndex's default SentenceSplitter may lazily fetch
        # the tiktoken vocabulary. These manager unit tests mock embeddings and
        # Qdrant, so they must remain hermetic as well.
        from rag_pipeline.core.index_manager.manager import Settings

        monkeypatch.setattr(Settings, "_node_parser", MagicMock(), raising=False)


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
        MockQdrant.assert_called_once_with(
            url="http://localhost:6333",
            api_key=None,
            timeout=30,
        )

    def test_close_releases_coordinator_and_qdrant_client(self):
        from rag_pipeline.core.index_manager.manager import RAGIndexManager

        manager = object.__new__(RAGIndexManager)
        manager._mutation_coordinator = MagicMock()
        manager.qdrant_client = MagicMock()

        manager.close()

        manager._mutation_coordinator.close.assert_called_once_with()
        manager.qdrant_client.close.assert_called_once_with()

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

    def test_branch_operator_alias_is_readable_and_branch_specific(self):
        from rag_pipeline.core.index_manager.manager import RAGIndexManager

        manager = object.__new__(RAGIndexManager)
        manager.config = MagicMock(qdrant_collection_prefix="rag")
        manager.config.qdrant_collection_prefix = "rag"

        assert manager._get_branch_operator_alias(
            "Workspace", "Project", "develop"
        ) == "rag_workspace__project__develop"
        assert manager._get_branch_operator_alias(
            "Workspace", "Project", "release/1.2"
        ).startswith("rag_workspace__project__release_1_2_")

    def test_branch_operator_alias_preserves_case_sensitive_identity(self):
        from rag_pipeline.core.index_manager.manager import RAGIndexManager

        manager = object.__new__(RAGIndexManager)
        manager.config = MagicMock(qdrant_collection_prefix="rag")
        manager.config.qdrant_collection_prefix = "rag"

        lowercase = manager._get_branch_operator_alias(
            "Workspace", "Project", "feature"
        )
        uppercase = manager._get_branch_operator_alias(
            "Workspace", "Project", "Feature"
        )

        assert lowercase != uppercase
        assert lowercase == "rag_workspace__project__feature"
        assert uppercase.startswith("rag_workspace__project__feature_")

    def test_readable_alias_publication_requires_immutable_generation_target(self):
        from rag_pipeline.core.index_manager.manager import RAGIndexManager

        manager = object.__new__(RAGIndexManager)

        with pytest.raises(
            ValueError,
            match="require an immutable collection target",
        ):
            manager.index_repository(
                repo_path="/tmp/repository",
                workspace="workspace",
                project="project",
                branch="main",
                commit="abc123",
                publish_branch_alias=True,
            )

    @patch(
        "rag_pipeline.core.index_manager.manager.verify_repository_source_tree"
    )
    def test_exact_snapshot_forwards_resolved_prior_generation_for_vector_reuse(
        self,
        verify_source_tree,
    ):
        from rag_pipeline.core.index_manager.manager import RAGIndexManager

        manager = object.__new__(RAGIndexManager)
        manager._collection_manager = MagicMock()
        manager._collection_manager.resolve_collection_target.return_value = (
            "prior-generation-physical"
        )
        manager._indexer = MagicMock()
        manager._indexer.index_repository.return_value = MagicMock()
        manager._mutation_coordinator = MagicMock()
        lease = MagicMock(token="operation-token")
        lease.assert_owned = MagicMock()
        manager._mutation_coordinator.acquire.return_value.__enter__.return_value = (
            lease
        )
        manager._publication_aliases = MagicMock(return_value=[])
        manager._publication_scope = MagicMock(return_value=None)
        source_tree = MagicMock(tree_sha256="f" * 64)
        verify_source_tree.return_value = source_tree

        manager.index_repository(
            repo_path="/tmp/repository",
            workspace="workspace",
            project="project",
            branch="main",
            commit="a" * 40,
            source_tree_sha256="f" * 64,
            collection_target="new-generation",
            reuse_collection_target="prior-generation",
        )

        manager._collection_manager.resolve_collection_target.assert_called_once_with(
            "prior-generation"
        )
        assert manager._indexer.index_repository.call_args.kwargs[
            "reuse_collection_name"
        ] == "prior-generation-physical"

    @patch("rag_pipeline.core.index_manager.manager.create_embedding_model")
    @patch("rag_pipeline.core.index_manager.manager.get_embedding_model_info")
    @patch("rag_pipeline.core.index_manager.manager.QdrantClient")
    def test_delete_branch_delegates(self, MockQdrant, mock_info, mock_create):
        from rag_pipeline.core.index_manager.manager import RAGIndexManager

        mock_info.return_value = {"provider": "ollama", "type": "local", "model": "nomic", "embedding_dim": 768}
        mock_create.return_value = self._make_embed_mock()

        mgr = RAGIndexManager(self._mock_config())
        mgr._mutation_coordinator.enabled = False
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
