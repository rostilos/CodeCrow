"""Render semantic chunks and fallback source into stored text nodes."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from langchain_text_splitters import Language, RecursiveCharacterTextSplitter
from ..documents import Document, TextNode
from .metadata import MetadataExtractor, ContentType
from .chunk import ASTChunk, generate_deterministic_id, compute_file_hash, normalize_chunk, normalize_metadata

logger = logging.getLogger(__name__)

class ChunkEmitter:
    DEFAULT_MAX_CHUNK_SIZE = 8000

    def __init__(self, *, max_chunk_size, min_chunk_size, chunk_overlap, metadata_extractor):
        self.max_chunk_size = max_chunk_size
        self.min_chunk_size = min_chunk_size
        self.chunk_overlap = chunk_overlap
        self._metadata_extractor = metadata_extractor
        self._splitter_cache = {}
        self._default_splitter = RecursiveCharacterTextSplitter(
            chunk_size=max_chunk_size, chunk_overlap=chunk_overlap, length_function=len,
        )

    def _process_chunks(
        self,
        chunks: List[ASTChunk],
        doc: Document,
        language: Optional[Language],
        path: str
    ) -> List[TextNode]:
        """Process AST chunks into TextNodes, handling oversized chunks."""
        nodes = []
        chunk_counter = 0

        for ast_chunk in chunks:
            if len(ast_chunk.content) > self.max_chunk_size:
                sub_nodes = self._split_oversized_chunk(ast_chunk, language, doc.metadata, path)
                nodes.extend(sub_nodes)
                chunk_counter += len(sub_nodes)
            else:
                metadata = self._build_metadata(ast_chunk, doc.metadata, chunk_counter, len(chunks))
                chunk_id = generate_deterministic_id(path, ast_chunk.content, chunk_counter)

                node = TextNode(
                    id_=chunk_id,
                    text=ast_chunk.content,
                    metadata=metadata
                )
                nodes.append(node)
                chunk_counter += 1

        return nodes


    def _split_oversized_chunk(
        self,
        chunk: ASTChunk,
        language: Optional[Language],
        base_metadata: Dict[str, Any],
        path: str
    ) -> List[TextNode]:
        """
        Split an oversized chunk using RecursiveCharacterTextSplitter.

        IMPORTANT: Splitting an AST chunk loses semantic integrity.
        We try to preserve what we can:
        - Parent context and primary name are kept (they're still relevant)
        - Detailed lists (methods, properties, calls) are NOT copied to sub-chunks
          because they describe the whole unit, not the fragment
        Fragment ownership remains in metadata; stored text stays raw source.
        """
        splitter = self._get_text_splitter(language) if language else self._default_splitter
        # Short tails can carry a final return, guard, or closing statement.
        # Every nonblank source fragment remains evidence regardless of size.
        sub_chunks = [
            fragment
            for fragment in splitter.split_text(chunk.content)
            if fragment and fragment.strip()
        ]

        nodes = []
        parent_id = generate_deterministic_id(path, chunk.content, 0)
        located_sub_chunks = []
        previous_fragment_offset = -1
        for sub_chunk in sub_chunks:
            # RecursiveCharacterTextSplitter may trim leading/trailing whitespace,
            # but every emitted fragment remains an ordered substring of the AST
            # unit. Locate that exact substring so source coordinates describe the
            # evidence returned by getStructuralUnit, not the whole owning unit.
            fragment_offset = chunk.content.find(
                sub_chunk,
                previous_fragment_offset + 1,
            )
            if fragment_offset < 0:
                logger.warning(
                    "Skipped oversized fragment that could not be located in "
                    "its source unit: %s",
                    path,
                )
                continue
            previous_fragment_offset = fragment_offset
            located_sub_chunks.append((sub_chunk, fragment_offset))

        total_sub = len(located_sub_chunks)
        for sub_idx, (sub_chunk, fragment_offset) in enumerate(located_sub_chunks):
            fragment_start_line = (
                chunk.start_line
                + chunk.content[:fragment_offset].count('\n')
            )
            fragment_end_line = fragment_start_line + sub_chunk.count('\n')

            # Build metadata for this fragment
            # DO NOT copy detailed lists - they don't apply to fragments
            metadata = dict(base_metadata)
            metadata['content_type'] = ContentType.OVERSIZED_SPLIT.value
            metadata['original_content_type'] = chunk.content_type.value
            metadata['parent_chunk_id'] = parent_id
            metadata['sub_chunk_index'] = sub_idx
            metadata['total_sub_chunks'] = total_sub
            metadata['start_line'] = fragment_start_line
            metadata['end_line'] = fragment_end_line

            # Keep parent context - still relevant
            if chunk.parent_context:
                metadata['parent_context'] = chunk.parent_context
                metadata['parent_class'] = chunk.parent_context[-1]

            # Keep primary name - this fragment belongs to this unit
            if chunk.symbol_names:
                # A fragment carries its owning unit's primary identity only;
                # unit-wide inventories belong to the intact semantic chunk.
                metadata['symbol_names'] = [chunk.symbol_names[0]]
                metadata['primary_name'] = chunk.symbol_names[0]

            # Add note that this is a fragment
            metadata['is_fragment'] = True
            metadata['fragment_of'] = chunk.symbol_names[0] if chunk.symbol_names else None

            # Compute information density for fragment
            metadata['information_density'] = self._compute_information_density(metadata)

            chunk_id = generate_deterministic_id(path, sub_chunk, sub_idx)
            nodes.append(TextNode(id_=chunk_id, text=sub_chunk, metadata=metadata))

        # Log when splitting happens - it's a signal the chunk_size might need adjustment
        if nodes:
            logger.info(
                f"Split oversized {chunk.node_type or 'chunk'} "
                f"'{chunk.symbol_names[0] if chunk.symbol_names else 'unknown'}' "
                f"({len(chunk.content)} chars) into {len(nodes)} fragments"
            )

        return nodes


    def _split_fallback(
        self,
        doc: Document,
        language: Optional[Language] = None,
        extract_language_metadata: bool = True,
    ) -> List[TextNode]:
        """Fallback splitting that preserves every source character exactly."""
        text = doc.text
        path = doc.metadata.get('path', 'unknown')

        if not text:
            return []

        chunks = self._split_fallback_text_losslessly(text)

        nodes = []
        lang_str = doc.metadata.get('language', 'text')
        next_line = 1

        for i, chunk in enumerate(chunks):
            # Calculate line numbers
            start_line = next_line
            end_line = start_line + chunk.count('\n')
            next_line = end_line

            metadata = dict(doc.metadata)
            metadata['content_type'] = ContentType.FALLBACK.value
            metadata['chunk_index'] = i
            metadata['total_chunks'] = len(chunks)
            metadata['start_line'] = start_line
            metadata['end_line'] = end_line

            if extract_language_metadata:
                # Generic fallback when no selected plugin owns syntax.
                names = self._metadata_extractor.extract_names_from_content(
                    chunk,
                    lang_str,
                )
                if names:
                    metadata['symbol_names'] = names
                    metadata['primary_name'] = names[0]

                inheritance = self._metadata_extractor.extract_inheritance(
                    chunk,
                    lang_str,
                )
                if inheritance.get('extends'):
                    metadata['extends'] = inheritance['extends']
                    metadata['parent_types'] = inheritance['extends']
                if inheritance.get('implements'):
                    metadata['implements'] = inheritance['implements']
                if inheritance.get('imports'):
                    metadata['imports'] = inheritance['imports']

                normalize_metadata(metadata)

            # Compute information density for fallback chunks too
            metadata['information_density'] = self._compute_information_density(metadata)

            chunk_id = generate_deterministic_id(path, chunk, i)
            nodes.append(TextNode(id_=chunk_id, text=chunk, metadata=metadata))

        return nodes


    def _split_fallback_text_losslessly(self, text: str) -> List[str]:
        """Partition fallback text at boundaries with exact reconstruction."""
        if not text:
            return []
        max_size = min(
            max(1, int(self.max_chunk_size)),
            self.DEFAULT_MAX_CHUNK_SIZE,
        )
        fragments: List[str] = []
        start = 0
        text_length = len(text)

        while start < text_length:
            hard_end = min(text_length, start + max_size)
            if hard_end == text_length:
                fragments.append(text[start:hard_end])
                break

            window = text[start:hard_end]
            preferred_floor = max(1, len(window) // 2)
            boundary = 0

            # Prefer paragraph and line boundaries in the latter half of the
            # window, then any whitespace boundary. If an atom itself exceeds
            # max_size, the hard boundary preserves it across exact fragments.
            paragraph = window.rfind("\n\n")
            if paragraph >= 0 and paragraph + 2 >= preferred_floor:
                boundary = paragraph + 2
            if not boundary:
                newline = window.rfind("\n")
                if newline >= 0 and newline + 1 >= preferred_floor:
                    boundary = newline + 1
            if not boundary:
                for index in range(len(window), 0, -1):
                    if window[index - 1].isspace():
                        boundary = index
                        break
            if not boundary:
                boundary = len(window)

            end = start + boundary
            fragments.append(text[start:end])
            start = end

        return fragments


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


    def _get_text_splitter(self, language: Language) -> RecursiveCharacterTextSplitter:
        """Get language-specific text splitter."""
        if language not in self._splitter_cache:
            try:
                self._splitter_cache[language] = RecursiveCharacterTextSplitter.from_language(
                    language=language,
                    chunk_size=self.max_chunk_size,
                    chunk_overlap=self.chunk_overlap,
                )
            except Exception:
                self._splitter_cache[language] = self._default_splitter
        return self._splitter_cache[language]


