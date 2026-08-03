"""
Embedding factory for creating embedding models based on configuration.
Supports switching between local (Ollama) and cloud (OpenRouter) providers.
"""

import logging
import os
from typing import Union

from llama_index.core.base.embeddings.base import BaseEmbedding

from ..models.config import RAGConfig
from .ollama_embedding import OllamaEmbedding
from .openrouter_embedding import OpenRouterEmbedding

logger = logging.getLogger(__name__)


def create_embedding_model(
    config: RAGConfig,
    *,
    workload: str = "index",
) -> BaseEmbedding:
    """
    Create an embedding model based on the configuration.
    
    Args:
        config: RAGConfig with embedding provider settings
        
    Returns:
        BaseEmbedding instance (OllamaEmbedding or OpenRouterEmbedding)
    """
    provider = config.embedding_provider.lower()
    
    if provider == "ollama":
        timeout = float(os.getenv("OLLAMA_TIMEOUT", "120"))
        logger.info(f"Creating Ollama embedding model: {config.ollama_model} (timeout={timeout}s)")
        return OllamaEmbedding(
            model=config.ollama_model,
            base_url=config.ollama_base_url,
            timeout=timeout,
            expected_dim=config.embedding_dim
        )
    
    elif provider == "openrouter":
        timeout = float(os.getenv("OPENROUTER_TIMEOUT", "300"))
        provider_sort = (
            config.openrouter_query_provider_sort
            if workload == "query"
            else config.openrouter_index_provider_sort
        )
        logger.info(
            "Creating OpenRouter embedding model: %s "
            "(workload=%s timeout=%ss provider_sort=%s)",
            config.openrouter_model,
            workload,
            timeout,
            provider_sort,
        )
        return OpenRouterEmbedding(
            api_key=config.openrouter_api_key,
            model=config.openrouter_model,
            api_base=config.openrouter_base_url,
            timeout=timeout,
            max_retries=3,
            expected_dim=config.embedding_dim,
            embed_batch_size=config.openrouter_batch_size,
            workload=workload,
            provider_sort=provider_sort,
            index_concurrency=config.openrouter_index_concurrency,
            service_max_in_flight=config.openrouter_max_in_flight,
            redis_url=os.getenv("REDIS_URL", "redis://redis:6379/1"),
        )
    
    else:
        logger.warning(f"Unknown embedding provider '{provider}', defaulting to Ollama")
        timeout = float(os.getenv("OLLAMA_TIMEOUT", "120"))
        return OllamaEmbedding(
            model=config.ollama_model,
            base_url=config.ollama_base_url,
            timeout=timeout,
            expected_dim=config.embedding_dim
        )


def get_embedding_model_info(config: RAGConfig) -> dict:
    """
    Get information about the configured embedding model.
    
    Args:
        config: RAGConfig with embedding provider settings
        
    Returns:
        Dictionary with provider info
    """
    provider = config.embedding_provider.lower()
    
    if provider == "ollama":
        return {
            "provider": "ollama",
            "model": config.ollama_model,
            "base_url": config.ollama_base_url,
            "embedding_dim": config.embedding_dim,
            "type": "local"
        }
    elif provider == "openrouter":
        return {
            "provider": "openrouter", 
            "model": config.openrouter_model,
            "base_url": config.openrouter_base_url,
            "embedding_dim": config.embedding_dim,
            "type": "cloud"
        }
    else:
        return {
            "provider": provider,
            "embedding_dim": config.embedding_dim,
            "type": "unknown"
        }
