"""
Context builder utilities for RAG metrics, neutral chunking, and caching.

The multi-stage review pipeline now owns semantic context assembly. Utilities in
this module intentionally avoid language/extension-specific classification.
"""

import hashlib
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

RAG_CACHE_TTL_SECONDS = int(os.environ.get("RAG_CACHE_TTL_SECONDS", "300"))
RAG_CACHE_MAX_SIZE = int(os.environ.get("RAG_CACHE_MAX_SIZE", "100"))
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
                "low": self.low_priority_hits,
            },
            "relevance_scores": {
                "avg": round(self.avg_relevance_score, 4),
                "min": round(self.min_relevance_score, 4),
                "max": round(self.max_relevance_score, 4),
            },
            "processing_time_ms": round(self.processing_time_ms, 2),
            "reranking_applied": self.reranking_applied,
            "cache_hit": self.cache_hit,
        }

    @classmethod
    def from_results(
        cls,
        results: List[Dict[str, Any]],
        query_count: int = 1,
        processing_time_ms: float = 0.0,
        reranking_applied: bool = False,
        cache_hit: bool = False,
    ) -> "RAGMetrics":
        """Create metrics from RAG results without classifying result priority."""
        if not results:
            return cls(
                query_count=query_count,
                processing_time_ms=processing_time_ms,
                reranking_applied=reranking_applied,
                cache_hit=cache_hit,
            )

        scores = [r.get("score", r.get("relevance_score", 0)) for r in results]
        return cls(
            query_count=query_count,
            total_results=len(results),
            filtered_results=len(results),
            avg_relevance_score=sum(scores) / len(scores) if scores else 0,
            min_relevance_score=min(scores) if scores else 0,
            max_relevance_score=max(scores) if scores else 0,
            processing_time_ms=processing_time_ms,
            reranking_applied=reranking_applied,
            cache_hit=cache_hit,
        )


class SmartChunker:
    """
    Neutral chunker kept for compatibility.

    It chunks by size, preserving diff hunks when chunking unified diffs. It does
    not detect language from file extension or use language-specific boundaries.
    """

    LANGUAGE_PATTERNS: Dict[str, List[tuple]] = {"neutral": []}
    IMPORT_PATTERNS: Dict[str, str] = {"neutral": ""}
    EXTENSION_MAP: Dict[str, str] = {}
    PRIORITY_ORDER: List[str] = []

    @classmethod
    def _detect_language(cls, file_path: str) -> str:
        """Compatibility API; always returns neutral."""
        return "neutral"

    @classmethod
    def _get_patterns_for_language(cls, language: str) -> List[tuple]:
        """Compatibility API; no language-specific patterns are used."""
        return []

    @classmethod
    def _get_import_pattern(cls, language: str) -> str:
        """Compatibility API; imports are not treated specially."""
        return ""

    @classmethod
    def smart_chunk(
        cls,
        content: str,
        max_tokens: int,
        tokens_per_char: float = 0.25,
        preserve_imports: bool = True,
        file_path: str = "",
    ) -> List[str]:
        max_chars = max(1, int(max_tokens / tokens_per_char))
        if len(content) <= max_chars:
            return [content]
        return cls._chunk_by_lines(content, max_chars)

    @classmethod
    def _find_boundaries(cls, lines: List[str], file_path: str = "") -> List[tuple]:
        """No-op compatibility hook; semantic boundaries are LLM/parser owned."""
        return []

    @classmethod
    def _chunk_by_lines(
        cls,
        content: str,
        max_chars: int,
        imports_section: str = "",
    ) -> List[str]:
        lines = content.split("\n")
        chunks: List[str] = []
        current = imports_section

        for line in lines:
            addition = line + "\n"
            if current and len(current) + len(addition) > max_chars:
                chunks.append(current)
                current = imports_section + addition
            else:
                current += addition

        if current.strip():
            chunks.append(current)

        return chunks or [content[:max_chars]]

    @classmethod
    def chunk_diff(
        cls,
        diff_content: str,
        max_tokens: int,
        tokens_per_char: float = 0.25,
    ) -> List[str]:
        max_chars = max(1, int(max_tokens / tokens_per_char))
        if len(diff_content) <= max_chars:
            return [diff_content]

        hunk_pattern = r"(@@ -\d+,?\d* \+\d+,?\d* @@)"
        parts = re.split(hunk_pattern, diff_content)

        chunks: List[str] = []
        current = ""
        header = ""
        if parts and not parts[0].startswith("@@"):
            header = parts[0]
            parts = parts[1:]

        i = 0
        while i < len(parts):
            hunk_header = parts[i] if i < len(parts) else ""
            hunk_content = parts[i + 1] if i + 1 < len(parts) else ""
            full_hunk = hunk_header + hunk_content
            if current and len(header) + len(current) + len(full_hunk) > max_chars:
                chunks.append(header + current)
                current = full_hunk
            else:
                current += full_hunk
            i += 2

        if current:
            chunks.append(header + current)

        return chunks or cls._chunk_by_lines(diff_content, max_chars)


class RAGCache:
    """In-memory cache for RAG results with TTL support."""

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
        pr_description: str = "",
    ) -> str:
        sorted_files = sorted(changed_files) if changed_files else []
        key_data = (
            f"{workspace}:{project}:{branch}:{','.join(sorted_files)}:"
            f"{pr_title}:{hashlib.md5(pr_description.encode()).hexdigest()}"
        )
        return hashlib.md5(key_data.encode()).hexdigest()

    def get(
        self,
        workspace: str,
        project: str,
        branch: str,
        changed_files: List[str],
        pr_title: str = "",
        pr_description: str = "",
    ) -> Optional[Dict[str, Any]]:
        key = self._make_key(workspace, project, branch, changed_files, pr_title, pr_description)
        entry = self._cache.get(key)
        if not entry:
            return None

        now = datetime.now().timestamp()
        if now - entry.get("_cached_at", 0) > self._ttl_seconds:
            del self._cache[key]
            logger.debug("RAG cache expired for key: %s", key[:16])
            return None

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
        pr_description: str = "",
    ) -> None:
        if len(self._cache) >= self._max_size:
            self._evict_oldest()

        key = self._make_key(workspace, project, branch, changed_files, pr_title, pr_description)
        entry = result.copy()
        entry["_cached_at"] = datetime.now().timestamp()
        self._cache[key] = entry

    def _evict_oldest(self) -> None:
        if not self._cache:
            return
        entries = sorted(
            ((key, value.get("_cached_at", 0)) for key, value in self._cache.items()),
            key=lambda item: item[1],
        )
        for key, _ in entries[:max(1, len(entries) // 10)]:
            del self._cache[key]

    def invalidate(
        self,
        workspace: str,
        project: str,
        branch: Optional[str] = None,
    ) -> int:
        now = datetime.now().timestamp()
        half_ttl = self._ttl_seconds / 2
        count = 0
        for key, entry in list(self._cache.items()):
            if now - entry.get("_cached_at", 0) > half_ttl:
                del self._cache[key]
                count += 1
        logger.info("Invalidated %d RAG cache entries", count)
        return count

    def clear(self) -> None:
        count = len(self._cache)
        self._cache.clear()
        logger.info("Cleared %d RAG cache entries", count)

    def stats(self) -> Dict[str, Any]:
        now = datetime.now().timestamp()
        active_entries = 0
        expired_entries = 0
        total_size_estimate = 0

        for entry in self._cache.values():
            if now - entry.get("_cached_at", 0) <= self._ttl_seconds:
                active_entries += 1
            else:
                expired_entries += 1
            total_size_estimate += len(str(entry))

        return {
            "total_entries": len(self._cache),
            "active_entries": active_entries,
            "expired_entries": expired_entries,
            "max_size": self._max_size,
            "ttl_seconds": self._ttl_seconds,
            "size_estimate_bytes": total_size_estimate,
        }


_rag_cache = RAGCache()


def get_rag_cache() -> RAGCache:
    """Get the global RAG cache instance."""
    return _rag_cache
