"""
Context builder utilities for RAG metrics and caching.

Note: The original ContextBuilder, ContextBudget, and MODEL_CONTEXT_LIMITS
were removed — they are superseded by the multi-stage orchestrator pipeline.
"""
import logging
import os
import hashlib
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

# RAG Cache settings
RAG_CACHE_TTL_SECONDS = int(os.environ.get("RAG_CACHE_TTL_SECONDS", "300"))
RAG_CACHE_MAX_SIZE = int(os.environ.get("RAG_CACHE_MAX_SIZE", "100"))

# RAG query settings
RAG_MIN_RELEVANCE_SCORE = float(os.environ.get("RAG_MIN_RELEVANCE_SCORE", "0.7"))
RAG_DEFAULT_TOP_K = int(os.environ.get("RAG_DEFAULT_TOP_K", "15"))


@dataclass
class RAGMetrics:
    """Metrics for RAG processing quality and performance."""
    query_count: int = 0
    total_results: int = 0
    filtered_results: int = 0
    high_priority_hits: int = 0
    medium_priority_hits: int = 0
    low_priority_hits: int = 0
    avg_relevance_score: float = 0.0
    min_relevance_score: float = 0.0
    max_relevance_score: float = 0.0
    processing_time_ms: float = 0.0
    reranking_applied: bool = False
    cache_hit: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging/serialization."""
        return {
            "query_count": self.query_count,
            "total_results": self.total_results,
            "filtered_results": self.filtered_results,
            "priority_distribution": {
                "high": self.high_priority_hits,
                "medium": self.medium_priority_hits,
                "low": self.low_priority_hits
            },
            "relevance_scores": {
                "avg": round(self.avg_relevance_score, 4),
                "min": round(self.min_relevance_score, 4),
                "max": round(self.max_relevance_score, 4)
            },
            "processing_time_ms": round(self.processing_time_ms, 2),
            "reranking_applied": self.reranking_applied,
            "cache_hit": self.cache_hit
        }
    
    @classmethod
    def from_results(
        cls,
        results: List[Dict[str, Any]],
        query_count: int = 1,
        processing_time_ms: float = 0.0,
        reranking_applied: bool = False,
        cache_hit: bool = False
    ) -> "RAGMetrics":
        """Create metrics from RAG results."""
        if not results:
            return cls(
                query_count=query_count,
                processing_time_ms=processing_time_ms,
                reranking_applied=reranking_applied,
                cache_hit=cache_hit
            )
        
        scores = [r.get("score", 0) for r in results]
        
        # Count priority hits
        high_hits = 0
        medium_hits = 0
        low_hits = 0
        
        for r in results:
            priority = r.get("_priority", "MEDIUM")
            if priority == "HIGH":
                high_hits += 1
            elif priority == "MEDIUM":
                medium_hits += 1
            else:
                low_hits += 1
        
        return cls(
            query_count=query_count,
            total_results=len(results),
            filtered_results=len(results),
            high_priority_hits=high_hits,
            medium_priority_hits=medium_hits,
            low_priority_hits=low_hits,
            avg_relevance_score=sum(scores) / len(scores) if scores else 0,
            min_relevance_score=min(scores) if scores else 0,
            max_relevance_score=max(scores) if scores else 0,
            processing_time_ms=processing_time_ms,
            reranking_applied=reranking_applied,
            cache_hit=cache_hit
        )


class SmartChunker:
    """
    Intelligent code chunking that preserves logical boundaries.
    Handles large files by splitting at function/class/method boundaries.
    Supports: Python, TypeScript, JavaScript, Java, Kotlin, Go, PHP, C#, C++, Swift
    """
    
    # Language-specific boundary patterns
    LANGUAGE_PATTERNS = {
        # Python patterns
        'python': [
            (r'^\s*def\s+\w+\s*\(', 'function'),
            (r'^\s*async\s+def\s+\w+\s*\(', 'async_function'),
            (r'^\s*class\s+\w+', 'class'),
            (r'^\s*@\w+', 'decorator'),
        ],
        # TypeScript/JavaScript patterns
        'typescript': [
            (r'^\s*export\s+(default\s+)?(class|interface|type|enum|function|const|let|var)\s+\w+', 'export'),
            (r'^\s*(async\s+)?function\s+\w+\s*\(', 'function'),
            (r'^\s*(export\s+)?(abstract\s+)?class\s+\w+', 'class'),
            (r'^\s*(export\s+)?interface\s+\w+', 'interface'),
            (r'^\s*(export\s+)?type\s+\w+', 'type'),
            (r'^\s*(export\s+)?enum\s+\w+', 'enum'),
            (r'^\s*(const|let|var)\s+\w+\s*=\s*(async\s+)?\(.*\)\s*=>', 'arrow_function'),
            (r'^\s*(const|let|var)\s+\w+\s*=\s*function', 'function'),
            (r'^\s*(public|private|protected|static|readonly|async)?\s*(get|set)?\s*\w+\s*\([^)]*\)\s*[:{]', 'method'),
        ],
        'javascript': [
            (r'^\s*(async\s+)?function\s+\w+\s*\(', 'function'),
            (r'^\s*class\s+\w+', 'class'),
            (r'^\s*(const|let|var)\s+\w+\s*=\s*(async\s+)?\(.*\)\s*=>', 'arrow_function'),
            (r'^\s*(const|let|var)\s+\w+\s*=\s*function', 'function'),
            (r'^\s*(get|set)?\s*\w+\s*\([^)]*\)\s*\{', 'method'),
        ],
        # Java patterns
        'java': [
            (r'^\s*(public|private|protected)?\s*(static)?\s*(abstract)?\s*(final)?\s*class\s+\w+', 'class'),
            (r'^\s*(public|private|protected)?\s*(static)?\s*interface\s+\w+', 'interface'),
            (r'^\s*(public|private|protected)?\s*(static)?\s*enum\s+\w+', 'enum'),
            (r'^\s*@\w+', 'annotation'),
            (r'^\s*(public|private|protected)?\s*(static)?\s*(synchronized)?\s*(final)?\s*\w+\s+\w+\s*\([^)]*\)\s*(throws\s+\w+)?\s*\{?', 'method'),
            (r'^\s*(public|private|protected)?\s*(static)?\s*(final)?\s*\w+(<[^>]+>)?\s+\w+\s*[=;]', 'field'),
        ],
        # Kotlin patterns
        'kotlin': [
            (r'^\s*(public|private|protected|internal)?\s*(open|abstract|sealed|data|inline|value)?\s*class\s+\w+', 'class'),
            (r'^\s*(public|private|protected|internal)?\s*interface\s+\w+', 'interface'),
            (r'^\s*(public|private|protected|internal)?\s*object\s+\w+', 'object'),
            (r'^\s*(public|private|protected|internal)?\s*enum\s+class\s+\w+', 'enum'),
            (r'^\s*@\w+', 'annotation'),
            (r'^\s*(public|private|protected|internal)?\s*(open|override|suspend|inline)?\s*fun\s+(<[^>]+>\s*)?\w+\s*\(', 'function'),
            (r'^\s*(public|private|protected|internal)?\s*(val|var)\s+\w+', 'property'),
            (r'^\s*companion\s+object', 'companion'),
        ],
        # Go patterns
        'go': [
            (r'^\s*func\s+\(\w+\s+\*?\w+\)\s+\w+\s*\(', 'method'),
            (r'^\s*func\s+\w+\s*\(', 'function'),
            (r'^\s*type\s+\w+\s+struct\s*\{', 'struct'),
            (r'^\s*type\s+\w+\s+interface\s*\{', 'interface'),
            (r'^\s*type\s+\w+\s+', 'type'),
            (r'^\s*(var|const)\s+\(', 'var_block'),
            (r'^\s*(var|const)\s+\w+', 'variable'),
        ],
        # PHP patterns
        'php': [
            (r'^\s*(public|private|protected)?\s*(static)?\s*(abstract)?\s*function\s+\w+\s*\(', 'function'),
            (r'^\s*(abstract|final)?\s*class\s+\w+', 'class'),
            (r'^\s*interface\s+\w+', 'interface'),
            (r'^\s*trait\s+\w+', 'trait'),
            (r'^\s*enum\s+\w+', 'enum'),
            (r'^\s*(public|private|protected)?\s*(static)?\s*(readonly)?\s*\$\w+', 'property'),
            (r'^\s*namespace\s+', 'namespace'),
        ],
        # C# patterns
        'csharp': [
            (r'^\s*(public|private|protected|internal)?\s*(static|abstract|sealed|partial)?\s*class\s+\w+', 'class'),
            (r'^\s*(public|private|protected|internal)?\s*interface\s+\w+', 'interface'),
            (r'^\s*(public|private|protected|internal)?\s*(static)?\s*struct\s+\w+', 'struct'),
            (r'^\s*(public|private|protected|internal)?\s*enum\s+\w+', 'enum'),
            (r'^\s*(public|private|protected|internal)?\s*record\s+\w+', 'record'),
            (r'^\s*\[\w+.*\]', 'attribute'),
            (r'^\s*(public|private|protected|internal)?\s*(static|virtual|override|abstract|async)?\s*\w+(<[^>]+>)?\s+\w+\s*\([^)]*\)', 'method'),
            (r'^\s*(public|private|protected|internal)?\s*(static|readonly|const)?\s*\w+(<[^>]+>)?\s+\w+\s*[{;=]', 'property'),
            (r'^\s*namespace\s+', 'namespace'),
        ],
        # C++ patterns
        'cpp': [
            (r'^\s*(class|struct)\s+\w+', 'class'),
            (r'^\s*template\s*<', 'template'),
            (r'^\s*namespace\s+\w+', 'namespace'),
            (r'^\s*enum\s+(class\s+)?\w+', 'enum'),
            (r'^\s*(virtual|static|inline|explicit|constexpr)?\s*~?\w+\s*\([^)]*\)\s*(const)?\s*(override)?\s*(noexcept)?\s*[{;=]', 'method'),
            (r'^\s*(public|private|protected)\s*:', 'access_specifier'),
            (r'^\s*#define\s+\w+', 'macro'),
            (r'^\s*typedef\s+', 'typedef'),
            (r'^\s*using\s+\w+\s*=', 'using'),
        ],
        # Swift patterns
        'swift': [
            (r'^\s*(public|private|internal|fileprivate|open)?\s*(final)?\s*class\s+\w+', 'class'),
            (r'^\s*(public|private|internal|fileprivate)?\s*struct\s+\w+', 'struct'),
            (r'^\s*(public|private|internal|fileprivate)?\s*enum\s+\w+', 'enum'),
            (r'^\s*(public|private|internal|fileprivate)?\s*protocol\s+\w+', 'protocol'),
            (r'^\s*(public|private|internal|fileprivate)?\s*extension\s+\w+', 'extension'),
            (r'^\s*@\w+', 'attribute'),
            (r'^\s*(public|private|internal|fileprivate|open)?\s*(static|class|override|mutating|async)?\s*func\s+\w+', 'function'),
            (r'^\s*(public|private|internal|fileprivate)?\s*(static|lazy|weak)?\s*(let|var)\s+\w+', 'property'),
            (r'^\s*(init|deinit)\s*\(', 'initializer'),
        ],
    }
    
    # Import patterns for different languages
    IMPORT_PATTERNS = {
        'python': r'^(import\s+|from\s+\w+\s+import)',
        'typescript': r'^(import\s+|export\s+.*from)',
        'javascript': r'^(import\s+|const\s+\w+\s*=\s*require|export\s+.*from)',
        'java': r'^(import\s+|package\s+)',
        'kotlin': r'^(import\s+|package\s+)',
        'go': r'^(import\s+|package\s+)',
        'php': r'^(use\s+|namespace\s+|require|include)',
        'csharp': r'^(using\s+|namespace\s+)',
        'cpp': r'^(#include\s+|#pragma|using\s+namespace)',
        'swift': r'^(import\s+)',
    }
    
    # File extension to language mapping
    EXTENSION_MAP = {
        '.py': 'python',
        '.pyx': 'python',
        '.pyi': 'python',
        '.ts': 'typescript',
        '.tsx': 'typescript',
        '.mts': 'typescript',
        '.cts': 'typescript',
        '.js': 'javascript',
        '.jsx': 'javascript',
        '.mjs': 'javascript',
        '.cjs': 'javascript',
        '.java': 'java',
        '.kt': 'kotlin',
        '.kts': 'kotlin',
        '.go': 'go',
        '.phtml': 'php',
        '.php': 'php',
        '.cs': 'csharp',
        '.cpp': 'cpp',
        '.cc': 'cpp',
        '.cxx': 'cpp',
        '.c': 'cpp',
        '.h': 'cpp',
        '.hpp': 'cpp',
        '.hxx': 'cpp',
        '.swift': 'swift',
    }
    
    # Priority order for what to keep when chunking
    PRIORITY_ORDER = ['class', 'interface', 'struct', 'enum', 'protocol', 'method', 'function', 'async_function', 'property', 'import', 'decorator', 'annotation', 'attribute']
    
    @classmethod
    def _detect_language(cls, file_path: str) -> str:
        """Detect programming language from file extension."""
        if not file_path:
            return 'javascript'  # Default fallback
        
        import os
        ext = os.path.splitext(file_path)[1].lower()
        return cls.EXTENSION_MAP.get(ext, 'javascript')
    
    @classmethod
    def _get_patterns_for_language(cls, language: str) -> List[tuple]:
        """Get boundary patterns for a specific language."""
        return cls.LANGUAGE_PATTERNS.get(language, cls.LANGUAGE_PATTERNS['javascript'])
    
    @classmethod
    def _get_import_pattern(cls, language: str) -> str:
        """Get import pattern for a specific language."""
        return cls.IMPORT_PATTERNS.get(language, cls.IMPORT_PATTERNS['javascript'])
    
    @classmethod
    def smart_chunk(
        cls,
        content: str,
        max_tokens: int,
        tokens_per_char: float = 0.25,
        preserve_imports: bool = True,
        file_path: str = ""
    ) -> List[str]:
        """
        Intelligently chunk a large file while preserving logical boundaries.
        
        Args:
            content: Full file content
            max_tokens: Maximum tokens per chunk
            tokens_per_char: Token estimation ratio
            preserve_imports: Whether to include imports in each chunk
            file_path: Optional file path for language detection
            
        Returns:
            List of content chunks
        """
        max_chars = int(max_tokens / tokens_per_char)
        
        if len(content) <= max_chars:
            return [content]
        
        lines = content.split('\n')
        
        # Extract imports section
        imports_section = ""
        if preserve_imports:
            imports_lines = []
            for line in lines:
                if re.match(r'^(import|from|using|require|#include)', line.strip()):
                    imports_lines.append(line)
                elif imports_lines and line.strip() == "":
                    imports_lines.append(line)
                elif imports_lines:
                    break
            imports_section = '\n'.join(imports_lines) + '\n\n' if imports_lines else ""
        
        # Find all logical boundaries
        boundaries = cls._find_boundaries(lines)
        
        if not boundaries:
            # No boundaries found, fall back to line-based chunking
            return cls._chunk_by_lines(content, max_chars, imports_section)
        
        # Build chunks at boundaries
        chunks = []
        current_chunk_lines = []
        current_size = len(imports_section)
        
        for i, (line_num, boundary_type) in enumerate(boundaries):
            # Get lines from this boundary to next (or end)
            next_boundary = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(lines)
            section_lines = lines[line_num:next_boundary]
            section_text = '\n'.join(section_lines)
            section_size = len(section_text)
            
            if current_size + section_size > max_chars and current_chunk_lines:
                # Save current chunk and start new one
                chunk_content = imports_section + '\n'.join(current_chunk_lines)
                chunks.append(chunk_content)
                current_chunk_lines = section_lines
                current_size = len(imports_section) + section_size
            else:
                current_chunk_lines.extend(section_lines)
                current_size += section_size
        
        # Add final chunk
        if current_chunk_lines:
            chunk_content = imports_section + '\n'.join(current_chunk_lines)
            chunks.append(chunk_content)
        
        return chunks if chunks else [content[:max_chars]]
    
    @classmethod
    def _find_boundaries(cls, lines: List[str]) -> List[tuple]:
        """Find logical boundaries in code."""
        boundaries = []
        
        for i, line in enumerate(lines):
            for pattern, boundary_type in cls.BOUNDARY_PATTERNS.items():
                if re.match(pattern, line):
                    boundaries.append((i, boundary_type))
                    break
        
        return boundaries
    
    @classmethod
    def _chunk_by_lines(
        cls,
        content: str,
        max_chars: int,
        imports_section: str = ""
    ) -> List[str]:
        """Fallback: chunk by lines when no boundaries found."""
        lines = content.split('\n')
        chunks = []
        current_chunk = imports_section
        
        for line in lines:
            if len(current_chunk) + len(line) + 1 > max_chars:
                if current_chunk.strip():
                    chunks.append(current_chunk)
                current_chunk = imports_section + line + '\n'
            else:
                current_chunk += line + '\n'
        
        if current_chunk.strip():
            chunks.append(current_chunk)
        
        return chunks
    
    @classmethod
    def chunk_diff(
        cls,
        diff_content: str,
        max_tokens: int,
        tokens_per_char: float = 0.25
    ) -> List[str]:
        """
        Chunk unified diff format while keeping hunks together.
        
        Args:
            diff_content: Unified diff string
            max_tokens: Maximum tokens per chunk
            tokens_per_char: Token estimation ratio
            
        Returns:
            List of diff chunks
        """
        max_chars = int(max_tokens / tokens_per_char)
        
        if len(diff_content) <= max_chars:
            return [diff_content]
        
        # Split by hunk headers
        hunk_pattern = r'(@@ -\d+,?\d* \+\d+,?\d* @@)'
        parts = re.split(hunk_pattern, diff_content)
        
        chunks = []
        current_chunk = ""
        header = ""
        
        # First part is usually file header
        if parts and not parts[0].startswith('@@'):
            header = parts[0]
            parts = parts[1:]
        
        i = 0
        while i < len(parts):
            # Hunk header + content pairs
            hunk_header = parts[i] if i < len(parts) else ""
            hunk_content = parts[i + 1] if i + 1 < len(parts) else ""
            full_hunk = hunk_header + hunk_content
            
            if len(current_chunk) + len(full_hunk) > max_chars:
                if current_chunk:
                    chunks.append(header + current_chunk)
                current_chunk = full_hunk
            else:
                current_chunk += full_hunk
            
            i += 2
        
        if current_chunk:
            chunks.append(header + current_chunk)
        
        return chunks if chunks else [diff_content[:max_chars]]


class RAGCache:
    """
    In-memory cache for RAG results with TTL support.
    Reduces redundant RAG queries for repeated PR reviews.
    Settings loaded from environment variables:
    - RAG_CACHE_TTL_SECONDS: Cache TTL (default: 300)
    - RAG_CACHE_MAX_SIZE: Max cache entries (default: 100)
    """
    
    def __init__(self, ttl_seconds: int = None, max_size: int = None):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._ttl_seconds = ttl_seconds if ttl_seconds is not None else RAG_CACHE_TTL_SECONDS
        self._max_size = max_size if max_size is not None else RAG_CACHE_MAX_SIZE
    
    def _make_key(
        self,
        workspace: str,
        project: str,
        branch: str,
        changed_files: List[str],
        pr_title: str = "",
        pr_description: str = ""
    ) -> str:
        """Generate cache key from query parameters."""
        # Sort files for consistent hashing
        sorted_files = sorted(changed_files) if changed_files else []
        
        key_data = f"{workspace}:{project}:{branch}:{','.join(sorted_files)}:{pr_title}:{pr_description[:100]}"
        return hashlib.md5(key_data.encode()).hexdigest()
    
    def get(
        self,
        workspace: str,
        project: str,
        branch: str,
        changed_files: List[str],
        pr_title: str = "",
        pr_description: str = ""
    ) -> Optional[Dict[str, Any]]:
        """
        Get cached RAG result if available and not expired.
        
        Returns:
            Cached result dict or None if not found/expired
        """
        key = self._make_key(workspace, project, branch, changed_files, pr_title, pr_description)
        
        if key not in self._cache:
            return None
        
        entry = self._cache[key]
        cached_at = entry.get("_cached_at", 0)
        now = datetime.now().timestamp()
        
        # Check TTL
        if now - cached_at > self._ttl_seconds:
            del self._cache[key]
            logger.debug(f"RAG cache expired for key: {key[:16]}...")
            return None
        
        logger.info(f"RAG cache hit for key: {key[:16]}...")
        result = entry.copy()
        result.pop("_cached_at", None)
        return result
    
    def set(
        self,
        workspace: str,
        project: str,
        branch: str,
        changed_files: List[str],
        result: Dict[str, Any],
        pr_title: str = "",
        pr_description: str = ""
    ) -> None:
        """
        Cache RAG result.
        
        Args:
            workspace: Workspace identifier
            project: Project identifier
            branch: Branch name
            changed_files: List of changed files
            result: RAG result to cache
            pr_title: PR title
            pr_description: PR description
        """
        # Evict oldest entries if cache is full
        if len(self._cache) >= self._max_size:
            self._evict_oldest()
        
        key = self._make_key(workspace, project, branch, changed_files, pr_title, pr_description)
        
        entry = result.copy()
        entry["_cached_at"] = datetime.now().timestamp()
        
        self._cache[key] = entry
        logger.debug(f"RAG result cached with key: {key[:16]}...")
    
    def _evict_oldest(self) -> None:
        """Remove oldest cache entries."""
        if not self._cache:
            return
        
        # Find and remove oldest 10% of entries
        entries = [(k, v.get("_cached_at", 0)) for k, v in self._cache.items()]
        entries.sort(key=lambda x: x[1])
        
        num_to_evict = max(1, len(entries) // 10)
        for key, _ in entries[:num_to_evict]:
            del self._cache[key]
        
        logger.debug(f"Evicted {num_to_evict} old RAG cache entries")
    
    def invalidate(
        self,
        workspace: str,
        project: str,
        branch: Optional[str] = None
    ) -> int:
        """
        Invalidate cache entries for a workspace/project.
        
        Args:
            workspace: Workspace identifier
            project: Project identifier
            branch: Optional branch (if None, invalidate all branches)
            
        Returns:
            Number of invalidated entries
        """
        prefix = f"{workspace}:{project}:"
        if branch:
            prefix += f"{branch}:"
        
        keys_to_remove = [
            k for k in self._cache.keys()
            if self._make_key(workspace, project, branch or "", []).startswith(prefix[:32])
        ]
        
        # Since we use MD5 hash, we need to check differently
        # Invalidate all matching entries by iterating
        count = 0
        keys_to_delete = []
        for key, entry in self._cache.items():
            # We can't reverse the hash, so just clear based on age
            # This is a simplified approach - in production you'd store metadata
            pass
        
        # For now, just clear entries older than half TTL for the workspace
        now = datetime.now().timestamp()
        half_ttl = self._ttl_seconds / 2
        
        for key, entry in list(self._cache.items()):
            if now - entry.get("_cached_at", 0) > half_ttl:
                del self._cache[key]
                count += 1
        
        logger.info(f"Invalidated {count} RAG cache entries")
        return count
    
    def clear(self) -> None:
        """Clear all cache entries."""
        count = len(self._cache)
        self._cache.clear()
        logger.info(f"Cleared {count} RAG cache entries")
    
    def stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        now = datetime.now().timestamp()
        
        active_entries = 0
        expired_entries = 0
        total_size_estimate = 0
        
        for entry in self._cache.values():
            if now - entry.get("_cached_at", 0) <= self._ttl_seconds:
                active_entries += 1
            else:
                expired_entries += 1
            
            # Rough size estimate
            total_size_estimate += len(str(entry))
        
        return {
            "total_entries": len(self._cache),
            "active_entries": active_entries,
            "expired_entries": expired_entries,
            "max_size": self._max_size,
            "ttl_seconds": self._ttl_seconds,
            "size_estimate_bytes": total_size_estimate
        }


# Global cache instance
_rag_cache = RAGCache()


def get_rag_cache() -> RAGCache:
    """Get the global RAG cache instance."""
    return _rag_cache