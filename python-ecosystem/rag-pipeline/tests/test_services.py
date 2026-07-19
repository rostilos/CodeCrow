"""
Tests for rag_pipeline.services — RAGQueryBase, SemanticSearchMixin,
DeterministicContextMixin, PRContextMixin.
"""
import pytest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, PropertyMock


def _mock_config(**overrides):
    """Create a mock RAGConfig for service tests."""
    cfg = MagicMock()
    cfg.qdrant_url = overrides.get("qdrant_url", "http://localhost:6333")
    cfg.qdrant_api_key = overrides.get("qdrant_api_key", None)
    cfg.qdrant_collection_prefix = overrides.get("qdrant_collection_prefix", "rag")
    cfg.embedding_provider = overrides.get("embedding_provider", "ollama")
    cfg.embedding_dim = overrides.get("embedding_dim", 768)
    cfg.embedding_supports_instructions = overrides.get("embedding_supports_instructions", False)
    cfg.fallback_branches = overrides.get("fallback_branches", ["main", "master"])
    cfg.ollama_model = "nomic-embed-text"
    cfg.ollama_base_url = "http://localhost:11434"
    cfg.openrouter_api_key = "sk-test"
    cfg.openrouter_model = "openai/text-embedding-3-small"
    cfg.openrouter_base_url = "https://openrouter.ai/api/v1"
    return cfg


# ─────────────────────────────────────────────────────────────
# RAGQueryBase
# ─────────────────────────────────────────────────────────────
class TestRAGQueryBase:

    @patch("rag_pipeline.services.base.create_embedding_model")
    @patch("rag_pipeline.services.base.get_embedding_model_info")
    @patch("rag_pipeline.services.base.QdrantClient")
    def test_init(self, MockQdrant, mock_info, mock_create):
        from rag_pipeline.services.base import RAGQueryBase

        mock_info.return_value = {"provider": "ollama", "type": "local"}
        mock_create.return_value = MagicMock()

        config = _mock_config()
        base = RAGQueryBase(config)

        assert base.config is config
        MockQdrant.assert_called_once_with(url="http://localhost:6333", api_key=None)
        assert base.qdrant_client is not None
        assert base.embed_model is not None

    @patch("rag_pipeline.services.base.create_embedding_model")
    @patch("rag_pipeline.services.base.get_embedding_model_info")
    @patch("rag_pipeline.services.base.QdrantClient")
    def test_collection_or_alias_exists_true(self, MockQdrant, mock_info, mock_create):
        from rag_pipeline.services.base import RAGQueryBase

        mock_info.return_value = {"provider": "ollama", "type": "local"}
        mock_create.return_value = MagicMock()

        config = _mock_config()
        base = RAGQueryBase(config)

        mock_collection = MagicMock()
        mock_collection.name = "test_collection"
        base.qdrant_client.get_collections.return_value.collections = [mock_collection]
        base.qdrant_client.get_aliases.return_value.aliases = []

        assert base._collection_or_alias_exists("test_collection") is True

    @patch("rag_pipeline.services.base.create_embedding_model")
    @patch("rag_pipeline.services.base.get_embedding_model_info")
    @patch("rag_pipeline.services.base.QdrantClient")
    def test_collection_or_alias_exists_false(self, MockQdrant, mock_info, mock_create):
        from rag_pipeline.services.base import RAGQueryBase

        mock_info.return_value = {"provider": "ollama", "type": "local"}
        mock_create.return_value = MagicMock()

        config = _mock_config()
        base = RAGQueryBase(config)

        base.qdrant_client.get_collections.return_value.collections = []
        base.qdrant_client.get_aliases.return_value.aliases = []

        assert base._collection_or_alias_exists("nonexistent") is False

    @patch("rag_pipeline.services.base.create_embedding_model")
    @patch("rag_pipeline.services.base.get_embedding_model_info")
    @patch("rag_pipeline.services.base.QdrantClient")
    def test_get_project_collection_name(self, MockQdrant, mock_info, mock_create):
        from rag_pipeline.services.base import RAGQueryBase

        mock_info.return_value = {"provider": "ollama", "type": "local"}
        mock_create.return_value = MagicMock()

        config = _mock_config(qdrant_collection_prefix="rag")
        base = RAGQueryBase(config)

        name = base._get_project_collection_name("workspace1", "project1")
        assert name.startswith("rag_")

    @patch("rag_pipeline.services.base.create_embedding_model")
    @patch("rag_pipeline.services.base.get_embedding_model_info")
    @patch("rag_pipeline.services.base.QdrantClient")
    def test_get_fallback_branch(self, MockQdrant, mock_info, mock_create):
        from rag_pipeline.services.base import RAGQueryBase

        mock_info.return_value = {"provider": "ollama", "type": "local"}
        mock_create.return_value = MagicMock()

        config = _mock_config(fallback_branches=["main", "master"])
        base = RAGQueryBase(config)

        # Collection exists
        mock_coll = MagicMock()
        mock_coll.name = "rag_workspace1__project1"
        base.qdrant_client.get_collections.return_value.collections = [mock_coll]
        base.qdrant_client.get_aliases.return_value.aliases = []

        # main has data
        mock_count = MagicMock()
        mock_count.count = 100
        base.qdrant_client.count.return_value = mock_count

        result = base._get_fallback_branch("workspace1", "project1", "feature/xyz")
        assert result == "main"

    @patch("rag_pipeline.services.base.create_embedding_model")
    @patch("rag_pipeline.services.base.get_embedding_model_info")
    @patch("rag_pipeline.services.base.QdrantClient")
    def test_get_fallback_branch_no_collection(self, MockQdrant, mock_info, mock_create):
        from rag_pipeline.services.base import RAGQueryBase

        mock_info.return_value = {"provider": "ollama", "type": "local"}
        mock_create.return_value = MagicMock()

        config = _mock_config()
        base = RAGQueryBase(config)

        base.qdrant_client.get_collections.return_value.collections = []
        base.qdrant_client.get_aliases.return_value.aliases = []

        result = base._get_fallback_branch("workspace1", "project1", "feature/xyz")
        assert result is None


# ─────────────────────────────────────────────────────────────
# SemanticSearchMixin._dedupe_by_branch_priority
# ─────────────────────────────────────────────────────────────
class TestSemanticSearchDedup:

    def test_dedupe_empty_results(self):
        from rag_pipeline.services.semantic_search import SemanticSearchMixin

        mixin = SemanticSearchMixin()
        result = mixin._dedupe_by_branch_priority([], "feature")
        assert result == []

    def test_dedupe_prefers_target_branch(self):
        from rag_pipeline.services.semantic_search import SemanticSearchMixin

        mixin = SemanticSearchMixin()
        results = [
            {"text": "code A", "score": 0.8, "metadata": {"path": "a.py", "branch": "main"}},
            {"text": "code A new", "score": 0.9, "metadata": {"path": "a.py", "branch": "feature"}},
        ]
        deduped = mixin._dedupe_by_branch_priority(results, "feature")
        # Should keep feature branch version for same path
        feature_results = [r for r in deduped if r["metadata"]["branch"] == "feature"]
        assert len(feature_results) >= 1


class TestSemanticSearchExecutionBinding:

    def test_exact_search_rejects_pr_overlays_from_other_executions(self):
        from rag_pipeline.models.snapshot import ContextSnapshotV1
        from rag_pipeline.services.semantic_search import SemanticSearchMixin

        snapshot = ContextSnapshotV1(
            base_sha="a" * 40,
            head_sha="b" * 40,
            merge_base_sha="c" * 40,
        )
        common = {
            "path": "src/dependency.py",
            "branch": "feature",
            "snapshot_sha": snapshot.head_sha,
            "parser_version": snapshot.parser_version,
            "chunker_version": snapshot.chunker_version,
            "embedding_version": snapshot.embedding_version,
        }

        def node(text, metadata):
            return SimpleNamespace(
                score=0.9,
                node=SimpleNamespace(text=text, metadata=metadata),
            )

        retriever = MagicMock()
        retriever.retrieve.return_value = [
            node("regular exact index", dict(common)),
            node(
                "current overlay",
                {**common, "pr": True, "execution_id": "execution-1"},
            ),
            node(
                "foreign overlay",
                {**common, "pr": True, "execution_id": "execution-2"},
            ),
        ]
        index = MagicMock()
        index.as_retriever.return_value = retriever

        class Service(SemanticSearchMixin):
            _supports_instructions = False

            @staticmethod
            def _get_project_collection_name(_workspace, _project):
                return "collection"

            @staticmethod
            def _collection_or_alias_exists(_collection):
                return True

            @staticmethod
            def _get_or_create_index(_collection):
                return index

        results = Service().semantic_search(
            query="dependency",
            workspace="ws",
            project="project",
            branch="feature",
            revision=snapshot.head_sha,
            snapshot=snapshot,
            execution_id="execution-1",
        )

        assert [result["text"] for result in results] == [
            "regular exact index",
            "current overlay",
        ]


# ─────────────────────────────────────────────────────────────
# PRContextMixin — _infer_primary_ecosystem (module-level function)
# ─────────────────────────────────────────────────────────────
class TestInferPrimaryEcosystem:

    def test_python_ecosystem(self):
        from rag_pipeline.services.pr_context import _infer_primary_ecosystem

        files = ["src/main.py", "src/utils.py", "tests/test_main.py"]
        result = _infer_primary_ecosystem(files)
        assert result == "python"

    def test_mixed_ecosystem_returns_none(self):
        from rag_pipeline.services.pr_context import _infer_primary_ecosystem

        files = ["Main.java", "app.py", "index.ts"]
        result = _infer_primary_ecosystem(files)
        # Mixed — no dominant ecosystem (< 70%)
        assert result is None

    def test_jvm_ecosystem(self):
        from rag_pipeline.services.pr_context import _infer_primary_ecosystem

        files = ["src/Main.java", "src/Service.java", "src/Repo.java", "build.gradle"]
        result = _infer_primary_ecosystem(files)
        assert result == "jvm"

    def test_empty_files(self):
        from rag_pipeline.services.pr_context import _infer_primary_ecosystem

        assert _infer_primary_ecosystem([]) is None
