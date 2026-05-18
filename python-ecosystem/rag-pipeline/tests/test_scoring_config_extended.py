"""
Extended unit tests for rag_pipeline.models.scoring_config.
Adds edge cases: density penalty, missing density, ecosystem, oversized, per-file cap.
"""
import pytest
from rag_pipeline.models.scoring_config import (
    ScoringConfig,
    ContentTypeBoost,
    get_scoring_config,
    reset_scoring_config,
)


class TestContentTypeBoost:

    def test_get_known_types(self):
        boost = ContentTypeBoost()
        assert boost.get("functions_classes") == pytest.approx(1.1)
        assert boost.get("fallback") == pytest.approx(1.0)
        assert boost.get("oversized_split") == pytest.approx(0.95)
        assert boost.get("simplified_code") == pytest.approx(0.8)

    def test_get_unknown_type_returns_1(self):
        boost = ContentTypeBoost()
        assert boost.get("nonexistent") == 1.0


class TestDensityPenalty:

    def test_high_density_no_penalty(self):
        config = ScoringConfig()
        score, _ = config.calculate_boosted_score(0.80, "fallback", information_density=0.5)
        # density 0.5 > threshold 0.1 → no penalty
        assert score == pytest.approx(0.80, abs=0.001)

    def test_zero_density_gets_floor_penalty(self):
        config = ScoringConfig()
        score, _ = config.calculate_boosted_score(0.80, "fallback", information_density=0.0)
        # density=0 → multiplier = floor (0.3)
        assert score == pytest.approx(0.80 * 0.3, abs=0.001)

    def test_mid_density_linear_interpolation(self):
        config = ScoringConfig()
        # density=0.05, threshold=0.1 → factor = 0.3 + 0.7 * (0.05/0.1) = 0.65
        score, _ = config.calculate_boosted_score(1.0, "fallback", information_density=0.05)
        assert score == pytest.approx(0.65, abs=0.001)

    def test_density_at_threshold_no_penalty(self):
        config = ScoringConfig()
        score, _ = config.calculate_boosted_score(0.80, "fallback", information_density=0.1)
        # At exactly threshold → no penalty branch
        assert score == pytest.approx(0.80, abs=0.001)

    def test_missing_density_gets_moderate_penalty(self):
        config = ScoringConfig()
        # information_density=-1 (not available) → missing_density_penalty=0.85
        score, _ = config.calculate_boosted_score(1.0, "fallback", information_density=-1.0)
        assert score == pytest.approx(0.85, abs=0.001)

    def test_missing_density_default(self):
        """Default call (no density arg) should apply missing density penalty."""
        config = ScoringConfig()
        score, _ = config.calculate_boosted_score(1.0, "fallback")
        # Default information_density=-1.0 → 0.85 penalty
        assert score == pytest.approx(0.85, abs=0.001)


class TestScoreCap:

    def test_score_capped_at_max(self):
        config = ScoringConfig()
        score, _ = config.calculate_boosted_score(0.99, "functions_classes", information_density=0.5)
        # 0.99 * 1.1 = 1.089, capped at 1.0
        assert score <= 1.0

    def test_custom_max_cap(self):
        config = ScoringConfig(max_score_cap=0.9)
        score, _ = config.calculate_boosted_score(0.85, "functions_classes", information_density=0.5)
        # 0.85 * 1.1 = 0.935, capped at 0.9
        assert score == pytest.approx(0.9, abs=0.001)


class TestScoringConfigDefaults:

    def test_default_values(self):
        config = ScoringConfig()
        assert config.min_relevance_score == pytest.approx(0.7)
        assert config.max_score_cap == pytest.approx(1.0)
        assert config.density_threshold == pytest.approx(0.1)
        assert config.density_floor == pytest.approx(0.3)
        assert config.ecosystem_mismatch_penalty == pytest.approx(0.2)
        assert config.max_chunks_per_source_file == 2
        assert config.oversized_chunk_threshold == 4000
        assert config.oversized_chunk_penalty == pytest.approx(0.5)
        assert config.missing_density_penalty == pytest.approx(0.85)


class TestSingleton:

    def test_singleton_returns_same_instance(self):
        reset_scoring_config()
        c1 = get_scoring_config()
        c2 = get_scoring_config()
        assert c1 is c2

    def test_reset_clears_singleton(self):
        c1 = get_scoring_config()
        reset_scoring_config()
        c2 = get_scoring_config()
        assert c1 is not c2


class TestPriorityAlwaysMedium:

    def test_priority_is_always_medium(self):
        config = ScoringConfig()
        for ct in ["functions_classes", "fallback", "oversized_split", "simplified_code", "unknown"]:
            _, priority = config.calculate_boosted_score(0.80, ct, information_density=0.5)
            assert priority == "MEDIUM"
