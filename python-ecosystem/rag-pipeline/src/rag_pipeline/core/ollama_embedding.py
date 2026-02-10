"""
Ollama embedding wrapper for LlamaIndex.
Supports local embedding models running via Ollama.
"""

import os
from typing import Any, List, Optional
from llama_index.core.base.embeddings.base import BaseEmbedding
import httpx
import logging

from ..models.config import get_embedding_dim_for_model

logger = logging.getLogger(__name__)

# Default batch size - can be overridden via OLLAMA_BATCH_SIZE env var
DEFAULT_BATCH_SIZE = int(os.getenv("OLLAMA_BATCH_SIZE", "100"))
# Default timeout - can be overridden via OLLAMA_TIMEOUT env var
DEFAULT_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "120"))


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
            "embedding_dim": embedding_dim
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
        """
        if not texts:
            return []

        expected_dim = self._config.get("embedding_dim", 1024)
        batch_size = self._config.get("embed_batch_size", DEFAULT_BATCH_SIZE)
        all_embeddings = []

        # Process in batches
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            logger.debug(f"Embedding batch {i // batch_size + 1}: {len(batch)} texts")

            # Preprocess batch
            processed_batch = []
            for text in batch:
                if not text or not text.strip():
                    processed_batch.append(" ")  # Placeholder for empty
                else:
                    # Truncate if too long
                    if len(text) > 24000:
                        text = text[:24000]
                    processed_batch.append(text.strip())

            try:
                # Use /api/embed with array input for batch processing
                response = self._client.post(
                    "/api/embed",
                    json={
                        "model": self._config["model"],
                        "input": processed_batch
                    }
                )
                response.raise_for_status()
                data = response.json()

                # /api/embed returns {"embeddings": [[...], [...], ...]}
                if "embeddings" in data:
                    batch_embeddings = data["embeddings"]
                    all_embeddings.extend(batch_embeddings)
                else:
                    logger.error(f"Unexpected batch response format: {list(data.keys())}")
                    # Fallback to zeros
                    all_embeddings.extend([[0.0] * expected_dim] * len(processed_batch))

            except Exception as e:
                logger.error(f"Batch embedding failed: {e}, falling back to single requests")
                # Fallback to single embedding requests
                for text in processed_batch:
                    try:
                        embedding = self._get_embedding(text)
                        all_embeddings.append(embedding)
                    except Exception:
                        all_embeddings.append([0.0] * expected_dim)

        return all_embeddings

    def _get_embedding(self, text: str) -> List[float]:
        """Get embedding from Ollama API."""
        expected_dim = self._config.get("embedding_dim", 1024)

        try:
            # Validate input
            if not text or not text.strip():
                logger.warning("Empty text provided for embedding, using placeholder")
                return [0.0] * expected_dim

            # Truncate if too long (Ollama typically handles ~8k tokens)
            max_chars = 24000
            if len(text) > max_chars:
                logger.warning(f"Text too long ({len(text)} chars), truncating to {max_chars}")
                text = text[:max_chars]

            # Clean the text
            text = text.strip()
            if not text:
                logger.warning("Text became empty after stripping")
                return [0.0] * expected_dim

            # Call Ollama embeddings API
            response = self._client.post(
                "/api/embeddings",
                json={
                    "model": self._config["model"],
                    "prompt": text
                }
            )
            response.raise_for_status()
            data = response.json()

            # Ollama returns embedding in 'embedding' field
            if "embedding" in data:
                embedding = data["embedding"]
            elif "embeddings" in data and len(data["embeddings"]) > 0:
                # Fallback for potential future API changes
                embedding = data["embeddings"][0]
            else:
                logger.error(f"Unexpected response format from Ollama: {data}")
                return [0.0] * expected_dim

            # Validate embedding dimensions
            if len(embedding) != expected_dim:
                logger.warning(f"Unexpected embedding dimension: {len(embedding)}, expected {expected_dim}")
                # Update expected dim if this is consistently different
                self._config["embedding_dim"] = len(embedding)

            return embedding

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error getting embedding from Ollama: {e}")
            logger.error(f"Response: {e.response.text if e.response else 'No response'}")
            raise
        except Exception as e:
            logger.error(f"Error getting embedding from Ollama: {e}")
            logger.error(f"Text length: {len(text) if text else 0}, Text preview: {text[:100] if text else 'None'}...")
            raise

    async def _aget_query_embedding(self, query: str) -> List[float]:
        """Async get embedding for a query text."""
        return self._get_query_embedding(query)

    async def _aget_text_embedding(self, text: str) -> List[float]:
        """Async get embedding for a text."""
        return self._get_text_embedding(text)

    async def _aget_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Async batch get embeddings for multiple texts."""
        return self._get_text_embeddings(texts)
