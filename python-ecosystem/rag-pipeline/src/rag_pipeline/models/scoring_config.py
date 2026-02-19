"""
Scoring configuration for RAG query result scoring.

Provides configurable boost factors for content types that can be
overridden via environment variables.

DESIGN NOTE: We deliberately keep scoring simple — only content-type boost.
File-path-based boosting and metadata bonuses were removed because they 
caused irrelevant results to be ranked above relevant ones (e.g., a Magezon
helper function beating an actual PR file because "helper" matched a 
high-priority pattern).

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
    
    Deliberately minimal — only content-type boost is applied here.
    Intelligent reranking (PR context, dependency proximity) is handled
    by the LLM reranker in the inference-orchestrator.
    
    Environment variables:
    - RAG_BOOST_FUNCTIONS_CLASSES (default: 1.1)
    - RAG_BOOST_FALLBACK (default: 1.0)
    - RAG_BOOST_OVERSIZED (default: 0.95)
    - RAG_BOOST_SIMPLIFIED (default: 0.8)
    - RAG_MIN_RELEVANCE_SCORE (default: 0.7)
    - RAG_MAX_SCORE_CAP (default: 1.0)
    
    Usage:
        config = ScoringConfig()
        score, _ = config.calculate_boosted_score(0.85, 'functions_classes')
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
    
    def calculate_boosted_score(
        self,
        base_score: float,
        content_type: str,
        # Legacy parameters kept for API compatibility but ignored
        file_path: str = "",
        has_semantic_names: bool = False,
        has_docstring: bool = False,
        has_signature: bool = False
    ) -> tuple:
        """
        Calculate final score for a result.
        
        Only applies content-type boost. File-path and metadata boosts
        were removed to prevent irrelevant result inflation.
        
        Args:
            base_score: Original similarity score
            content_type: Content type (functions_classes, fallback, etc.)
            file_path: (ignored, kept for API compatibility)
            has_semantic_names: (ignored, kept for API compatibility)
            has_docstring: (ignored, kept for API compatibility)
            has_signature: (ignored, kept for API compatibility)
            
        Returns:
            Tuple of (boosted_score, priority_level)
        """
        # Single factor: content type quality
        content_boost = self.content_type_boost.get(content_type)
        score = base_score * content_boost
        
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
        logger.info("ScoringConfig initialized (simplified: content-type boost only)")
        logger.info(f"  Content type boosts: functions_classes={_scoring_config.content_type_boost.functions_classes}")
    return _scoring_config


def reset_scoring_config():
    """Reset the global config (useful for testing)."""
    global _scoring_config
    _scoring_config = None
