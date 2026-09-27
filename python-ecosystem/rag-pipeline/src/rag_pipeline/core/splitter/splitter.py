"""Compose syntax selection, semantic extraction and source-node rendering."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from langchain_text_splitters import Language, RecursiveCharacterTextSplitter
from ..documents import Document, TextNode
from .metadata import MetadataExtractor, ContentType
from .chunk import ASTChunk, generate_deterministic_id, compute_file_hash, normalize_chunk, normalize_metadata

logger = logging.getLogger(__name__)

from .languages import AST_SUPPORTED_LANGUAGES, LANGUAGE_TO_TREESITTER, get_language_from_path, get_treesitter_name, is_ast_supported
from .tree_parser import get_parser
from .query_runner import get_query_runner
from .semantic_extractor import SemanticExtractor
from .chunk_emitter import ChunkEmitter

class ASTCodeSplitter:
    """Select syntax and isolate per-file parsing failures for the indexer."""
    DEFAULT_MAX_CHUNK_SIZE = ChunkEmitter.DEFAULT_MAX_CHUNK_SIZE
    DEFAULT_MIN_CHUNK_SIZE = 100
    DEFAULT_CHUNK_OVERLAP = 200
    DEFAULT_PARSER_THRESHOLD = 3

    def __init__(self, max_chunk_size=DEFAULT_MAX_CHUNK_SIZE,
                 min_chunk_size=DEFAULT_MIN_CHUNK_SIZE,
                 chunk_overlap=DEFAULT_CHUNK_OVERLAP,
                 parser_threshold=DEFAULT_PARSER_THRESHOLD, plugin_runtime=None):
        self.max_chunk_size = max_chunk_size
        self.min_chunk_size = min_chunk_size
        self.chunk_overlap = chunk_overlap
        self.parser_threshold = parser_threshold
        self._plugin_runtime = plugin_runtime
        self._parser = get_parser()
        metadata = MetadataExtractor()
        self.extractor = SemanticExtractor(self._parser, get_query_runner(), metadata)
        self.emitter = ChunkEmitter(max_chunk_size=max_chunk_size,
                                    min_chunk_size=min_chunk_size,
                                    chunk_overlap=chunk_overlap, metadata_extractor=metadata)

    def split_documents(
        self,
        documents: List[Document],
        capabilities: Any = None,
    ) -> List[TextNode]:
        """
        Split repository documents using AST-based parsing.

        Args:
            documents: Repository source documents

        Returns:
            List of TextNode objects with enriched metadata
        """
        all_nodes = []

        for doc in documents:
            path = doc.metadata.get('path', 'unknown')
            language = get_language_from_path(path)
            syntax = None
            syntax_diagnostics = ()
            if self._plugin_runtime is not None and capabilities is not None:
                syntax, syntax_diagnostics = (
                    self._plugin_runtime.syntax_contribution(path, capabilities)
                )

            line_count = doc.text.count('\n') + 1
            if syntax is not None:
                use_ast = (
                    line_count >= self.parser_threshold
                    and self._parser.is_available()
                    and self._parser.get_plugin_language(syntax) is not None
                )
            else:
                use_ast = (
                    language is not None
                    and language in AST_SUPPORTED_LANGUAGES
                    and line_count >= self.parser_threshold
                    and self._parser.is_available()
                )

            if use_ast:
                nodes = self._split_with_ast(doc, language, syntax)
            else:
                nodes = self.emitter._split_fallback(
                    doc,
                    language if syntax is None else None,
                    extract_language_metadata=syntax is None,
                )

            if syntax is not None or syntax_diagnostics:
                self._attach_syntax_metadata(
                    nodes,
                    syntax,
                    syntax_diagnostics,
                )

            all_nodes.extend(nodes)
            logger.debug(f"Split {path} into {len(nodes)} chunks (AST={use_ast})")

        return all_nodes


    def split_documents_resilient(
        self,
        documents: List[Document],
        capabilities: Any = None,
    ) -> tuple[List[TextNode], tuple[str, ...]]:
        """Split documents independently and quarantine file-local failures."""
        nodes: List[TextNode] = []
        skipped_paths: list[str] = []
        for document in documents:
            path = document.metadata.get("path", "unknown")
            try:
                nodes.extend(self.split_documents(
                    [document],
                    capabilities=capabilities,
                ))
            except MemoryError:
                raise
            except Exception:
                skipped_paths.append(path)
                logger.exception(
                    "Skipping repository file that failed parsing/enrichment: %s",
                    path,
                )
        return nodes, tuple(skipped_paths)


    @staticmethod
    def _attach_syntax_metadata(
        nodes: List[TextNode],
        syntax: Any,
        diagnostics: tuple,
    ) -> None:
        """Expose bounded selection diagnostics without placing them in chunk text."""
        for node in nodes:
            if syntax is not None:
                node.metadata["plugin_syntax"] = {
                    "plugin": syntax.plugin_id,
                    "language": syntax.language_id,
                }
            if diagnostics:
                node.metadata["plugin_syntax_diagnostics"] = [
                    {
                        "code": diagnostic.code,
                        "plugin": diagnostic.plugin_id,
                    }
                    for diagnostic in diagnostics
                ]


    def _split_with_ast(
        self,
        doc: Document,
        language: Optional[Language],
        syntax: Any = None,
    ) -> List[TextNode]:
        """Split document using AST parsing with query-based extraction."""
        text = doc.text
        path = doc.metadata.get('path', 'unknown')
        ts_lang = (
            syntax.language_id
            if syntax is not None
            else get_treesitter_name(language)
        )

        if not ts_lang:
            return self.emitter._split_fallback(
                doc,
                language if syntax is None else None,
                extract_language_metadata=syntax is None,
            )

        # Try query-based extraction first
        chunks = self.extractor._extract_with_queries(text, ts_lang, path, syntax)

        # If no queries available, fall back to traversal-based extraction
        if not chunks and self.extractor.details._is_rich_ast_traversal_safe(ts_lang, syntax):
            chunks = self.extractor._extract_with_traversal(text, ts_lang, path, syntax)

        # Still no chunks? Use fallback
        if not chunks:
            return self.emitter._split_fallback(
                doc,
                language if syntax is None else None,
                extract_language_metadata=syntax is None,
            )

        return self.emitter._process_chunks(chunks, doc, language, path)


    @staticmethod
    def get_supported_languages() -> List[str]:
        """Return list of languages with AST support."""
        return list(LANGUAGE_TO_TREESITTER.values())


    @staticmethod
    def is_ast_supported(path: str) -> bool:
        """Check if AST parsing is supported for a file."""
        return is_ast_supported(path)
