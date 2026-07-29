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
    def test_plugin_identity_requires_descriptor_but_accepts_older_build_content(
        self, MockQdrant, mock_info, mock_create
    ):
        from rag_pipeline.services.base import RAGQueryBase

        mock_info.return_value = {"provider": "ollama", "type": "local"}
        mock_create.return_value = MagicMock()
        catalog = MagicMock()
        catalog.registry.fingerprint_for.return_value = "sha256:descriptor"
        catalog.implementation_fingerprint.return_value = "sha256:implementation"
        base = RAGQueryBase(_mock_config(), plugin_catalog=catalog)
        current = {
            "plugin_ids": ["python", "fastapi"],
            "plugin_descriptor_fingerprint": "sha256:descriptor",
            "plugin_implementation_fingerprint": "sha256:implementation",
            "index_representation_fingerprint": (
                base.index_representation_fingerprint
            ),
        }

        assert base._plugin_identity_compatible(current) is True
        assert base._plugin_identity_compatible({
            **current,
            "plugin_implementation_fingerprint": "sha256:old",
            "index_representation_fingerprint": "sha256:older-host",
        }) is True
        assert base._plugin_identity_compatible({
            **current,
            "plugin_descriptor_fingerprint": "sha256:other-descriptor",
        }) is False
        catalog.registry.fingerprint_for.assert_called_once_with(
            ("python", "fastapi")
        )
        catalog.implementation_fingerprint.assert_not_called()


class TestRAGQueryService:

    @patch("rag_pipeline.services.base.create_embedding_model")
    @patch("rag_pipeline.services.base.get_embedding_model_info")
    @patch("rag_pipeline.services.base.QdrantClient")
    def test_facade_forwards_plugin_catalog(
        self, MockQdrant, mock_info, mock_create
    ):
        from rag_pipeline.services.query_service import RAGQueryService

        mock_info.return_value = {"provider": "ollama", "type": "local"}
        mock_create.return_value = MagicMock()
        catalog = MagicMock()

        service = RAGQueryService(_mock_config(), plugin_catalog=catalog)

        assert service.plugin_catalog is catalog
        assert service._plugin_identity_cache == {}


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

    def test_search_post_filters_boolean_plugin_points(self):
        from rag_pipeline.services.semantic_search import SemanticSearchMixin

        semantic_node = SimpleNamespace(
            node=SimpleNamespace(text="class Service {}", metadata={
                "path": "src/Service.java",
                "branch": "main",
            }),
            score=0.9,
        )
        architecture_node = SimpleNamespace(
            node=SimpleNamespace(text="opaque snapshot", metadata={
                "path": "__architecture__/spring",
                "branch": "main",
                "repository_snapshot": True,
            }),
            score=1.0,
        )
        retriever = MagicMock()
        retriever.retrieve.return_value = [architecture_node, semantic_node]
        index = MagicMock()
        index.as_retriever.return_value = retriever
        mixin = SemanticSearchMixin()
        mixin._get_project_collection_name = MagicMock(return_value="rag_ws__project")
        mixin._collection_or_alias_exists = MagicMock(return_value=True)
        mixin._get_or_create_index = MagicMock(return_value=index)
        mixin._require_compatible_branches = MagicMock()
        mixin._supports_instructions = False
        mixin._plugin_identity_compatible = MagicMock(return_value=True)

        results = mixin.semantic_search_multi_branch(
            query="service",
            workspace="ws",
            project="project",
            branches=["main"],
            top_k=2,
        )

        assert [result["metadata"]["path"] for result in results] == [
            "src/Service.java"
        ]
        assert index.as_retriever.call_args.kwargs["similarity_top_k"] == 8
        filters = index.as_retriever.call_args.kwargs["filters"].filters
        assert [metadata_filter.key for metadata_filter in filters] == ["branch"]

    def test_search_discards_incompatible_plugin_descriptor_before_result_limit(self):
        from rag_pipeline.services.semantic_search import SemanticSearchMixin

        stale_node = SimpleNamespace(
            node=SimpleNamespace(text="stale", metadata={
                "path": "src/Stale.py",
                "branch": "feature",
                "compatible": False,
            }),
            score=1.0,
        )
        current_node = SimpleNamespace(
            node=SimpleNamespace(text="current", metadata={
                "path": "src/Current.py",
                "branch": "main",
                "compatible": True,
            }),
            score=0.9,
        )
        retriever = MagicMock()
        retriever.retrieve.return_value = [stale_node, current_node]
        index = MagicMock()
        index.as_retriever.return_value = retriever
        mixin = SemanticSearchMixin()
        mixin._get_project_collection_name = MagicMock(return_value="rag_ws__project")
        mixin._collection_or_alias_exists = MagicMock(return_value=True)
        mixin._get_or_create_index = MagicMock(return_value=index)
        mixin._require_compatible_branches = MagicMock()
        mixin._supports_instructions = False
        mixin._plugin_identity_compatible = lambda metadata: metadata["compatible"]

        results = mixin.semantic_search_multi_branch(
            query="service",
            workspace="ws",
            project="project",
            branches=["feature", "main"],
            top_k=1,
        )

        assert [result["text"] for result in results] == ["current"]


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
