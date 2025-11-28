"""
CodeCrow RAG Pipeline

A RAG (Retrieval-Augmented Generation) pipeline for code repositories.
Provides indexing and querying capabilities for code using LlamaIndex and MongoDB.
"""

__version__ = "1.0.0"

from .models.config import RAGConfig, DocumentMetadata, IndexStats
from .core.index_manager import RAGIndexManager
from .services.query_service import RAGQueryService
from .core.loader import DocumentLoader
from .core.chunking import CodeAwareSplitter, FunctionAwareSplitter
from .utils.utils import make_namespace, detect_language_from_path

__all__ = [
    "RAGConfig",
    "DocumentMetadata",
    "IndexStats",
    "RAGIndexManager",
    "RAGQueryService",
    "DocumentLoader",
    "CodeAwareSplitter",
    "FunctionAwareSplitter",
    "make_namespace",
    "detect_language_from_path",
]

