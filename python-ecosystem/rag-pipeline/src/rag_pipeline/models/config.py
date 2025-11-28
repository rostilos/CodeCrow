import os
from typing import Optional
from pydantic import BaseModel, Field, field_validator
import logging
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


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

    # Embedding dimensions (text-embedding-3-small uses 1536 dimensions)
    embedding_dim: int = Field(default=1536)

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

