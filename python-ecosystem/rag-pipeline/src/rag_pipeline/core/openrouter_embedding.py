"""
Custom OpenRouter embedding wrapper for LlamaIndex.
Bypasses model name validation to work with OpenRouter's model naming format.
Supports batch embeddings for efficient processing.
"""

import asyncio
from contextlib import nullcontext
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import os
import threading
import time
from typing import Any, List, Optional
from llama_index.core.base.embeddings.base import BaseEmbedding
from openai import OpenAI
import logging

from ..models.config import get_embedding_dim_for_model
from .coordination import RedisPermitPool
from .ollama_embedding import EmbeddingError

logger = logging.getLogger(__name__)

# Batch size for embedding requests (OpenAI/OpenRouter limit is typically 2048)
EMBEDDING_BATCH_SIZE = int(os.getenv("OPENROUTER_BATCH_SIZE", "50"))
MAX_CHARS = int(os.getenv("OPENROUTER_MAX_CHARS", "24000"))
OPENROUTER_APP_HEADERS = {
    "HTTP-Referer": "https://codecrow.cloud",
    "X-Title": "CodeCrow AI",
}


class _AdaptiveConcurrencyGate:
    """Reduce live requests after overload and recover after stable success."""

    def __init__(self, limit: int, recovery_successes: int = 8):
        self.maximum = max(1, limit)
        self.limit = self.maximum
        self.recovery_successes = max(1, recovery_successes)
        self.active = 0
        self.successes = 0
        self.condition = threading.Condition()

    def acquire(self) -> None:
        with self.condition:
            while self.active >= self.limit:
                self.condition.wait()
            self.active += 1

    def record_overload(self) -> None:
        with self.condition:
            previous = self.limit
            self.limit = max(1, self.limit // 2)
            self.successes = 0
            if self.limit != previous:
                logger.warning(
                    "Reduced OpenRouter index concurrency from %s to %s",
                    previous,
                    self.limit,
                )
            self.condition.notify_all()

    def release(self, *, successful: bool) -> None:
        with self.condition:
            self.active -= 1
            if successful:
                self.successes += 1
                if (
                    self.limit < self.maximum
                    and self.successes >= self.recovery_successes
                ):
                    self.limit += 1
                    self.successes = 0
                    logger.info(
                        "Recovered OpenRouter index concurrency to %s",
                        self.limit,
                    )
            self.condition.notify_all()

    def snapshot(self) -> tuple[int, int]:
        with self.condition:
            return self.limit, self.active


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
        timeout: float = float(os.getenv("OPENROUTER_TIMEOUT", "300")),
        max_retries: int = 3,
        embed_batch_size: int = EMBEDDING_BATCH_SIZE,
        expected_dim: Optional[int] = None,
        max_chars: Optional[int] = None,
        workload: str = "index",
        provider_sort: Optional[str] = None,
        index_concurrency: int = 8,
        service_max_in_flight: int = 16,
        redis_url: str = "redis://redis:6379/1",
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

        logger.info("OpenRouterEmbedding: API key configured")
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
            "workload": workload,
            "provider_sort": provider_sort,
            "index_concurrency": max(1, index_concurrency),
            "service_max_in_flight": max(1, service_max_in_flight),
        })

        object.__setattr__(
            self,
            "_adaptive_gate",
            _AdaptiveConcurrencyGate(index_concurrency)
            if workload == "index"
            else None,
        )
        object.__setattr__(
            self,
            "_permit_pool",
            RedisPermitPool(
                redis_url,
                service_max_in_flight,
                permit_seconds=max(
                    60,
                    int(timeout) * (max_retries + 1) + 60,
                ),
                acquire_timeout_seconds=min(30.0, max(1.0, timeout)),
            )
            if workload == "index"
            else None,
        )

        # Initialize OpenAI client pointed at OpenRouter
        object.__setattr__(self, '_client', OpenAI(
            api_key=api_key,
            base_url=api_base,
            timeout=timeout,
            # Retry explicitly so every rejected attempt is observable and
            # can reduce indexing concurrency. All retries retain the batch.
            max_retries=0,
            default_headers=OPENROUTER_APP_HEADERS,
        ))

        logger.info(f"OpenRouter embeddings initialized successfully")

    def close(self):
        """Close the OpenAI client and free resources."""
        try:
            if hasattr(self, '_client') and self._client:
                self._client.close()
                logger.info("OpenRouter embedding client closed")
            permit_pool = getattr(self, "_permit_pool", None)
            if permit_pool is not None:
                permit_pool.close()
        except Exception as e:
            logger.warning(f"Error closing OpenRouter client: {e}")

    def __del__(self):
        """Destructor to ensure client is closed."""
        self.close()

    @property
    def model(self) -> str:
        """Get the model name."""
        return self._config["model"]

    @staticmethod
    def _status_code(exception: Exception) -> Optional[int]:
        status_code = getattr(exception, "status_code", None)
        if isinstance(status_code, int):
            return status_code
        response = getattr(exception, "response", None)
        status_code = getattr(response, "status_code", None)
        return status_code if isinstance(status_code, int) else None

    @classmethod
    def _is_overload(cls, exception: Exception) -> bool:
        status_code = cls._status_code(exception)
        if status_code in {408, 409, 425, 429, 529} or (
            status_code is not None and 500 <= status_code <= 599
        ):
            return True
        name = type(exception).__name__.casefold()
        message = str(exception).casefold()
        return any(
            marker in name or marker in message
            for marker in ("timeout", "connection", "rate limit", "overload")
        )

    @staticmethod
    def _retry_after_seconds(exception: Exception, attempt: int) -> float:
        """Respect provider retry guidance, with bounded exponential fallback."""
        response = getattr(exception, "response", None)
        headers = getattr(response, "headers", None) or {}
        retry_after_ms = headers.get("retry-after-ms")
        if retry_after_ms is not None:
            try:
                return min(60.0, max(0.0, float(retry_after_ms) / 1000.0))
            except (TypeError, ValueError):
                pass
        retry_after = headers.get("retry-after")
        if retry_after is not None:
            try:
                return min(60.0, max(0.0, float(retry_after)))
            except (TypeError, ValueError):
                try:
                    retry_at = parsedate_to_datetime(str(retry_after))
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    return min(
                        60.0,
                        max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds()),
                    )
                except (TypeError, ValueError, OverflowError):
                    pass
        return min(8.0, 0.5 * (2 ** attempt))

    def _request_embeddings(self, input_value):
        """Issue one measured request through routing and capacity controls."""
        gate = getattr(self, "_adaptive_gate", None)
        pool = getattr(self, "_permit_pool", None)
        if gate is not None:
            gate.acquire()
        started = time.perf_counter()
        successful = False
        try:
            extra_body = None
            provider_sort = self._config.get("provider_sort")
            if provider_sort and provider_sort != "price":
                extra_body = {"provider": {"sort": provider_sort}}
            request_kwargs = {
                "input": input_value,
                "model": self._config["model"],
            }
            if extra_body is not None:
                request_kwargs["extra_body"] = extra_body
            text_count = len(input_value) if isinstance(input_value, list) else 1
            char_count = (
                sum(len(value) for value in input_value)
                if isinstance(input_value, list)
                else len(input_value)
            )
            max_retries = max(0, int(self._config.get("max_retries", 0)))
            for attempt in range(max_retries + 1):
                permit_started = time.perf_counter()
                capacity_wait_ms = 0
                provider_duration_ms = 0
                provider_started = None
                try:
                    with pool.permit() if pool is not None else nullcontext():
                        capacity_wait_ms = round(
                            (time.perf_counter() - permit_started) * 1000
                        )
                        provider_started = time.perf_counter()
                        response = self._client.embeddings.create(**request_kwargs)
                        provider_duration_ms = round(
                            (time.perf_counter() - provider_started) * 1000
                        )
                    elapsed = time.perf_counter() - started
                    usage = getattr(response, "usage", None)
                    token_count = (
                        getattr(usage, "total_tokens", None) if usage else None
                    )
                    model_extra = getattr(response, "model_extra", None) or {}
                    concurrency_limit, active_requests = (
                        gate.snapshot() if gate is not None else (None, None)
                    )
                    successful = True
                    logger.info(
                        "OpenRouter embedding request completed workload=%s "
                        "texts=%s chars=%s duration_ms=%s provider_duration_ms=%s "
                        "capacity_wait_ms=%s provider=%s request_id=%s tokens=%s "
                        "retry_count=%s concurrency_limit=%s active_requests=%s",
                        self._config.get("workload", "index"),
                        text_count,
                        char_count,
                        round(elapsed * 1000),
                        provider_duration_ms,
                        capacity_wait_ms,
                        model_extra.get("provider", "unknown"),
                        getattr(response, "_request_id", None),
                        token_count,
                        attempt,
                        concurrency_limit,
                        active_requests,
                    )
                    return response
                except Exception as exception:
                    if provider_started is not None:
                        provider_duration_ms = round(
                            (time.perf_counter() - provider_started) * 1000
                        )
                    overloaded = self._is_overload(exception)
                    if overloaded and gate is not None:
                        gate.record_overload()
                    concurrency_limit, active_requests = (
                        gate.snapshot() if gate is not None else (None, None)
                    )
                    will_retry = overloaded and attempt < max_retries
                    retry_after = (
                        self._retry_after_seconds(exception, attempt)
                        if will_retry
                        else None
                    )
                    logger.warning(
                        "OpenRouter embedding request attempt failed workload=%s "
                        "texts=%s chars=%s duration_ms=%s provider_duration_ms=%s "
                        "capacity_wait_ms=%s status=%s "
                        "overload=%s error_type=%s attempt=%s/%s "
                        "will_retry=%s retry_after_ms=%s concurrency_limit=%s "
                        "active_requests=%s",
                        self._config.get("workload", "index"),
                        text_count,
                        char_count,
                        round((time.perf_counter() - started) * 1000),
                        provider_duration_ms,
                        capacity_wait_ms,
                        self._status_code(exception),
                        overloaded,
                        type(exception).__name__,
                        attempt + 1,
                        max_retries + 1,
                        will_retry,
                        round(retry_after * 1000) if retry_after is not None else None,
                        concurrency_limit,
                        active_requests,
                    )
                    if not will_retry:
                        raise
                    time.sleep(retry_after)
        finally:
            if gate is not None:
                gate.release(successful=successful)

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
            response = self._request_embeddings(processed_texts)

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
            # Transient provider failures stay as batch failures. PointOperations
            # subdivides only provider-rejected request shapes (400/413/422).
            raise

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
            response = self._request_embeddings(text)

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
            logger.error("Rejected OpenRouter embedding text length: %s", len(text))
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
