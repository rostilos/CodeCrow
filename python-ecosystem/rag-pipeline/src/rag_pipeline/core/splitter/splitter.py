"""
AST-based Code Splitter using Tree-sitter for accurate code parsing.

This module provides true AST-aware code chunking that:
1. Uses Tree-sitter queries for efficient pattern matching (15+ languages)
2. Splits code into semantic units (classes, functions, methods)
3. Uses RecursiveCharacterTextSplitter for oversized chunks
4. Enriches metadata for deterministic structural retrieval
5. Maintains parent context ("breadcrumbs") for nested structures
6. Uses deterministic IDs for structural-generation deduplication
"""

import hashlib
import logging
from typing import List, Dict, Any, Optional, Set
from pathlib import Path
from dataclasses import dataclass, field

from langchain_text_splitters import RecursiveCharacterTextSplitter, Language
from ..documents import Document, TextNode

from .languages import (
    EXTENSION_TO_LANGUAGE, AST_SUPPORTED_LANGUAGES, LANGUAGE_TO_TREESITTER,
    get_language_from_path, get_treesitter_name, is_ast_supported
)
from .tree_parser import get_parser
from .query_runner import get_query_runner, QueryMatch
from .metadata import MetadataExtractor, ContentType, ChunkMetadata

logger = logging.getLogger(__name__)


def _stable_unique_strings(values: List[str]) -> List[str]:
    """Deduplicate metadata values while retaining AST/query source order."""
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def generate_deterministic_id(path: str, content: str, chunk_index: int = 0) -> str:
    """
    Generate a deterministic ID for a chunk based on file path and content.

    This ensures the same code chunk always gets the same ID, preventing
    duplicate structural units during re-indexing.
    """
    hash_input = f"{path}:{chunk_index}:{content[:500]}"
    return hashlib.sha256(hash_input.encode('utf-8')).hexdigest()[:32]


def compute_file_hash(content: str) -> str:
    """Compute hash of file content for change detection."""
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


@dataclass
class ASTChunk:
    """Represents a chunk of code from AST parsing with rich metadata."""
    content: str
    content_type: ContentType
    language: str
    path: str

    # Identity
    symbol_names: List[str] = field(default_factory=list)
    node_type: Optional[str] = None
    namespace: Optional[str] = None

    # Location
    start_line: int = 0
    end_line: int = 0

    # Hierarchy & Context
    parent_context: List[str] = field(default_factory=list)  # Breadcrumb path

    # Documentation
    docstring: Optional[str] = None
    signature: Optional[str] = None

    # Type relationships
    extends: List[str] = field(default_factory=list)
    implements: List[str] = field(default_factory=list)

    # Dependencies
    imports: List[str] = field(default_factory=list)

    # --- RICH AST FIELDS (extracted from tree-sitter) ---

    # Methods/functions within this chunk (for classes)
    methods: List[str] = field(default_factory=list)

    # Properties/fields within this chunk (for classes)
    properties: List[str] = field(default_factory=list)

    # Parameters (for functions/methods)
    parameters: List[str] = field(default_factory=list)

    # Return type (for functions/methods)
    return_type: Optional[str] = None

    # Decorators/annotations
    decorators: List[str] = field(default_factory=list)

    # Modifiers (public, private, static, async, abstract, etc.)
    modifiers: List[str] = field(default_factory=list)

    # Called functions/methods (dependencies)
    calls: List[str] = field(default_factory=list)

    # Referenced types (type annotations, generics)
    referenced_types: List[str] = field(default_factory=list)

    # Variables declared in this chunk
    variables: List[str] = field(default_factory=list)

    # Constants defined
    constants: List[str] = field(default_factory=list)

    # Generic type parameters (e.g., <T, U>)
    type_parameters: List[str] = field(default_factory=list)

    # Explicit signal that structural metadata was admitted only partially.
    metadata_partial_reasons: List[str] = field(default_factory=list)


class ASTCodeSplitter:
    """
    AST-based code splitter using Tree-sitter queries for accurate parsing.

    Features:
    - Uses .scm query files for declarative pattern matching
    - Splits code into semantic units (classes, functions, methods)
    - Falls back to RecursiveCharacterTextSplitter when needed
    - Uses deterministic IDs for structural-generation deduplication
    - Enriches metadata for deterministic structural retrieval

    Chunk Size Strategy keeps most semantic units intact and splits only
    unusually large classes or functions.

    Usage:
        splitter = ASTCodeSplitter(max_chunk_size=8000)
        nodes = splitter.split_documents(documents)
    """

    # Most classes/functions are 500-5000 characters. Keep semantic units
    # whole and split only when required by storage and prompt-size bounds.
    DEFAULT_MAX_CHUNK_SIZE = 8000  # ~2000 tokens, fits most semantic units
    DEFAULT_MIN_CHUNK_SIZE = 100
    DEFAULT_CHUNK_OVERLAP = 200
    DEFAULT_PARSER_THRESHOLD = 3  # Low threshold - AST benefits even small files
    RICH_AST_TRAVERSAL_UNSAFE_LANGUAGES = {"java"}
    RICH_AST_MAX_DEPTH = 10
    METADATA_LIST_LIMITS = {
        "symbol_names": 30,
        "extends": 20,
        "implements": 30,
        "imports": 50,
        "methods": 50,
        "properties": 50,
        "parameters": 30,
        "decorators": 20,
        "calls": 80,
        "referenced_types": 50,
        "variables": 50,
        "constants": 50,
        "type_parameters": 20,
    }

    def __init__(
        self,
        max_chunk_size: int = DEFAULT_MAX_CHUNK_SIZE,
        min_chunk_size: int = DEFAULT_MIN_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
        parser_threshold: int = DEFAULT_PARSER_THRESHOLD,
        plugin_runtime: Any = None,
    ):
        """
        Initialize AST code splitter.

        Args:
            max_chunk_size: Maximum characters per chunk
            min_chunk_size: Minimum characters for a valid chunk
            chunk_overlap: Overlap between chunks when splitting oversized content
            parser_threshold: Minimum lines for AST parsing (3 recommended)
        """
        self.max_chunk_size = max_chunk_size
        self.min_chunk_size = min_chunk_size
        self.chunk_overlap = chunk_overlap
        self.parser_threshold = parser_threshold
        self._plugin_runtime = plugin_runtime

        # Components
        self._parser = get_parser()
        self._query_runner = get_query_runner()
        self._metadata_extractor = MetadataExtractor()

        # Cache text splitters
        self._splitter_cache: Dict[Language, RecursiveCharacterTextSplitter] = {}

        # Default splitter
        self._default_splitter = RecursiveCharacterTextSplitter(
            chunk_size=max_chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
        )

    @staticmethod
    def _add_partial_reason(chunk: ASTChunk, reason: str) -> None:
        if reason not in chunk.metadata_partial_reasons:
            chunk.metadata_partial_reasons.append(reason)

    def _bound_chunk_metadata(self, chunk: ASTChunk) -> None:
        """Bound persisted structural inventories and record every omission."""
        for field_name, limit in self.METADATA_LIST_LIMITS.items():
            values = _stable_unique_strings(list(getattr(chunk, field_name)))
            if len(values) > limit:
                self._add_partial_reason(chunk, f"{field_name}_limit")
            setattr(chunk, field_name, values[:limit])

    def _bound_metadata_dict(self, metadata: Dict[str, Any]) -> None:
        """Apply the same admission to fallback metadata dictionaries."""
        partial_reasons = list(metadata.get("structural_metadata_partial_reasons", ()))
        for field_name, limit in self.METADATA_LIST_LIMITS.items():
            values = metadata.get(field_name)
            if not isinstance(values, list):
                continue
            unique_values = _stable_unique_strings(values)
            if len(unique_values) > limit:
                reason = f"{field_name}_limit"
                if reason not in partial_reasons:
                    partial_reasons.append(reason)
            metadata[field_name] = unique_values[:limit]
        if partial_reasons:
            metadata["structural_metadata_complete"] = False
            metadata["structural_metadata_partial_reasons"] = sorted(partial_reasons)

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
                nodes = self._split_fallback(
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
            return self._split_fallback(
                doc,
                language if syntax is None else None,
                extract_language_metadata=syntax is None,
            )

        # Try query-based extraction first
        chunks = self._extract_with_queries(text, ts_lang, path, syntax)

        # If no queries available, fall back to traversal-based extraction
        if not chunks and self._is_rich_ast_traversal_safe(ts_lang, syntax):
            chunks = self._extract_with_traversal(text, ts_lang, path, syntax)

        # Still no chunks? Use fallback
        if not chunks:
            return self._split_fallback(
                doc,
                language if syntax is None else None,
                extract_language_metadata=syntax is None,
            )

        return self._process_chunks(chunks, doc, language, path)

    def _extract_with_queries(
        self,
        text: str,
        lang_name: str,
        path: str,
        syntax: Any = None,
    ) -> List[ASTChunk]:
        """Extract chunks using tree-sitter query files with rich metadata."""
        if not self._query_runner.has_query(lang_name, syntax):
            return []

        tree = (
            self._parser.parse_plugin(text, syntax)
            if syntax is not None
            else self._parser.parse(text, lang_name)
        )
        if not tree:
            return []

        matches = self._query_runner.run_query(
            text,
            lang_name,
            tree,
            syntax,
        )
        if not matches:
            return []

        source_bytes = text.encode('utf-8')
        chunks = []
        chunk_ranges: List[tuple[int, int, ASTChunk]] = []
        chunk_by_range: Dict[tuple, ASTChunk] = {}
        processed_ranges: Set[tuple] = set()

        # Collect file-level metadata from all matches
        imports = []
        namespace = None
        decorators_map: Dict[int, List[str]] = {}  # line -> decorators

        for match in matches:
            # Handle imports (multiple capture variations)
            if match.pattern_name in ('import', 'use'):
                import_path = (
                    match.get('import.path') or
                    match.get('import') or
                    match.get('use.path') or
                    match.get('use')
                )
                if import_path:
                    imports.append(import_path.text.strip().strip('"\''))
                continue

            # Handle namespace/package/module
            if match.pattern_name in ('namespace', 'package', 'module'):
                ns_cap = match.get(f'{match.pattern_name}.name') or match.get(match.pattern_name)
                if ns_cap:
                    namespace = ns_cap.text.strip()
                continue

            # Handle standalone decorators/attributes
            if match.pattern_name in ('decorator', 'attribute', 'annotation'):
                dec_cap = match.get(f'{match.pattern_name}.name') or match.get(match.pattern_name)
                if dec_cap:
                    line = dec_cap.start_line
                    if line not in decorators_map:
                        decorators_map[line] = []
                    decorators_map[line].append(dec_cap.text.strip())
                continue

            # Handle main constructs: functions, classes, methods, etc.
            semantic_patterns = (
                'function', 'method', 'class', 'interface', 'struct', 'trait',
                'enum', 'impl', 'constructor', 'closure', 'arrow', 'const',
                'var', 'static', 'type', 'record'
            )
            if match.pattern_name in semantic_patterns:
                main_cap = match.get(match.pattern_name)
                if not main_cap:
                    continue

                range_key = (main_cap.start_byte, main_cap.end_byte)
                if range_key in processed_ranges:
                    self._merge_query_definition_metadata(chunk_by_range[range_key], match)
                    continue
                processed_ranges.add(range_key)

                # Get name from various capture patterns
                name_cap = (
                    match.get(f'{match.pattern_name}.name') or
                    match.get('name')
                )
                name = name_cap.text if name_cap else None

                # Get inheritance (extends/implements/embeds/supertrait)
                extends = []
                implements = []

                for ext_capture in ('extends', 'embeds', 'supertrait', 'base_type'):
                    cap = match.get(f'{match.pattern_name}.{ext_capture}')
                    if cap:
                        extends.extend(self._parse_type_list(cap.text))

                for impl_capture in ('implements', 'trait'):
                    cap = match.get(f'{match.pattern_name}.{impl_capture}')
                    if cap:
                        implements.extend(self._parse_type_list(cap.text))

                if not extends and not implements:
                    inheritance = self._metadata_extractor.extract_inheritance(main_cap.text, lang_name)
                    extends = inheritance.get('extends', [])
                    implements = inheritance.get('implements', [])

                # Get additional metadata from captures
                visibility = match.get(f'{match.pattern_name}.visibility')
                return_type = match.get(f'{match.pattern_name}.return_type')
                params = match.get(f'{match.pattern_name}.params')
                modifiers = []

                for mod in ('static', 'abstract', 'final', 'async', 'readonly', 'const', 'unsafe'):
                    if match.get(f'{match.pattern_name}.{mod}'):
                        modifiers.append(mod)

                chunk = ASTChunk(
                    content=main_cap.text,
                    content_type=ContentType.FUNCTIONS_CLASSES,
                    language=lang_name,
                    path=path,
                    symbol_names=[name] if name else [],
                    parent_context=[],
                    start_line=main_cap.start_line,
                    end_line=main_cap.end_line,
                    node_type=match.pattern_name,
                    extends=extends,
                    implements=implements,
                    modifiers=modifiers,
                )

                # Extract docstring and signature — prefer AST traversal over regex
                ts_node = self._find_node_at_position(
                    tree.root_node, main_cap.start_byte, main_cap.end_byte
                ) if tree else None
                chunk.docstring = self._metadata_extractor.extract_docstring(main_cap.text, lang_name, ts_node=ts_node)
                chunk.signature = self._metadata_extractor.extract_signature(main_cap.text, lang_name, ts_node=ts_node)

                # Extract rich AST details (methods, properties, params, calls, etc.).
                # Some native tree-sitter bindings can segfault while walking large
                # Java trees, so keep this optional and only run it for known-safe
                # languages. Query captures still provide semantic Java chunks.
                if self._is_rich_ast_traversal_safe(lang_name, syntax):
                    self._extract_rich_ast_details(chunk, tree, main_cap, lang_name)

                chunks.append(chunk)
                chunk_ranges.append((main_cap.start_byte, main_cap.end_byte, chunk))
                chunk_by_range[range_key] = chunk

        self._attach_query_relationship_metadata(matches, chunk_ranges)
        self._attach_query_parent_context(chunk_ranges)

        # Admit source-ordered imports and other structural inventories within
        # the per-chunk payload limits used by deterministic retrieval.
        imports = _stable_unique_strings(imports)
        for chunk in chunks:
            chunk.imports = list(imports)
            chunk.namespace = namespace
            self._bound_chunk_metadata(chunk)

        # Create simplified code chunk
        if chunks:
            simplified = self._create_simplified_code(text, chunks, lang_name)
            if simplified and len(simplified.strip()) > 50:
                chunks.append(ASTChunk(
                    content=simplified,
                    content_type=ContentType.SIMPLIFIED_CODE,
                    language=lang_name,
                    path=path,
                    start_line=1,
                    end_line=text.count('\n') + 1,
                    node_type='simplified',
                    imports=list(imports),
                    namespace=namespace,
                ))

        return chunks

    def _merge_query_definition_metadata(self, chunk: ASTChunk, match: QueryMatch) -> None:
        """Merge metadata from duplicate query matches for the same definition."""
        def add_unique(values: List[str], value: str) -> None:
            for item in self._parse_type_list(value):
                if item and item not in values:
                    values.append(item)

        for ext_capture in ('extends', 'embeds', 'supertrait', 'base_type'):
            cap = match.get(f'{match.pattern_name}.{ext_capture}')
            if cap:
                add_unique(chunk.extends, cap.text)

        for impl_capture in ('implements', 'trait'):
            cap = match.get(f'{match.pattern_name}.{impl_capture}')
            if cap:
                add_unique(chunk.implements, cap.text)

        return_type = match.get(f'{match.pattern_name}.return_type')
        if return_type:
            chunk.return_type = return_type.text.strip()
        self._bound_chunk_metadata(chunk)

    def _attach_query_relationship_metadata(
        self,
        matches: List[QueryMatch],
        chunk_ranges: List[tuple[int, int, ASTChunk]],
    ) -> None:
        """Attach non-definition query captures to the smallest containing chunk."""
        if not chunk_ranges:
            return

        def add_unique(values: List[str], value: Optional[str]) -> None:
            if not value:
                return
            value = value.strip().strip('"\'`;')
            if not value or value in values:
                return
            values.append(value)

        def best_chunk(start: int, end: int) -> Optional[ASTChunk]:
            containing = [
                (range_end - range_start, chunk)
                for range_start, range_end, chunk in chunk_ranges
                if range_start <= start and end <= range_end
            ]
            if not containing:
                return None
            return min(containing, key=lambda item: item[0])[1]

        for match in matches:
            main_cap = match.get(match.pattern_name)
            if not main_cap:
                continue

            chunk = best_chunk(main_cap.start_byte, main_cap.end_byte)
            if not chunk:
                continue

            if match.pattern_name == 'call':
                call_name = match.get('call.name')
                call_object = match.get('call.object')
                add_unique(chunk.calls, call_name.text if call_name else main_cap.text)
                if call_object and call_object.text[:1].isupper():
                    add_unique(chunk.referenced_types, call_object.text)
                continue

            if match.pattern_name == 'type_reference':
                type_name = match.get('type_reference.name') or main_cap
                add_unique(chunk.referenced_types, type_name.text)
                continue

            if match.pattern_name == 'parameter':
                parameter_name = match.get('parameter.name')
                parameter_type = match.get('parameter.type')
                add_unique(chunk.parameters, parameter_name.text if parameter_name else None)
                add_unique(chunk.referenced_types, parameter_type.text if parameter_type else None)
                continue

            if match.pattern_name == 'field':
                field_name = match.get('field.name') or match.get('name')
                field_type = match.get('field.type')
                add_unique(chunk.properties, field_name.text if field_name else None)
                add_unique(chunk.referenced_types, field_type.text if field_type else None)
                continue

            if match.pattern_name == 'variable':
                variable_name = match.get('variable.name')
                variable_type = match.get('variable.type')
                add_unique(chunk.variables, variable_name.text if variable_name else None)
                add_unique(chunk.referenced_types, variable_type.text if variable_type else None)

        for chunk in {id(item[2]): item[2] for item in chunk_ranges}.values():
            self._bound_chunk_metadata(chunk)

    def _attach_query_parent_context(
        self,
        chunk_ranges: List[tuple[int, int, ASTChunk]],
    ) -> None:
        """Infer parent class context from query capture containment."""
        class_like = {
            'class', 'interface', 'record', 'enum', 'annotation', 'struct', 'trait'
        }
        member_like = {'method', 'constructor', 'function'}
        property_like = {'field', 'var', 'const', 'static'}

        parents = [
            (start, end, chunk)
            for start, end, chunk in chunk_ranges
            if chunk.node_type in class_like and chunk.symbol_names
        ]
        if not parents:
            return

        for start, end, chunk in chunk_ranges:
            if chunk.node_type in class_like:
                continue
            containing = [
                (parent_end - parent_start, parent)
                for parent_start, parent_end, parent in parents
                if parent_start <= start and end <= parent_end
            ]
            if not containing:
                continue
            parent = min(containing, key=lambda item: item[0])[1]
            parent_name = parent.symbol_names[0]
            chunk.parent_context = [*parent.parent_context, parent_name]

            child_name = chunk.symbol_names[0] if chunk.symbol_names else None
            if not child_name:
                continue
            if chunk.node_type in member_like and child_name not in parent.methods:
                parent.methods.append(child_name)
                self._bound_chunk_metadata(parent)
            elif chunk.node_type in property_like and child_name not in parent.properties:
                parent.properties.append(child_name)
                self._bound_chunk_metadata(parent)

    def _extract_with_traversal(
        self,
        text: str,
        lang_name: str,
        path: str,
        syntax: Any = None,
    ) -> List[ASTChunk]:
        """Fallback: extract chunks using manual AST traversal."""
        tree = (
            self._parser.parse_plugin(text, syntax)
            if syntax is not None
            else self._parser.parse(text, lang_name)
        )
        if not tree:
            return []

        source_bytes = text.encode('utf-8')
        chunks = []
        processed_ranges: Set[tuple] = set()

        # Node types for semantic chunking
        semantic_types = self._get_semantic_node_types(lang_name)
        class_types = set(semantic_types.get('class', []))
        function_types = set(semantic_types.get('function', []))
        all_types = class_types | function_types

        def get_node_text(node) -> str:
            return source_bytes[node.start_byte:node.end_byte].decode('utf-8', errors='replace')

        def get_node_name(node) -> Optional[str]:
            for child in node.children:
                if child.type in ('identifier', 'name', 'type_identifier', 'property_identifier'):
                    return get_node_text(child)
            return None

        def traverse(node, parent_context: List[str]):
            node_range = (node.start_byte, node.end_byte)

            if node.type in all_types:
                if node_range in processed_ranges:
                    return

                content = get_node_text(node)
                start_line = source_bytes[:node.start_byte].count(b'\n') + 1
                end_line = start_line + content.count('\n')
                node_name = get_node_name(node)
                is_class = node.type in class_types

                chunk = ASTChunk(
                    content=content,
                    content_type=ContentType.FUNCTIONS_CLASSES,
                    language=lang_name,
                    path=path,
                    symbol_names=[node_name] if node_name else [],
                    parent_context=list(parent_context),
                    start_line=start_line,
                    end_line=end_line,
                    node_type=node.type,
                )

                chunk.docstring = self._metadata_extractor.extract_docstring(content, lang_name, ts_node=node)
                chunk.signature = self._metadata_extractor.extract_signature(content, lang_name, ts_node=node)

                # Extract inheritance via regex
                inheritance = self._metadata_extractor.extract_inheritance(content, lang_name)
                chunk.extends = inheritance.get('extends', [])
                chunk.implements = inheritance.get('implements', [])
                chunk.imports = inheritance.get('imports', [])

                # Extract rich AST details directly from this node
                self._extract_rich_details_from_node(chunk, node, source_bytes, lang_name)

                chunks.append(chunk)
                processed_ranges.add(node_range)

                if is_class and node_name:
                    for child in node.children:
                        traverse(child, parent_context + [node_name])
            else:
                for child in node.children:
                    traverse(child, parent_context)

        traverse(tree.root_node, [])

        # Create simplified code
        if chunks:
            simplified = self._create_simplified_code(text, chunks, lang_name)
            if simplified and len(simplified.strip()) > 50:
                chunks.append(ASTChunk(
                    content=simplified,
                    content_type=ContentType.SIMPLIFIED_CODE,
                    language=lang_name,
                    path=path,
                    start_line=1,
                    end_line=text.count('\n') + 1,
                    node_type='simplified',
                ))

        return chunks

    def _extract_rich_ast_details(
        self,
        chunk: ASTChunk,
        tree: Any,
        captured_node: Any,
        lang_name: str
    ) -> None:
        """
        Extract rich AST details from tree-sitter node by traversing its children.

        This extracts:
        - Methods (for classes)
        - Properties/fields (for classes)
        - Parameters (for functions/methods)
        - Return type
        - Decorators/annotations
        - Called functions/methods
        - Referenced types
        - Variables
        - Type parameters (generics)
        """
        source_bytes = chunk.content.encode('utf-8')

        # Find the actual tree-sitter node for this capture
        node = self._find_node_at_position(
            tree.root_node,
            captured_node.start_byte,
            captured_node.end_byte
        )
        if not node:
            return

        # Language-specific node type mappings
        node_types = self._get_rich_node_types(lang_name)

        def get_text(n) -> str:
            """Get text for a node relative to chunk content."""
            start = n.start_byte - captured_node.start_byte
            end = n.end_byte - captured_node.start_byte
            if 0 <= start < len(source_bytes) and start < end <= len(source_bytes):
                return source_bytes[start:end].decode('utf-8', errors='replace')
            return ''

        def extract_identifier(n) -> Optional[str]:
            """Extract identifier name from a node."""
            for child in n.children:
                if child.type in node_types['identifier']:
                    return get_text(child)
            return None

        def record_details(n):
            """Record metadata for one AST node."""
            node_type = n.type

            # Extract methods (for classes)
            if node_type in node_types['method']:
                method_name = extract_identifier(n)
                if method_name and method_name not in chunk.methods:
                    chunk.methods.append(method_name)

            # Extract properties/fields
            if node_type in node_types['property']:
                prop_name = extract_identifier(n)
                if prop_name and prop_name not in chunk.properties:
                    chunk.properties.append(prop_name)

            # Extract parameters
            if node_type in node_types['parameter']:
                param_name = extract_identifier(n)
                if param_name and param_name not in chunk.parameters:
                    chunk.parameters.append(param_name)

            # Extract decorators/annotations
            if node_type in node_types['decorator']:
                dec_text = get_text(n).strip()
                # Normalize before checking membership so equivalent capture
                # spellings do not create duplicate semantic records.
                if dec_text.startswith('@'):
                    dec_text = dec_text[1:]
                if '(' in dec_text:
                    dec_text = dec_text.split('(')[0]
                if dec_text and dec_text not in chunk.decorators:
                    chunk.decorators.append(dec_text)

            # Extract function calls
            if node_type in node_types['call']:
                call_name = extract_identifier(n)
                if call_name and call_name not in chunk.calls:
                    chunk.calls.append(call_name)

            # Extract type references
            if node_type in node_types['type_ref']:
                type_text = get_text(n).strip()
                if '<' in type_text:
                    type_text = type_text.split('<')[0]
                if type_text and type_text not in chunk.referenced_types:
                    chunk.referenced_types.append(type_text)

            # Extract return type
            if node_type in node_types['return_type'] and not chunk.return_type:
                chunk.return_type = get_text(n).strip()

            # Extract type parameters (generics)
            if node_type in node_types['type_param']:
                param_text = get_text(n).strip()
                if param_text and param_text not in chunk.type_parameters:
                    chunk.type_parameters.append(param_text)

            # Extract variables
            if node_type in node_types['variable']:
                var_name = extract_identifier(n)
                if var_name and var_name not in chunk.variables:
                    chunk.variables.append(var_name)

        # Iterative traversal avoids Python recursion limits while bounding
        # pathological nesting that otherwise multiplies CPU and metadata.
        pending = [(node, 0)]
        while pending:
            current, depth = pending.pop()
            record_details(current)
            if depth >= self.RICH_AST_MAX_DEPTH:
                if current.children:
                    self._add_partial_reason(chunk, "ast_depth_limit")
                continue
            pending.extend(
                (child, depth + 1) for child in reversed(current.children)
            )
        self._bound_chunk_metadata(chunk)

    def _find_node_at_position(self, root, start_byte: int, end_byte: int) -> Optional[Any]:
        """Find the tree-sitter node at the given byte position."""
        def find(node):
            if node.start_byte == start_byte and node.end_byte == end_byte:
                return node
            for child in node.children:
                if child.start_byte <= start_byte and child.end_byte >= end_byte:
                    result = find(child)
                    if result:
                        return result
            return None
        return find(root)

    def _is_rich_ast_traversal_safe(
        self,
        language: str,
        syntax: Any = None,
    ) -> bool:
        """Return whether recursive native tree-sitter traversal is safe for metadata enrichment."""
        if syntax is not None:
            return syntax.rich_traversal_safe
        return language not in self.RICH_AST_TRAVERSAL_UNSAFE_LANGUAGES

    def _get_rich_node_types(self, language: str) -> Dict[str, List[str]]:
        """Get tree-sitter node types for extracting rich details."""
        # Common patterns across languages
        common = {
            'identifier': ['identifier', 'name', 'type_identifier', 'property_identifier'],
            'call': ['call_expression', 'call', 'function_call', 'method_invocation'],
            'type_ref': ['type_identifier', 'generic_type', 'type_annotation', 'type'],
            'type_param': ['type_parameter', 'type_parameters', 'generic_parameter'],
        }

        types = {
            'python': {
                **common,
                'method': ['function_definition'],
                'property': ['assignment', 'expression_statement'],
                'parameter': ['parameter', 'default_parameter', 'typed_parameter'],
                'decorator': ['decorator'],
                'return_type': ['type'],
                'variable': ['assignment'],
            },
            'java': {
                **common,
                'method': ['method_declaration', 'constructor_declaration'],
                'property': ['field_declaration'],
                'parameter': ['formal_parameter', 'spread_parameter'],
                'decorator': ['annotation', 'marker_annotation'],
                'return_type': ['type_identifier', 'generic_type', 'void_type'],
                'variable': ['local_variable_declaration'],
            },
            'javascript': {
                **common,
                'method': ['method_definition', 'function_declaration'],
                'property': ['field_definition', 'public_field_definition'],
                'parameter': ['formal_parameters', 'required_parameter'],
                'decorator': ['decorator'],
                'return_type': ['type_annotation'],
                'variable': ['variable_declarator'],
            },
            'typescript': {
                **common,
                'method': ['method_definition', 'method_signature', 'function_declaration'],
                'property': ['public_field_definition', 'property_signature'],
                'parameter': ['required_parameter', 'optional_parameter'],
                'decorator': ['decorator'],
                'return_type': ['type_annotation'],
                'variable': ['variable_declarator'],
            },
            'go': {
                **common,
                'method': ['method_declaration', 'function_declaration'],
                'property': ['field_declaration'],
                'parameter': ['parameter_declaration'],
                'decorator': [],  # Go doesn't have decorators
                'return_type': ['type_identifier', 'pointer_type'],
                'variable': ['short_var_declaration', 'var_declaration'],
            },
            'rust': {
                **common,
                'method': ['function_item', 'associated_item'],
                'property': ['field_declaration'],
                'parameter': ['parameter'],
                'decorator': ['attribute_item'],
                'return_type': ['type_identifier', 'generic_type'],
                'variable': ['let_declaration'],
            },
            'c_sharp': {
                **common,
                'method': ['method_declaration', 'constructor_declaration'],
                'property': ['property_declaration', 'field_declaration'],
                'parameter': ['parameter'],
                'decorator': ['attribute_list', 'attribute'],
                'return_type': ['predefined_type', 'generic_name'],
                'variable': ['variable_declaration'],
            },
            'php': {
                **common,
                'method': ['method_declaration', 'function_definition'],
                'property': ['property_declaration'],
                'parameter': ['simple_parameter'],
                'decorator': ['attribute_list'],
                'return_type': ['named_type', 'union_type'],
                'variable': ['property_declaration', 'simple_variable'],
            },
        }

        return types.get(language, {
            **common,
            'method': [],
            'property': [],
            'parameter': [],
            'decorator': [],
            'return_type': [],
            'variable': [],
        })

    def _extract_rich_details_from_node(
        self,
        chunk: ASTChunk,
        node: Any,
        source_bytes: bytes,
        lang_name: str
    ) -> None:
        """
        Extract rich AST details directly from a tree-sitter node.
        Used by traversal-based extraction when we already have the node.
        """
        node_types = self._get_rich_node_types(lang_name)

        def get_text(n) -> str:
            return source_bytes[n.start_byte:n.end_byte].decode('utf-8', errors='replace')

        def extract_identifier(n) -> Optional[str]:
            for child in n.children:
                if child.type in node_types['identifier']:
                    return get_text(child)
            return None

        def record_details(n):
            node_type = n.type

            if node_type in node_types['method']:
                name = extract_identifier(n)
                if name and name not in chunk.methods:
                    chunk.methods.append(name)

            if node_type in node_types['property']:
                name = extract_identifier(n)
                if name and name not in chunk.properties:
                    chunk.properties.append(name)

            if node_type in node_types['parameter']:
                name = extract_identifier(n)
                if name and name not in chunk.parameters:
                    chunk.parameters.append(name)

            if node_type in node_types['decorator']:
                dec_text = get_text(n).strip()
                if dec_text.startswith('@'):
                    dec_text = dec_text[1:]
                if '(' in dec_text:
                    dec_text = dec_text.split('(')[0]
                if dec_text and dec_text not in chunk.decorators:
                    chunk.decorators.append(dec_text)

            if node_type in node_types['call']:
                name = extract_identifier(n)
                if name and name not in chunk.calls:
                    chunk.calls.append(name)

            if node_type in node_types['type_ref']:
                type_text = get_text(n).strip()
                if '<' in type_text:
                    type_text = type_text.split('<')[0]
                if type_text and type_text not in chunk.referenced_types:
                    chunk.referenced_types.append(type_text)

            if node_type in node_types['return_type'] and not chunk.return_type:
                chunk.return_type = get_text(n).strip()

            if node_type in node_types['type_param']:
                param_text = get_text(n).strip()
                if param_text and param_text not in chunk.type_parameters:
                    chunk.type_parameters.append(param_text)

            if node_type in node_types['variable']:
                name = extract_identifier(n)
                if name and name not in chunk.variables:
                    chunk.variables.append(name)

        pending = [(node, 0)]
        while pending:
            current, depth = pending.pop()
            record_details(current)
            if depth >= self.RICH_AST_MAX_DEPTH:
                if current.children:
                    self._add_partial_reason(chunk, "ast_depth_limit")
                continue
            pending.extend(
                (child, depth + 1) for child in reversed(current.children)
            )
        self._bound_chunk_metadata(chunk)

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
        raw_sub_chunks = [
            fragment
            for fragment in splitter.split_text(chunk.content)
            if fragment
            and fragment.strip()
        ]
        sub_chunks = (
            [
                fragment
                for fragment in raw_sub_chunks
                if len(fragment.strip()) >= self.min_chunk_size
            ]
            if len(raw_sub_chunks) > 1
            else raw_sub_chunks
        )

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
        text_offset = 0

        for i, chunk in enumerate(chunks):
            # Calculate line numbers
            start_line = text[:text_offset].count('\n') + 1 if text_offset > 0 else 1
            text_offset += len(chunk)
            end_line = start_line + chunk.count('\n')

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

                self._bound_metadata_dict(metadata)

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
        self._bound_chunk_metadata(chunk)
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

    def _create_simplified_code(
        self,
        source_code: str,
        chunks: List[ASTChunk],
        language: str
    ) -> str:
        """Create simplified code with placeholders for extracted chunks.

        Uses AST start_line/end_line offsets for a single-pass replacement
        instead of O(n*m) string find() calls per chunk.
        """
        semantic_chunks = [c for c in chunks if c.content_type == ContentType.FUNCTIONS_CLASSES]
        if not semantic_chunks:
            return source_code

        # Sort by start_line descending so replacements don't shift offsets
        sorted_chunks = sorted(
            semantic_chunks,
            key=lambda x: x.start_line,
            reverse=True
        )

        lines = source_code.split('\n')
        comment_prefix = self._metadata_extractor.get_comment_prefix(language)

        for chunk in sorted_chunks:
            # Validate line range (1-indexed from AST)
            start = chunk.start_line - 1  # Convert to 0-indexed
            end = chunk.end_line  # end_line is inclusive, but slice is exclusive
            if start < 0 or end > len(lines):
                continue

            first_line = chunk.content.split('\n')[0].strip()
            if len(first_line) > 60:
                first_line = first_line[:60] + '...'

            breadcrumb = ""
            if chunk.parent_context:
                breadcrumb = f" (in {'.'.join(chunk.parent_context)})"

            placeholder = f"{comment_prefix} Code for: {first_line}{breadcrumb}"
            lines[start:end] = [placeholder]

        return '\n'.join(lines).strip()

    def _parse_type_list(self, text: str) -> List[str]:
        """Parse a comma-separated list of types."""
        if not text:
            return []

        text = text.strip().strip('()[]')

        # Remove keywords
        for kw in ('extends', 'implements', 'with', ':'):
            text = text.replace(kw, ' ')

        types = []
        for part in text.split(','):
            name = part.strip()
            if '<' in name:
                name = name.split('<')[0].strip()
            if '(' in name:
                name = name.split('(')[0].strip()
            if name:
                types.append(name)

        return types

    def _get_semantic_node_types(self, language: str) -> Dict[str, List[str]]:
        """Get semantic node types for manual traversal fallback."""
        types = {
            'python': {
                'class': ['class_definition'],
                'function': ['function_definition'],
            },
            'java': {
                'class': ['class_declaration', 'interface_declaration', 'enum_declaration'],
                'function': ['method_declaration', 'constructor_declaration'],
            },
            'javascript': {
                'class': ['class_declaration'],
                'function': ['function_declaration', 'method_definition', 'arrow_function'],
            },
            'typescript': {
                'class': ['class_declaration', 'interface_declaration'],
                'function': ['function_declaration', 'method_definition', 'arrow_function'],
            },
            'go': {
                'class': ['type_declaration'],
                'function': ['function_declaration', 'method_declaration'],
            },
            'rust': {
                'class': ['struct_item', 'impl_item', 'trait_item', 'enum_item'],
                'function': ['function_item'],
            },
            'c_sharp': {
                'class': ['class_declaration', 'interface_declaration', 'struct_declaration'],
                'function': ['method_declaration', 'constructor_declaration'],
            },
            'php': {
                'class': ['class_declaration', 'interface_declaration', 'trait_declaration'],
                'function': ['function_definition', 'method_declaration'],
            },
        }
        return types.get(language, {'class': [], 'function': []})

    @staticmethod
    def get_supported_languages() -> List[str]:
        """Return list of languages with AST support."""
        return list(LANGUAGE_TO_TREESITTER.values())

    @staticmethod
    def is_ast_supported(path: str) -> bool:
        """Check if AST parsing is supported for a file."""
        return is_ast_supported(path)
