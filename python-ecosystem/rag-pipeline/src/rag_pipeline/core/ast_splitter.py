"""
AST-based Code Splitter using Tree-sitter for accurate code parsing.

This module provides true AST-aware code chunking that:
1. Uses Tree-sitter for accurate AST parsing (15+ languages)
2. Splits code into semantic units (classes, functions, methods)
3. Uses RecursiveCharacterTextSplitter for oversized chunks (large methods)
4. Enriches metadata for better RAG retrieval
5. Maintains parent context ("breadcrumbs") for nested structures
6. Uses deterministic IDs for Qdrant deduplication

Key benefits over regex-based splitting:
- Accurate function/class boundary detection
- Language-aware parsing for 15+ languages
- Better metadata: content_type, language, semantic_names, parent_class
- Handles edge cases (nested functions, decorators, etc.)
- Deterministic chunk IDs prevent duplicates on re-indexing
"""

import re
import hashlib
import logging
from typing import List, Dict, Any, Optional, Set
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum

from langchain_text_splitters import RecursiveCharacterTextSplitter, Language
from llama_index.core.schema import Document as LlamaDocument, TextNode

logger = logging.getLogger(__name__)


class ContentType(Enum):
    """Content type as determined by AST parsing"""
    FUNCTIONS_CLASSES = "functions_classes"  # Full function/class definition
    SIMPLIFIED_CODE = "simplified_code"      # Remaining code with placeholders
    FALLBACK = "fallback"                    # Non-AST parsed content
    OVERSIZED_SPLIT = "oversized_split"      # Large chunk split by RecursiveCharacterTextSplitter


# Map file extensions to LangChain Language enum
EXTENSION_TO_LANGUAGE: Dict[str, Language] = {
    # Python
    '.py': Language.PYTHON,
    '.pyw': Language.PYTHON,
    '.pyi': Language.PYTHON,

    # Java/JVM
    '.java': Language.JAVA,
    '.kt': Language.KOTLIN,
    '.kts': Language.KOTLIN,
    '.scala': Language.SCALA,

    # JavaScript/TypeScript
    '.js': Language.JS,
    '.jsx': Language.JS,
    '.mjs': Language.JS,
    '.cjs': Language.JS,
    '.ts': Language.TS,
    '.tsx': Language.TS,

    # Systems languages
    '.go': Language.GO,
    '.rs': Language.RUST,
    '.c': Language.C,
    '.h': Language.C,
    '.cpp': Language.CPP,
    '.cc': Language.CPP,
    '.cxx': Language.CPP,
    '.hpp': Language.CPP,
    '.hxx': Language.CPP,
    '.cs': Language.CSHARP,

    # Web/Scripting
    '.php': Language.PHP,
    '.rb': Language.RUBY,
    '.lua': Language.LUA,
    '.pl': Language.PERL,
    '.pm': Language.PERL,
    '.swift': Language.SWIFT,

    # Markup/Config
    '.md': Language.MARKDOWN,
    '.markdown': Language.MARKDOWN,
    '.html': Language.HTML,
    '.htm': Language.HTML,
    '.rst': Language.RST,
    '.tex': Language.LATEX,
    '.proto': Language.PROTO,
    '.sol': Language.SOL,
    '.hs': Language.HASKELL,
    '.cob': Language.COBOL,
    '.cbl': Language.COBOL,
}

# Languages that support full AST parsing via tree-sitter
AST_SUPPORTED_LANGUAGES = {
    Language.PYTHON, Language.JAVA, Language.KOTLIN, Language.JS, Language.TS,
    Language.GO, Language.RUST, Language.C, Language.CPP, Language.CSHARP,
    Language.PHP, Language.RUBY, Language.SCALA, Language.LUA, Language.PERL,
    Language.SWIFT, Language.HASKELL, Language.COBOL
}

# Tree-sitter language name mapping (tree-sitter-languages uses these names)
LANGUAGE_TO_TREESITTER: Dict[Language, str] = {
    Language.PYTHON: 'python',
    Language.JAVA: 'java',
    Language.KOTLIN: 'kotlin',
    Language.JS: 'javascript',
    Language.TS: 'typescript',
    Language.GO: 'go',
    Language.RUST: 'rust',
    Language.C: 'c',
    Language.CPP: 'cpp',
    Language.CSHARP: 'c_sharp',
    Language.PHP: 'php',
    Language.RUBY: 'ruby',
    Language.SCALA: 'scala',
    Language.LUA: 'lua',
    Language.PERL: 'perl',
    Language.SWIFT: 'swift',
    Language.HASKELL: 'haskell',
}

# Node types that represent semantic units (classes, functions, etc.)
SEMANTIC_NODE_TYPES: Dict[str, Dict[str, List[str]]] = {
    'python': {
        'class': ['class_definition'],
        'function': ['function_definition', 'async_function_definition'],
    },
    'java': {
        'class': ['class_declaration', 'interface_declaration', 'enum_declaration'],
        'function': ['method_declaration', 'constructor_declaration'],
    },
    'javascript': {
        'class': ['class_declaration'],
        'function': ['function_declaration', 'method_definition', 'arrow_function', 'generator_function_declaration'],
    },
    'typescript': {
        'class': ['class_declaration', 'interface_declaration'],
        'function': ['function_declaration', 'method_definition', 'arrow_function'],
    },
    'go': {
        'class': ['type_declaration'],  # structs, interfaces
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
    'kotlin': {
        'class': ['class_declaration', 'object_declaration', 'interface_declaration'],
        'function': ['function_declaration'],
    },
    'php': {
        'class': ['class_declaration', 'interface_declaration', 'trait_declaration'],
        'function': ['function_definition', 'method_declaration'],
    },
    'ruby': {
        'class': ['class', 'module'],
        'function': ['method', 'singleton_method'],
    },
    'cpp': {
        'class': ['class_specifier', 'struct_specifier'],
        'function': ['function_definition'],
    },
    'c': {
        'class': ['struct_specifier'],
        'function': ['function_definition'],
    },
    'scala': {
        'class': ['class_definition', 'object_definition', 'trait_definition'],
        'function': ['function_definition'],
    },
}

# Metadata extraction patterns (fallback when AST doesn't provide names)
METADATA_PATTERNS = {
    'python': {
        'class': re.compile(r'^class\s+(\w+)', re.MULTILINE),
        'function': re.compile(r'^(?:async\s+)?def\s+(\w+)\s*\(', re.MULTILINE),
    },
    'java': {
        'class': re.compile(r'(?:public\s+|private\s+|protected\s+)?(?:abstract\s+|final\s+)?class\s+(\w+)', re.MULTILINE),
        'interface': re.compile(r'(?:public\s+)?interface\s+(\w+)', re.MULTILINE),
        'method': re.compile(r'(?:public|private|protected)\s+(?:static\s+)?[\w<>,\s]+\s+(\w+)\s*\(', re.MULTILINE),
    },
    'javascript': {
        'class': re.compile(r'(?:export\s+)?(?:default\s+)?class\s+(\w+)', re.MULTILINE),
        'function': re.compile(r'(?:export\s+)?(?:async\s+)?function\s*\*?\s*(\w+)\s*\(', re.MULTILINE),
    },
    'typescript': {
        'class': re.compile(r'(?:export\s+)?(?:default\s+)?class\s+(\w+)', re.MULTILINE),
        'interface': re.compile(r'(?:export\s+)?interface\s+(\w+)', re.MULTILINE),
        'function': re.compile(r'(?:export\s+)?(?:async\s+)?function\s*\*?\s*(\w+)\s*\(', re.MULTILINE),
    },
    'go': {
        'function': re.compile(r'^func\s+(?:\([^)]+\)\s+)?(\w+)\s*\(', re.MULTILINE),
        'struct': re.compile(r'^type\s+(\w+)\s+struct\s*\{', re.MULTILINE),
    },
    'rust': {
        'function': re.compile(r'^(?:pub\s+)?(?:async\s+)?fn\s+(\w+)', re.MULTILINE),
        'struct': re.compile(r'^(?:pub\s+)?struct\s+(\w+)', re.MULTILINE),
    },
    'c_sharp': {
        'class': re.compile(r'(?:public\s+|private\s+|internal\s+)?(?:abstract\s+|sealed\s+)?class\s+(\w+)', re.MULTILINE),
        'method': re.compile(r'(?:public|private|protected|internal)\s+(?:static\s+)?[\w<>,\s]+\s+(\w+)\s*\(', re.MULTILINE),
    },
    'kotlin': {
        'class': re.compile(r'(?:data\s+|sealed\s+|open\s+)?class\s+(\w+)', re.MULTILINE),
        'function': re.compile(r'(?:fun|suspend\s+fun)\s+(\w+)\s*\(', re.MULTILINE),
    },
    'php': {
        'class': re.compile(r'(?:abstract\s+|final\s+)?class\s+(\w+)', re.MULTILINE),
        'function': re.compile(r'(?:public|private|protected|static|\s)*function\s+(\w+)\s*\(', re.MULTILINE),
    },
}


@dataclass
class ASTChunk:
    """Represents a chunk of code from AST parsing"""
    content: str
    content_type: ContentType
    language: str
    path: str
    semantic_names: List[str] = field(default_factory=list)
    parent_context: List[str] = field(default_factory=list)  # Breadcrumb: ["MyClass", "inner_method"]
    docstring: Optional[str] = None
    signature: Optional[str] = None
    start_line: int = 0
    end_line: int = 0
    node_type: Optional[str] = None


def generate_deterministic_id(path: str, content: str, chunk_index: int = 0) -> str:
    """
    Generate a deterministic ID for a chunk based on file path and content.

    This ensures the same code chunk always gets the same ID, preventing
    duplicates in Qdrant during re-indexing.

    Args:
        path: File path
        content: Chunk content
        chunk_index: Index of chunk within file (for disambiguation)

    Returns:
        Deterministic hex ID string
    """
    # Use path + content hash + index for uniqueness
    hash_input = f"{path}:{chunk_index}:{content[:500]}"  # First 500 chars for efficiency
    return hashlib.sha256(hash_input.encode('utf-8')).hexdigest()[:32]


def compute_file_hash(content: str) -> str:
    """Compute hash of file content for change detection"""
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


class ASTCodeSplitter:
    """
    AST-based code splitter using Tree-sitter for accurate parsing.

    Features:
    - True AST parsing via tree-sitter for accurate code structure detection
    - Splits code into semantic units (classes, functions, methods)
    - Maintains parent context (breadcrumbs) for nested structures
    - Falls back to RecursiveCharacterTextSplitter for oversized chunks
    - Uses deterministic IDs for Qdrant deduplication
    - Enriches metadata for improved RAG retrieval

    Usage:
        splitter = ASTCodeSplitter(max_chunk_size=2000)
        nodes = splitter.split_documents(documents)
    """

    DEFAULT_MAX_CHUNK_SIZE = 2000
    DEFAULT_MIN_CHUNK_SIZE = 100
    DEFAULT_CHUNK_OVERLAP = 200
    DEFAULT_PARSER_THRESHOLD = 10  # Minimum lines for AST parsing

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
            max_chunk_size: Maximum characters per chunk. Larger chunks are split.
            min_chunk_size: Minimum characters for a valid chunk.
            chunk_overlap: Overlap between chunks when splitting oversized content.
            parser_threshold: Minimum lines for AST parsing (smaller files use fallback).
        """
        self.max_chunk_size = max_chunk_size
        self.min_chunk_size = min_chunk_size
        self.chunk_overlap = chunk_overlap
        self.parser_threshold = parser_threshold

        # Cache text splitters for oversized chunks
        self._splitter_cache: Dict[Language, RecursiveCharacterTextSplitter] = {}

        # Default text splitter for unknown languages
        self._default_splitter = RecursiveCharacterTextSplitter(
            chunk_size=max_chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
        )

        # Track if tree-sitter is available
        self._tree_sitter_available: Optional[bool] = None
        # Cache for language modules and parsers
        self._language_cache: Dict[str, Any] = {}

    def _get_tree_sitter_language(self, lang_name: str):
        """
        Get tree-sitter Language object for a language name.
        Uses the new tree-sitter API with individual language packages.
        """
        if lang_name in self._language_cache:
            return self._language_cache[lang_name]

        try:
            from tree_sitter import Language

            # Map language names to their package modules
            lang_modules = {
                'python': 'tree_sitter_python',
                'java': 'tree_sitter_java',
                'javascript': 'tree_sitter_javascript',
                'typescript': 'tree_sitter_typescript',
                'go': 'tree_sitter_go',
                'rust': 'tree_sitter_rust',
                'c': 'tree_sitter_c',
                'cpp': 'tree_sitter_cpp',
                'c_sharp': 'tree_sitter_c_sharp',
                'ruby': 'tree_sitter_ruby',
                'php': 'tree_sitter_php',
            }

            module_name = lang_modules.get(lang_name)
            if not module_name:
                return None

            # Dynamic import of language module
            import importlib
            lang_module = importlib.import_module(module_name)

            # Create Language object using the new API
            language = Language(lang_module.language())
            self._language_cache[lang_name] = language
            return language

        except Exception as e:
            logger.debug(f"Could not load tree-sitter language '{lang_name}': {e}")
            return None

    def _check_tree_sitter(self) -> bool:
        """Check if tree-sitter is available"""
        if self._tree_sitter_available is None:
            try:
                from tree_sitter import Parser, Language
                import tree_sitter_python as tspython

                # Test with the new API
                py_language = Language(tspython.language())
                parser = Parser(py_language)
                parser.parse(b"def test(): pass")

                self._tree_sitter_available = True
                logger.info("tree-sitter is available and working")
            except ImportError as e:
                logger.warning(f"tree-sitter not installed: {e}")
                self._tree_sitter_available = False
            except Exception as e:
                logger.warning(f"tree-sitter error: {type(e).__name__}: {e}")
                self._tree_sitter_available = False
        return self._tree_sitter_available

    def _get_language_from_path(self, path: str) -> Optional[Language]:
        """Determine Language enum from file path"""
        ext = Path(path).suffix.lower()
        return EXTENSION_TO_LANGUAGE.get(ext)

    def _get_treesitter_language(self, language: Language) -> Optional[str]:
        """Get tree-sitter language name from Language enum"""
        return LANGUAGE_TO_TREESITTER.get(language)

    def _get_text_splitter(self, language: Language) -> RecursiveCharacterTextSplitter:
        """Get language-specific text splitter for oversized chunks"""
        if language not in self._splitter_cache:
            try:
                self._splitter_cache[language] = RecursiveCharacterTextSplitter.from_language(
                    language=language,
                    chunk_size=self.max_chunk_size,
                    chunk_overlap=self.chunk_overlap,
                )
            except Exception:
                # Fallback if language not supported
                self._splitter_cache[language] = self._default_splitter
        return self._splitter_cache[language]

    def _parse_with_ast(
        self,
        text: str,
        language: Language,
        path: str
    ) -> List[ASTChunk]:
        """
        Parse code using AST via tree-sitter.

        Returns list of ASTChunk objects with content and metadata.
        """
        if not self._check_tree_sitter():
            return []

        ts_lang = self._get_treesitter_language(language)
        if not ts_lang:
            logger.debug(f"No tree-sitter mapping for {language}, using fallback")
            return []

        try:
            from tree_sitter import Parser

            # Get Language object for this language
            lang_obj = self._get_tree_sitter_language(ts_lang)
            if not lang_obj:
                logger.debug(f"tree-sitter language '{ts_lang}' not available")
                return []

            # Create parser with the language
            parser = Parser(lang_obj)
            tree = parser.parse(bytes(text, "utf8"))

            # Extract chunks with breadcrumb context
            chunks = self._extract_ast_chunks_with_context(
                tree.root_node,
                text,
                ts_lang,
                path
            )

            return chunks

        except Exception as e:
            logger.warning(f"AST parsing failed for {path}: {e}")
            return []

    def _extract_ast_chunks_with_context(
        self,
        root_node,
        source_code: str,
        language: str,
        path: str
    ) -> List[ASTChunk]:
        """
        Extract function/class chunks from AST tree with parent context (breadcrumbs).

        This solves the "context loss" problem by tracking parent classes/modules
        so that a method knows it belongs to a specific class.
        """
        chunks = []
        processed_ranges: Set[tuple] = set()  # Track (start, end) to avoid duplicates

        # Get node types for this language
        lang_node_types = SEMANTIC_NODE_TYPES.get(language, {})
        class_types = set(lang_node_types.get('class', []))
        function_types = set(lang_node_types.get('function', []))
        all_semantic_types = class_types | function_types

        def get_node_name(node) -> Optional[str]:
            """Extract name from a node (class/function name)"""
            for child in node.children:
                if child.type == 'identifier' or child.type == 'name':
                    return source_code[child.start_byte:child.end_byte]
                # For some languages, name might be in a specific child
                if child.type in ('type_identifier', 'property_identifier'):
                    return source_code[child.start_byte:child.end_byte]
            return None

        def traverse(node, parent_context: List[str], depth: int = 0):
            """
            Recursively traverse AST and extract semantic chunks with breadcrumbs.

            Args:
                node: Current AST node
                parent_context: List of parent class/function names (breadcrumb)
                depth: Current depth in tree
            """
            node_range = (node.start_byte, node.end_byte)

            # Check if this is a semantic unit
            if node.type in all_semantic_types:
                # Skip if already processed (nested in another chunk)
                if node_range in processed_ranges:
                    return

                content = source_code[node.start_byte:node.end_byte]

                # Calculate line numbers
                start_line = source_code[:node.start_byte].count('\n') + 1
                end_line = start_line + content.count('\n')

                # Get the name of this node
                node_name = get_node_name(node)

                # Determine content type
                is_class = node.type in class_types

                chunk = ASTChunk(
                    content=content,
                    content_type=ContentType.FUNCTIONS_CLASSES,
                    language=language,
                    path=path,
                    semantic_names=[node_name] if node_name else [],
                    parent_context=list(parent_context),  # Copy the breadcrumb
                    start_line=start_line,
                    end_line=end_line,
                    node_type=node.type,
                )

                chunks.append(chunk)
                processed_ranges.add(node_range)

                # If this is a class, traverse children with updated context
                if is_class and node_name:
                    new_context = parent_context + [node_name]
                    for child in node.children:
                        traverse(child, new_context, depth + 1)
            else:
                # Continue traversing children with current context
                for child in node.children:
                    traverse(child, parent_context, depth + 1)

        traverse(root_node, [])

        # Create simplified code (skeleton with placeholders)
        simplified = self._create_simplified_code(source_code, chunks, language)
        if simplified and simplified.strip() and len(simplified.strip()) > 50:
            chunks.append(ASTChunk(
                content=simplified,
                content_type=ContentType.SIMPLIFIED_CODE,
                language=language,
                path=path,
                semantic_names=[],
                parent_context=[],
                start_line=1,
                end_line=source_code.count('\n') + 1,
                node_type='simplified',
            ))

        return chunks

    def _create_simplified_code(
        self,
        source_code: str,
        chunks: List[ASTChunk],
        language: str
    ) -> str:
        """
        Create simplified code with placeholders for extracted chunks.

        This gives RAG context about the overall file structure without
        including full function/class bodies.

        Example output:
            # Code for: class MyClass:
            # Code for: def my_function():
            if __name__ == "__main__":
                main()
        """
        if not chunks:
            return source_code

        # Get chunks that are functions_classes type (not simplified)
        semantic_chunks = [c for c in chunks if c.content_type == ContentType.FUNCTIONS_CLASSES]

        if not semantic_chunks:
            return source_code

        # Sort by start position (reverse) to replace from end
        sorted_chunks = sorted(
            semantic_chunks,
            key=lambda x: source_code.find(x.content),
            reverse=True
        )

        result = source_code

        # Comment style by language
        comment_prefix = {
            'python': '#',
            'javascript': '//',
            'typescript': '//',
            'java': '//',
            'kotlin': '//',
            'go': '//',
            'rust': '//',
            'c': '//',
            'cpp': '//',
            'c_sharp': '//',
            'php': '//',
            'ruby': '#',
            'lua': '--',
            'perl': '#',
            'scala': '//',
        }.get(language, '//')

        for chunk in sorted_chunks:
            # Find the position of this chunk in the source
            pos = result.find(chunk.content)
            if pos == -1:
                continue

            # Extract first line for placeholder
            first_line = chunk.content.split('\n')[0].strip()
            # Truncate if too long
            if len(first_line) > 60:
                first_line = first_line[:60] + '...'

            # Add breadcrumb context to placeholder
            breadcrumb = ""
            if chunk.parent_context:
                breadcrumb = f" (in {'.'.join(chunk.parent_context)})"

            placeholder = f"{comment_prefix} Code for: {first_line}{breadcrumb}\n"

            result = result[:pos] + placeholder + result[pos + len(chunk.content):]

        return result.strip()

    def _extract_metadata(
        self,
        chunk: ASTChunk,
        base_metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Extract and enrich metadata from an AST chunk"""
        metadata = dict(base_metadata)

        # Core AST metadata
        metadata['content_type'] = chunk.content_type.value
        metadata['node_type'] = chunk.node_type

        # Breadcrumb context (critical for RAG)
        if chunk.parent_context:
            metadata['parent_context'] = chunk.parent_context
            metadata['parent_class'] = chunk.parent_context[-1] if chunk.parent_context else None
            metadata['full_path'] = '.'.join(chunk.parent_context + chunk.semantic_names[:1])

        # Semantic names
        if chunk.semantic_names:
            metadata['semantic_names'] = chunk.semantic_names[:10]
            metadata['primary_name'] = chunk.semantic_names[0]

        # Line numbers
        metadata['start_line'] = chunk.start_line
        metadata['end_line'] = chunk.end_line

        # Try to extract additional metadata via regex patterns
        patterns = METADATA_PATTERNS.get(chunk.language, {})

        # Extract docstring
        docstring = self._extract_docstring(chunk.content, chunk.language)
        if docstring:
            metadata['docstring'] = docstring[:500]

        # Extract signature
        signature = self._extract_signature(chunk.content, chunk.language)
        if signature:
            metadata['signature'] = signature

        # Extract additional names not caught by AST
        if not chunk.semantic_names:
            names = []
            for pattern_type, pattern in patterns.items():
                matches = pattern.findall(chunk.content)
                names.extend(matches)
            if names:
                metadata['semantic_names'] = list(set(names))[:10]
                metadata['primary_name'] = names[0]

        return metadata

    def _extract_docstring(self, content: str, language: str) -> Optional[str]:
        """Extract docstring from code chunk"""
        if language == 'python':
            match = re.search(r'"""([\s\S]*?)"""|\'\'\'([\s\S]*?)\'\'\'', content)
            if match:
                return (match.group(1) or match.group(2)).strip()

        elif language in ('javascript', 'typescript', 'java', 'kotlin', 'c_sharp', 'php', 'go', 'scala'):
            match = re.search(r'/\*\*([\s\S]*?)\*/', content)
            if match:
                doc = match.group(1)
                doc = re.sub(r'^\s*\*\s?', '', doc, flags=re.MULTILINE)
                return doc.strip()

        elif language == 'rust':
            lines = []
            for line in content.split('\n'):
                if line.strip().startswith('///'):
                    lines.append(line.strip()[3:].strip())
                elif lines:
                    break
            if lines:
                return '\n'.join(lines)

        return None

    def _extract_signature(self, content: str, language: str) -> Optional[str]:
        """Extract function/method signature from code chunk"""
        lines = content.split('\n')

        for line in lines[:15]:
            line = line.strip()

            if language == 'python':
                if line.startswith(('def ', 'async def ', 'class ')):
                    sig = line
                    if line.startswith('class ') and ':' in line:
                        return line.split(':')[0] + ':'
                    if ')' not in sig and ':' not in sig:
                        idx = -1
                        for i, l in enumerate(lines):
                            if l.strip() == line:
                                idx = i
                                break
                        if idx >= 0:
                            for next_line in lines[idx+1:idx+5]:
                                sig += ' ' + next_line.strip()
                                if ')' in next_line:
                                    break
                    if ':' in sig:
                        return sig.split(':')[0] + ':'
                    return sig

            elif language in ('java', 'kotlin', 'c_sharp'):
                if any(kw in line for kw in ['public ', 'private ', 'protected ', 'internal ', 'fun ']):
                    if '(' in line and not line.startswith('//'):
                        return line.split('{')[0].strip()

            elif language in ('javascript', 'typescript'):
                if line.startswith(('function ', 'async function ', 'class ')):
                    return line.split('{')[0].strip()
                if '=>' in line and '(' in line:
                    return line.split('=>')[0].strip() + ' =>'

            elif language == 'go':
                if line.startswith('func ') or line.startswith('type '):
                    return line.split('{')[0].strip()

            elif language == 'rust':
                if line.startswith(('fn ', 'pub fn ', 'async fn ', 'pub async fn ', 'impl ', 'struct ', 'trait ')):
                    return line.split('{')[0].strip()

        return None

    def _split_oversized_chunk(
        self,
        chunk: ASTChunk,
        language: Optional[Language],
        base_metadata: Dict[str, Any],
        path: str
    ) -> List[TextNode]:
        """
        Split an oversized chunk using RecursiveCharacterTextSplitter.

        This is used when AST-parsed chunks (e.g., very large classes/functions)
        still exceed the max_chunk_size.
        """
        splitter = (
            self._get_text_splitter(language)
            if language and language in AST_SUPPORTED_LANGUAGES
            else self._default_splitter
        )

        sub_chunks = splitter.split_text(chunk.content)
        nodes = []

        # Parent ID for linking sub-chunks
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

            # Preserve breadcrumb context
            if chunk.parent_context:
                metadata['parent_context'] = chunk.parent_context
                metadata['parent_class'] = chunk.parent_context[-1]

            if chunk.semantic_names:
                metadata['semantic_names'] = chunk.semantic_names
                metadata['primary_name'] = chunk.semantic_names[0]

            # Deterministic ID for this sub-chunk
            chunk_id = generate_deterministic_id(path, sub_chunk, i)

            node = TextNode(
                id_=chunk_id,
                text=sub_chunk,
                metadata=metadata
            )
            nodes.append(node)

        return nodes

    def split_documents(self, documents: List[LlamaDocument]) -> List[TextNode]:
        """
        Split LlamaIndex documents using AST-based parsing.

        Args:
            documents: List of LlamaIndex Document objects

        Returns:
            List of TextNode objects with enriched metadata and deterministic IDs
        """
        all_nodes = []

        for doc in documents:
            path = doc.metadata.get('path', 'unknown')

            # Determine Language enum
            language = self._get_language_from_path(path)

            # Check if AST parsing is supported and beneficial
            line_count = doc.text.count('\n') + 1
            use_ast = (
                language is not None
                and language in AST_SUPPORTED_LANGUAGES
                and line_count >= self.parser_threshold
                and self._check_tree_sitter()
            )

            if use_ast:
                nodes = self._split_with_ast(doc, language)
            else:
                nodes = self._split_fallback(doc, language)

            all_nodes.extend(nodes)
            logger.debug(f"Split {path} into {len(nodes)} chunks (AST={use_ast})")

        return all_nodes

    def _split_with_ast(
        self,
        doc: LlamaDocument,
        language: Language
    ) -> List[TextNode]:
        """Split document using AST parsing with breadcrumb context"""
        text = doc.text
        path = doc.metadata.get('path', 'unknown')

        # Try AST parsing
        ast_chunks = self._parse_with_ast(text, language, path)

        if not ast_chunks:
            return self._split_fallback(doc, language)

        nodes = []
        chunk_counter = 0

        for ast_chunk in ast_chunks:
            # Check if chunk is oversized
            if len(ast_chunk.content) > self.max_chunk_size:
                # Split oversized chunk
                sub_nodes = self._split_oversized_chunk(
                    ast_chunk,
                    language,
                    doc.metadata,
                    path
                )
                nodes.extend(sub_nodes)
                chunk_counter += len(sub_nodes)
            else:
                # Create node with enriched metadata
                metadata = self._extract_metadata(ast_chunk, doc.metadata)
                metadata['chunk_index'] = chunk_counter
                metadata['total_chunks'] = len(ast_chunks)

                # Deterministic ID
                chunk_id = generate_deterministic_id(path, ast_chunk.content, chunk_counter)

                node = TextNode(
                    id_=chunk_id,
                    text=ast_chunk.content,
                    metadata=metadata
                )
                nodes.append(node)
                chunk_counter += 1

        return nodes

    def _split_fallback(
        self,
        doc: LlamaDocument,
        language: Optional[Language] = None
    ) -> List[TextNode]:
        """Fallback splitting using RecursiveCharacterTextSplitter"""
        text = doc.text
        path = doc.metadata.get('path', 'unknown')

        if not text or not text.strip():
            return []

        splitter = (
            self._get_text_splitter(language)
            if language and language in AST_SUPPORTED_LANGUAGES
            else self._default_splitter
        )

        chunks = splitter.split_text(text)
        nodes = []
        text_offset = 0

        for i, chunk in enumerate(chunks):
            if not chunk or not chunk.strip():
                continue

            if len(chunk.strip()) < self.min_chunk_size and len(chunks) > 1:
                continue

            # Truncate if too large
            if len(chunk) > 30000:
                chunk = chunk[:30000]

            # Calculate line numbers
            start_line = text[:text_offset].count('\n') + 1 if text_offset > 0 else 1
            chunk_pos = text.find(chunk, text_offset)
            if chunk_pos >= 0:
                text_offset = chunk_pos + len(chunk)
            end_line = start_line + chunk.count('\n')

            # Extract metadata using regex patterns
            lang_str = doc.metadata.get('language', 'text')
            metadata = dict(doc.metadata)
            metadata['content_type'] = ContentType.FALLBACK.value
            metadata['chunk_index'] = i
            metadata['total_chunks'] = len(chunks)
            metadata['start_line'] = start_line
            metadata['end_line'] = end_line

            # Try to extract semantic names
            patterns = METADATA_PATTERNS.get(lang_str, {})
            names = []
            for pattern_type, pattern in patterns.items():
                matches = pattern.findall(chunk)
                names.extend(matches)
            if names:
                metadata['semantic_names'] = list(set(names))[:10]
                metadata['primary_name'] = names[0]

            # Deterministic ID
            chunk_id = generate_deterministic_id(path, chunk, i)

            node = TextNode(
                id_=chunk_id,
                text=chunk,
                metadata=metadata
            )
            nodes.append(node)

        return nodes

    @staticmethod
    def get_supported_languages() -> List[str]:
        """Return list of languages with AST support"""
        return list(LANGUAGE_TO_TREESITTER.values())

    @staticmethod
    def is_ast_supported(path: str) -> bool:
        """Check if AST parsing is supported for a file"""
        ext = Path(path).suffix.lower()
        lang = EXTENSION_TO_LANGUAGE.get(ext)
        return lang is not None and lang in AST_SUPPORTED_LANGUAGES
