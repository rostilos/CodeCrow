"""
AST-based code splitter module using Tree-sitter.

Provides semantic code chunking with:
- Tree-sitter query-based extraction (.scm files)
- Fallback to manual AST traversal
- RecursiveCharacterTextSplitter for oversized chunks
- Rich metadata extraction for RAG
"""

from .splitter import ASTCodeSplitter, ASTChunk, generate_deterministic_id, compute_file_hash
from .languages import (
    get_language_from_path,
    get_treesitter_name,
    is_ast_supported,
    get_supported_languages,
    EXTENSION_TO_LANGUAGE,
    AST_SUPPORTED_LANGUAGES,
    LANGUAGE_TO_TREESITTER,
)
from .metadata import ContentType, ChunkMetadata, MetadataExtractor
from .tree_parser import TreeSitterParser, get_parser
from .query_runner import QueryRunner, QueryMatch, CapturedNode, get_query_runner

__all__ = [
    # Main splitter
    "ASTCodeSplitter",
    "ASTChunk",
    "generate_deterministic_id",
    "compute_file_hash",
    
    # Languages
    "get_language_from_path",
    "get_treesitter_name",
    "is_ast_supported",
    "get_supported_languages",
    "EXTENSION_TO_LANGUAGE",
    "AST_SUPPORTED_LANGUAGES",
    "LANGUAGE_TO_TREESITTER",
    
    # Metadata
    "ContentType",
    "ChunkMetadata",
    "MetadataExtractor",
    
    # Tree-sitter
    "TreeSitterParser",
    "get_parser",
    "QueryRunner",
    "QueryMatch",
    "CapturedNode",
    "get_query_runner",
]
