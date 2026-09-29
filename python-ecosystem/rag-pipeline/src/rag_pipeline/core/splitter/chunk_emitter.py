"""Render complete semantic source units without embedding-size fragmentation."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from langchain_text_splitters import Language
from ..documents import Document, TextNode
from .metadata import ContentType
from .chunk import ASTChunk, generate_deterministic_id, normalize_chunk, normalize_metadata


class ChunkEmitter:
    # Retained for existing ASTCodeSplitter constructor defaults, not an output cap.
    DEFAULT_MAX_CHUNK_SIZE = 8000

    def __init__(self, *, metadata_extractor):
        self._metadata_extractor = metadata_extractor

    def _process_chunks(
        self, chunks: List[ASTChunk], doc: Document,
        language: Optional[Language], path: str,
    ) -> List[TextNode]:
        """Keep each declaration and its complete structural metadata together."""
        return [TextNode(
            id_=generate_deterministic_id(path, chunk.content, index),
            text=chunk.content,
            metadata=self._build_metadata(chunk, doc.metadata, index, len(chunks)),
        ) for index, chunk in enumerate(chunks)]

    def _split_fallback(
        self, doc: Document, language: Optional[Language] = None,
        extract_language_metadata: bool = True,
    ) -> List[TextNode]:
        """Retain complete source when no syntax provider can define ownership."""
        if not doc.text:
            return []
        path = doc.metadata.get("path", "unknown")
        metadata = {
            **doc.metadata,
            "content_type": ContentType.FALLBACK.value,
            "chunk_index": 0,
            "total_chunks": 1,
            "start_line": 1,
            "end_line": doc.text.count("\n") + 1,
            "start_byte": 0,
            "end_byte": len(doc.text.encode("utf-8")),
        }
        if extract_language_metadata:
            lang_str = doc.metadata.get("language", "text")
            names = self._metadata_extractor.extract_names_from_content(doc.text, lang_str)
            if names:
                metadata["symbol_names"] = names
                metadata["primary_name"] = names[0]
            inheritance = self._metadata_extractor.extract_inheritance(doc.text, lang_str)
            for field in ("extends", "implements", "imports"):
                if inheritance.get(field):
                    metadata[field] = inheritance[field]
            if inheritance.get("extends"):
                metadata["parent_types"] = inheritance["extends"]
            normalize_metadata(metadata)
        metadata["information_density"] = self._compute_information_density(metadata)
        return [TextNode(id_=generate_deterministic_id(path, doc.text),
                         text=doc.text, metadata=metadata)]

    def _build_metadata(
        self,
        chunk: ASTChunk,
        base_metadata: Dict[str, Any],
        chunk_index: int,
        total_chunks: int
    ) -> Dict[str, Any]:
        """Build metadata dictionary from ASTChunk."""
        normalize_chunk(chunk)
        metadata = dict(base_metadata)

        metadata['content_type'] = chunk.content_type.value
        metadata['node_type'] = chunk.node_type
        metadata['chunk_index'] = chunk_index
        metadata['total_chunks'] = total_chunks
        metadata['start_line'] = chunk.start_line
        metadata['end_line'] = chunk.end_line

        if chunk.parent_context:
            metadata['parent_context'] = chunk.parent_context
            metadata['parent_class'] = chunk.parent_context[-1]
            # ``full_path`` is the breadcrumb plus the primary symbol.  The
            # complete inventory is stored independently in ``symbol_names``.
            primary_leaf = [chunk.symbol_names[0]] if chunk.symbol_names else []
            metadata['full_path'] = '.'.join(chunk.parent_context + primary_leaf)

        if chunk.symbol_names:
            metadata['symbol_names'] = chunk.symbol_names
            metadata['primary_name'] = chunk.symbol_names[0]

        if chunk.docstring:
            metadata['docstring'] = chunk.docstring

        if chunk.signature:
            metadata['signature'] = chunk.signature

        if chunk.extends:
            metadata['extends'] = chunk.extends
            metadata['parent_types'] = chunk.extends

        if chunk.implements:
            metadata['implements'] = chunk.implements

        if chunk.imports:
            metadata['imports'] = chunk.imports

        if chunk.namespace:
            metadata['namespace'] = chunk.namespace

        # --- RICH AST METADATA ---

        if chunk.methods:
            metadata['methods'] = chunk.methods

        if chunk.properties:
            metadata['properties'] = chunk.properties

        if chunk.parameters:
            metadata['parameters'] = chunk.parameters

        if chunk.return_type:
            metadata['return_type'] = chunk.return_type

        if chunk.decorators:
            metadata['decorators'] = chunk.decorators

        if chunk.modifiers:
            metadata['modifiers'] = chunk.modifiers

        if chunk.calls:
            metadata['calls'] = chunk.calls

        if chunk.referenced_types:
            metadata['referenced_types'] = chunk.referenced_types

        if chunk.variables:
            metadata['variables'] = chunk.variables

        if chunk.constants:
            metadata['constants'] = chunk.constants

        if chunk.type_parameters:
            metadata['type_parameters'] = chunk.type_parameters

        if chunk.metadata_partial_reasons:
            metadata['structural_metadata_complete'] = False
            metadata['structural_metadata_partial_reasons'] = sorted(
                chunk.metadata_partial_reasons
            )

        # Compute information density — ratio of meaningful AST signals per line
        metadata['information_density'] = self._compute_information_density(metadata)

        return metadata


    @staticmethod
    def _compute_information_density(metadata: Dict[str, Any]) -> float:
        """
        Compute information density for a chunk based on its AST metadata.

        Measures how much meaningful structure a chunk contains per line of code.
        Low-density chunks (e.g., import-only files, blank boilerplate, config dumps)
        contribute noise to RAG results and should be scored lower.

        The metric counts distinct categories of AST signal present in the chunk:
        - Structural: symbol_names, signature, methods, properties
        - Relational: extends, implements, calls, referenced_types
        - Documentation: docstring
        - Declarations: parameters, variables, constants, decorators

        Returns a float in [0.0, 1.0] representing the density.
        """
        line_span = max(metadata.get('end_line', 1) - metadata.get('start_line', 0), 1)

        # Count distinct meaningful signals (not their individual items —
        # a 200-line class with 50 methods is dense, but so is a 10-line
        # class with 3 methods. We care about signals-per-line.)
        signal_count = 0

        # Structural identity signals (high value)
        if metadata.get('symbol_names'):
            signal_count += len(metadata['symbol_names'])
        if metadata.get('signature'):
            signal_count += 1

        # Contained definitions (high value — indicates the chunk defines things)
        signal_count += len(metadata.get('methods', []))
        signal_count += len(metadata.get('properties', []))
        signal_count += len(metadata.get('constants', []))

        # Type relationships (medium value — indicates structural connections)
        signal_count += len(metadata.get('extends', []))
        signal_count += len(metadata.get('implements', []))

        # Dependencies & usage (medium value)
        # Cap calls/referenced_types to avoid inflating density for huge call-heavy functions
        signal_count += min(len(metadata.get('calls', [])), 10)
        signal_count += min(len(metadata.get('referenced_types', [])), 10)

        # Documentation (small bonus)
        if metadata.get('docstring'):
            signal_count += 1

        # Parameters and decorators (small bonus)
        signal_count += min(len(metadata.get('parameters', [])), 5)
        signal_count += min(len(metadata.get('decorators', [])), 3)

        # Density = signals per line, capped at 1.0
        # A well-structured 20-line function typically has:
        #   name(1) + signature(1) + params(2-3) + calls(3-5) + types(1-2) = ~10 signals
        #   density = 10/20 = 0.5 (good)
        # A 200-line import-only block:
        #   no names, no signature, no methods = ~0 signals
        #   density = 0/200 = 0.0 (bad)
        density = signal_count / line_span
        return round(min(density, 1.0), 4)


