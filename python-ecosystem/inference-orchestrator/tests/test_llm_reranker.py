"""
Unit tests for the LLMReranker — heuristic reranking logic.
Tests PR-file proximity boosting, directory matching, and penalty logic.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from service.rag.llm_reranker import LLMReranker, RerankResult


def make_result(path: str, score: float, text: str = "some code") -> dict:
    """Helper to create a mock RAG result."""
    return {
        "metadata": {"path": path},
        "text": text,
        "score": score,
    }


class TestHeuristicRerank:
    """Tests for _heuristic_rerank method."""

    def test_pr_file_boosted_highest(self):
        """Files that ARE in the changed files list should be boosted to the top."""
        reranker = LLMReranker()
        
        results = [
            make_result("app/code/Magezon/PageBuilder/Block/Widget.php", 0.85),
            make_result("app/code/Perspective/Akeneo/Model/VariantGroup.php", 0.75),
            make_result("app/code/Other/Module/Helper.php", 0.80),
        ]
        
        changed_files = [
            "app/code/Perspective/Akeneo/Model/VariantGroup.php",
            "app/code/Perspective/Akeneo/view/frontend/templates/selector.phtml",
        ]
        
        reranked = reranker._heuristic_rerank(results, changed_files)
        
        # VariantGroup.php should be first despite lower raw score (0.75)
        # because it's in the changed files list (1.5x boost → 1.125)
        assert reranked[0]["metadata"]["path"] == "app/code/Perspective/Akeneo/Model/VariantGroup.php"

    def test_same_directory_boosted(self):
        """Files in the same directory as changed files should be boosted."""
        reranker = LLMReranker()
        
        results = [
            make_result("app/code/Unrelated/Module/Service.php", 0.82),
            make_result("app/code/MyModule/Model/Helper.php", 0.78),
        ]
        
        changed_files = ["app/code/MyModule/Model/Product.php"]
        
        reranked = reranker._heuristic_rerank(results, changed_files)
        
        # Helper.php should be boosted above Service.php due to same directory (1.3x)
        assert reranked[0]["metadata"]["path"] == "app/code/MyModule/Model/Helper.php"

    def test_test_files_penalized(self):
        """Test files should be penalized."""
        reranker = LLMReranker()
        
        results = [
            make_result("tests/unit/TestHelper.php", 0.85),
            make_result("app/code/Module/Service.php", 0.80),
        ]
        
        reranked = reranker._heuristic_rerank(results, [])
        
        # Service.php should outrank the test file despite lower raw score
        assert reranked[0]["metadata"]["path"] == "app/code/Module/Service.php"

    def test_config_files_penalized(self):
        """Config files (.xml, .json, etc.) should be penalized."""
        reranker = LLMReranker()
        
        results = [
            make_result("app/code/Module/etc/di.xml", 0.90),
            make_result("app/code/Module/Model/Service.php", 0.80),
        ]
        
        reranked = reranker._heuristic_rerank(results, [])
        
        # Service.php should outrank the config file
        # xml: 0.90 * 0.7 = 0.63  vs  Service: 0.80
        assert reranked[0]["metadata"]["path"] == "app/code/Module/Model/Service.php"

    def test_empty_results(self):
        """Empty results should return empty."""
        reranker = LLMReranker()
        assert reranker._heuristic_rerank([], None) == []

    def test_no_changed_files(self):
        """Without changed files, should sort by raw score."""
        reranker = LLMReranker()
        
        results = [
            make_result("b.php", 0.90),
            make_result("a.php", 0.95),
        ]
        
        reranked = reranker._heuristic_rerank(results, None)
        assert reranked[0]["metadata"]["path"] == "a.php"

    def test_heuristic_score_annotated(self):
        """Reranked results should have _heuristic_score annotation."""
        reranker = LLMReranker()
        
        results = [make_result("a.php", 0.80)]
        reranked = reranker._heuristic_rerank(results, [])
        
        assert "_heuristic_score" in reranked[0]


class TestRerankEntryPoint:
    """Tests for the main rerank() method."""

    @pytest.mark.asyncio
    async def test_empty_results(self):
        """Empty input returns empty output with 'none' method."""
        reranker = LLMReranker()
        results, metadata = await reranker.rerank([])
        
        assert results == []
        assert metadata.method == "none"
        assert metadata.success is True

    @pytest.mark.asyncio
    async def test_heuristic_when_no_llm(self):
        """Without LLM client, should use heuristic method."""
        reranker = LLMReranker(llm_client=None)
        
        results = [
            make_result("a.php", 0.90),
            make_result("b.php", 0.80),
            make_result("c.php", 0.70),
            make_result("d.php", 0.60),
            make_result("e.php", 0.50),
        ]
        
        reranked, metadata = await reranker.rerank(results)
        
        assert metadata.method == "heuristic"
        assert metadata.success is True
        assert len(reranked) == 5

    @pytest.mark.asyncio
    async def test_heuristic_when_below_threshold(self):
        """Below threshold, should use heuristic even with LLM client."""
        mock_llm = MagicMock()
        reranker = LLMReranker(llm_client=mock_llm)
        
        # Only 3 results, below default threshold of 5
        results = [
            make_result("a.php", 0.90),
            make_result("b.php", 0.80),
            make_result("c.php", 0.70),
        ]
        
        reranked, metadata = await reranker.rerank(results)
        
        assert metadata.method == "heuristic"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
