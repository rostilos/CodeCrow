"""
Tests for rag_pipeline.core.ollama_embedding and openrouter_embedding.
"""
import pytest
from unittest.mock import patch, MagicMock, PropertyMock

from rag_pipeline.core.ollama_embedding import OllamaEmbedding, EmbeddingError


# ─────────────────────────────────────────────────────────────
# OllamaEmbedding
# ─────────────────────────────────────────────────────────────
class TestOllamaEmbedding:

    @patch.object(OllamaEmbedding, "_test_connection")
    def test_init_stores_model(self, mock_test):
        embed = OllamaEmbedding(model="nomic-embed-text", base_url="http://localhost:11434", expected_dim=768)
        assert embed.model == "nomic-embed-text"

    @patch.object(OllamaEmbedding, "_test_connection")
    def test_model_property(self, mock_test):
        embed = OllamaEmbedding(model="test-model", base_url="http://localhost:11434", expected_dim=768)
        assert embed.model == "test-model"

    @patch.object(OllamaEmbedding, "_test_connection")
    def test_config_stores_expected_dim(self, mock_test):
        embed = OllamaEmbedding(model="nomic-embed-text", base_url="http://localhost:11434", expected_dim=1024)
        assert embed._config["embedding_dim"] == 1024

    @patch.object(OllamaEmbedding, "_test_connection")
    def test_config_batch_size(self, mock_test):
        embed = OllamaEmbedding(
            model="nomic-embed-text",
            base_url="http://localhost:11434",
            embed_batch_size=25,
            expected_dim=768,
        )
        assert embed._config["embed_batch_size"] == 25

    @patch.object(OllamaEmbedding, "_test_connection")
    def test_get_embedding_raises_on_empty_text(self, mock_test):
        embed = OllamaEmbedding(model="test", base_url="http://localhost:11434", expected_dim=768)
        with pytest.raises(EmbeddingError):
            embed._get_embedding("")

    @patch.object(OllamaEmbedding, "_test_connection")
    def test_get_embedding_raises_on_whitespace_only(self, mock_test):
        embed = OllamaEmbedding(model="test", base_url="http://localhost:11434", expected_dim=768)
        with pytest.raises(EmbeddingError):
            embed._get_embedding("   ")

    @patch.object(OllamaEmbedding, "_test_connection")
    def test_get_embedding_success(self, mock_test):
        embed = OllamaEmbedding(model="test", base_url="http://localhost:11434", expected_dim=3)
        # Mock the _client.post to return valid embedding
        mock_response = MagicMock()
        mock_response.json.return_value = {"embedding": [0.1, 0.2, 0.3]}
        mock_response.raise_for_status = MagicMock()
        embed._client.post = MagicMock(return_value=mock_response)

        result = embed._get_embedding("hello world")
        assert result == [0.1, 0.2, 0.3]

    @patch.object(OllamaEmbedding, "_test_connection")
    def test_get_embedding_dimension_mismatch_raises(self, mock_test):
        embed = OllamaEmbedding(model="test", base_url="http://localhost:11434", expected_dim=768)
        mock_response = MagicMock()
        mock_response.json.return_value = {"embedding": [0.1, 0.2, 0.3]}
        mock_response.raise_for_status = MagicMock()
        embed._client.post = MagicMock(return_value=mock_response)

        with pytest.raises(EmbeddingError, match="dimension mismatch"):
            embed._get_embedding("hello world")

    @patch.object(OllamaEmbedding, "_test_connection")
    def test_retry_with_backoff_succeeds_first_try(self, mock_test):
        embed = OllamaEmbedding(model="test", base_url="http://localhost:11434", expected_dim=768)
        result = embed._retry_with_backoff(lambda: "ok")
        assert result == "ok"

    @patch.object(OllamaEmbedding, "_test_connection")
    def test_close(self, mock_test):
        embed = OllamaEmbedding(model="test", base_url="http://localhost:11434", expected_dim=768)
        embed._client = MagicMock()
        embed.close()
        embed._client.close.assert_called_once()

    @patch.object(OllamaEmbedding, "_test_connection")
    def test_get_text_embeddings_empty_list(self, mock_test):
        embed = OllamaEmbedding(model="test", base_url="http://localhost:11434", expected_dim=768)
        result = embed._get_text_embeddings([])
        assert result == []

    @patch.object(OllamaEmbedding, "_test_connection")
    def test_get_text_embeddings_all_empty_raises(self, mock_test):
        embed = OllamaEmbedding(model="test", base_url="http://localhost:11434", expected_dim=768)
        with pytest.raises(EmbeddingError):
            embed._get_text_embeddings(["", "  "])


# ─────────────────────────────────────────────────────────────
# OpenRouterEmbedding
# ─────────────────────────────────────────────────────────────
class TestOpenRouterEmbedding:

    @patch("rag_pipeline.core.openrouter_embedding.OpenAI")
    def test_init_stores_model(self, MockOpenAI):
        from rag_pipeline.core.openrouter_embedding import OpenRouterEmbedding
        embed = OpenRouterEmbedding(
            api_key="sk-test-key",
            model="openai/text-embedding-3-small",
            expected_dim=1536,
        )
        assert embed.model == "openai/text-embedding-3-small"

    @patch("rag_pipeline.core.openrouter_embedding.OpenAI")
    def test_init_requires_api_key(self, MockOpenAI):
        from rag_pipeline.core.openrouter_embedding import OpenRouterEmbedding
        with pytest.raises(ValueError, match="API key"):
            OpenRouterEmbedding(api_key="", model="test", expected_dim=768)

    @patch("rag_pipeline.core.openrouter_embedding.OpenAI")
    def test_config_stores_expected_dim(self, MockOpenAI):
        from rag_pipeline.core.openrouter_embedding import OpenRouterEmbedding
        embed = OpenRouterEmbedding(api_key="sk-test", model="test", expected_dim=1024)
        assert embed._config["embedding_dim"] == 1024

    @patch("rag_pipeline.core.openrouter_embedding.OpenAI")
    def test_get_embedding_raises_on_empty(self, MockOpenAI):
        from rag_pipeline.core.openrouter_embedding import OpenRouterEmbedding
        embed = OpenRouterEmbedding(api_key="sk-test", model="test", expected_dim=768)
        with pytest.raises(EmbeddingError):
            embed._get_embedding("")

    @patch("rag_pipeline.core.openrouter_embedding.OpenAI")
    def test_get_text_embeddings_empty_returns_empty(self, MockOpenAI):
        from rag_pipeline.core.openrouter_embedding import OpenRouterEmbedding
        embed = OpenRouterEmbedding(api_key="sk-test", model="test", expected_dim=768)
        result = embed._get_text_embeddings([])
        assert result == []

    @patch("rag_pipeline.core.openrouter_embedding.OpenAI")
    def test_close(self, MockOpenAI):
        from rag_pipeline.core.openrouter_embedding import OpenRouterEmbedding
        embed = OpenRouterEmbedding(api_key="sk-test", model="test", expected_dim=768)
        embed._client = MagicMock()
        embed.close()
        embed._client.close.assert_called_once()
