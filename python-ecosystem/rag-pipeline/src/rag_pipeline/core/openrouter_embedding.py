"""
Custom OpenRouter embedding wrapper for LlamaIndex.
Bypasses model name validation to work with OpenRouter's model naming format.
Supports batch embeddings for efficient processing.
"""

import asyncio
import os
from typing import Any, List, Optional
from llama_index.core.base.embeddings.base import BaseEmbedding
from openai import OpenAI
import logging

from ..models.config import get_embedding_dim_for_model
from .ollama_embedding import EmbeddingError

logger = logging.getLogger(__name__)

# Batch size for embedding requests (OpenAI/OpenRouter limit is typically 2048)
EMBEDDING_BATCH_SIZE = int(os.getenv("OPENROUTER_BATCH_SIZE", "100"))
MAX_CHARS = int(os.getenv("OPENROUTER_MAX_CHARS", "24000"))


class OpenRouterEmbedding(BaseEmbedding):
    """
    Custom embedding class for OpenRouter API.

    OpenRouter uses format like 'openai/text-embedding-3-small'
    which LlamaIndex's OpenAIEmbedding doesn't accept.

    Supports batch embeddings for efficient processing.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "openai/text-embedding-3-small",
        api_base: str = "https://openrouter.ai/api/v1",
        timeout: float = 60.0,
        max_retries: int = 3,
        embed_batch_size: int = EMBEDDING_BATCH_SIZE,
        expected_dim: Optional[int] = None,
        max_chars: Optional[int] = None,
        **kwargs: Any
    ):
        # Pass embed_batch_size to parent class so get_text_embedding_batch uses correct batch size
        super().__init__(embed_batch_size=embed_batch_size, **kwargs)

        # Validate API key
        if not api_key or api_key.strip() == "":
            logger.error("OpenRouterEmbedding: API key is empty or None!")
            raise ValueError("OpenRouter API key is required")

        # Determine expected embedding dimension
        if expected_dim is not None:
            embedding_dim = expected_dim
        else:
            embedding_dim = get_embedding_dim_for_model(model)

        logger.info(f"OpenRouterEmbedding: Initializing with API key: {api_key[:10]}...{api_key[-4:]}")
        logger.info(f"OpenRouterEmbedding: Using model: {model}")
        logger.info(f"OpenRouterEmbedding: Expected embedding dimension: {embedding_dim}")
        logger.info(f"OpenRouterEmbedding: API base URL: {api_base}")
        logger.info(f"OpenRouterEmbedding: Batch size: {embed_batch_size}")

        # Use object.__setattr__ to bypass Pydantic validation
        object.__setattr__(self, '_config', {
            "api_key": api_key,
            "model": model,
            "api_base": api_base,
            "timeout": timeout,
            "max_retries": max_retries,
            "embed_batch_size": embed_batch_size,
            "embedding_dim": embedding_dim,
            "max_chars": max_chars if max_chars is not None else MAX_CHARS,
        })

        # Initialize OpenAI client pointed at OpenRouter
        object.__setattr__(self, '_client', OpenAI(
            api_key=api_key,
            base_url=api_base,
            timeout=timeout,
            max_retries=max_retries
        ))

        logger.info(f"OpenRouter embeddings initialized successfully")

    def close(self):
        """Close the OpenAI client and free resources."""
        try:
            if hasattr(self, '_client') and self._client:
                self._client.close()
                logger.info("OpenRouter embedding client closed")
        except Exception as e:
            logger.warning(f"Error closing OpenRouter client: {e}")

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
        Get embeddings for multiple texts in a single API call (batch processing).
        This is much more efficient than calling _get_text_embedding for each text.

        Raises EmbeddingError if all texts are empty or if the API fails
        unrecoverably.
        """
        if not texts:
            return []

        expected_dim = self._config["embedding_dim"]
        max_chars = self._config["max_chars"]

        logger.debug(f"Embedding batch: {len(texts)} texts in single API call")

        # Process texts: clean and truncate, track empties
        processed_texts = []
        empty_indices = []

        for idx, text in enumerate(texts):
            if not text or not text.strip():
                empty_indices.append(idx)
                continue
            text = text.strip()
            if len(text) > max_chars:
                text = text[:max_chars]
            processed_texts.append(text)

        if not processed_texts:
            raise EmbeddingError(
                f"All {len(texts)} texts in batch were empty, cannot produce embeddings"
            )

        if empty_indices:
            logger.warning(
                f"Skipping {len(empty_indices)} empty texts at indices "
                f"{empty_indices[:5]}{'...' if len(empty_indices) > 5 else ''}"
            )

        try:
            # Send all texts in a single API call
            response = self._client.embeddings.create(
                input=processed_texts,
                model=self._config["model"]
            )

            # Validate response
            if not response.data or len(response.data) != len(processed_texts):
                raise EmbeddingError(
                    f"Unexpected response: got {len(response.data) if response.data else 0} "
                    f"embeddings for {len(processed_texts)} texts"
                )

            # Sort by index since API may return in different order
            sorted_embeddings = sorted(response.data, key=lambda x: x.index)
            embeddings = [item.embedding for item in sorted_embeddings]

            # Validate dimensions
            for emb in embeddings:
                if len(emb) != expected_dim:
                    raise EmbeddingError(
                        f"Embedding dimension mismatch: got {len(emb)}, "
                        f"expected {expected_dim}. Check model configuration."
                    )

            return embeddings

        except EmbeddingError:
            raise
        except Exception as e:
            logger.error(f"Error getting batch embeddings from OpenRouter: {e}")
            logger.warning("Falling back to individual embedding requests")
            # Fall back to individual processing — failures propagate
            return [self._get_embedding(t) for t in processed_texts]

    def _get_embedding(self, text: str) -> List[float]:
        """Get embedding from OpenRouter API.

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

        try:
            response = self._client.embeddings.create(
                input=text,
                model=self._config["model"]
            )

            # Validate response
            if not response.data or len(response.data) == 0:
                raise EmbeddingError(
                    f"No embedding data received from OpenRouter for text length: {len(text)}"
                )

            embedding = response.data[0].embedding

            # Validate embedding dimensions — hard error, never silently adjust
            if len(embedding) != expected_dim:
                raise EmbeddingError(
                    f"Embedding dimension mismatch: got {len(embedding)}, expected {expected_dim}. "
                    f"The Qdrant collection was created with dimension {expected_dim}. "
                    f"Check that the model '{self._config['model']}' produces {expected_dim}-dim vectors."
                )

            return embedding

        except EmbeddingError:
            raise
        except Exception as e:
            logger.error(f"Error getting embedding from OpenRouter: {e}")
            logger.error(f"Text length: {len(text) if text else 0}, Text preview: {text[:100] if text else 'None'}...")
            raise

    async def _aget_query_embedding(self, query: str) -> List[float]:
        """Async get embedding for a query text (offloaded to thread pool)."""
        return await asyncio.to_thread(self._get_query_embedding, query)

    async def _aget_text_embedding(self, text: str) -> List[float]:
        """Async get embedding for a text (offloaded to thread pool)."""
        return await asyncio.to_thread(self._get_text_embedding, text)

    async def _aget_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Async batch get embeddings for multiple texts (offloaded to thread pool)."""
        return await asyncio.to_thread(self._get_text_embeddings, texts)
