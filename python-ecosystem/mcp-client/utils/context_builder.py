"""
Context builder for structured code review with Lost-in-the-Middle protection.
Implements priority-based context assembly with token budget management.
"""
import logging
import os
import re
import hashlib
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

from .file_classifier import FileClassifier, FilePriority, ClassifiedFile

logger = logging.getLogger(__name__)

# === Environment-based configuration ===
# Context Budget percentages (must sum to 1.0)
CONTEXT_BUDGET_HIGH_PRIORITY_PCT = float(os.environ.get("CONTEXT_BUDGET_HIGH_PRIORITY_PCT", "0.30"))
CONTEXT_BUDGET_MEDIUM_PRIORITY_PCT = float(os.environ.get("CONTEXT_BUDGET_MEDIUM_PRIORITY_PCT", "0.40"))
CONTEXT_BUDGET_LOW_PRIORITY_PCT = float(os.environ.get("CONTEXT_BUDGET_LOW_PRIORITY_PCT", "0.20"))
CONTEXT_BUDGET_RAG_PCT = float(os.environ.get("CONTEXT_BUDGET_RAG_PCT", "0.10"))

# Validate budget percentages sum to 1.0
_budget_sum = CONTEXT_BUDGET_HIGH_PRIORITY_PCT + CONTEXT_BUDGET_MEDIUM_PRIORITY_PCT + CONTEXT_BUDGET_LOW_PRIORITY_PCT + CONTEXT_BUDGET_RAG_PCT
if abs(_budget_sum - 1.0) > 0.01:
    logger.warning(f"Context budget percentages sum to {_budget_sum}, expected 1.0. Normalizing...")
    _factor = 1.0 / _budget_sum
    CONTEXT_BUDGET_HIGH_PRIORITY_PCT *= _factor
    CONTEXT_BUDGET_MEDIUM_PRIORITY_PCT *= _factor
    CONTEXT_BUDGET_LOW_PRIORITY_PCT *= _factor
    CONTEXT_BUDGET_RAG_PCT *= _factor

# RAG Cache settings
RAG_CACHE_TTL_SECONDS = int(os.environ.get("RAG_CACHE_TTL_SECONDS", "300"))
RAG_CACHE_MAX_SIZE = int(os.environ.get("RAG_CACHE_MAX_SIZE", "100"))

# RAG query settings
RAG_MIN_RELEVANCE_SCORE = float(os.environ.get("RAG_MIN_RELEVANCE_SCORE", "0.7"))
RAG_DEFAULT_TOP_K = int(os.environ.get("RAG_DEFAULT_TOP_K", "15"))


# Model context limits (approximate usable tokens after system prompt)
MODEL_CONTEXT_LIMITS = {
    # OpenAI models
    "gpt-4-turbo": 90000,
    "gpt-4-turbo-preview": 90000,
    "gpt-4-0125-preview": 90000,
    "gpt-4-1106-preview": 90000,
    "gpt-4": 6000,
    "gpt-4-32k": 24000,
    "gpt-3.5-turbo": 12000,
    "gpt-3.5-turbo-16k": 12000,
    # Anthropic models
    "claude-3-opus": 140000,
    "claude-3-opus-20240229": 140000,
    "claude-3-sonnet": 140000,
    "claude-3-sonnet-20240229": 140000,
    "claude-3-haiku": 140000,
    "claude-3-haiku-20240307": 140000,
    "claude-3-5-sonnet": 140000,
    "claude-3-5-sonnet-20240620": 140000,
    "claude-3-5-sonnet-20241022": 140000,
    # OpenRouter models (common ones)
    "anthropic/claude-3-opus": 140000,
    "anthropic/claude-3-sonnet": 140000,
    "anthropic/claude-3-haiku": 140000,
    "openai/gpt-4-turbo": 90000,
    "openai/gpt-4": 6000,
    "google/gemini-pro": 24000,
    "google/gemini-pro-1.5": 700000,
    "meta-llama/llama-3-70b-instruct": 6000,
    "mistralai/mistral-large": 24000,
    "deepseek/deepseek-chat": 48000,
    "deepseek/deepseek-coder": 48000,

    "openai/gpt-5-mini": 400000,
    "openai/gpt-5.1-codex-mini": 400000,
    "openai/gpt-5.2": 512000,
    "openai/gpt-5.1": 400000,
    "openai/gpt-5.1-thinking": 196000,
    "openai/gpt-5": 128000,
    "openai/o3-high": 200000,
    "openai/o4-mini": 128000,

    "anthropic/claude-4.5-opus": 500000,
    "anthropic/claude-4.5-sonnet": 200000,
    "anthropic/claude-4.5-haiku": 200000,
    "anthropic/claude-3.7-sonnet": 200000,
    
    "google/gemini-3-pro": 1000000,
    "google/gemini-3-flash": 1000000,
    "google/gemini-2.5-pro": 2000000,
    "google/gemini-2.5-flash": 1000000,
    "google/gemini-3-flash-preview": 1000000,
    "google/gemini-3-pro-preview": 1000000,
    
    "llama-4-405b": 128000,     
    "llama-4-70b": 128000,
    "llama-4-scout": 1000000,   
    
    "deepseek-v3.1": 160000,    
    "deepseek-v3": 128000,
    "mistral-large-2025": 128000,
    
    "gpt-4o": 128000,
    "gpt-4-turbo": 128000,      
    "claude-3-5-sonnet": 200000,

    "x-ai/grok-4.1-fast": 2000000,
    
    "default": 200000
}


@dataclass
class ContextBudget:
    """Token budget allocation for different context sections."""
    total_tokens: int = 45000  # Default max context budget
    
    # Budget distribution (percentages) - loaded from environment
    high_priority_pct: float = field(default_factory=lambda: CONTEXT_BUDGET_HIGH_PRIORITY_PCT)
    medium_priority_pct: float = field(default_factory=lambda: CONTEXT_BUDGET_MEDIUM_PRIORITY_PCT)
    low_priority_pct: float = field(default_factory=lambda: CONTEXT_BUDGET_LOW_PRIORITY_PCT)
    rag_context_pct: float = field(default_factory=lambda: CONTEXT_BUDGET_RAG_PCT)
    
    @property
    def high_priority_tokens(self) -> int:
        return int(self.total_tokens * self.high_priority_pct)
    
    @property
    def medium_priority_tokens(self) -> int:
        return int(self.total_tokens * self.medium_priority_pct)
    
    @property
    def low_priority_tokens(self) -> int:
        return int(self.total_tokens * self.low_priority_pct)
    
    @property
    def rag_tokens(self) -> int:
        return int(self.total_tokens * self.rag_context_pct)
    
    @classmethod
    def for_model(cls, model_name: str) -> "ContextBudget":
        """
        Create a ContextBudget optimized for a specific model.
        
        Args:
            model_name: The model identifier (e.g., "gpt-4-turbo", "claude-3-opus")
            
        Returns:
            ContextBudget with appropriate token limits
        """
        # Normalize model name for lookup
        model_lower = model_name.lower()
        
        # Try exact match first
        limit = MODEL_CONTEXT_LIMITS.get(model_lower)
        
        # Try partial matches
        if limit is None:
            for key, value in MODEL_CONTEXT_LIMITS.items():
                if key in model_lower or model_lower in key:
                    limit = value
                    break
        
        # Use default if no match
        if limit is None:
            limit = MODEL_CONTEXT_LIMITS["default"]
            logger.warning(f"Unknown model '{model_name}', using default context budget: {limit}")
        
        logger.info(f"Context budget for model '{model_name}': {limit} tokens")
        return cls(total_tokens=limit)


@dataclass
class ContextSection:
    """A section of structured context."""
    priority: str
    title: str
    description: str
    content: str
    file_count: int
    token_estimate: int
    files_included: List[str] = field(default_factory=list)
    files_truncated: List[str] = field(default_factory=list)


@dataclass  
class StructuredContext:
    """Complete structured context with all sections."""
    sections: List[ContextSection]
    total_files: int
    total_tokens_estimated: int
    files_analyzed: List[str]
    files_skipped: List[str]
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_prompt_string(self) -> str:
        """Convert to formatted string for prompt injection."""
        parts = []
        
        # Add metadata header
        parts.append("=" * 60)
        parts.append("STRUCTURED CONTEXT WITH PRIORITY MARKERS")
        parts.append(f"Total files: {self.total_files} | Estimated tokens: {self.total_tokens_estimated}")
        parts.append("=" * 60)
        parts.append("")
        
        for section in self.sections:
            if section.content:
                parts.append(f"=== {section.priority} PRIORITY: {section.title} ===")
                parts.append(f"({section.description})")
                parts.append(f"Files: {section.file_count} | ~{section.token_estimate} tokens")
                parts.append("")
                parts.append(section.content)
                parts.append("")
                parts.append(f"=== END {section.priority} PRIORITY ===")
                parts.append("")
        
        return "\n".join(parts)


class ContextBuilder:
    """
    Builds structured context for code review with Lost-in-the-Middle protection.
    
    Key features:
    1. Priority-based file ordering (HIGH -> MEDIUM -> LOW -> RAG)
    2. Token budget management per section
    3. Explicit section markers for LLM attention
    4. Smart truncation when budget exceeded
    """
    
    # Approximate tokens per character (conservative estimate)
    TOKENS_PER_CHAR = 0.25
    
    def __init__(self, budget: Optional[ContextBudget] = None):
        self.budget = budget or ContextBudget()
        self.file_classifier = FileClassifier()
    
    def build_structured_context(
        self,
        pr_metadata: Dict[str, Any],
        diff_content: Dict[str, str],  # file_path -> diff/content
        rag_context: Optional[Dict[str, Any]] = None,
        file_paths: Optional[List[str]] = None
    ) -> StructuredContext:
        """
        Build structured context from PR data and RAG results.
        
        Args:
            pr_metadata: PR metadata (title, description, etc.)
            diff_content: Dict mapping file paths to their diff content
            rag_context: Optional RAG query results
            file_paths: Optional list of changed file paths
            
        Returns:
            StructuredContext with prioritized sections
        """
        file_paths = file_paths or list(diff_content.keys())
        
        # Step 1: Classify files by priority
        classified = FileClassifier.classify_files(file_paths)
        stats = FileClassifier.get_priority_stats(classified)
        logger.info(f"File classification: {stats}")
        
        sections = []
        files_analyzed = []
        files_skipped = []
        total_tokens = 0
        
        # Step 2: Build HIGH priority section
        high_section = self._build_priority_section(
            priority=FilePriority.HIGH,
            files=classified[FilePriority.HIGH],
            diff_content=diff_content,
            token_budget=self.budget.high_priority_tokens,
            title="Core Business Logic",
            description="Analyze FIRST - security, auth, core services"
        )
        sections.append(high_section)
        files_analyzed.extend(high_section.files_included)
        files_skipped.extend(high_section.files_truncated)
        total_tokens += high_section.token_estimate
        
        # Step 3: Build MEDIUM priority section
        medium_section = self._build_priority_section(
            priority=FilePriority.MEDIUM,
            files=classified[FilePriority.MEDIUM],
            diff_content=diff_content,
            token_budget=self.budget.medium_priority_tokens,
            title="Dependencies & Shared Utils",
            description="Models, DTOs, utilities, components"
        )
        sections.append(medium_section)
        files_analyzed.extend(medium_section.files_included)
        files_skipped.extend(medium_section.files_truncated)
        total_tokens += medium_section.token_estimate
        
        # Step 4: Build LOW priority section
        low_section = self._build_priority_section(
            priority=FilePriority.LOW,
            files=classified[FilePriority.LOW],
            diff_content=diff_content,
            token_budget=self.budget.low_priority_tokens,
            title="Tests & Config",
            description="Test files, configurations (quick scan)"
        )
        sections.append(low_section)
        files_analyzed.extend(low_section.files_included)
        files_skipped.extend(low_section.files_truncated)
        total_tokens += low_section.token_estimate
        
        # Step 5: Build RAG context section
        rag_section = self._build_rag_section(
            rag_context=rag_context,
            token_budget=self.budget.rag_tokens,
            already_included=set(files_analyzed)
        )
        sections.append(rag_section)
        total_tokens += rag_section.token_estimate
        
        # Add skipped files from classification
        for f in classified[FilePriority.SKIP]:
            files_skipped.append(f.path)
        
        return StructuredContext(
            sections=sections,
            total_files=len(file_paths),
            total_tokens_estimated=total_tokens,
            files_analyzed=files_analyzed,
            files_skipped=files_skipped,
            metadata={
                "classification_stats": stats,
                "budget": {
                    "total": self.budget.total_tokens,
                    "high": self.budget.high_priority_tokens,
                    "medium": self.budget.medium_priority_tokens,
                    "low": self.budget.low_priority_tokens,
                    "rag": self.budget.rag_tokens
                }
            }
        )
    
    def _build_priority_section(
        self,
        priority: FilePriority,
        files: List[ClassifiedFile],
        diff_content: Dict[str, str],
        token_budget: int,
        title: str,
        description: str
    ) -> ContextSection:
        """Build a single priority section with budget management."""
        content_parts = []
        files_included = []
        files_truncated = []
        current_tokens = 0
        
        for classified_file in files:
            path = classified_file.path
            if path not in diff_content:
                continue
            
            file_content = diff_content[path]
            file_tokens = self._estimate_tokens(file_content)
            
            # Check if adding this file would exceed budget
            if current_tokens + file_tokens > token_budget:
                # Try to include truncated version if significant budget remains
                remaining_budget = token_budget - current_tokens
                if remaining_budget > 500:  # Only truncate if meaningful space left
                    truncated_content = self._truncate_content(file_content, remaining_budget)
                    content_parts.append(f"### {path} (TRUNCATED)")
                    content_parts.append(truncated_content)
                    content_parts.append("")
                    files_included.append(path)
                    current_tokens += self._estimate_tokens(truncated_content)
                else:
                    files_truncated.append(path)
                continue
            
            content_parts.append(f"### {path}")
            content_parts.append(f"Category: {classified_file.category} | Importance: {classified_file.estimated_importance:.2f}")
            content_parts.append("```")
            content_parts.append(file_content)
            content_parts.append("```")
            content_parts.append("")
            
            files_included.append(path)
            current_tokens += file_tokens
        
        return ContextSection(
            priority=priority.value,
            title=title,
            description=description,
            content="\n".join(content_parts),
            file_count=len(files_included),
            token_estimate=current_tokens,
            files_included=files_included,
            files_truncated=files_truncated
        )
    
    def _build_rag_section(
        self,
        rag_context: Optional[Dict[str, Any]],
        token_budget: int,
        already_included: set
    ) -> ContextSection:
        """Build RAG context section with deduplication."""
        if not rag_context or not rag_context.get("relevant_code"):
            return ContextSection(
                priority="RAG",
                title="Additional Context from Codebase",
                description="No RAG context available",
                content="",
                file_count=0,
                token_estimate=0
            )
        
        content_parts = []
        files_included = []
        current_tokens = 0
        max_rag_chunks = 5  # Limit RAG chunks
        
        relevant_code = rag_context.get("relevant_code", [])
        chunk_count = 0
        
        for chunk in relevant_code:
            if chunk_count >= max_rag_chunks:
                break
            
            # Skip if file already included in priority sections
            chunk_path = chunk.get("metadata", {}).get("path", "unknown")
            if chunk_path in already_included:
                continue
            
            chunk_text = chunk.get("text", "")
            chunk_score = chunk.get("score", 0)
            chunk_tokens = self._estimate_tokens(chunk_text)
            
            if current_tokens + chunk_tokens > token_budget:
                break
            
            content_parts.append(f"### RAG Context {chunk_count + 1}: {chunk_path}")
            content_parts.append(f"Relevance score: {chunk_score:.3f}")
            content_parts.append("```")
            content_parts.append(chunk_text)
            content_parts.append("```")
            content_parts.append("")
            
            files_included.append(chunk_path)
            current_tokens += chunk_tokens
            chunk_count += 1
        
        return ContextSection(
            priority="RAG",
            title="Additional Context from Codebase",
            description="Semantically relevant code from repository (max 5 chunks)",
            content="\n".join(content_parts),
            file_count=len(files_included),
            token_estimate=current_tokens,
            files_included=files_included
        )
    
    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count for text."""
        return int(len(text) * self.TOKENS_PER_CHAR)
    
    def _truncate_content(self, content: str, target_tokens: int) -> str:
        """Truncate content to fit within token budget."""
        target_chars = int(target_tokens / self.TOKENS_PER_CHAR)
        
        if len(content) <= target_chars:
            return content
        
        # Try to truncate at a logical boundary
        truncated = content[:target_chars]
        
        # Find last newline to avoid cutting mid-line
        last_newline = truncated.rfind('\n')
        if last_newline > target_chars * 0.8:  # Only use if we don't lose too much
            truncated = truncated[:last_newline]
        
        return truncated + "\n... [TRUNCATED - remaining content omitted for token budget]"


class RagReranker:
    """
    Reranks RAG results for better relevance using multiple strategies.
    """
    
    @staticmethod
    def rerank_by_file_priority(
        rag_results: List[Dict[str, Any]],
        classified_files: Dict[FilePriority, List[ClassifiedFile]],
        boost_factor: float = 1.5
    ) -> List[Dict[str, Any]]:
        """
        Boost RAG results that relate to high-priority files.
        
        Args:
            rag_results: Original RAG results
            classified_files: Files classified by priority
            boost_factor: Multiplier for scores of related high-priority results
            
        Returns:
            Reranked results with adjusted scores
        """
        # Build set of high-priority file paths
        high_priority_paths = {
            f.path for f in classified_files.get(FilePriority.HIGH, [])
        }
        
        # Get directory patterns from high-priority files
        high_priority_dirs = set()
        for path in high_priority_paths:
            parts = path.split('/')
            for i in range(1, len(parts)):
                high_priority_dirs.add('/'.join(parts[:i]))
        
        reranked = []
        for result in rag_results:
            result_copy = result.copy()
            result_path = result.get("metadata", {}).get("path", "")
            
            # Boost if directly matches high-priority file
            if result_path in high_priority_paths:
                result_copy["score"] = result.get("score", 0) * boost_factor
                result_copy["_boost_reason"] = "direct_high_priority_match"
            # Smaller boost if in high-priority directory
            elif any(result_path.startswith(d) for d in high_priority_dirs):
                result_copy["score"] = result.get("score", 0) * (boost_factor * 0.7)
                result_copy["_boost_reason"] = "high_priority_directory"
            
            reranked.append(result_copy)
        
        # Sort by adjusted score
        reranked.sort(key=lambda x: x.get("score", 0), reverse=True)
        return reranked
    
    @staticmethod
    def filter_by_relevance_threshold(
        results: List[Dict[str, Any]],
        min_score: float = None,
        min_results: int = 3
    ) -> List[Dict[str, Any]]:
        """
        Filter results by minimum relevance score.
        Always returns at least min_results if available.
        Default min_score from RAG_MIN_RELEVANCE_SCORE env var.
        """
        if min_score is None:
            min_score = RAG_MIN_RELEVANCE_SCORE
        filtered = [r for r in results if r.get("score", 0) >= min_score]
        
        # Ensure minimum results
        if len(filtered) < min_results and len(results) >= min_results:
            # Add top results regardless of score
            for result in results:
                if result not in filtered:
                    filtered.append(result)
                    if len(filtered) >= min_results:
                        break
        
        return filtered
    
    @staticmethod
    def deduplicate_by_content(
        results: List[Dict[str, Any]],
        similarity_threshold: float = 0.8
    ) -> List[Dict[str, Any]]:
        """
        Remove near-duplicate results based on content similarity.
        Uses simple text overlap for efficiency.
        """
        if not results:
            return []
        
        unique_results = [results[0]]
        
        for result in results[1:]:
            is_duplicate = False
            result_text = result.get("text", "")
            
            for existing in unique_results:
                existing_text = existing.get("text", "")
                
                # Simple overlap check
                overlap = RagReranker._calculate_overlap(result_text, existing_text)
                if overlap > similarity_threshold:
                    is_duplicate = True
                    break
            
            if not is_duplicate:
                unique_results.append(result)
        
        return unique_results
    
    @staticmethod
    def _calculate_overlap(text1: str, text2: str) -> float:
        """Calculate simple text overlap ratio."""
        if not text1 or not text2:
            return 0.0
        
        # Use set of words for simple comparison
        words1 = set(text1.lower().split())
        words2 = set(text2.lower().split())
        
        if not words1 or not words2:
            return 0.0
        
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        
        return intersection / union if union > 0 else 0.0


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