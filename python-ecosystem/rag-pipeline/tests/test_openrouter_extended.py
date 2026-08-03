"""
Tests for rag_pipeline.core.openrouter_embedding — OpenRouterEmbedding.

Covers uncovered lines:
- __init__: empty API key raises ValueError
- _get_embedding: empty text, truncation, dimension mismatch, no data
- _get_text_embeddings: batch processing, all empty, response count mismatch,
  sorted by index, dimension validation, fallback to individual
- close / __del__
"""
import pytest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

from rag_pipeline.core.openrouter_embedding import (
    OpenRouterEmbedding,
    _AdaptiveConcurrencyGate,
)
from rag_pipeline.core.ollama_embedding import EmbeddingError


DIM = 1536
MODEL = "openai/text-embedding-3-small"


def _build_embedding(max_chars=100, max_retries=0):
    """
    Construct an OpenRouterEmbedding instance with all external I/O mocked.
    """
    with patch.object(OpenRouterEmbedding, "__init__", lambda self, **kw: None):
        emb = OpenRouterEmbedding()
    object.__setattr__(emb, "_config", {
        "api_key": "sk-test-key",
        "model": MODEL,
        "api_base": "https://openrouter.ai/api/v1",
        "timeout": 10.0,
        "max_retries": max_retries,
        "embed_batch_size": 10,
        "embedding_dim": DIM,
        "max_chars": max_chars,
    })
    mock_client = MagicMock()
    object.__setattr__(emb, "_client", mock_client)
    return emb, mock_client


def _make_embedding_data(embedding, index=0):
    """Helper to build a response.data item."""
    return SimpleNamespace(embedding=embedding, index=index)


# ─────────────────────────────────────────────────────────────
# __init__  validation
# ─────────────────────────────────────────────────────────────
class TestInit:

    @patch("rag_pipeline.core.openrouter_embedding.get_embedding_dim_for_model", return_value=DIM)
    @patch("rag_pipeline.core.openrouter_embedding.OpenAI")
    def test_empty_api_key_raises(self, mock_openai_cls, mock_dim):
        with pytest.raises(ValueError, match="API key is required"):
            OpenRouterEmbedding(api_key="")

    @patch("rag_pipeline.core.openrouter_embedding.get_embedding_dim_for_model", return_value=DIM)
    @patch("rag_pipeline.core.openrouter_embedding.OpenAI")
    def test_none_api_key_raises(self, mock_openai_cls, mock_dim):
        with pytest.raises(ValueError, match="API key is required"):
            OpenRouterEmbedding(api_key=None)

    @patch("rag_pipeline.core.openrouter_embedding.get_embedding_dim_for_model", return_value=DIM)
    @patch("rag_pipeline.core.openrouter_embedding.OpenAI")
    def test_whitespace_api_key_raises(self, mock_openai_cls, mock_dim):
        with pytest.raises(ValueError, match="API key is required"):
            OpenRouterEmbedding(api_key="   ")


# ─────────────────────────────────────────────────────────────
# _get_embedding
# ─────────────────────────────────────────────────────────────
class TestGetEmbedding:

    def test_empty_text_raises(self):
        emb, _ = _build_embedding()
        with pytest.raises(EmbeddingError, match="empty text"):
            emb._get_embedding("")

    def test_whitespace_only_raises(self):
        emb, _ = _build_embedding()
        with pytest.raises(EmbeddingError, match="empty text"):
            emb._get_embedding("   ")

    def test_success(self):
        emb, client = _build_embedding()
        resp = MagicMock()
        resp.data = [_make_embedding_data([0.5] * DIM)]
        client.embeddings.create.return_value = resp

        result = emb._get_embedding("hello")
        assert result == [0.5] * DIM

    def test_truncation(self):
        emb, client = _build_embedding(max_chars=5)
        resp = MagicMock()
        resp.data = [_make_embedding_data([0.1] * DIM)]
        client.embeddings.create.return_value = resp

        result = emb._get_embedding("abcdefghij")
        assert result == [0.1] * DIM
        call_args = client.embeddings.create.call_args
        assert len(call_args[1]["input"]) <= 5

    def test_no_data_raises(self):
        emb, client = _build_embedding()
        resp = MagicMock()
        resp.data = []
        client.embeddings.create.return_value = resp

        with pytest.raises(EmbeddingError, match="No embedding data"):
            emb._get_embedding("hello")

    def test_none_data_raises(self):
        emb, client = _build_embedding()
        resp = MagicMock()
        resp.data = None
        client.embeddings.create.return_value = resp

        with pytest.raises(EmbeddingError, match="No embedding data"):
            emb._get_embedding("hello")

    def test_dimension_mismatch_raises(self):
        emb, client = _build_embedding()
        resp = MagicMock()
        resp.data = [_make_embedding_data([0.1] * 999)]
        client.embeddings.create.return_value = resp

        with pytest.raises(EmbeddingError, match="dimension mismatch"):
            emb._get_embedding("hello")

    def test_api_error_propagates(self):
        emb, client = _build_embedding()
        client.embeddings.create.side_effect = RuntimeError("API down")

        with pytest.raises(RuntimeError, match="API down"):
            emb._get_embedding("hello")


# ─────────────────────────────────────────────────────────────
# _get_text_embeddings  (batch)
# ─────────────────────────────────────────────────────────────
class TestGetTextEmbeddings:

    def test_empty_list(self):
        emb, _ = _build_embedding()
        assert emb._get_text_embeddings([]) == []

    def test_all_empty_raises(self):
        emb, _ = _build_embedding()
        with pytest.raises(EmbeddingError, match="empty"):
            emb._get_text_embeddings(["", "  "])

    def test_batch_success(self):
        emb, client = _build_embedding()
        resp = MagicMock()
        resp.data = [
            _make_embedding_data([0.1] * DIM, index=0),
            _make_embedding_data([0.2] * DIM, index=1),
        ]
        client.embeddings.create.return_value = resp

        result = emb._get_text_embeddings(["hello", "world"])
        assert len(result) == 2
        assert result[0] == [0.1] * DIM
        assert result[1] == [0.2] * DIM

    def test_index_batch_requests_throughput_provider_routing(self):
        emb, client = _build_embedding()
        emb._config["workload"] = "index"
        emb._config["provider_sort"] = "throughput"
        resp = MagicMock()
        resp.data = [_make_embedding_data([0.1] * DIM, index=0)]
        client.embeddings.create.return_value = resp

        emb._get_text_embeddings(["hello"])

        assert client.embeddings.create.call_args.kwargs["extra_body"] == {
            "provider": {"sort": "throughput"}
        }

    def test_sorted_by_index(self):
        emb, client = _build_embedding()
        # API returns in reverse order
        resp = MagicMock()
        resp.data = [
            _make_embedding_data([0.2] * DIM, index=1),
            _make_embedding_data([0.1] * DIM, index=0),
        ]
        client.embeddings.create.return_value = resp

        result = emb._get_text_embeddings(["hello", "world"])
        assert result[0] == [0.1] * DIM
        assert result[1] == [0.2] * DIM

    def test_response_count_mismatch_raises(self):
        emb, client = _build_embedding()
        resp = MagicMock()
        resp.data = [_make_embedding_data([0.1] * DIM)]  # only 1 for 2 texts
        client.embeddings.create.return_value = resp

        with pytest.raises(EmbeddingError, match="Unexpected response"):
            emb._get_text_embeddings(["hello", "world"])

    def test_dimension_mismatch_in_batch(self):
        emb, client = _build_embedding()
        resp = MagicMock()
        resp.data = [_make_embedding_data([0.1] * 999, index=0)]
        client.embeddings.create.return_value = resp

        with pytest.raises(EmbeddingError, match="dimension mismatch"):
            emb._get_text_embeddings(["hello"])

    def test_partial_empties_skipped(self):
        emb, client = _build_embedding()
        resp = MagicMock()
        resp.data = [_make_embedding_data([0.1] * DIM, index=0)]
        client.embeddings.create.return_value = resp

        result = emb._get_text_embeddings(["hello", "", "  "])
        assert len(result) == 1

    def test_transient_batch_failure_does_not_fan_out_to_individual_requests(self):
        emb, client = _build_embedding()

        client.embeddings.create.side_effect = ConnectionError("boom")

        with pytest.raises(ConnectionError, match="boom"):
            emb._get_text_embeddings(["hello", "world"])

        assert client.embeddings.create.call_count == 1

    @patch("rag_pipeline.core.openrouter_embedding.time.sleep")
    def test_rate_limit_retries_the_original_batch_and_honors_retry_after(
        self,
        mock_sleep,
    ):
        emb, client = _build_embedding(max_retries=1)
        rate_limit = RuntimeError("rate limited")
        rate_limit.status_code = 429
        rate_limit.response = SimpleNamespace(headers={"retry-after": "1.5"})
        gate = _AdaptiveConcurrencyGate(8)
        object.__setattr__(emb, "_adaptive_gate", gate)
        response = MagicMock()
        response.data = [_make_embedding_data([0.1] * DIM, index=0)]
        client.embeddings.create.side_effect = [rate_limit, response]

        result = emb._get_text_embeddings(["hello"])

        assert result == [[0.1] * DIM]
        assert client.embeddings.create.call_count == 2
        assert all(
            call.kwargs["input"] == ["hello"]
            for call in client.embeddings.create.call_args_list
        )
        mock_sleep.assert_called_once_with(1.5)
        assert gate.limit == 4
        assert gate.active == 0

    def test_non_transient_request_rejection_is_not_retried(self):
        emb, client = _build_embedding(max_retries=3)
        rejection = RuntimeError("invalid input")
        rejection.status_code = 400
        client.embeddings.create.side_effect = rejection

        with pytest.raises(RuntimeError, match="invalid input"):
            emb._get_text_embeddings(["hello", "world"])

        assert client.embeddings.create.call_count == 1

    @pytest.mark.parametrize("failure_kind", ["timeout", "503"])
    @patch("rag_pipeline.core.openrouter_embedding.time.sleep")
    def test_transient_timeout_and_5xx_retry_the_batch(
        self,
        mock_sleep,
        failure_kind,
    ):
        emb, client = _build_embedding(max_retries=1)
        if failure_kind == "timeout":
            failure = TimeoutError("provider timeout")
        else:
            failure = RuntimeError("provider unavailable")
            failure.status_code = 503
        response = MagicMock()
        response.data = [_make_embedding_data([0.1] * DIM, index=0)]
        client.embeddings.create.side_effect = [failure, response]

        result = emb._get_text_embeddings(["hello"])

        assert result == [[0.1] * DIM]
        assert client.embeddings.create.call_count == 2
        assert all(
            call.kwargs["input"] == ["hello"]
            for call in client.embeddings.create.call_args_list
        )
        mock_sleep.assert_called_once_with(0.5)

    def test_truncation_in_batch(self):
        emb, client = _build_embedding(max_chars=5)
        resp = MagicMock()
        resp.data = [_make_embedding_data([0.1] * DIM, index=0)]
        client.embeddings.create.return_value = resp

        result = emb._get_text_embeddings(["abcdefghij"])
        assert len(result) == 1


# ─────────────────────────────────────────────────────────────
# close / __del__
# ─────────────────────────────────────────────────────────────
class TestCloseAndDel:

    def test_close(self):
        emb, client = _build_embedding()
        emb.close()
        client.close.assert_called_once()

    def test_close_no_client(self):
        with patch.object(OpenRouterEmbedding, "__init__", lambda self, **kw: None):
            emb = OpenRouterEmbedding()
        emb.close()  # should not raise

    def test_close_error_handled(self):
        emb, client = _build_embedding()
        client.close.side_effect = RuntimeError("err")
        emb.close()  # should not raise

    def test_del_calls_close(self):
        emb, client = _build_embedding()
        emb.__del__()
        client.close.assert_called_once()
