"""
Unit tests for the simplified ScoringConfig.
Tests that only content-type boost is applied (no path-based or metadata boosts).
"""
import pytest
import os


def test_content_type_boost_functions_classes():
    """functions_classes content type gets a 1.1 boost."""
    # Reset singleton to pick up test defaults
    from rag_pipeline.models.scoring_config import ScoringConfig, reset_scoring_config
    reset_scoring_config()
    config = ScoringConfig()
    
    score, priority = config.calculate_boosted_score(
        base_score=0.80,
        content_type="functions_classes"
    )
    
    assert abs(score - 0.88) < 0.001  # 0.80 * 1.1 = 0.88
    assert priority == "MEDIUM"


def test_content_type_boost_fallback():
    """fallback content type gets neutral boost (1.0)."""
    from rag_pipeline.models.scoring_config import ScoringConfig, reset_scoring_config
    reset_scoring_config()
    config = ScoringConfig()
    
    score, priority = config.calculate_boosted_score(
        base_score=0.80,
        content_type="fallback"
    )
    
    assert abs(score - 0.80) < 0.001  # 0.80 * 1.0 = 0.80


def test_content_type_boost_simplified():
    """simplified_code content type gets penalized (0.8)."""
    from rag_pipeline.models.scoring_config import ScoringConfig, reset_scoring_config
    reset_scoring_config()
    config = ScoringConfig()
    
    score, _ = config.calculate_boosted_score(
        base_score=0.80,
        content_type="simplified_code"
    )
    
    assert abs(score - 0.64) < 0.001  # 0.80 * 0.8 = 0.64


def test_content_type_boost_oversized():
    """oversized_split content type gets slight penalty (0.95)."""
    from rag_pipeline.models.scoring_config import ScoringConfig, reset_scoring_config
    reset_scoring_config()
    config = ScoringConfig()
    
    score, _ = config.calculate_boosted_score(
        base_score=0.80,
        content_type="oversized_split"
    )
    
    assert abs(score - 0.76) < 0.001  # 0.80 * 0.95 = 0.76


def test_score_cap():
    """Score should not exceed max_score_cap."""
    from rag_pipeline.models.scoring_config import ScoringConfig, reset_scoring_config
    reset_scoring_config()
    config = ScoringConfig()
    
    score, _ = config.calculate_boosted_score(
        base_score=0.99,
        content_type="functions_classes"
    )
    
    # 0.99 * 1.1 = 1.089, should be capped at 1.0
    assert score <= 1.0


def test_no_path_based_boost():
    """File path should NOT affect the score (path-based boosting was removed)."""
    from rag_pipeline.models.scoring_config import ScoringConfig, reset_scoring_config
    reset_scoring_config()
    config = ScoringConfig()
    
    # "Helper" path should get same score as any other path
    score_helper, _ = config.calculate_boosted_score(
        base_score=0.70,
        content_type="functions_classes",
        file_path="app/code/Magezon/SomeModule/Helper/Data.php"
    )
    
    score_normal, _ = config.calculate_boosted_score(
        base_score=0.70,
        content_type="functions_classes",
        file_path="app/code/Perspective/Akeneo/Model/VariantGroup.php"
    )
    
    assert score_helper == score_normal, "Path-based boosting should be removed"


def test_no_metadata_boost():
    """Metadata (semantic names, docstring, signature) should NOT affect the score."""
    from rag_pipeline.models.scoring_config import ScoringConfig, reset_scoring_config
    reset_scoring_config()
    config = ScoringConfig()
    
    # All metadata flags true
    score_with_meta, _ = config.calculate_boosted_score(
        base_score=0.70,
        content_type="fallback",
        has_semantic_names=True,
        has_docstring=True,
        has_signature=True
    )
    
    # No metadata
    score_without_meta, _ = config.calculate_boosted_score(
        base_score=0.70,
        content_type="fallback",
        has_semantic_names=False,
        has_docstring=False,
        has_signature=False
    )
    
    assert score_with_meta == score_without_meta, "Metadata should not affect score"


def test_singleton_get_scoring_config():
    """get_scoring_config should return a singleton."""
    from rag_pipeline.models.scoring_config import get_scoring_config, reset_scoring_config
    reset_scoring_config()
    
    config1 = get_scoring_config()
    config2 = get_scoring_config()
    
    assert config1 is config2


def test_unknown_content_type_defaults_to_1():
    """Unknown content types should default to 1.0 boost."""
    from rag_pipeline.models.scoring_config import ScoringConfig, reset_scoring_config
    reset_scoring_config()
    config = ScoringConfig()
    
    score, _ = config.calculate_boosted_score(
        base_score=0.80,
        content_type="some_unknown_type"
    )
    
    assert abs(score - 0.80) < 0.001  # 0.80 * 1.0 = 0.80


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
