"""Core functionality for indexing and document processing"""
__all__ = [
    "DocumentLoader",
    "CodeAwareSplitter",
    "FunctionAwareSplitter",
    "SemanticCodeSplitter",
    "RAGIndexManager"
]

from .index_manager import RAGIndexManager
from .chunking import CodeAwareSplitter, FunctionAwareSplitter
from .semantic_splitter import SemanticCodeSplitter
from .loader import DocumentLoader


