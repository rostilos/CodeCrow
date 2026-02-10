"""
Scoring configuration for RAG query result reranking.

Provides configurable boost factors and priority patterns that can be
overridden via environment variables.
"""

import os
from typing import Dict, List
from pydantic import BaseModel, Field
import logging

logger = logging.getLogger(__name__)


def _parse_list_env(env_var: str, default: List[str]) -> List[str]:
    """Parse comma-separated environment variable into list."""
    value = os.getenv(env_var)
    if not value:
        return default
    return [item.strip() for item in value.split(',') if item.strip()]


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
    """Boost factors for different content types from AST parsing."""
    
    functions_classes: float = Field(
        default_factory=lambda: _parse_float_env("RAG_BOOST_FUNCTIONS_CLASSES", 1.2),
        description="Boost for full function/class definitions (highest value)"
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
        default_factory=lambda: _parse_float_env("RAG_BOOST_SIMPLIFIED", 0.7),
        description="Boost for code with placeholders (context only)"
    )
    
    def get(self, content_type: str) -> float:
        """Get boost factor for a content type."""
        return getattr(self, content_type, 1.0)


class FilePriorityPatterns(BaseModel):
    """File path patterns for priority-based boosting."""
    
    high: List[str] = Field(
        default_factory=lambda: _parse_list_env(
            "RAG_HIGH_PRIORITY_PATTERNS",
            ['service', 'controller', 'handler', 'api', 'core', 'auth', 'security',
             'permission', 'repository', 'dao', 'migration']
        ),
        description="Patterns for high-priority files (1.3x boost)"
    )
    
    medium: List[str] = Field(
        default_factory=lambda: _parse_list_env(
            "RAG_MEDIUM_PRIORITY_PATTERNS",
            ['model', 'entity', 'dto', 'schema', 'util', 'helper', 'common',
             'shared', 'component', 'hook', 'client', 'integration']
        ),
        description="Patterns for medium-priority files (1.1x boost)"
    )
    
    low: List[str] = Field(
        default_factory=lambda: _parse_list_env(
            "RAG_LOW_PRIORITY_PATTERNS",
            ['test', 'spec', 'config', 'mock', 'fixture', 'stub']
        ),
        description="Patterns for low-priority files (0.8x penalty)"
    )
    
    high_boost: float = Field(
        default_factory=lambda: _parse_float_env("RAG_HIGH_PRIORITY_BOOST", 1.3)
    )
    medium_boost: float = Field(
        default_factory=lambda: _parse_float_env("RAG_MEDIUM_PRIORITY_BOOST", 1.1)
    )
    low_boost: float = Field(
        default_factory=lambda: _parse_float_env("RAG_LOW_PRIORITY_BOOST", 0.8)
    )
    
    def get_priority(self, file_path: str) -> tuple:
        """
        Get priority level and boost factor for a file path.
        
        Uses word-boundary matching to avoid false positives like 'test' matching 'latest.py'.
        Patterns are matched against path segments (directories and filename).
        
        Returns:
            Tuple of (priority_name, boost_factor)
        """
        import re
        
        path_lower = file_path.lower()
        # Extract path segments for word-boundary matching
        segments = re.split(r'[/\\]', path_lower)
        # Also consider filename without extension
        filename = segments[-1] if segments else ''
        name_without_ext = filename.rsplit('.', 1)[0] if '.' in filename else filename
        
        def pattern_matches(patterns: List[str]) -> bool:
            for p in patterns:
                # Check if pattern matches as a complete segment
                if p in segments:
                    return True
                # Check if pattern matches at word boundaries in filename
                # E.g., 'test' matches 'test_utils.py' or 'UserServiceTest.java' but not 'latest.py'
                if re.search(rf'\b{re.escape(p)}\b', name_without_ext):
                    return True
                # Also check directory names for patterns like 'tests/', '__tests__/'
                for seg in segments[:-1]:
                    if re.search(rf'\b{re.escape(p)}\b', seg):
                        return True
            return False
        
        if pattern_matches(self.high):
            return ('HIGH', self.high_boost)
        elif pattern_matches(self.medium):
            return ('MEDIUM', self.medium_boost)
        elif pattern_matches(self.low):
            return ('LOW', self.low_boost)
        else:
            return ('MEDIUM', 1.0)


class MetadataBonus(BaseModel):
    """Bonus multipliers for metadata presence."""
    
    semantic_names: float = Field(
        default_factory=lambda: _parse_float_env("RAG_BONUS_SEMANTIC_NAMES", 1.1),
        description="Bonus for chunks with extracted semantic names"
    )
    docstring: float = Field(
        default_factory=lambda: _parse_float_env("RAG_BONUS_DOCSTRING", 1.05),
        description="Bonus for chunks with docstrings"
    )
    signature: float = Field(
        default_factory=lambda: _parse_float_env("RAG_BONUS_SIGNATURE", 1.02),
        description="Bonus for chunks with function signatures"
    )


class ScoringConfig(BaseModel):
    """
    Complete scoring configuration for RAG query reranking.
    
    All values can be overridden via environment variables:
    - RAG_BOOST_FUNCTIONS_CLASSES, RAG_BOOST_FALLBACK, etc.
    - RAG_HIGH_PRIORITY_PATTERNS (comma-separated)
    - RAG_HIGH_PRIORITY_BOOST, RAG_MEDIUM_PRIORITY_BOOST, etc.
    - RAG_BONUS_SEMANTIC_NAMES, RAG_BONUS_DOCSTRING, etc.
    
    Usage:
        config = ScoringConfig()
        boost = config.content_type_boost.get('functions_classes')
        priority, boost = config.file_priority.get_priority('/src/UserService.java')
    """
    
    content_type_boost: ContentTypeBoost = Field(default_factory=ContentTypeBoost)
    file_priority: FilePriorityPatterns = Field(default_factory=FilePriorityPatterns)
    metadata_bonus: MetadataBonus = Field(default_factory=MetadataBonus)
    
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
        file_path: str,
        content_type: str,
        has_semantic_names: bool = False,
        has_docstring: bool = False,
        has_signature: bool = False
    ) -> tuple:
        """
        Calculate final boosted score for a result.
        
        Args:
            base_score: Original similarity score
            file_path: File path of the chunk
            content_type: Content type (functions_classes, fallback, etc.)
            has_semantic_names: Whether chunk has semantic names
            has_docstring: Whether chunk has docstring
            has_signature: Whether chunk has signature
            
        Returns:
            Tuple of (boosted_score, priority_level)
        """
        score = base_score
        
        # File priority boost
        priority, priority_boost = self.file_priority.get_priority(file_path)
        score *= priority_boost
        
        # Content type boost
        content_boost = self.content_type_boost.get(content_type)
        score *= content_boost
        
        # Metadata bonuses
        if has_semantic_names:
            score *= self.metadata_bonus.semantic_names
        if has_docstring:
            score *= self.metadata_bonus.docstring
        if has_signature:
            score *= self.metadata_bonus.signature
        
        # Cap the score
        score = min(score, self.max_score_cap)
        
        return (score, priority)


# Global singleton
_scoring_config: ScoringConfig | None = None


def get_scoring_config() -> ScoringConfig:
    """Get the global ScoringConfig instance."""
    global _scoring_config
    if _scoring_config is None:
        _scoring_config = ScoringConfig()
        logger.info("ScoringConfig initialized with:")
        logger.info(f"  High priority patterns: {_scoring_config.file_priority.high[:5]}...")
        logger.info(f"  Content type boosts: functions_classes={_scoring_config.content_type_boost.functions_classes}")
    return _scoring_config


def reset_scoring_config():
    """Reset the global config (useful for testing)."""
    global _scoring_config
    _scoring_config = None
