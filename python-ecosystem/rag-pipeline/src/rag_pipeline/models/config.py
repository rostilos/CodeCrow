"""Runtime configuration for the structural repository index."""

import os
from typing import Optional

from pydantic import BaseModel, Field


DEFAULT_MAX_FILE_SIZE_BYTES = 512 * 1024


class RAGConfig(BaseModel):
    """Structural index configuration."""

    structural_index_root: str = Field(
        default_factory=lambda: os.getenv(
            "STRUCTURAL_INDEX_ROOT",
            "/var/lib/codecrow/structural-index",
        )
    )

    full_index_concurrency: int = Field(
        default_factory=lambda: int(os.getenv("RAG_FULL_INDEX_CONCURRENCY", "1")),
        ge=1,
    )
    rag_mutation_lease_seconds: int = Field(
        default_factory=lambda: int(os.getenv("RAG_MUTATION_LEASE_SECONDS", "300")),
        ge=30,
    )
    rag_mutation_acquire_timeout_seconds: float = Field(
        default_factory=lambda: float(
            os.getenv("RAG_MUTATION_ACQUIRE_TIMEOUT_SECONDS", "5")
        ),
        ge=0,
    )
    architecture_finalization_timeout_seconds: float = Field(
        default_factory=lambda: float(
            os.getenv("RAG_ARCHITECTURE_FINALIZATION_TIMEOUT_SECONDS", "600")
        ),
        ge=1,
    )
    review_generation_ttl_seconds: int = Field(
        default_factory=lambda: int(
            os.getenv("RAG_REVIEW_GENERATION_TTL_SECONDS", "21600")
        ),
        ge=300,
    )
    chunk_size: int = Field(default=8000)
    chunk_overlap: int = Field(default=200)
    max_file_size_bytes: int = Field(
        default_factory=lambda: int(
            os.getenv(
                "RAG_MAX_FILE_SIZE_BYTES",
                str(DEFAULT_MAX_FILE_SIZE_BYTES),
            )
        ),
        ge=1,
    )

    excluded_patterns: list[str] = Field(default_factory=lambda: [
        "node_modules/**", ".venv/**", "venv/**", "__pycache__/**",
        "*.pyc", "*.pyo", "*.so", "*.dll", "*.dylib", "*.exe",
        "*.bin", "*.jar", "*.war", "*.class", "target/**", "build/**",
        "dist/**", ".git/**", ".idea/**", "*.min.js", "*.min.css",
        "*.bundle.js", "*.lock", "package-lock.json", "yarn.lock",
        "bun.lockb", "*.bak", "*.orig", "*.map",
    ])

    max_chunks_per_index: int = Field(
        default_factory=lambda: int(os.getenv("RAG_MAX_CHUNKS_PER_INDEX", "1000000"))
    )
    max_files_per_index: int = Field(
        default_factory=lambda: int(os.getenv("RAG_MAX_FILES_PER_INDEX", "50000"))
    )
class IndexStats(BaseModel):
    namespace: str
    document_count: int
    chunk_count: int
    skipped_file_count: int = 0
    skipped_chunk_count: int = 0
    last_updated: str
    workspace: str
    project: str
    branch: str
    generation_manifest_sha256: Optional[str] = None
    source_tree_sha256: Optional[str] = None
    collection_target: Optional[str] = None
    generation_member_count: Optional[int] = None
