"""
Semantic Code Splitter - Intelligent code splitting using LangChain's language-aware splitters.

This module provides smart code chunking that:
1. Uses LangChain's RecursiveCharacterTextSplitter with language-specific separators
2. Supports 25+ programming languages out of the box
3. Enriches metadata with semantic information (function names, imports, etc.)
4. Falls back gracefully for unsupported languages
"""

import re
import hashlib
import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from enum import Enum

from langchain_text_splitters import RecursiveCharacterTextSplitter, Language
from llama_index.core.schema import Document, TextNode

logger = logging.getLogger(__name__)


class ChunkType(Enum):
    """Type of code chunk for semantic understanding"""
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    INTERFACE = "interface"
    MODULE = "module"
    IMPORTS = "imports"
    CONSTANTS = "constants"
    DOCUMENTATION = "documentation"
    CONFIG = "config"
    MIXED = "mixed"
    UNKNOWN = "unknown"


@dataclass
class CodeBlock:
    """Represents a logical block of code"""
    content: str
    chunk_type: ChunkType
    name: Optional[str] = None
    parent_name: Optional[str] = None
    start_line: int = 0
    end_line: int = 0
    imports: List[str] = field(default_factory=list)
    docstring: Optional[str] = None
    signature: Optional[str] = None


# Map internal language names to LangChain Language enum
LANGUAGE_MAP: Dict[str, Language] = {
    'python': Language.PYTHON,
    'java': Language.JAVA,
    'kotlin': Language.KOTLIN,
    'javascript': Language.JS,
    'typescript': Language.TS,
    'go': Language.GO,
    'rust': Language.RUST,
    'php': Language.PHP,
    'ruby': Language.RUBY,
    'scala': Language.SCALA,
    'swift': Language.SWIFT,
    'c': Language.C,
    'cpp': Language.CPP,
    'csharp': Language.CSHARP,
    'markdown': Language.MARKDOWN,
    'html': Language.HTML,
    'latex': Language.LATEX,
    'rst': Language.RST,
    'lua': Language.LUA,
    'perl': Language.PERL,
    'haskell': Language.HASKELL,
    'solidity': Language.SOL,
    'proto': Language.PROTO,
    'cobol': Language.COBOL,
}

# Patterns for metadata extraction
METADATA_PATTERNS = {
    'python': {
        'class': re.compile(r'^class\s+(\w+)', re.MULTILINE),
        'function': re.compile(r'^(?:async\s+)?def\s+(\w+)\s*\(', re.MULTILINE),
        'import': re.compile(r'^(?:from\s+[\w.]+\s+)?import\s+.+$', re.MULTILINE),
        'docstring': re.compile(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\''),
    },
    'java': {
        'class': re.compile(r'(?:public\s+|private\s+|protected\s+)?(?:abstract\s+|final\s+)?class\s+(\w+)', re.MULTILINE),
        'interface': re.compile(r'(?:public\s+)?interface\s+(\w+)', re.MULTILINE),
        'method': re.compile(r'(?:public|private|protected)\s+(?:static\s+)?(?:final\s+)?[\w<>,\s]+\s+(\w+)\s*\(', re.MULTILINE),
        'import': re.compile(r'^import\s+[\w.*]+;', re.MULTILINE),
    },
    'javascript': {
        'class': re.compile(r'(?:export\s+)?(?:default\s+)?class\s+(\w+)', re.MULTILINE),
        'function': re.compile(r'(?:export\s+)?(?:async\s+)?function\s*\*?\s*(\w+)\s*\(', re.MULTILINE),
        'arrow': re.compile(r'(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\([^)]*\)\s*=>', re.MULTILINE),
        'import': re.compile(r'^import\s+.*?from\s+[\'"]([^\'"]+)[\'"]', re.MULTILINE),
    },
    'typescript': {
        'class': re.compile(r'(?:export\s+)?(?:default\s+)?class\s+(\w+)', re.MULTILINE),
        'interface': re.compile(r'(?:export\s+)?interface\s+(\w+)', re.MULTILINE),
        'function': re.compile(r'(?:export\s+)?(?:async\s+)?function\s*\*?\s*(\w+)\s*\(', re.MULTILINE),
        'type': re.compile(r'(?:export\s+)?type\s+(\w+)', re.MULTILINE),
        'import': re.compile(r'^import\s+.*?from\s+[\'"]([^\'"]+)[\'"]', re.MULTILINE),
    },
    'go': {
        'function': re.compile(r'^func\s+(?:\([^)]+\)\s+)?(\w+)\s*\(', re.MULTILINE),
        'struct': re.compile(r'^type\s+(\w+)\s+struct\s*\{', re.MULTILINE),
        'interface': re.compile(r'^type\s+(\w+)\s+interface\s*\{', re.MULTILINE),
    },
    'rust': {
        'function': re.compile(r'^(?:pub\s+)?(?:async\s+)?fn\s+(\w+)', re.MULTILINE),
        'struct': re.compile(r'^(?:pub\s+)?struct\s+(\w+)', re.MULTILINE),
        'impl': re.compile(r'^impl(?:<[^>]+>)?\s+(?:\w+\s+for\s+)?(\w+)', re.MULTILINE),
        'trait': re.compile(r'^(?:pub\s+)?trait\s+(\w+)', re.MULTILINE),
    },
    'php': {
        'class': re.compile(r'(?:abstract\s+|final\s+)?class\s+(\w+)', re.MULTILINE),
        'interface': re.compile(r'interface\s+(\w+)', re.MULTILINE),
        'function': re.compile(r'(?:public|private|protected|static|\s)*function\s+(\w+)\s*\(', re.MULTILINE),
    },
    'csharp': {
        'class': re.compile(r'(?:public\s+|private\s+|protected\s+)?(?:abstract\s+|sealed\s+)?class\s+(\w+)', re.MULTILINE),
        'interface': re.compile(r'(?:public\s+)?interface\s+(\w+)', re.MULTILINE),
        'method': re.compile(r'(?:public|private|protected)\s+(?:static\s+)?(?:async\s+)?[\w<>,\s]+\s+(\w+)\s*\(', re.MULTILINE),
    },
}


class SemanticCodeSplitter:
    """
    Intelligent code splitter using LangChain's language-aware text splitters.
    
    Features:
    - Uses LangChain's RecursiveCharacterTextSplitter with language-specific separators
    - Supports 25+ programming languages (Python, Java, JS/TS, Go, Rust, PHP, etc.)
    - Enriches chunks with semantic metadata (function names, classes, imports)
    - Graceful fallback for unsupported languages
    """
    
    DEFAULT_CHUNK_SIZE = 1500
    DEFAULT_CHUNK_OVERLAP = 200
    DEFAULT_MIN_CHUNK_SIZE = 100
    
    def __init__(
        self,
        max_chunk_size: int = DEFAULT_CHUNK_SIZE,
        min_chunk_size: int = DEFAULT_MIN_CHUNK_SIZE,
        overlap: int = DEFAULT_CHUNK_OVERLAP
    ):
        self.max_chunk_size = max_chunk_size
        self.min_chunk_size = min_chunk_size
        self.overlap = overlap
        
        # Cache splitters for reuse
        self._splitter_cache: Dict[str, RecursiveCharacterTextSplitter] = {}
        
        # Default splitter for unknown languages
        self._default_splitter = RecursiveCharacterTextSplitter(
            chunk_size=max_chunk_size,
            chunk_overlap=overlap,
            length_function=len,
            is_separator_regex=False,
        )

    @staticmethod
    def _make_deterministic_id(namespace: str, path: str, chunk_index: int) -> str:
        """Generate deterministic chunk ID for idempotent indexing"""
        key = f"{namespace}:{path}:{chunk_index}"
        return hashlib.sha256(key.encode()).hexdigest()[:32]
    
    def _get_splitter(self, language: str) -> RecursiveCharacterTextSplitter:
        """Get or create a language-specific splitter"""
        if language in self._splitter_cache:
            return self._splitter_cache[language]
        
        lang_enum = LANGUAGE_MAP.get(language.lower())
        
        if lang_enum:
            splitter = RecursiveCharacterTextSplitter.from_language(
                language=lang_enum,
                chunk_size=self.max_chunk_size,
                chunk_overlap=self.overlap,
            )
            self._splitter_cache[language] = splitter
            return splitter
        
        return self._default_splitter
    
    def split_documents(self, documents: List[Document]) -> List[TextNode]:
        """Split documents into semantic chunks with enriched metadata"""
        return list(self.iter_split_documents(documents))

    def iter_split_documents(self, documents: List[Document]):
        """Generator that yields chunks one at a time for memory efficiency"""
        for doc in documents:
            language = doc.metadata.get("language", "text")
            path = doc.metadata.get("path", "unknown")
            
            try:
                for node in self._split_document(doc, language):
                    yield node
            except Exception as e:
                logger.warning(f"Splitting failed for {path}: {e}, using fallback")
                for node in self._fallback_split(doc):
                    yield node
    
    def _split_document(self, doc: Document, language: str) -> List[TextNode]:
        """Split a single document using language-aware splitter"""
        text = doc.text
        
        if not text or not text.strip():
            return []
        
        # Get language-specific splitter
        splitter = self._get_splitter(language)
        
        # Split the text
        chunks = splitter.split_text(text)
        
        # Filter empty chunks and convert to nodes with metadata
        nodes = []
        text_offset = 0
        
        for i, chunk in enumerate(chunks):
            if not chunk or not chunk.strip():
                continue
            
            # Skip very small chunks unless they're standalone
            if len(chunk.strip()) < self.min_chunk_size and len(chunks) > 1:
                # Try to find and merge with adjacent chunk
                continue
            
            # Calculate approximate line numbers
            start_line = text[:text_offset].count('\n') + 1 if text_offset > 0 else 1
            chunk_pos = text.find(chunk, text_offset)
            if chunk_pos >= 0:
                text_offset = chunk_pos + len(chunk)
            end_line = start_line + chunk.count('\n')
            
            # Extract semantic metadata
            metadata = self._extract_metadata(chunk, language, doc.metadata)
            metadata.update({
                'chunk_index': i,
                'total_chunks': len(chunks),
                'start_line': start_line,
                'end_line': end_line,
            })
            
            chunk_id = self._make_deterministic_id(
                metadata.get('namespace', ''),
                metadata.get('path', ''),
                i
            )
            node = TextNode(
                id_=chunk_id,
                text=chunk,
                metadata=metadata
            )
            nodes.append(node)
        
        return nodes
    
    def _extract_metadata(
        self,
        chunk: str,
        language: str,
        base_metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Extract semantic metadata from a code chunk"""
        metadata = dict(base_metadata)
        
        # Determine chunk type and extract names
        chunk_type = ChunkType.MIXED
        names = []
        imports = []
        
        patterns = METADATA_PATTERNS.get(language.lower(), {})
        
        # Check for classes
        if 'class' in patterns:
            matches = patterns['class'].findall(chunk)
            if matches:
                chunk_type = ChunkType.CLASS
                names.extend(matches)
        
        # Check for interfaces
        if 'interface' in patterns:
            matches = patterns['interface'].findall(chunk)
            if matches:
                chunk_type = ChunkType.INTERFACE
                names.extend(matches)
        
        # Check for functions/methods
        if chunk_type == ChunkType.MIXED:
            for key in ['function', 'method', 'arrow']:
                if key in patterns:
                    matches = patterns[key].findall(chunk)
                    if matches:
                        chunk_type = ChunkType.FUNCTION
                        names.extend(matches)
                        break
        
        # Check for imports
        if 'import' in patterns:
            import_matches = patterns['import'].findall(chunk)
            if import_matches:
                imports = import_matches[:10]  # Limit
                if not names:  # Pure import block
                    chunk_type = ChunkType.IMPORTS
        
        # Check for documentation files
        if language in ('markdown', 'rst', 'text'):
            chunk_type = ChunkType.DOCUMENTATION
        
        # Check for config files
        if language in ('json', 'yaml', 'yml', 'toml', 'xml', 'ini'):
            chunk_type = ChunkType.CONFIG
        
        # Extract docstring if present
        docstring = self._extract_docstring(chunk, language)
        
        # Extract function signature
        signature = self._extract_signature(chunk, language)
        
        # Update metadata
        metadata['chunk_type'] = chunk_type.value
        
        if names:
            metadata['semantic_names'] = names[:5]  # Limit to 5 names
            metadata['primary_name'] = names[0]
        
        if imports:
            metadata['imports'] = imports
        
        if docstring:
            metadata['docstring'] = docstring[:500]  # Limit size
        
        if signature:
            metadata['signature'] = signature
        
        return metadata
    
    def _extract_docstring(self, chunk: str, language: str) -> Optional[str]:
        """Extract docstring from code chunk"""
        if language == 'python':
            # Python docstrings
            match = re.search(r'"""([\s\S]*?)"""|\'\'\'([\s\S]*?)\'\'\'', chunk)
            if match:
                return (match.group(1) or match.group(2)).strip()
        
        elif language in ('javascript', 'typescript', 'java', 'csharp', 'php', 'go'):
            # JSDoc / JavaDoc style
            match = re.search(r'/\*\*([\s\S]*?)\*/', chunk)
            if match:
                # Clean up the comment
                doc = match.group(1)
                doc = re.sub(r'^\s*\*\s?', '', doc, flags=re.MULTILINE)
                return doc.strip()
        
        return None
    
    def _extract_signature(self, chunk: str, language: str) -> Optional[str]:
        """Extract function/method signature from code chunk"""
        lines = chunk.split('\n')
        
        for line in lines[:10]:  # Check first 10 lines
            line = line.strip()
            
            if language == 'python':
                if line.startswith(('def ', 'async def ')):
                    # Get full signature including multi-line params
                    sig = line
                    if ')' not in sig:
                        # Multi-line signature
                        idx = lines.index(line.strip()) if line.strip() in lines else -1
                        if idx >= 0:
                            for next_line in lines[idx+1:idx+5]:
                                sig += ' ' + next_line.strip()
                                if ')' in next_line:
                                    break
                    return sig.split(':')[0] + ':'
            
            elif language in ('java', 'csharp', 'kotlin'):
                if any(kw in line for kw in ['public ', 'private ', 'protected ', 'internal ']):
                    if '(' in line and not line.startswith('//'):
                        return line.split('{')[0].strip()
            
            elif language in ('javascript', 'typescript'):
                if line.startswith(('function ', 'async function ')):
                    return line.split('{')[0].strip()
                if '=>' in line and '(' in line:
                    return line.split('=>')[0].strip() + ' =>'
            
            elif language == 'go':
                if line.startswith('func '):
                    return line.split('{')[0].strip()
            
            elif language == 'rust':
                if line.startswith(('fn ', 'pub fn ', 'async fn ', 'pub async fn ')):
                    return line.split('{')[0].strip()
        
        return None
    
    def _fallback_split(self, doc: Document) -> List[TextNode]:
        """Fallback splitting for problematic documents"""
        text = doc.text
        
        if not text or not text.strip():
            return []
        
        # Use default splitter
        chunks = self._default_splitter.split_text(text)
        
        nodes = []
        for i, chunk in enumerate(chunks):
            if not chunk or not chunk.strip():
                continue
            
            # Truncate if too large
            if len(chunk) > 30000:
                chunk = chunk[:30000]
            
            metadata = dict(doc.metadata)
            metadata['chunk_index'] = i
            metadata['total_chunks'] = len(chunks)
            metadata['chunk_type'] = 'fallback'
            
            chunk_id = self._make_deterministic_id(
                metadata.get('namespace', ''),
                metadata.get('path', ''),
                i
            )
            nodes.append(TextNode(
                id_=chunk_id,
                text=chunk,
                metadata=metadata
            ))
        
        return nodes
    
    @staticmethod
    def get_supported_languages() -> List[str]:
        """Return list of supported languages"""
        return list(LANGUAGE_MAP.keys())
    
    @staticmethod
    def get_separators_for_language(language: str) -> Optional[List[str]]:
        """Get the separators used for a specific language"""
        lang_enum = LANGUAGE_MAP.get(language.lower())
        if lang_enum:
            return RecursiveCharacterTextSplitter.get_separators_for_language(lang_enum)
        return None
