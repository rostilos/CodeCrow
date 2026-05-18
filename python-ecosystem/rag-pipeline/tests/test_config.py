"""
Unit tests for rag_pipeline.models.config — RAGConfig validators and helpers.
"""
import os
import pytest
from unittest.mock import patch

from rag_pipeline.models.config import (
    RAGConfig,
    get_embedding_dim_for_model,
    EMBEDDING_MODEL_DIMENSIONS,
    _parse_csv_env,
    IndexStats,
)


class TestGetEmbeddingDimForModel:

    def test_exact_match(self):
        assert get_embedding_dim_for_model("openai/text-embedding-3-small") == 1536
        assert get_embedding_dim_for_model("all-minilm") == 384
        assert get_embedding_dim_for_model("nomic-embed-text") == 768

    def test_partial_match(self):
        # "qwen3-embedding-0.6b" should match via partial
        dim = get_embedding_dim_for_model("qwen3-embedding-0.6b")
        assert dim == 1024

    def test_unknown_model_returns_default(self):
        assert get_embedding_dim_for_model("totally-unknown-model") == 1536

    def test_all_known_models_have_positive_dim(self):
        for model, dim in EMBEDDING_MODEL_DIMENSIONS.items():
            assert dim > 0, f"Model {model} has non-positive dim {dim}"


class TestParseCsvEnv:

    def test_returns_default_when_env_not_set(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RAG_TEST_CSV", None)
            assert _parse_csv_env("RAG_TEST_CSV", ["a", "b"]) == ["a", "b"]

    def test_parses_comma_separated(self):
        with patch.dict(os.environ, {"RAG_TEST_CSV": "main,develop,release"}):
            assert _parse_csv_env("RAG_TEST_CSV", []) == ["main", "develop", "release"]

    def test_strips_whitespace(self):
        with patch.dict(os.environ, {"RAG_TEST_CSV": " main , develop "}):
            assert _parse_csv_env("RAG_TEST_CSV", []) == ["main", "develop"]

    def test_empty_value_returns_default(self):
        with patch.dict(os.environ, {"RAG_TEST_CSV": "  "}):
            assert _parse_csv_env("RAG_TEST_CSV", ["default"]) == ["default"]


class TestRAGConfig:

    def test_default_values(self):
        config = RAGConfig()
        assert config.chunk_size == 8000
        assert config.chunk_overlap == 200
        assert config.text_chunk_size == 2000
        assert config.retrieval_top_k == 10
        assert config.similarity_threshold == 0.7
        assert config.max_file_size_bytes == 1024 * 1024

    def test_auto_detect_embedding_dim_ollama(self):
        config = RAGConfig(embedding_provider="ollama", ollama_model="all-minilm", embedding_dim=0)
        assert config.embedding_dim == 384

    def test_auto_detect_embedding_dim_openrouter(self):
        config = RAGConfig(
            embedding_provider="openrouter",
            openrouter_model="openai/text-embedding-3-small",
            openrouter_api_key="test-key-12345",
            embedding_dim=0,
        )
        assert config.embedding_dim == 1536

    def test_explicit_embedding_dim_overrides_auto(self):
        config = RAGConfig(embedding_dim=512)
        assert config.embedding_dim == 512

    def test_openrouter_without_key_raises(self):
        with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
            RAGConfig(embedding_provider="openrouter", openrouter_api_key="")

    def test_unknown_provider_falls_back_to_ollama(self):
        config = RAGConfig(embedding_provider="unknown_provider")
        assert config.embedding_provider == "ollama"

    def test_excluded_patterns_defaults(self):
        config = RAGConfig()
        assert "node_modules/**" in config.excluded_patterns
        assert ".git/**" in config.excluded_patterns
        assert "*.min.js" in config.excluded_patterns

    def test_fallback_branches_default(self):
        config = RAGConfig()
        assert "main" in config.fallback_branches
        assert "master" in config.fallback_branches


class TestIndexStats:

    def test_round_trip(self):
        stats = IndexStats(
            namespace="ws__proj__main",
            document_count=100,
            chunk_count=500,
            last_updated="2026-01-01T00:00:00",
            workspace="ws",
            project="proj",
            branch="main",
        )
        data = stats.model_dump()
        restored = IndexStats(**data)
        assert restored.namespace == "ws__proj__main"
        assert restored.chunk_count == 500
