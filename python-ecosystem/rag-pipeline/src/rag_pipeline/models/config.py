import os
from typing import Optional, Literal
from pydantic import BaseModel, Field, field_validator, model_validator
import logging
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Embedding provider types
EmbeddingProvider = Literal["ollama", "openrouter"]

# Known embedding model dimensions
EMBEDDING_MODEL_DIMENSIONS = {
    # OpenAI models
    "openai/text-embedding-3-small": 1536,
    "openai/text-embedding-3-large": 3072,
    "openai/text-embedding-ada-002": 1536,
    # Other common models via OpenRouter
    "mistralai/mistral-embed": 1024,
    "cohere/embed-english-v3.0": 1024,
    "cohere/embed-multilingual-v3.0": 1024,
    "voyage/voyage-large-2": 1536,
    "voyage/voyage-code-2": 1536,
    # Alibaba/Qwen models
    "qwen/qwen-embedding": 4096,
    "qwen/qwen3-embedding-0.6b": 1024,
    "qwen/qwen3-embedding-8b": 4096,
    # Ollama local models (same Qwen models)
    "qwen3-embedding-0.6b": 1024,
    "qwen3-embedding:0.6b": 1024,
    "snowflake-arctic-embed": 1024,
    "nomic-embed-text": 768,
    "mxbai-embed-large": 1024,
    "all-minilm": 384,
    # Default fallback
    "default": 1536,
}


def get_embedding_dim_for_model(model: str) -> int:
    """Get the embedding dimension for a given model."""
    if model in EMBEDDING_MODEL_DIMENSIONS:
        return EMBEDDING_MODEL_DIMENSIONS[model]
    # Check partial matches
    for key, dim in EMBEDDING_MODEL_DIMENSIONS.items():
        if key in model or model in key:
            return dim
    logger.warning(f"Unknown embedding model '{model}', using default dimension 1536")
    return EMBEDDING_MODEL_DIMENSIONS["default"]


class RAGConfig(BaseModel):
    """Configuration for RAG pipeline"""
    load_dotenv(interpolate=False)
    # Qdrant for vector storage
    qdrant_url: str = Field(default_factory=lambda: os.getenv("QDRANT_URL", "http://qdrant:6333"))
    qdrant_collection_prefix: str = Field(default_factory=lambda: os.getenv("QDRANT_COLLECTION_PREFIX", "codecrow"))

    # Embedding provider selection: "ollama" (local) or "openrouter" (cloud)
    embedding_provider: str = Field(default_factory=lambda: os.getenv("EMBEDDING_PROVIDER", "ollama"))
    
    # Ollama configuration (local embeddings - default)
    ollama_base_url: str = Field(default_factory=lambda: os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
    ollama_model: str = Field(default_factory=lambda: os.getenv("OLLAMA_EMBEDDING_MODEL", "qwen3-embedding:0.6b"))

    # OpenRouter configuration (cloud embeddings - optional)
    openrouter_api_key: str = Field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY", ""))
    openrouter_model: str = Field(default_factory=lambda: os.getenv("OPENROUTER_MODEL", "qwen/qwen3-embedding-8b"))
    openrouter_base_url: str = Field(default="https://openrouter.ai/api/v1")

    # Embedding dimensions - auto-detected from model or set via env var
    # Common dimensions: text-embedding-3-small=1536, text-embedding-3-large=3072, some models=4096
    embedding_dim: int = Field(default_factory=lambda: int(os.getenv("EMBEDDING_DIM", "0")))

    @model_validator(mode='after')
    def set_embedding_dim_from_model(self) -> 'RAGConfig':
        """Auto-detect embedding dimension from model if not explicitly set."""
        if self.embedding_dim == 0:
            # Use the model for the selected provider
            model = self.ollama_model if self.embedding_provider == "ollama" else self.openrouter_model
            self.embedding_dim = get_embedding_dim_for_model(model)
            logger.info(f"Auto-detected embedding dimension {self.embedding_dim} for model {model}")
        else:
            logger.info(f"Using configured embedding dimension: {self.embedding_dim}")
        return self

    @model_validator(mode='after')
    def validate_provider_config(self) -> 'RAGConfig':
        """Validate that the selected provider has required configuration."""
        if self.embedding_provider == "openrouter":
            if not self.openrouter_api_key or self.openrouter_api_key.strip() == "":
                logger.error("OPENROUTER_API_KEY is not set but embedding_provider is 'openrouter'!")
                raise ValueError("OPENROUTER_API_KEY is required when using OpenRouter provider")
            logger.info(f"Using OpenRouter embeddings with model: {self.openrouter_model}")
            logger.info(f"OpenRouter API key loaded: {self.openrouter_api_key[:10]}...{self.openrouter_api_key[-4:]}")
        elif self.embedding_provider == "ollama":
            logger.info(f"Using Ollama local embeddings with model: {self.ollama_model}")
            logger.info(f"Ollama base URL: {self.ollama_base_url}")
        else:
            logger.warning(f"Unknown embedding provider '{self.embedding_provider}', defaulting to 'ollama'")
            self.embedding_provider = "ollama"
        return self

    # Chunk size for code files
    # text-embedding-3-small supports ~8191 tokens (~32K chars)
    # 8000 chars keeps most semantic units (classes, functions) intact
    chunk_size: int = Field(default=8000)
    chunk_overlap: int = Field(default=200)

    # Text chunk size for non-code files (markdown, docs)
    text_chunk_size: int = Field(default=2000)
    text_chunk_overlap: int = Field(default=200)

    base_index_namespace: str = Field(default="code_rag")

    max_file_size_bytes: int = Field(default=1024 * 1024)  # 1MB

    excluded_patterns: list[str] = Field(default_factory=lambda: [
        "node_modules/**",
        ".venv/**",
        "venv/**",
        "__pycache__/**",
        "*.pyc",
        "*.pyo",
        "*.so",
        "*.dll",
        "*.dylib",
        "*.exe",
        "*.bin",
        "*.jar",
        "*.war",
        "*.class",
        "target/**",
        "build/**",
        "dist/**",
        ".git/**",
        ".idea/**",
        "*.min.js",
        "*.min.css",
        "*.bundle.js",
        "*.lock",
        "package-lock.json",
        "yarn.lock",
        "bun.lockb",
    ])

    retrieval_top_k: int = Field(default=10)
    similarity_threshold: float = Field(default=0.7)

    # Chunk limits
    max_chunks_per_index: int = Field(default_factory=lambda: int(os.getenv("RAG_MAX_CHUNKS_PER_INDEX", "1000000")))
    max_files_per_index: int = Field(default_factory=lambda: int(os.getenv("RAG_MAX_FILES_PER_INDEX", "50000")))

class IndexStats(BaseModel):
    """Statistics about an index"""
    namespace: str
    document_count: int
    chunk_count: int
    last_updated: str
    workspace: str
    project: str
    branch: str

