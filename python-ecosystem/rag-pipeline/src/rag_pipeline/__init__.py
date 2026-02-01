"""
CodeCrow RAG Pipeline

A RAG (Retrieval-Augmented Generation) pipeline for code repositories.
Provides indexing and querying capabilities for code using LlamaIndex, Tree-sitter and Qdrant.
"""

__version__ = "1.0.0"

from .models.config import RAGConfig, IndexStats
from .core.index_manager import RAGIndexManager
from .services.query_service import RAGQueryService
from .core.loader import DocumentLoader
from .core.splitter import ASTCodeSplitter
from .utils.utils import make_namespace, detect_language_from_path

__all__ = [
    "RAGConfig",
    "IndexStats",
    "RAGIndexManager",
    "RAGQueryService",
    "DocumentLoader",
    "ASTCodeSplitter",
    "make_namespace",
    "detect_language_from_path",
]

