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
    """Represents a chunk of code from AST parsing."""
    content: str
    content_type: ContentType
    language: str
    path: str
    semantic_names: List[str] = field(default_factory=list)
    parent_context: List[str] = field(default_factory=list)
    docstring: Optional[str] = None
    signature: Optional[str] = None
    start_line: int = 0
    end_line: int = 0
    node_type: Optional[str] = None
    extends: List[str] = field(default_factory=list)
    implements: List[str] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    namespace: Optional[str] = None


class ASTCodeSplitter:
    """
    AST-based code splitter using Tree-sitter queries for accurate parsing.
    
    Features:
    - Uses .scm query files for declarative pattern matching
    - Splits code into semantic units (classes, functions, methods)
    - Falls back to RecursiveCharacterTextSplitter when needed
    - Uses deterministic IDs for Qdrant deduplication
    - Enriches metadata for improved RAG retrieval
    
    Usage:
        splitter = ASTCodeSplitter(max_chunk_size=2000)
        nodes = splitter.split_documents(documents)
    """
    
    DEFAULT_MAX_CHUNK_SIZE = 2000
    DEFAULT_MIN_CHUNK_SIZE = 100
    DEFAULT_CHUNK_OVERLAP = 200
    DEFAULT_PARSER_THRESHOLD = 10
    
    def __init__(
        self,
        max_chunk_size: int = DEFAULT_MAX_CHUNK_SIZE,
        min_chunk_size: int = DEFAULT_MIN_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
        parser_threshold: int = DEFAULT_PARSER_THRESHOLD
    ):
        """
        Initialize AST code splitter.
        
        Args:
            max_chunk_size: Maximum characters per chunk
            min_chunk_size: Minimum characters for a valid chunk
            chunk_overlap: Overlap between chunks when splitting oversized content
            parser_threshold: Minimum lines for AST parsing
        """
        self.max_chunk_size = max_chunk_size
        self.min_chunk_size = min_chunk_size
        self.chunk_overlap = chunk_overlap
        self.parser_threshold = parser_threshold
        
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
                )
                
                # Extract docstring and signature
                chunk.docstring = self._metadata_extractor.extract_docstring(main_cap.text, lang_name)
                chunk.signature = self._metadata_extractor.extract_signature(main_cap.text, lang_name)
                
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
        """Split an oversized chunk using RecursiveCharacterTextSplitter."""
        splitter = self._get_text_splitter(language) if language else self._default_splitter
        sub_chunks = splitter.split_text(chunk.content)
        
        nodes = []
        parent_id = generate_deterministic_id(path, chunk.content, 0)
        
        for i, sub_chunk in enumerate(sub_chunks):
            if not sub_chunk or not sub_chunk.strip():
                continue
            if len(sub_chunk.strip()) < self.min_chunk_size and len(sub_chunks) > 1:
                continue
            
            metadata = dict(base_metadata)
            metadata['content_type'] = ContentType.OVERSIZED_SPLIT.value
            metadata['original_content_type'] = chunk.content_type.value
            metadata['parent_chunk_id'] = parent_id
            metadata['sub_chunk_index'] = i
            metadata['total_sub_chunks'] = len(sub_chunks)
            
            if chunk.parent_context:
                metadata['parent_context'] = chunk.parent_context
                metadata['parent_class'] = chunk.parent_context[-1]
            
            if chunk.semantic_names:
                metadata['semantic_names'] = chunk.semantic_names
                metadata['primary_name'] = chunk.semantic_names[0]
            
            chunk_id = generate_deterministic_id(path, sub_chunk, i)
            nodes.append(TextNode(id_=chunk_id, text=sub_chunk, metadata=metadata))
        
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
            
            chunk_id = generate_deterministic_id(path, chunk, i)
            nodes.append(TextNode(id_=chunk_id, text=chunk, metadata=metadata))
        
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
        
        return metadata
    
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
