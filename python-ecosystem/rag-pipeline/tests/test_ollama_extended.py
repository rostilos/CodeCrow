"""
Tests for rag_pipeline.core.ollama_embedding — OllamaEmbedding.

Covers uncovered lines:
- _get_embedding: empty text, truncation, response parsing, dimension mismatch
- _get_text_embeddings: batch processing, empty batch, partial empties,
  fallback to single, dimension mismatch
- _retry_with_backoff: ConnectError, TimeoutException, HTTPStatusError retry
- _test_connection: model listing, warning paths
- close / __del__
"""
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
import httpx

from rag_pipeline.core.ollama_embedding import OllamaEmbedding, EmbeddingError


DIM = 384
MODEL = "test-model"
BASE_URL = "http://localhost:11434"


def _build_embedding(max_retries=0, retry_base_delay=0.0, max_chars=100):
    """
    Construct an OllamaEmbedding instance with all external I/O mocked out.
    """
    with patch.object(OllamaEmbedding, "__init__", lambda self, **kw: None):
        emb = OllamaEmbedding()
    object.__setattr__(emb, "_config", {
        "model": MODEL,
        "base_url": BASE_URL,
        "timeout": 10.0,
        "embed_batch_size": 10,
        "embedding_dim": DIM,
        "max_chars": max_chars,
        "max_retries": max_retries,
        "retry_base_delay": retry_base_delay,
    })
    mock_client = MagicMock()
    object.__setattr__(emb, "_client", mock_client)
    return emb, mock_client


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

    def test_truncation(self):
        emb, client = _build_embedding(max_chars=10)
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"embedding": [0.1] * DIM}
        resp.raise_for_status = MagicMock()
        client.post.return_value = resp

        result = emb._get_embedding("a" * 50)
        assert len(result) == DIM
        # Verify the posted text was truncated
        call_json = client.post.call_args[1]["json"]
        assert len(call_json["prompt"]) <= 10

    def test_embedding_key(self):
        emb, client = _build_embedding()
        resp = MagicMock()
        resp.json.return_value = {"embedding": [0.5] * DIM}
        resp.raise_for_status = MagicMock()
        client.post.return_value = resp

        result = emb._get_embedding("hello")
        assert result == [0.5] * DIM

    def test_embeddings_key_fallback(self):
        emb, client = _build_embedding()
        resp = MagicMock()
        resp.json.return_value = {"embeddings": [[0.3] * DIM]}
        resp.raise_for_status = MagicMock()
        client.post.return_value = resp

        result = emb._get_embedding("hello")
        assert result == [0.3] * DIM

    def test_unexpected_format_raises(self):
        emb, client = _build_embedding()
        resp = MagicMock()
        resp.json.return_value = {"something_else": 42}
        resp.raise_for_status = MagicMock()
        client.post.return_value = resp

        with pytest.raises(EmbeddingError, match="Unexpected response"):
            emb._get_embedding("hello")

    def test_dimension_mismatch_raises(self):
        emb, client = _build_embedding()
        resp = MagicMock()
        resp.json.return_value = {"embedding": [0.1] * 999}
        resp.raise_for_status = MagicMock()
        client.post.return_value = resp

        with pytest.raises(EmbeddingError, match="dimension mismatch"):
            emb._get_embedding("hello")


# ─────────────────────────────────────────────────────────────
# _get_text_embeddings  (batch API)
# ─────────────────────────────────────────────────────────────
class TestGetTextEmbeddings:

    def test_empty_list(self):
        emb, _ = _build_embedding()
        assert emb._get_text_embeddings([]) == []

    def test_all_empty_raises(self):
        emb, _ = _build_embedding()
        with pytest.raises(EmbeddingError, match="all .* texts were empty"):
            emb._get_text_embeddings(["", "  ", None])

    def test_batch_success(self):
        emb, client = _build_embedding()
        resp = MagicMock()
        resp.json.return_value = {"embeddings": [[0.1] * DIM, [0.2] * DIM]}
        resp.raise_for_status = MagicMock()
        client.post.return_value = resp

        result = emb._get_text_embeddings(["hello", "world"])
        assert len(result) == 2
        assert result[0] == [0.1] * DIM

    def test_partial_empties_warning(self):
        emb, client = _build_embedding()
        resp = MagicMock()
        resp.json.return_value = {"embeddings": [[0.1] * DIM]}
        resp.raise_for_status = MagicMock()
        client.post.return_value = resp

        result = emb._get_text_embeddings(["hello", "", "  "])
        assert len(result) == 1

    def test_dimension_mismatch_in_batch(self):
        emb, client = _build_embedding()
        resp = MagicMock()
        resp.json.return_value = {"embeddings": [[0.1] * 999]}
        resp.raise_for_status = MagicMock()
        client.post.return_value = resp

        with pytest.raises(EmbeddingError, match="dimension mismatch"):
            emb._get_text_embeddings(["hello"])

    def test_unexpected_batch_format(self):
        emb, client = _build_embedding()
        resp = MagicMock()
        resp.json.return_value = {"nope": True}
        resp.raise_for_status = MagicMock()
        client.post.return_value = resp

        with pytest.raises(EmbeddingError, match="Unexpected batch response"):
            emb._get_text_embeddings(["hello"])

    def test_fallback_to_single(self):
        emb, client = _build_embedding()
        # First call (batch) raises a generic error → triggers fallback
        # Second calls (single) succeed
        resp_ok = MagicMock()
        resp_ok.json.return_value = {"embedding": [0.1] * DIM}
        resp_ok.raise_for_status = MagicMock()

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("boom")
            return resp_ok

        client.post.side_effect = side_effect

        result = emb._get_text_embeddings(["hello", "world"])
        assert len(result) == 2

    def test_truncation_in_batch(self):
        emb, client = _build_embedding(max_chars=5)
        resp = MagicMock()
        resp.json.return_value = {"embeddings": [[0.1] * DIM]}
        resp.raise_for_status = MagicMock()
        client.post.return_value = resp

        result = emb._get_text_embeddings(["abcdefghij"])
        assert len(result) == 1


# ─────────────────────────────────────────────────────────────
# _retry_with_backoff
# ─────────────────────────────────────────────────────────────
class TestRetryWithBackoff:

    def test_success_first_try(self):
        emb, _ = _build_embedding(max_retries=2)
        result = emb._retry_with_backoff(lambda: 42)
        assert result == 42

    @patch("time.sleep")
    def test_connect_error_retry(self, mock_sleep):
        emb, _ = _build_embedding(max_retries=1, retry_base_delay=0.0)
        call_count = 0

        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectError("refused")
            return "ok"

        assert emb._retry_with_backoff(flaky) == "ok"
        assert call_count == 2

    @patch("time.sleep")
    def test_timeout_retry(self, mock_sleep):
        emb, _ = _build_embedding(max_retries=1, retry_base_delay=0.0)
        call_count = 0

        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ReadTimeout("timeout")
            return "ok"

        assert emb._retry_with_backoff(flaky) == "ok"

    @patch("time.sleep")
    def test_http_429_retry(self, mock_sleep):
        emb, _ = _build_embedding(max_retries=1, retry_base_delay=0.0)
        call_count = 0
        resp_429 = MagicMock()
        resp_429.status_code = 429

        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.HTTPStatusError("rate limited", request=MagicMock(), response=resp_429)
            return "ok"

        assert emb._retry_with_backoff(flaky) == "ok"

    @patch("time.sleep")
    def test_http_503_retry(self, mock_sleep):
        emb, _ = _build_embedding(max_retries=1, retry_base_delay=0.0)
        call_count = 0
        resp_503 = MagicMock()
        resp_503.status_code = 503

        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.HTTPStatusError("unavailable", request=MagicMock(), response=resp_503)
            return "ok"

        assert emb._retry_with_backoff(flaky) == "ok"

    def test_non_retryable_http_raises(self):
        emb, _ = _build_embedding(max_retries=2)
        resp_400 = MagicMock()
        resp_400.status_code = 400

        def fail():
            raise httpx.HTTPStatusError("bad request", request=MagicMock(), response=resp_400)

        with pytest.raises(httpx.HTTPStatusError):
            emb._retry_with_backoff(fail)

    @patch("time.sleep")
    def test_all_retries_exhausted(self, mock_sleep):
        emb, _ = _build_embedding(max_retries=1, retry_base_delay=0.0)

        def always_fail():
            raise httpx.ConnectError("down")

        with pytest.raises(httpx.ConnectError):
            emb._retry_with_backoff(always_fail)


# ─────────────────────────────────────────────────────────────
# _test_connection
# ─────────────────────────────────────────────────────────────
class TestTestConnection:

    def test_model_available(self):
        emb, client = _build_embedding()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"models": [{"name": MODEL}]}
        client.get.return_value = resp

        emb._test_connection()  # should not raise

    def test_model_not_available_warns(self):
        emb, client = _build_embedding()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"models": [{"name": "other-model"}]}
        client.get.return_value = resp

        emb._test_connection()  # should log warning but not raise

    def test_bad_status_code_warns(self):
        emb, client = _build_embedding()
        resp = MagicMock()
        resp.status_code = 500
        client.get.return_value = resp

        emb._test_connection()  # should not raise

    def test_connection_error_warns(self):
        emb, client = _build_embedding()
        client.get.side_effect = ConnectionError("refused")

        emb._test_connection()  # should not raise


# ─────────────────────────────────────────────────────────────
# close / __del__
# ─────────────────────────────────────────────────────────────
class TestCloseAndDel:

    def test_close(self):
        emb, client = _build_embedding()
        emb.close()
        client.close.assert_called_once()

    def test_close_no_client(self):
        with patch.object(OllamaEmbedding, "__init__", lambda self, **kw: None):
            emb = OllamaEmbedding()
        # No _client attribute at all
        emb.close()  # should not raise

    def test_close_error_handled(self):
        emb, client = _build_embedding()
        client.close.side_effect = RuntimeError("err")
        emb.close()  # should not raise

    def test_del_calls_close(self):
        emb, client = _build_embedding()
        emb.__del__()
        client.close.assert_called_once()
