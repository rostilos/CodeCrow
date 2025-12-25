import os
from typing import Optional
from pydantic import BaseModel, Field, field_validator, model_validator
import logging
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

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
    # Alibaba models (often 4096)
    "qwen/qwen-embedding": 4096,
    "qwen/qwen3-embedding-8b": 4096,
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
    load_dotenv()
    # Qdrant for vector storage
    qdrant_url: str = Field(default_factory=lambda: os.getenv("QDRANT_URL", "http://localhost:6333"))
    qdrant_collection_prefix: str = Field(default_factory=lambda: os.getenv("QDRANT_COLLECTION_PREFIX", "codecrow"))

    # OpenRouter for embeddings
    openrouter_api_key: str = Field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY", ""))
    openrouter_model: str = Field(default_factory=lambda: os.getenv("OPENROUTER_MODEL", "openai/text-embedding-3-small"))
    openrouter_base_url: str = Field(default="https://openrouter.ai/api/v1")

    # Embedding dimensions - auto-detected from model or set via env var
    # Common dimensions: text-embedding-3-small=1536, text-embedding-3-large=3072, some models=4096
    embedding_dim: int = Field(default_factory=lambda: int(os.getenv("EMBEDDING_DIM", "0")))

    @model_validator(mode='after')
    def set_embedding_dim_from_model(self) -> 'RAGConfig':
        """Auto-detect embedding dimension from model if not explicitly set."""
        if self.embedding_dim == 0:
            self.embedding_dim = get_embedding_dim_for_model(self.openrouter_model)
            logger.info(f"Auto-detected embedding dimension {self.embedding_dim} for model {self.openrouter_model}")
        else:
            logger.info(f"Using configured embedding dimension: {self.embedding_dim}")
        return self

    @field_validator('openrouter_api_key')
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        if not v or v.strip() == "":
            logger.error("OPENROUTER_API_KEY is not set or is empty!")
            logger.error("Please set the OPENROUTER_API_KEY environment variable")
            raise ValueError("OPENROUTER_API_KEY is required but not set")
        logger.info(f"OpenRouter API key loaded: {v[:10]}...{v[-4:]}")
        return v

    chunk_size: int = Field(default=800)
    chunk_overlap: int = Field(default=200)

    text_chunk_size: int = Field(default=1000)
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


class DocumentMetadata(BaseModel):
    """Metadata for indexed documents"""
    workspace: str
    project: str
    branch: str
    path: str
    commit: str
    language: str
    filetype: str
    chunk_index: Optional[int] = None


class IndexStats(BaseModel):
    """Statistics about an index"""
    namespace: str
    document_count: int
    chunk_count: int
    last_updated: str
    workspace: str
    project: str
    branch: str

