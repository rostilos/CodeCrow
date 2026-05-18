"""
Tests for rag_pipeline.core.embedding_factory — create_embedding_model, get_embedding_model_info.
"""
import pytest
from unittest.mock import patch, MagicMock

from rag_pipeline.core.embedding_factory import create_embedding_model, get_embedding_model_info


def _mock_config(**overrides):
    """Create a mock RAGConfig with sensible embedding defaults."""
    cfg = MagicMock()
    cfg.embedding_provider = overrides.get("embedding_provider", "ollama")
    cfg.ollama_model = overrides.get("ollama_model", "nomic-embed-text")
    cfg.ollama_base_url = overrides.get("ollama_base_url", "http://localhost:11434")
    cfg.embedding_dim = overrides.get("embedding_dim", 768)
    cfg.openrouter_api_key = overrides.get("openrouter_api_key", "sk-test-key")
    cfg.openrouter_model = overrides.get("openrouter_model", "openai/text-embedding-3-small")
    cfg.openrouter_base_url = overrides.get("openrouter_base_url", "https://openrouter.ai/api/v1")
    return cfg


# ─────────────────────────────────────────────────────────────
# create_embedding_model
# ─────────────────────────────────────────────────────────────
class TestCreateEmbeddingModel:

    @patch("rag_pipeline.core.embedding_factory.OllamaEmbedding")
    def test_creates_ollama_model(self, MockOllama):
        config = _mock_config(embedding_provider="ollama")
        create_embedding_model(config)
        MockOllama.assert_called_once()
        call_kwargs = MockOllama.call_args[1]
        assert call_kwargs["model"] == "nomic-embed-text"
        assert call_kwargs["base_url"] == "http://localhost:11434"

    @patch("rag_pipeline.core.embedding_factory.OpenRouterEmbedding")
    def test_creates_openrouter_model(self, MockOR):
        config = _mock_config(embedding_provider="openrouter")
        create_embedding_model(config)
        MockOR.assert_called_once()
        call_kwargs = MockOR.call_args[1]
        assert call_kwargs["api_key"] == "sk-test-key"
        assert call_kwargs["model"] == "openai/text-embedding-3-small"

    @patch("rag_pipeline.core.embedding_factory.OllamaEmbedding")
    def test_unknown_provider_defaults_to_ollama(self, MockOllama):
        config = _mock_config(embedding_provider="unknown_provider")
        create_embedding_model(config)
        MockOllama.assert_called_once()

    @patch("rag_pipeline.core.embedding_factory.OllamaEmbedding")
    def test_passes_expected_dim(self, MockOllama):
        config = _mock_config(embedding_dim=1536)
        create_embedding_model(config)
        call_kwargs = MockOllama.call_args[1]
        assert call_kwargs["expected_dim"] == 1536


# ─────────────────────────────────────────────────────────────
# get_embedding_model_info
# ─────────────────────────────────────────────────────────────
class TestGetEmbeddingModelInfo:

    def test_ollama_info(self):
        config = _mock_config(embedding_provider="ollama")
        info = get_embedding_model_info(config)
        assert info["provider"] == "ollama"
        assert info["model"] == "nomic-embed-text"
        assert info["type"] == "local"

    def test_openrouter_info(self):
        config = _mock_config(embedding_provider="openrouter")
        info = get_embedding_model_info(config)
        assert info["provider"] == "openrouter"
        assert info["model"] == "openai/text-embedding-3-small"
        assert info["type"] == "cloud"

    def test_unknown_provider_info(self):
        config = _mock_config(embedding_provider="custom")
        info = get_embedding_model_info(config)
        assert info["provider"] == "custom"
        assert info["type"] == "unknown"

    def test_includes_embedding_dim(self):
        config = _mock_config(embedding_dim=1024)
        info = get_embedding_model_info(config)
        assert info["embedding_dim"] == 1024
