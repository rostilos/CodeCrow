"""
AST-based Code Splitter using Tree-sitter for accurate code parsing.

This module provides true AST-aware code chunking that:
1. Uses Tree-sitter queries for efficient pattern matching (15+ languages)
2. Splits code into semantic units (classes, functions, methods)
3. Uses RecursiveCharacterTextSplitter for oversized chunks
4. Enriches metadata for better RAG retrieval
5. Maintains parent context ("breadcrumbs") for nested structures
6. Uses deterministic IDs for Qdrant deduplication
"""

import hashlib
import logging
from typing import List, Dict, Any, Optional, Set
from pathlib import Path
from dataclasses import dataclass, field

from langchain_text_splitters import RecursiveCharacterTextSplitter, Language
from llama_index.core.schema import Document as LlamaDocument, TextNode

from .languages import (
    EXTENSION_TO_LANGUAGE, AST_SUPPORTED_LANGUAGES, LANGUAGE_TO_TREESITTER,
    get_language_from_path, get_treesitter_name, is_ast_supported
)
from .tree_parser import get_parser
from .query_runner import get_query_runner, QueryMatch
from .metadata import MetadataExtractor, ContentType, ChunkMetadata

logger = logging.getLogger(__name__)


def generate_deterministic_id(path: str, content: str, chunk_index: int = 0) -> str:
    """
    Generate a deterministic ID for a chunk based on file path and content.
    
    This ensures the same code chunk always gets the same ID, preventing
    duplicates in Qdrant during re-indexing.
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
    semantic_names: List[str] = field(default_factory=list)
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


class ASTCodeSplitter:
    """
    AST-based code splitter using Tree-sitter queries for accurate parsing.
    
    Features:
    - Uses .scm query files for declarative pattern matching
    - Splits code into semantic units (classes, functions, methods)
    - Falls back to RecursiveCharacterTextSplitter when needed
    - Uses deterministic IDs for Qdrant deduplication
    - Enriches metadata for improved RAG retrieval
    - Prepares embedding-optimized text with semantic context
    
    Chunk Size Strategy:
    - text-embedding-3-small supports ~8191 tokens (~32K chars)
    - We use 8000 chars as default to keep semantic units intact
    - Only truly massive classes/functions get split
    - Splitting loses AST benefits, so we avoid it when possible
    
    Usage:
        splitter = ASTCodeSplitter(max_chunk_size=8000)
        nodes = splitter.split_documents(documents)
    """
    
    # Chunk size considerations:
    # - Embedding models (text-embedding-3-small): ~8191 tokens = ~32K chars
    # - Most classes/functions: 500-5000 chars
    # - Keeping semantic units whole improves retrieval quality
    # - Only split when absolutely necessary
    DEFAULT_MAX_CHUNK_SIZE = 8000  # ~2000 tokens, fits most semantic units
    DEFAULT_MIN_CHUNK_SIZE = 100
    DEFAULT_CHUNK_OVERLAP = 200
    DEFAULT_PARSER_THRESHOLD = 3  # Low threshold - AST benefits even small files
    
    def __init__(
        self,
        max_chunk_size: int = DEFAULT_MAX_CHUNK_SIZE,
        min_chunk_size: int = DEFAULT_MIN_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
        parser_threshold: int = DEFAULT_PARSER_THRESHOLD,
        enrich_embedding_text: bool = True
    ):
        """
        Initialize AST code splitter.
        
        Args:
            max_chunk_size: Maximum characters per chunk
            min_chunk_size: Minimum characters for a valid chunk
            chunk_overlap: Overlap between chunks when splitting oversized content
            parser_threshold: Minimum lines for AST parsing (3 recommended)
            enrich_embedding_text: Whether to prepend semantic context to chunk text
                                   for better embedding quality
        """
        self.max_chunk_size = max_chunk_size
        self.min_chunk_size = min_chunk_size
        self.chunk_overlap = chunk_overlap
        self.parser_threshold = parser_threshold
        self.enrich_embedding_text = enrich_embedding_text
        
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
    
    def split_documents(self, documents: List[LlamaDocument]) -> List[TextNode]:
        """
        Split LlamaIndex documents using AST-based parsing.
        
        Args:
            documents: List of LlamaIndex Document objects
            
        Returns:
            List of TextNode objects with enriched metadata
        """
        all_nodes = []
        
        for doc in documents:
            path = doc.metadata.get('path', 'unknown')
            language = get_language_from_path(path)
            
            line_count = doc.text.count('\n') + 1
            use_ast = (
                language is not None
                and language in AST_SUPPORTED_LANGUAGES
                and line_count >= self.parser_threshold
                and self._parser.is_available()
            )
            
            if use_ast:
                nodes = self._split_with_ast(doc, language)
            else:
                nodes = self._split_fallback(doc, language)
            
            all_nodes.extend(nodes)
            logger.debug(f"Split {path} into {len(nodes)} chunks (AST={use_ast})")
        
        return all_nodes
    
    def _split_with_ast(self, doc: LlamaDocument, language: Language) -> List[TextNode]:
        """Split document using AST parsing with query-based extraction."""
        text = doc.text
        path = doc.metadata.get('path', 'unknown')
        ts_lang = get_treesitter_name(language)
        
        if not ts_lang:
            return self._split_fallback(doc, language)
        
        # Try query-based extraction first
        chunks = self._extract_with_queries(text, ts_lang, path)
        
        # If no queries available, fall back to traversal-based extraction
        if not chunks:
            chunks = self._extract_with_traversal(text, ts_lang, path)
        
        # Still no chunks? Use fallback
        if not chunks:
            return self._split_fallback(doc, language)
        
        return self._process_chunks(chunks, doc, language, path)
    
    def _extract_with_queries(
        self,
        text: str,
        lang_name: str,
        path: str
    ) -> List[ASTChunk]:
        """Extract chunks using tree-sitter query files with rich metadata."""
        if not self._query_runner.has_query(lang_name):
            return []
        
        tree = self._parser.parse(text, lang_name)
        if not tree:
            return []
        
        matches = self._query_runner.run_query(text, lang_name, tree)
        if not matches:
            return []
        
        source_bytes = text.encode('utf-8')
        chunks = []
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
                    semantic_names=[name] if name else [],
                    parent_context=[],
                    start_line=main_cap.start_line,
                    end_line=main_cap.end_line,
                    node_type=match.pattern_name,
                    extends=extends,
                    implements=implements,
                    modifiers=modifiers,
                )
                
                # Extract docstring and signature
                chunk.docstring = self._metadata_extractor.extract_docstring(main_cap.text, lang_name)
                chunk.signature = self._metadata_extractor.extract_signature(main_cap.text, lang_name)
                
                # Extract rich AST details (methods, properties, params, calls, etc.)
                self._extract_rich_ast_details(chunk, tree, main_cap, lang_name)
                
                chunks.append(chunk)
        
        # Add imports and namespace to all chunks
        for chunk in chunks:
            chunk.imports = imports[:30]
            chunk.namespace = namespace
        
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
                    imports=imports[:30],
                    namespace=namespace,
                ))
        
        return chunks
    
    def _extract_with_traversal(
        self,
        text: str,
        lang_name: str,
        path: str
    ) -> List[ASTChunk]:
        """Fallback: extract chunks using manual AST traversal."""
        tree = self._parser.parse(text, lang_name)
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
                    semantic_names=[node_name] if node_name else [],
                    parent_context=list(parent_context),
                    start_line=start_line,
                    end_line=end_line,
                    node_type=node.type,
                )
                
                chunk.docstring = self._metadata_extractor.extract_docstring(content, lang_name)
                chunk.signature = self._metadata_extractor.extract_signature(content, lang_name)
                
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
        
        def traverse_for_details(n, depth: int = 0):
            """Recursively traverse to extract details."""
            if depth > 10:  # Prevent infinite recursion
                return
            
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
                if dec_text and dec_text not in chunk.decorators:
                    # Clean up decorator text
                    if dec_text.startswith('@'):
                        dec_text = dec_text[1:]
                    if '(' in dec_text:
                        dec_text = dec_text.split('(')[0]
                    chunk.decorators.append(dec_text)
            
            # Extract function calls
            if node_type in node_types['call']:
                call_name = extract_identifier(n)
                if call_name and call_name not in chunk.calls:
                    chunk.calls.append(call_name)
            
            # Extract type references
            if node_type in node_types['type_ref']:
                type_text = get_text(n).strip()
                if type_text and type_text not in chunk.referenced_types:
                    # Clean generic params
                    if '<' in type_text:
                        type_text = type_text.split('<')[0]
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
            
            # Recurse into children
            for child in n.children:
                traverse_for_details(child, depth + 1)
        
        traverse_for_details(node)
        
        # Limit list sizes to prevent bloat
        chunk.methods = chunk.methods[:30]
        chunk.properties = chunk.properties[:30]
        chunk.parameters = chunk.parameters[:20]
        chunk.decorators = chunk.decorators[:10]
        chunk.calls = chunk.calls[:50]
        chunk.referenced_types = chunk.referenced_types[:30]
        chunk.variables = chunk.variables[:30]
        chunk.type_parameters = chunk.type_parameters[:10]
    
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
        
        def traverse(n, depth: int = 0):
            if depth > 10:
                return
            
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
                if dec_text and dec_text not in chunk.decorators:
                    if dec_text.startswith('@'):
                        dec_text = dec_text[1:]
                    if '(' in dec_text:
                        dec_text = dec_text.split('(')[0]
                    chunk.decorators.append(dec_text)
            
            if node_type in node_types['call']:
                name = extract_identifier(n)
                if name and name not in chunk.calls:
                    chunk.calls.append(name)
            
            if node_type in node_types['type_ref']:
                type_text = get_text(n).strip()
                if type_text and type_text not in chunk.referenced_types:
                    if '<' in type_text:
                        type_text = type_text.split('<')[0]
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
            
            for child in n.children:
                traverse(child, depth + 1)
        
        traverse(node)
        
        # Limit sizes
        chunk.methods = chunk.methods[:30]
        chunk.properties = chunk.properties[:30]
        chunk.parameters = chunk.parameters[:20]
        chunk.decorators = chunk.decorators[:10]
        chunk.calls = chunk.calls[:50]
        chunk.referenced_types = chunk.referenced_types[:30]
        chunk.variables = chunk.variables[:30]
        chunk.type_parameters = chunk.type_parameters[:10]
    
    def _process_chunks(
        self,
        chunks: List[ASTChunk],
        doc: LlamaDocument,
        language: Language,
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
                
                # Create embedding-enriched text with semantic context
                enriched_text = self._create_embedding_text(ast_chunk.content, metadata)
                
                node = TextNode(
                    id_=chunk_id,
                    text=enriched_text,
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
        - A summary of the original unit is prepended to help embeddings
        """
        splitter = self._get_text_splitter(language) if language else self._default_splitter
        sub_chunks = splitter.split_text(chunk.content)
        
        nodes = []
        parent_id = generate_deterministic_id(path, chunk.content, 0)
        total_sub = len([s for s in sub_chunks if s and s.strip()])
        
        # Build a brief summary of the original semantic unit
        # This helps embeddings understand context even in fragments
        unit_summary_parts = []
        if chunk.semantic_names:
            unit_summary_parts.append(f"{chunk.node_type or 'code'}: {chunk.semantic_names[0]}")
        if chunk.extends:
            unit_summary_parts.append(f"extends {', '.join(chunk.extends[:3])}")
        if chunk.implements:
            unit_summary_parts.append(f"implements {', '.join(chunk.implements[:3])}")
        if chunk.methods:
            unit_summary_parts.append(f"has {len(chunk.methods)} methods")
        
        unit_summary = " | ".join(unit_summary_parts) if unit_summary_parts else None
        
        sub_idx = 0
        for i, sub_chunk in enumerate(sub_chunks):
            if not sub_chunk or not sub_chunk.strip():
                continue
            if len(sub_chunk.strip()) < self.min_chunk_size and total_sub > 1:
                continue
            
            # Build metadata for this fragment
            # DO NOT copy detailed lists - they don't apply to fragments
            metadata = dict(base_metadata)
            metadata['content_type'] = ContentType.OVERSIZED_SPLIT.value
            metadata['original_content_type'] = chunk.content_type.value
            metadata['parent_chunk_id'] = parent_id
            metadata['sub_chunk_index'] = sub_idx
            metadata['total_sub_chunks'] = total_sub
            metadata['start_line'] = chunk.start_line
            metadata['end_line'] = chunk.end_line
            
            # Keep parent context - still relevant
            if chunk.parent_context:
                metadata['parent_context'] = chunk.parent_context
                metadata['parent_class'] = chunk.parent_context[-1]
            
            # Keep primary name - this fragment belongs to this unit
            if chunk.semantic_names:
                metadata['semantic_names'] = chunk.semantic_names[:1]  # Just the main name
                metadata['primary_name'] = chunk.semantic_names[0]
            
            # Add note that this is a fragment
            metadata['is_fragment'] = True
            metadata['fragment_of'] = chunk.semantic_names[0] if chunk.semantic_names else None
            
            # For embedding: prepend fragment context
            if unit_summary:
                fragment_header = f"[Fragment {sub_idx + 1}/{total_sub} of {unit_summary}]"
                enriched_text = f"{fragment_header}\n\n{sub_chunk}"
            else:
                enriched_text = sub_chunk
            
            chunk_id = generate_deterministic_id(path, sub_chunk, sub_idx)
            nodes.append(TextNode(id_=chunk_id, text=enriched_text, metadata=metadata))
            sub_idx += 1
        
        # Log when splitting happens - it's a signal the chunk_size might need adjustment
        if nodes:
            logger.info(
                f"Split oversized {chunk.node_type or 'chunk'} "
                f"'{chunk.semantic_names[0] if chunk.semantic_names else 'unknown'}' "
                f"({len(chunk.content)} chars) into {len(nodes)} fragments"
            )
        
        return nodes
    
    def _split_fallback(
        self,
        doc: LlamaDocument,
        language: Optional[Language] = None
    ) -> List[TextNode]:
        """Fallback splitting using RecursiveCharacterTextSplitter."""
        text = doc.text
        path = doc.metadata.get('path', 'unknown')
        
        if not text or not text.strip():
            return []
        
        splitter = self._get_text_splitter(language) if language else self._default_splitter
        chunks = splitter.split_text(text)
        
        nodes = []
        lang_str = doc.metadata.get('language', 'text')
        text_offset = 0
        
        for i, chunk in enumerate(chunks):
            if not chunk or not chunk.strip():
                continue
            if len(chunk.strip()) < self.min_chunk_size and len(chunks) > 1:
                continue
            if len(chunk) > 30000:
                chunk = chunk[:30000]
            
            # Calculate line numbers
            start_line = text[:text_offset].count('\n') + 1 if text_offset > 0 else 1
            chunk_pos = text.find(chunk, text_offset)
            if chunk_pos >= 0:
                text_offset = chunk_pos + len(chunk)
            end_line = start_line + chunk.count('\n')
            
            metadata = dict(doc.metadata)
            metadata['content_type'] = ContentType.FALLBACK.value
            metadata['chunk_index'] = i
            metadata['total_chunks'] = len(chunks)
            metadata['start_line'] = start_line
            metadata['end_line'] = end_line
            
            # Extract names via regex
            names = self._metadata_extractor.extract_names_from_content(chunk, lang_str)
            if names:
                metadata['semantic_names'] = names
                metadata['primary_name'] = names[0]
            
            # Extract inheritance
            inheritance = self._metadata_extractor.extract_inheritance(chunk, lang_str)
            if inheritance.get('extends'):
                metadata['extends'] = inheritance['extends']
                metadata['parent_types'] = inheritance['extends']
            if inheritance.get('implements'):
                metadata['implements'] = inheritance['implements']
            if inheritance.get('imports'):
                metadata['imports'] = inheritance['imports']
            
            # Create embedding-enriched text with semantic context
            enriched_text = self._create_embedding_text(chunk, metadata)
            
            chunk_id = generate_deterministic_id(path, chunk, i)
            nodes.append(TextNode(id_=chunk_id, text=enriched_text, metadata=metadata))
        
        return nodes
    
    def _build_metadata(
        self,
        chunk: ASTChunk,
        base_metadata: Dict[str, Any],
        chunk_index: int,
        total_chunks: int
    ) -> Dict[str, Any]:
        """Build metadata dictionary from ASTChunk."""
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
            metadata['full_path'] = '.'.join(chunk.parent_context + chunk.semantic_names[:1])
        
        if chunk.semantic_names:
            metadata['semantic_names'] = chunk.semantic_names
            metadata['primary_name'] = chunk.semantic_names[0]
        
        if chunk.docstring:
            metadata['docstring'] = chunk.docstring[:500]
        
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
        
        return metadata
    
    def _create_embedding_text(self, content: str, metadata: Dict[str, Any]) -> str:
        """
        Create embedding-optimized text by prepending concise semantic context.
        
        Design principles:
        1. Keep it SHORT - long headers can skew embeddings for small code chunks
        2. Avoid redundancy - don't repeat info that's obvious from the code
        3. Clean paths - strip commit hashes and archive prefixes
        4. Add VALUE - include info that helps semantic matching
        
        What we include (selectively):
        - Clean file path (without commit/archive prefixes)
        - Parent context (for nested structures - very valuable)
        - Extends/implements (inheritance is critical for understanding)
        - Docstring (helps semantic matching)
        - For CLASSES: method count (helps identify scope)
        - For METHODS: skip redundant method list
        """
        if not self.enrich_embedding_text:
            return content
        
        context_parts = []
        
        # Clean file path - remove commit hash prefixes and archive structure
        path = metadata.get('path', '')
        if path:
            path = self._clean_path(path)
            context_parts.append(f"File: {path}")
        
        # Parent context - valuable for nested structures
        parent_context = metadata.get('parent_context', [])
        if parent_context:
            context_parts.append(f"In: {'.'.join(parent_context)}")
        
        # Clean namespace - strip keyword if present
        namespace = metadata.get('namespace', '')
        if namespace:
            ns_clean = namespace.replace('namespace ', '').replace('package ', '').strip().rstrip(';')
            if ns_clean:
                context_parts.append(f"Namespace: {ns_clean}")
        
        # Type relationships - very valuable for understanding code structure
        extends = metadata.get('extends', [])
        implements = metadata.get('implements', [])
        if extends:
            context_parts.append(f"Extends: {', '.join(extends[:3])}")
        if implements:
            context_parts.append(f"Implements: {', '.join(implements[:3])}")
        
        # For CLASSES: show method/property counts (helps understand scope)
        # For METHODS/FUNCTIONS: skip - it's redundant
        node_type = metadata.get('node_type', '')
        is_container = node_type in ('class', 'interface', 'struct', 'trait', 'enum', 'impl')
        
        if is_container:
            methods = metadata.get('methods', [])
            properties = metadata.get('properties', [])
            if methods and len(methods) > 1:
                # Only show if there are multiple methods
                context_parts.append(f"Methods({len(methods)}): {', '.join(methods[:8])}")
            if properties and len(properties) > 1:
                context_parts.append(f"Fields({len(properties)}): {', '.join(properties[:5])}")
        
        # Docstring - valuable for semantic matching
        docstring = metadata.get('docstring', '')
        if docstring:
            # Take just the first sentence or 100 chars
            brief = docstring.split('.')[0][:100].strip()
            if brief:
                context_parts.append(f"Desc: {brief}")
        
        # Build final text - only if we have meaningful context
        if context_parts:
            context_header = " | ".join(context_parts)
            return f"[{context_header}]\n\n{content}"
        
        return content
    
    def _clean_path(self, path: str) -> str:
        """
        Clean file path for embedding text.
        
        Removes:
        - Commit hash prefixes (e.g., 'owner-repo-abc123def/')
        - Archive extraction paths
        - Redundant path components
        """
        if not path:
            return path
        
        # Split by '/' and look for src/, lib/, app/ etc as anchor points
        parts = path.split('/')
        
        # Common source directory markers
        source_markers = {'src', 'lib', 'app', 'source', 'main', 'test', 'tests', 'pkg', 'cmd', 'internal'}
        
        # Find the first source marker and start from there
        for i, part in enumerate(parts):
            if part.lower() in source_markers:
                return '/'.join(parts[i:])
        
        # If no marker found but path has commit-hash-like prefix (40 hex chars or similar)
        if parts and len(parts) > 1:
            first_part = parts[0]
            # Check if first part looks like "owner-repo-commithash" pattern
            if '-' in first_part and len(first_part) > 40:
                # Skip the first part
                return '/'.join(parts[1:])
        
        return path
    
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
        """Create simplified code with placeholders for extracted chunks."""
        semantic_chunks = [c for c in chunks if c.content_type == ContentType.FUNCTIONS_CLASSES]
        if not semantic_chunks:
            return source_code
        
        sorted_chunks = sorted(
            semantic_chunks,
            key=lambda x: source_code.find(x.content),
            reverse=True
        )
        
        result = source_code
        comment_prefix = self._metadata_extractor.get_comment_prefix(language)
        
        for chunk in sorted_chunks:
            pos = result.find(chunk.content)
            if pos == -1:
                continue
            
            first_line = chunk.content.split('\n')[0].strip()
            if len(first_line) > 60:
                first_line = first_line[:60] + '...'
            
            breadcrumb = ""
            if chunk.parent_context:
                breadcrumb = f" (in {'.'.join(chunk.parent_context)})"
            
            placeholder = f"{comment_prefix} Code for: {first_line}{breadcrumb}\n"
            result = result[:pos] + placeholder + result[pos + len(chunk.content):]
        
        return result.strip()
    
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
