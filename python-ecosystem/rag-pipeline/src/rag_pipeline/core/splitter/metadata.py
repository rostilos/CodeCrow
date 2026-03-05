"""
Metadata extraction from AST chunks.

Extracts semantic metadata like docstrings, signatures, inheritance info
from parsed code chunks for improved RAG retrieval.

Extraction strategy:
- For languages with .scm query files (python, java, javascript, typescript,
  c_sharp, go, rust, php): prefer tree-sitter AST node traversal for
  docstring and signature extraction. Falls back to regex if no ts_node.
- For all other languages: use language-specific regex patterns.
"""

import re
import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class ContentType(Enum):
    """Content type as determined by AST parsing."""
    FUNCTIONS_CLASSES = "functions_classes"
    SIMPLIFIED_CODE = "simplified_code"
    FALLBACK = "fallback"
    OVERSIZED_SPLIT = "oversized_split"


@dataclass
class ChunkMetadata:
    """Structured metadata for a code chunk."""
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
    # Class-level metadata
    extends: List[str] = field(default_factory=list)
    implements: List[str] = field(default_factory=list)
    # File-level metadata
    imports: List[str] = field(default_factory=list)
    namespace: Optional[str] = None


class MetadataExtractor:
    """
    Extract semantic metadata from code chunks.
    
    Uses both AST-derived information and regex fallbacks for
    comprehensive metadata extraction.
    """
    
    # Comment prefixes by language
    COMMENT_PREFIX: Dict[str, str] = {
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
    }
    
    def extract_docstring(self, content: str, language: str, ts_node: Any = None) -> Optional[str]:
        """
        Extract docstring from code chunk.
        
        If a tree-sitter node is provided AND the language has an .scm query,
        traverses the AST for docstring nodes (more reliable than regex for
        edge cases like multi-line strings, nested quotes, etc.).
        Falls back to regex when no ts_node is available.
        """
        # Try tree-sitter extraction first
        if ts_node is not None:
            result = self._extract_docstring_from_node(ts_node, language)
            if result:
                return result
        
        # Regex fallback
        return self._extract_docstring_regex(content, language)
    
    def _extract_docstring_regex(self, content: str, language: str) -> Optional[str]:
        if language == 'python':
            match = re.search(r'"""([\s\S]*?)"""|\'\'\'([\s\S]*?)\'\'\'', content)
            if match:
                return (match.group(1) or match.group(2)).strip()
        
        elif language in ('javascript', 'typescript', 'java', 'kotlin', 
                          'c_sharp', 'php', 'go', 'scala', 'c', 'cpp'):
            # JSDoc / JavaDoc style
            match = re.search(r'/\*\*([\s\S]*?)\*/', content)
            if match:
                doc = match.group(1)
                doc = re.sub(r'^\s*\*\s?', '', doc, flags=re.MULTILINE)
                return doc.strip()
        
        elif language == 'rust':
            # Rust doc comments
            lines = []
            for line in content.split('\n'):
                stripped = line.strip()
                if stripped.startswith('///'):
                    lines.append(stripped[3:].strip())
                elif stripped.startswith('//!'):
                    lines.append(stripped[3:].strip())
                elif lines:
                    break
            if lines:
                return '\n'.join(lines)
        
        return None
    
    def extract_signature(self, content: str, language: str, ts_node: Any = None) -> Optional[str]:
        """
        Extract function/method signature from code chunk.
        
        If a tree-sitter node is provided, extracts the signature by finding
        the first function/method declaration node and reading up to the body.
        Falls back to regex.
        """
        # Try tree-sitter extraction first
        if ts_node is not None:
            result = self._extract_signature_from_node(ts_node, language, content)
            if result:
                return result
        
        # Regex fallback
        return self._extract_signature_regex(content, language)
    
    def _extract_signature_regex(self, content: str, language: str) -> Optional[str]:
        """Extract function/method signature from code chunk using regex."""
        lines = content.split('\n')
        
        for line in lines[:15]:
            line = line.strip()
            
            if language == 'python':
                if line.startswith(('def ', 'async def ', 'class ')):
                    sig = line
                    if line.startswith('class ') and ':' in line:
                        return line.split(':')[0] + ':'
                    if ')' not in sig and ':' not in sig:
                        idx = next((i for i, l in enumerate(lines) if l.strip() == line), -1)
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
                if line.startswith(('fn ', 'pub fn ', 'async fn ', 'pub async fn ', 
                                    'impl ', 'struct ', 'trait ', 'enum ')):
                    return line.split('{')[0].strip()
            
            elif language == 'php':
                if 'function ' in line and '(' in line:
                    return line.split('{')[0].strip()
                if line.startswith('class ') or line.startswith('interface '):
                    return line.split('{')[0].strip()
        
        return None
    
    def extract_names_from_content(self, content: str, language: str) -> List[str]:
        """Extract semantic names (function/class names) using regex patterns."""
        patterns = self._get_name_patterns(language)
        names = []
        
        for pattern in patterns:
            matches = pattern.findall(content)
            names.extend(matches)
        
        # Deduplicate while preserving order
        seen = set()
        unique_names = []
        for name in names:
            if name not in seen:
                seen.add(name)
                unique_names.append(name)
        
        return unique_names[:30]  # Limit to 30 names
    
    def _get_name_patterns(self, language: str) -> List[re.Pattern]:
        """Get regex patterns for extracting names by language.
        
        Patterns allow optional leading whitespace (^\s*) to match indented code
        like methods inside classes.
        """
        patterns = {
            'python': [
                re.compile(r'^\s*class\s+(\w+)', re.MULTILINE),
                re.compile(r'^\s*(?:async\s+)?def\s+(\w+)\s*\(', re.MULTILINE),
            ],
            'java': [
                re.compile(r'(?:public\s+|private\s+|protected\s+)?(?:abstract\s+|final\s+)?class\s+(\w+)', re.MULTILINE),
                re.compile(r'(?:public\s+)?interface\s+(\w+)', re.MULTILINE),
                re.compile(r'(?:public|private|protected)\s+(?:static\s+)?[\w<>,\s]+\s+(\w+)\s*\(', re.MULTILINE),
            ],
            'javascript': [
                re.compile(r'(?:export\s+)?(?:default\s+)?class\s+(\w+)', re.MULTILINE),
                re.compile(r'(?:export\s+)?(?:async\s+)?function\s*\*?\s*(\w+)\s*\(', re.MULTILINE),
                re.compile(r'(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\([^)]*\)\s*=>', re.MULTILINE),
            ],
            'typescript': [
                re.compile(r'(?:export\s+)?(?:default\s+)?class\s+(\w+)', re.MULTILINE),
                re.compile(r'(?:export\s+)?interface\s+(\w+)', re.MULTILINE),
                re.compile(r'(?:export\s+)?(?:async\s+)?function\s*\*?\s*(\w+)\s*\(', re.MULTILINE),
                re.compile(r'(?:export\s+)?type\s+(\w+)', re.MULTILINE),
            ],
            'go': [
                re.compile(r'^\s*func\s+(?:\([^)]+\)\s+)?(\w+)\s*\(', re.MULTILINE),
                re.compile(r'^\s*type\s+(\w+)\s+(?:struct|interface)\s*\{', re.MULTILINE),
            ],
            'rust': [
                re.compile(r'^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)', re.MULTILINE),
                re.compile(r'^\s*(?:pub\s+)?struct\s+(\w+)', re.MULTILINE),
                re.compile(r'^\s*(?:pub\s+)?trait\s+(\w+)', re.MULTILINE),
                re.compile(r'^\s*(?:pub\s+)?enum\s+(\w+)', re.MULTILINE),
            ],
            'php': [
                re.compile(r'(?:abstract\s+|final\s+)?class\s+(\w+)', re.MULTILINE),
                re.compile(r'interface\s+(\w+)', re.MULTILINE),
                re.compile(r'(?:public|private|protected|static|\s)*function\s+(\w+)\s*\(', re.MULTILINE),
            ],
            'c_sharp': [
                re.compile(r'(?:public\s+|private\s+|internal\s+)?(?:abstract\s+|sealed\s+)?class\s+(\w+)', re.MULTILINE),
                re.compile(r'(?:public\s+)?interface\s+(\w+)', re.MULTILINE),
                re.compile(r'(?:public|private|protected|internal)\s+(?:static\s+)?[\w<>,\s]+\s+(\w+)\s*\(', re.MULTILINE),
            ],
        }
        return patterns.get(language, [])
    
    def extract_inheritance(self, content: str, language: str) -> Dict[str, List[str]]:
        """Extract inheritance information (extends, implements)."""
        result = {'extends': [], 'implements': [], 'imports': []}
        
        patterns = self._get_inheritance_patterns(language)
        
        if 'extends' in patterns:
            match = patterns['extends'].search(content)
            if match:
                extends = match.group(1).strip()
                result['extends'] = [e.strip() for e in extends.split(',') if e.strip()]
        
        if 'implements' in patterns:
            match = patterns['implements'].search(content)
            if match:
                implements = match.group(1).strip()
                result['implements'] = [i.strip() for i in implements.split(',') if i.strip()]
        
        for key in ('import', 'use', 'using', 'require'):
            if key in patterns:
                matches = patterns[key].findall(content)
                for m in matches:
                    if isinstance(m, tuple):
                        result['imports'].extend([x.strip() for x in m if x and x.strip()])
                    else:
                        result['imports'].append(m.strip())
        
        # Limit imports
        result['imports'] = result['imports'][:50]
        
        return result
    
    def _get_inheritance_patterns(self, language: str) -> Dict[str, re.Pattern]:
        """Get regex patterns for inheritance extraction."""
        patterns = {
            'python': {
                'extends': re.compile(r'class\s+\w+\s*\(\s*([\w.,\s]+)\s*\)\s*:', re.MULTILINE),
                'import': re.compile(r'^(?:from\s+([\w.]+)\s+)?import\s+([\w.,\s*]+)', re.MULTILINE),
            },
            'java': {
                'extends': re.compile(r'class\s+\w+\s+extends\s+([\w.]+)', re.MULTILINE),
                'implements': re.compile(r'class\s+\w+(?:\s+extends\s+[\w.]+)?\s+implements\s+([\w.,\s]+)', re.MULTILINE),
                'import': re.compile(r'^import\s+([\w.]+(?:\.\*)?);', re.MULTILINE),
            },
            'typescript': {
                'extends': re.compile(r'class\s+\w+\s+extends\s+([\w.]+)', re.MULTILINE),
                'implements': re.compile(r'class\s+\w+(?:\s+extends\s+[\w.]+)?\s+implements\s+([\w.,\s]+)', re.MULTILINE),
                'import': re.compile(r'^import\s+(?:[\w{},\s*]+\s+from\s+)?["\']([^"\']+)["\'];?', re.MULTILINE),
            },
            'javascript': {
                'extends': re.compile(r'class\s+\w+\s+extends\s+([\w.]+)', re.MULTILINE),
                'import': re.compile(r'^import\s+(?:[\w{},\s*]+\s+from\s+)?["\']([^"\']+)["\'];?', re.MULTILINE),
                'require': re.compile(r'require\s*\(\s*["\']([^"\']+)["\']\s*\)', re.MULTILINE),
            },
            'php': {
                'extends': re.compile(r'class\s+\w+\s+extends\s+([\w\\]+)', re.MULTILINE),
                'implements': re.compile(r'class\s+\w+(?:\s+extends\s+[\w\\]+)?\s+implements\s+([\w\\,\s]+)', re.MULTILINE),
                'use': re.compile(r'^use\s+([\w\\]+)(?:\s+as\s+\w+)?;', re.MULTILINE),
            },
            'c_sharp': {
                'extends': re.compile(r'class\s+\w+\s*:\s*([\w.]+)', re.MULTILINE),
                'using': re.compile(r'^using\s+([\w.]+);', re.MULTILINE),
            },
            'go': {
                'import': re.compile(r'^import\s+(?:\(\s*)?"([^"]+)"', re.MULTILINE),
            },
            'rust': {
                'use': re.compile(r'^use\s+([\w:]+(?:::\{[^}]+\})?);', re.MULTILINE),
            },
        }
        return patterns.get(language, {})
    
    def get_comment_prefix(self, language: str) -> str:
        """Get comment prefix for a language."""
        return self.COMMENT_PREFIX.get(language, '//')
    
    # ── Tree-sitter AST extraction methods ──
    # These use generic heuristics rather than per-language config, so they
    # work for ANY language with a tree-sitter grammar — no maintenance
    # needed when adding new languages.

    # Common body/block node types across tree-sitter grammars (language-agnostic)
    _BODY_NODE_TYPES = {
        'block', 'statement_block', 'compound_statement', 'class_body',
        'declaration_list', 'field_declaration_list', 'enum_body',
        'interface_body', 'match_block', 'block_node',
    }

    @staticmethod
    def _is_comment_node(node: Any) -> bool:
        """Check if a tree-sitter node is a comment.

        Works across all grammars — tree-sitter consistently names comment
        nodes with 'comment' in the type: comment, block_comment, line_comment.
        """
        return 'comment' in node.type

    @staticmethod
    def _is_string_node(node: Any) -> bool:
        """Check if a tree-sitter node is a string literal."""
        return node.type in ('string', 'string_literal', 'concatenated_string', 'string_content')

    def _is_body_node(self, node: Any) -> bool:
        """Check if a tree-sitter node is a body/block (known set + heuristic)."""
        return node.type in self._BODY_NODE_TYPES or 'body' in node.type or 'block' in node.type

    def _extract_docstring_from_node(self, ts_node: Any, language: str) -> Optional[str]:
        """
        Extract docstring by traversing tree-sitter node children.

        Strategy (generic):
        - Python: first string literal in the body (unique convention)
        - All others: preceding comment sibling (universal across grammars)
        """
        try:
            if language == 'python':
                result = self._extract_python_docstring_ast(ts_node)
                if result:
                    return result
            return self._extract_preceding_comment_docstring(ts_node)
        except Exception as e:
            logger.debug(f"AST docstring extraction failed for {language}: {e}", exc_info=True)
            return None

    def _extract_python_docstring_ast(self, node: Any) -> Optional[str]:
        """Extract Python docstring: first string in function/class body."""
        for child in node.children:
            if self._is_body_node(child):
                # First statement in the body
                for stmt in child.children:
                    if stmt.type == 'expression_statement':
                        for expr in stmt.children:
                            if self._is_string_node(expr):
                                text = expr.text.decode('utf-8', errors='replace') if isinstance(expr.text, bytes) else str(expr.text)
                                # Strip triple quotes
                                for quote in ('"""', "'''"):
                                    if text.startswith(quote) and text.endswith(quote):
                                        return text[3:-3].strip()
                                return text.strip('"\'').strip()
                        break  # Only check first statement
                break
        return None

    def _extract_preceding_comment_docstring(self, node: Any) -> Optional[str]:
        """
        Extract docstring from the preceding comment sibling.

        Works for any language — tree-sitter grammars consistently name
        comment nodes with 'comment' in the type.  For RAG, any comment
        directly preceding a definition is valuable context.
        """
        prev = node.prev_sibling if hasattr(node, 'prev_sibling') else None
        if prev is None:
            prev = getattr(node, 'prev_named_sibling', None)

        if prev is None or not self._is_comment_node(prev):
            return None

        text = prev.text.decode('utf-8', errors='replace') if isinstance(prev.text, bytes) else str(prev.text)
        return self._clean_comment_text(text)

    @staticmethod
    def _clean_comment_text(text: str) -> Optional[str]:
        """
        Clean comment text into plain docstring, regardless of style.

        Handles all common formats: /* */, /** */, //, ///, //!, #
        """
        stripped = text.strip()
        if not stripped:
            return None

        # Block comment style: /* ... */ or /** ... */
        if stripped.startswith('/*') and stripped.endswith('*/'):
            inner = stripped[2:-2]
            # Strip leading * from /** style
            if inner.startswith('*'):
                inner = inner[1:]
            lines = inner.split('\n')
            cleaned = [re.sub(r'^\s*\*\s?', '', line) for line in lines]
            result = '\n'.join(cleaned).strip()
            return result or None

        # Line comment style: collect lines, strip comment markers
        lines = stripped.split('\n')
        cleaned = []
        for line in lines:
            line = line.strip()
            # Strip common line-comment prefixes (longest match first)
            for prefix in ('///', '//!', '//', '#'):
                if line.startswith(prefix):
                    line = line[len(prefix):]
                    break
            cleaned.append(line.strip())
        result = '\n'.join(cleaned).strip()
        return result or None

    def _extract_signature_from_node(
        self, ts_node: Any, language: str, content: str
    ) -> Optional[str]:
        """
        Extract function/method signature from tree-sitter node.

        Reads from the start of the node to the start of the body block.
        Works generically: any node that has a body child is a definition —
        no per-language node-type whitelist needed.
        """
        try:
            # Find a node with a body child — try ts_node, then children
            target = self._find_node_with_body(ts_node)
            if target is None:
                # No body found — use first line as signature
                first_line = content.split('\n')[0].strip()
                return first_line if len(first_line) > 5 else None

            node, body_child = target
            body_start = body_child.start_byte - node.start_byte

            if body_start > 0:
                # Signature = everything before the body
                content_bytes = content.encode('utf-8') if isinstance(content, str) else content
                sig_bytes = content_bytes[:body_start]
                sig = sig_bytes.decode('utf-8', errors='replace').rstrip().rstrip('{').rstrip()

                # For Python, include the colon
                if language == 'python' and not sig.endswith(':'):
                    sig = sig.rstrip() + ':'

                return sig if len(sig) > 5 else None

            # Body at position 0 — use first line
            first_line = content.split('\n')[0].strip()
            return first_line if len(first_line) > 5 else None

        except Exception as e:
            logger.debug(f"AST signature extraction failed for {language}: {e}", exc_info=True)
            return None

    def _find_node_with_body(self, ts_node: Any) -> Optional[tuple]:
        """
        Find a node that has a body/block child.

        Returns (node, body_child) tuple or None.
        Checks ts_node first, then its immediate children.
        """
        # Check ts_node itself
        for child in ts_node.children:
            if self._is_body_node(child):
                return (ts_node, child)
        # Check immediate children (ts_node may be a wrapper)
        for child in ts_node.children:
            for grandchild in child.children:
                if self._is_body_node(grandchild):
                    return (child, grandchild)
        return None
    
    def build_metadata_dict(
        self,
        chunk_metadata: ChunkMetadata,
        base_metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Build final metadata dictionary from ChunkMetadata."""
        metadata = dict(base_metadata)
        
        metadata['content_type'] = chunk_metadata.content_type.value
        metadata['node_type'] = chunk_metadata.node_type
        metadata['start_line'] = chunk_metadata.start_line
        metadata['end_line'] = chunk_metadata.end_line
        
        if chunk_metadata.parent_context:
            metadata['parent_context'] = chunk_metadata.parent_context
            metadata['parent_class'] = chunk_metadata.parent_context[-1]
            full_path_parts = chunk_metadata.parent_context + chunk_metadata.semantic_names[:1]
            metadata['full_path'] = '.'.join(full_path_parts)
        
        if chunk_metadata.semantic_names:
            metadata['semantic_names'] = chunk_metadata.semantic_names
            metadata['primary_name'] = chunk_metadata.semantic_names[0]
        
        if chunk_metadata.docstring:
            metadata['docstring'] = chunk_metadata.docstring[:1000]
        
        if chunk_metadata.signature:
            metadata['signature'] = chunk_metadata.signature
        
        if chunk_metadata.extends:
            metadata['extends'] = chunk_metadata.extends
            metadata['parent_types'] = chunk_metadata.extends
        
        if chunk_metadata.implements:
            metadata['implements'] = chunk_metadata.implements
        
        if chunk_metadata.imports:
            metadata['imports'] = chunk_metadata.imports
        
        if chunk_metadata.namespace:
            metadata['namespace'] = chunk_metadata.namespace
        
        return metadata
