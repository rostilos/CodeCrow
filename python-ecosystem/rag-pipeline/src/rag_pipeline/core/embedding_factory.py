"""
Embedding factory for creating embedding models based on configuration.
Supports switching between local (Ollama) and cloud (OpenRouter) providers.
"""

import logging
from typing import Union

from llama_index.core.base.embeddings.base import BaseEmbedding

from ..models.config import RAGConfig
from .ollama_embedding import OllamaEmbedding
from .openrouter_embedding import OpenRouterEmbedding

logger = logging.getLogger(__name__)


def create_embedding_model(config: RAGConfig) -> BaseEmbedding:
    """
    Create an embedding model based on the configuration.
    
    Args:
        config: RAGConfig with embedding provider settings
        
    Returns:
        BaseEmbedding instance (OllamaEmbedding or OpenRouterEmbedding)
    """
    provider = config.embedding_provider.lower()
    
    if provider == "ollama":
        logger.info(f"Creating Ollama embedding model: {config.ollama_model}")
        return OllamaEmbedding(
            model=config.ollama_model,
            base_url=config.ollama_base_url,
            timeout=120.0,
            expected_dim=config.embedding_dim
        )
    
    elif provider == "openrouter":
        logger.info(f"Creating OpenRouter embedding model: {config.openrouter_model}")
        return OpenRouterEmbedding(
            api_key=config.openrouter_api_key,
            model=config.openrouter_model,
            api_base=config.openrouter_base_url,
            timeout=60.0,
            max_retries=3,
            expected_dim=config.embedding_dim
        )
    
    else:
        logger.warning(f"Unknown embedding provider '{provider}', defaulting to Ollama")
        return OllamaEmbedding(
            model=config.ollama_model,
            base_url=config.ollama_base_url,
            timeout=120.0,
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
