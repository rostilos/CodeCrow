"""
Ollama embedding wrapper for LlamaIndex.
Supports local embedding models running via Ollama.
"""

import asyncio
import os
from typing import Any, List, Optional
from llama_index.core.base.embeddings.base import BaseEmbedding
import httpx
import logging

from ..models.config import get_embedding_dim_for_model

logger = logging.getLogger(__name__)

# Configurable defaults via environment variables
DEFAULT_BATCH_SIZE = int(os.getenv("OLLAMA_BATCH_SIZE", "100"))
DEFAULT_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "120"))
DEFAULT_MAX_CHARS = int(os.getenv("OLLAMA_MAX_CHARS", "24000"))
DEFAULT_MAX_RETRIES = int(os.getenv("OLLAMA_MAX_RETRIES", "3"))
DEFAULT_RETRY_BASE_DELAY = float(os.getenv("OLLAMA_RETRY_BASE_DELAY", "1.0"))


class EmbeddingError(Exception):
    """Raised when an embedding cannot be produced for a given text.

    This prevents zero-vector fallbacks from silently corrupting the
    Qdrant vector store.
    """


class OllamaEmbedding(BaseEmbedding):
    """
    Custom embedding class for Ollama API.

    Supports local embedding models like qwen3-embedding:0.6b, nomic-embed-text, etc.
    """

    def __init__(
        self,
        model: str = "qwen3-embedding:0.6b",
        base_url: str = "http://localhost:11434",
        timeout: float = None,
        embed_batch_size: int = None,
        expected_dim: Optional[int] = None,
        max_chars: Optional[int] = None,
        max_retries: Optional[int] = None,
        retry_base_delay: Optional[float] = None,
        **kwargs: Any
    ):
        # Use env-configured defaults if not specified
        if timeout is None:
            timeout = DEFAULT_TIMEOUT
        if embed_batch_size is None:
            embed_batch_size = DEFAULT_BATCH_SIZE

        super().__init__(embed_batch_size=embed_batch_size, **kwargs)

        # Determine expected embedding dimension
        if expected_dim is not None:
            embedding_dim = expected_dim
        else:
            embedding_dim = get_embedding_dim_for_model(model)

        logger.info(f"OllamaEmbedding: Initializing with model: {model}")
        logger.info(f"OllamaEmbedding: Expected embedding dimension: {embedding_dim}")
        logger.info(f"OllamaEmbedding: Base URL: {base_url}")
        logger.info(f"OllamaEmbedding: Batch size: {embed_batch_size}")

        # Store config using object.__setattr__ to bypass Pydantic validation
        object.__setattr__(self, '_config', {
            "model": model,
            "base_url": base_url.rstrip('/'),
            "timeout": timeout,
            "embed_batch_size": embed_batch_size,
            "embedding_dim": embedding_dim,
            "max_chars": max_chars if max_chars is not None else DEFAULT_MAX_CHARS,
            "max_retries": max_retries if max_retries is not None else DEFAULT_MAX_RETRIES,
            "retry_base_delay": retry_base_delay if retry_base_delay is not None else DEFAULT_RETRY_BASE_DELAY,
        })

        # Initialize HTTP client
        object.__setattr__(self, '_client', httpx.Client(
            base_url=base_url.rstrip('/'),
            timeout=timeout
        ))

        # Test connection
        self._test_connection()
        logger.info(f"Ollama embeddings initialized successfully")

    def _test_connection(self):
        """Test connection to Ollama server."""
        try:
            response = self._client.get("/api/tags")
            if response.status_code == 200:
                models = response.json().get("models", [])
                model_names = [m.get("name", "") for m in models]
                logger.info(f"Connected to Ollama. Available models: {model_names}")

                # Check if our model is available
                model_name = self._config["model"]
                if not any(model_name in name or name in model_name for name in model_names):
                    logger.warning(f"Model '{model_name}' may not be available. Pull it with: ollama pull {model_name}")
            else:
                logger.warning(f"Could not list Ollama models: {response.status_code}")
        except Exception as e:
            logger.warning(f"Could not connect to Ollama at {self._config['base_url']}: {e}")
            logger.warning("Make sure Ollama is running: ollama serve")

    def close(self):
        """Close the HTTP client and free resources."""
        try:
            if hasattr(self, '_client') and self._client:
                self._client.close()
                logger.info("Ollama embedding client closed")
        except Exception as e:
            logger.warning(f"Error closing Ollama client: {e}")

    def __del__(self):
        """Destructor to ensure client is closed."""
        self.close()

    @property
    def model(self) -> str:
        """Get the model name."""
        return self._config["model"]

    def _retry_with_backoff(self, func, *args, **kwargs):
        """Execute a function with exponential backoff retry on transient errors.

        Retries on:
        - httpx.ConnectError (Ollama temporarily unreachable)
        - httpx.TimeoutException (slow to respond under load)
        - HTTP 429 (rate limited)
        - HTTP 503 (service temporarily unavailable)
        - HTTP 500 (transient server error)
        """
        max_retries = self._config["max_retries"]
        base_delay = self._config["retry_base_delay"]

        last_error = None
        for attempt in range(max_retries + 1):
            try:
                return func(*args, **kwargs)
            except httpx.ConnectError as e:
                last_error = e
                if attempt < max_retries:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"Ollama connection failed (attempt {attempt + 1}/{max_retries + 1}), retrying in {delay:.1f}s: {e}")
                    import time
                    time.sleep(delay)
            except httpx.TimeoutException as e:
                last_error = e
                if attempt < max_retries:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"Ollama timeout (attempt {attempt + 1}/{max_retries + 1}), retrying in {delay:.1f}s: {e}")
                    import time
                    time.sleep(delay)
            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code in (429, 500, 503) and attempt < max_retries:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"Ollama HTTP {e.response.status_code} (attempt {attempt + 1}/{max_retries + 1}), retrying in {delay:.1f}s")
                    import time
                    time.sleep(delay)
                else:
                    raise
        raise last_error

    def _get_query_embedding(self, query: str) -> List[float]:
        """Get embedding for a query text."""
        return self._get_embedding(query)

    def _get_text_embedding(self, text: str) -> List[float]:
        """Get embedding for a text."""
        return self._get_embedding(text)

    def _get_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        Get embeddings for multiple texts using batch API.
        Uses /api/embed which supports array input for batching.

        Raises EmbeddingError if an entire batch fails with no fallback possible.
        """
        if not texts:
            return []

        expected_dim = self._config["embedding_dim"]
        max_chars = self._config["max_chars"]
        batch_size = self._config.get("embed_batch_size", DEFAULT_BATCH_SIZE)
        all_embeddings = []

        # Process in batches
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            logger.debug(f"Embedding batch {i // batch_size + 1}: {len(batch)} texts")

            # Preprocess batch — skip empty texts, truncate long ones
            processed_batch = []
            empty_indices = []
            for idx, text in enumerate(batch):
                if not text or not text.strip():
                    empty_indices.append(i + idx)
                    continue
                if len(text) > max_chars:
                    text = text[:max_chars]
                processed_batch.append(text.strip())

            if not processed_batch:
                # All texts in this batch were empty — raise rather than zero-fill
                raise EmbeddingError(
                    f"Batch {i // batch_size + 1}: all {len(batch)} texts were empty, "
                    f"cannot produce embeddings"
                )

            if empty_indices:
                logger.warning(
                    f"Batch {i // batch_size + 1}: skipping {len(empty_indices)} empty texts "
                    f"at indices {empty_indices[:5]}{'...' if len(empty_indices) > 5 else ''}"
                )

            try:
                def _do_batch_embed():
                    response = self._client.post(
                        "/api/embed",
                        json={
                            "model": self._config["model"],
                            "input": processed_batch
                        }
                    )
                    response.raise_for_status()
                    return response.json()

                data = self._retry_with_backoff(_do_batch_embed)

                # /api/embed returns {"embeddings": [[...], [...], ...]}
                if "embeddings" in data:
                    batch_embeddings = data["embeddings"]
                    # Validate dimensions
                    for emb in batch_embeddings:
                        if len(emb) != expected_dim:
                            raise EmbeddingError(
                                f"Embedding dimension mismatch: got {len(emb)}, "
                                f"expected {expected_dim}. Check model configuration."
                            )
                    all_embeddings.extend(batch_embeddings)
                else:
                    raise EmbeddingError(
                        f"Unexpected batch response format from Ollama: {list(data.keys())}"
                    )

            except EmbeddingError:
                raise
            except Exception as e:
                logger.error(f"Batch embedding failed: {e}, falling back to single requests")
                # Fallback to single embedding requests — failures propagate
                for text in processed_batch:
                    embedding = self._get_embedding(text)
                    all_embeddings.append(embedding)

        return all_embeddings

    def _get_embedding(self, text: str) -> List[float]:
        """Get embedding from Ollama API.

        Raises EmbeddingError for empty/invalid text instead of returning
        zero vectors that would corrupt the vector store.
        """
        expected_dim = self._config["embedding_dim"]
        max_chars = self._config["max_chars"]

        # Validate input — raise instead of returning zero vectors
        if not text or not text.strip():
            raise EmbeddingError("Cannot embed empty text — refusing to produce zero vector")

        # Truncate if too long
        if len(text) > max_chars:
            logger.warning(f"Text too long ({len(text)} chars), truncating to {max_chars}")
            text = text[:max_chars]

        # Clean the text
        text = text.strip()
        if not text:
            raise EmbeddingError("Text became empty after stripping — refusing to produce zero vector")

        def _do_single_embed():
            response = self._client.post(
                "/api/embeddings",
                json={
                    "model": self._config["model"],
                    "prompt": text
                }
            )
            response.raise_for_status()
            return response.json()

        data = self._retry_with_backoff(_do_single_embed)

        # Ollama returns embedding in 'embedding' field
        if "embedding" in data:
            embedding = data["embedding"]
        elif "embeddings" in data and len(data["embeddings"]) > 0:
            embedding = data["embeddings"][0]
        else:
            raise EmbeddingError(f"Unexpected response format from Ollama: {list(data.keys())}")

        # Validate embedding dimensions — hard error, never silently adjust
        if len(embedding) != expected_dim:
            raise EmbeddingError(
                f"Embedding dimension mismatch: got {len(embedding)}, expected {expected_dim}. "
                f"The Qdrant collection was created with dimension {expected_dim}. "
                f"Check that the model '{self._config['model']}' produces {expected_dim}-dim vectors."
            )

        return embedding

    async def _aget_query_embedding(self, query: str) -> List[float]:
        """Async get embedding for a query text (offloaded to thread pool)."""
        return await asyncio.to_thread(self._get_query_embedding, query)

    async def _aget_text_embedding(self, text: str) -> List[float]:
        """Async get embedding for a text (offloaded to thread pool)."""
        return await asyncio.to_thread(self._get_text_embedding, text)

    async def _aget_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Async batch get embeddings for multiple texts (offloaded to thread pool)."""
        return await asyncio.to_thread(self._get_text_embeddings, texts)
