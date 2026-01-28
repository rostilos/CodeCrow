"""
Metadata extraction from AST chunks.

Extracts semantic metadata like docstrings, signatures, inheritance info
from parsed code chunks for improved RAG retrieval.
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
    
    def extract_docstring(self, content: str, language: str) -> Optional[str]:
        """Extract docstring from code chunk."""
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
    
    def extract_signature(self, content: str, language: str) -> Optional[str]:
        """Extract function/method signature from code chunk."""
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
        
        return unique_names[:10]  # Limit to 10 names
    
    def _get_name_patterns(self, language: str) -> List[re.Pattern]:
        """Get regex patterns for extracting names by language."""
        patterns = {
            'python': [
                re.compile(r'^class\s+(\w+)', re.MULTILINE),
                re.compile(r'^(?:async\s+)?def\s+(\w+)\s*\(', re.MULTILINE),
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
                re.compile(r'^func\s+(?:\([^)]+\)\s+)?(\w+)\s*\(', re.MULTILINE),
                re.compile(r'^type\s+(\w+)\s+(?:struct|interface)\s*\{', re.MULTILINE),
            ],
            'rust': [
                re.compile(r'^(?:pub\s+)?(?:async\s+)?fn\s+(\w+)', re.MULTILINE),
                re.compile(r'^(?:pub\s+)?struct\s+(\w+)', re.MULTILINE),
                re.compile(r'^(?:pub\s+)?trait\s+(\w+)', re.MULTILINE),
                re.compile(r'^(?:pub\s+)?enum\s+(\w+)', re.MULTILINE),
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
        result['imports'] = result['imports'][:20]
        
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
            metadata['docstring'] = chunk_metadata.docstring[:500]
        
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
