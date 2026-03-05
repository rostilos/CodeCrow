"""
Scoring configuration for RAG query result scoring.

Provides configurable boost factors for content types that can be
overridden via environment variables.

DESIGN NOTE: We deliberately keep scoring simple — content-type boost 
and information density penalty. File-path-based boosting and metadata 
bonuses were removed because they caused irrelevant results to be ranked 
above relevant ones (e.g., a Magezon helper function beating an actual 
PR file because "helper" matched a high-priority pattern).

Information density measures how much meaningful AST structure a chunk
contains per line of code. Chunks with very low density (import-only 
blocks, boilerplate config, blank scaffolding) get penalized to prevent 
them from polluting context windows.

Intelligent reranking (PR-file awareness, dependency proximity) is handled
by the LLM reranker in the inference-orchestrator service.
"""

import os
from typing import List
from pydantic import BaseModel, Field
import logging

logger = logging.getLogger(__name__)


def _parse_float_env(env_var: str, default: float) -> float:
    """Parse float from environment variable."""
    value = os.getenv(env_var)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        logger.warning(f"Invalid float value for {env_var}: {value}, using default {default}")
        return default


class ContentTypeBoost(BaseModel):
    """Boost factors for different content types from AST parsing.
    
    These are the ONLY score adjustments applied at the RAG pipeline level.
    Kept because content type genuinely reflects chunk quality:
    - functions_classes: Full, parseable definitions (highest value)
    - fallback: Regex-based splits (neutral)
    - oversized_split: Large chunks that were force-split (slightly penalized)
    - simplified_code: Placeholders only (significantly penalized)
    """
    
    functions_classes: float = Field(
        default_factory=lambda: _parse_float_env("RAG_BOOST_FUNCTIONS_CLASSES", 1.1),
        description="Boost for full function/class definitions"
    )
    fallback: float = Field(
        default_factory=lambda: _parse_float_env("RAG_BOOST_FALLBACK", 1.0),
        description="Boost for regex-based split chunks"
    )
    oversized_split: float = Field(
        default_factory=lambda: _parse_float_env("RAG_BOOST_OVERSIZED", 0.95),
        description="Boost for large chunks that were split"
    )
    simplified_code: float = Field(
        default_factory=lambda: _parse_float_env("RAG_BOOST_SIMPLIFIED", 0.8),
        description="Boost for code with placeholders (context only)"
    )
    
    def get(self, content_type: str) -> float:
        """Get boost factor for a content type."""
        return getattr(self, content_type, 1.0)


class ScoringConfig(BaseModel):
    """
    Scoring configuration for RAG query results.
    
    Applies two scoring factors:
    1. Content-type boost — reflects chunk parsing quality
    2. Information density penalty — penalizes low-signal chunks
    
    Intelligent reranking (PR context, dependency proximity) is handled
    by the LLM reranker in the inference-orchestrator.
    
    Environment variables:
    - RAG_BOOST_FUNCTIONS_CLASSES (default: 1.1)
    - RAG_BOOST_FALLBACK (default: 1.0)
    - RAG_BOOST_OVERSIZED (default: 0.95)
    - RAG_BOOST_SIMPLIFIED (default: 0.8)
    - RAG_MIN_RELEVANCE_SCORE (default: 0.7)
    - RAG_MAX_SCORE_CAP (default: 1.0)
    - RAG_DENSITY_THRESHOLD (default: 0.1) — below this, penalty applies
    - RAG_DENSITY_FLOOR (default: 0.3) — minimum density multiplier
    
    Usage:
        config = ScoringConfig()
        score, _ = config.calculate_boosted_score(0.85, 'functions_classes')
        score, _ = config.calculate_boosted_score(0.85, 'fallback', information_density=0.02)
    """
    
    content_type_boost: ContentTypeBoost = Field(default_factory=ContentTypeBoost)
    
    # Score thresholds
    min_relevance_score: float = Field(
        default_factory=lambda: _parse_float_env("RAG_MIN_RELEVANCE_SCORE", 0.7),
        description="Minimum score threshold for results"
    )
    
    max_score_cap: float = Field(
        default_factory=lambda: _parse_float_env("RAG_MAX_SCORE_CAP", 1.0),
        description="Maximum score cap after boosting"
    )
    
    # Information density penalty
    density_threshold: float = Field(
        default_factory=lambda: _parse_float_env("RAG_DENSITY_THRESHOLD", 0.1),
        description="Chunks with density below this get penalized"
    )
    density_floor: float = Field(
        default_factory=lambda: _parse_float_env("RAG_DENSITY_FLOOR", 0.3),
        description="Minimum multiplier for the lowest-density chunks"
    )

    # Ecosystem affinity — penalize chunks from a different language ecosystem
    ecosystem_mismatch_penalty: float = Field(
        default_factory=lambda: _parse_float_env("RAG_ECOSYSTEM_MISMATCH_PENALTY", 0.2),
        description="Multiplier when chunk's language ecosystem differs from the review target"
    )

    # Per-source-file cap — prevent one file from dominating results
    max_chunks_per_source_file: int = Field(
        default_factory=lambda: int(os.getenv("RAG_MAX_CHUNKS_PER_SOURCE_FILE", "2")),
        description="Max chunks kept from a single source file in results"
    )

    # Oversized chunk penalty — penalize very large chunks that waste token budget
    oversized_chunk_threshold: int = Field(
        default_factory=lambda: int(os.getenv("RAG_OVERSIZED_CHUNK_THRESHOLD", "4000")),
        description="Chunk text length above which size penalty starts"
    )
    oversized_chunk_penalty: float = Field(
        default_factory=lambda: _parse_float_env("RAG_OVERSIZED_CHUNK_PENALTY", 0.5),
        description="Minimum multiplier for the most oversized chunks"
    )

    # Missing density fallback — penalize chunks indexed before density feature
    missing_density_penalty: float = Field(
        default_factory=lambda: _parse_float_env("RAG_MISSING_DENSITY_PENALTY", 0.85),
        description="Multiplier when information_density metadata is absent (old indexed chunks)"
    )

    def calculate_boosted_score(
        self,
        base_score: float,
        content_type: str,
        # Legacy parameters kept for API compatibility but ignored
        file_path: str = "",
        has_semantic_names: bool = False,
        has_docstring: bool = False,
        has_signature: bool = False,
        # New: information density from chunk metadata
        information_density: float = -1.0
    ) -> tuple:
        """
        Calculate final score for a result.
        
        Applies content-type boost and information density penalty.
        File-path and metadata boosts were removed to prevent irrelevant 
        result inflation.
        
        Args:
            base_score: Original similarity score
            content_type: Content type (functions_classes, fallback, etc.)
            file_path: (ignored, kept for API compatibility)
            has_semantic_names: (ignored, kept for API compatibility)
            has_docstring: (ignored, kept for API compatibility)
            has_signature: (ignored, kept for API compatibility)
            information_density: AST signal density [0.0, 1.0], -1.0 means not available
            
        Returns:
            Tuple of (boosted_score, priority_level)
        """
        # Factor 1: content type quality
        content_boost = self.content_type_boost.get(content_type)
        score = base_score * content_boost
        
        # Factor 2: information density penalty
        if information_density < 0:
            # Chunk was indexed before the density feature — apply moderate penalty
            # since we cannot verify its signal quality
            score *= self.missing_density_penalty
        elif information_density < self.density_threshold:
            # Linear interpolation: density=0 → floor, density=threshold → 1.0
            density_factor = self.density_floor + (1.0 - self.density_floor) * (
                information_density / self.density_threshold
            )
            score *= density_factor
        
        # Cap the score
        score = min(score, self.max_score_cap)
        
        # Priority is always MEDIUM now (no path-based priority)
        return (score, 'MEDIUM')


# Global singleton
_scoring_config: ScoringConfig | None = None


def get_scoring_config() -> ScoringConfig:
    """Get the global ScoringConfig instance."""
    global _scoring_config
    if _scoring_config is None:
        _scoring_config = ScoringConfig()
        logger.info("ScoringConfig initialized (content-type + density + ecosystem + size)")
        logger.info(f"  Content type boosts: functions_classes={_scoring_config.content_type_boost.functions_classes}")
        logger.info(f"  Density: threshold={_scoring_config.density_threshold}, floor={_scoring_config.density_floor}, missing={_scoring_config.missing_density_penalty}")
        logger.info(f"  Ecosystem mismatch penalty: {_scoring_config.ecosystem_mismatch_penalty}")
        logger.info(f"  Per-file cap: {_scoring_config.max_chunks_per_source_file}, "
                    f"oversized threshold: {_scoring_config.oversized_chunk_threshold}")
    return _scoring_config


def reset_scoring_config():
    """Reset the global config (useful for testing)."""
    global _scoring_config
    _scoring_config = None
