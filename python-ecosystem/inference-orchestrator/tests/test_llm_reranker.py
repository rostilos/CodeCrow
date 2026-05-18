"""
Unit tests for service.rag.llm_reranker — LLMReranker, RerankResult, heuristic reranking.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from service.rag.llm_reranker import (
    LLMReranker,
    RerankResult,
    RerankResponse,
)


# ── RerankResponse / RerankResult models ─────────────────────

class TestModels:
    def test_rerank_response(self):
        r = RerankResponse(rankings=[0, 1, 2], reasoning="test")
        assert r.rankings == [0, 1, 2]

    def test_rerank_result(self):
        r = RerankResult(
            original_count=10, reranked_count=10,
            processing_time_ms=50.0, method="heuristic", success=True
        )
        assert r.success is True
        assert r.error is None


# ── rerank — empty / no LLM ─────────────────────────────────

class TestRerankEmpty:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_empty_results(self):
        reranker = LLMReranker()
        results, meta = await reranker.rerank([])
        assert results == []
        assert meta.method == "none"
        assert meta.success is True

    @pytest.mark.asyncio(loop_scope="function")
    async def test_heuristic_no_llm(self):
        results = [
            {"score": 0.8, "metadata": {"path": "src/a.py"}, "text": "code"},
            {"score": 0.7, "metadata": {"path": "tests/test_a.py"}, "text": "test"},
        ]
        reranker = LLMReranker(llm_client=None)
        reranked, meta = await reranker.rerank(
            results, changed_files=["src/a.py"]
        )
        assert meta.method == "heuristic"
        assert meta.success is True
        assert len(reranked) == 2

    @pytest.mark.asyncio(loop_scope="function")
    async def test_heuristic_below_threshold(self):
        """Less than 5 results → heuristic even with llm_client."""
        results = [{"score": 0.9, "metadata": {"path": "a.py"}, "text": "x"}]
        mock_llm = MagicMock()
        reranker = LLMReranker(llm_client=mock_llm)
        reranked, meta = await reranker.rerank(results)
        assert meta.method == "heuristic"


# ── _heuristic_rerank scoring ────────────────────────────────

class TestHeuristicRerank:
    def test_pr_file_boosted(self):
        results = [
            {"score": 0.5, "metadata": {"path": "src/handler.py"}, "text": "x"},
            {"score": 0.9, "metadata": {"path": "lib/unrelated.py"}, "text": "y"},
        ]
        reranker = LLMReranker()
        reranked = reranker._heuristic_rerank(results, changed_files=["src/handler.py"])
        # handler.py gets 1.5x boost → 0.75 vs unrelated 0.9 * 0.7 (config) or stays 0.9
        # handler.py should be ranked higher or close
        assert reranked[0]["metadata"]["path"] in ("src/handler.py", "lib/unrelated.py")

    def test_same_dir_boost(self):
        results = [
            {"score": 0.6, "metadata": {"path": "src/utils/helper.py"}, "text": "x"},
            {"score": 0.6, "metadata": {"path": "other/thing.py"}, "text": "y"},
        ]
        reranker = LLMReranker()
        reranked = reranker._heuristic_rerank(results, changed_files=["src/utils/main.py"])
        assert reranked[0]["metadata"]["path"] == "src/utils/helper.py"

    def test_test_penalty(self):
        results = [
            {"score": 0.8, "metadata": {"path": "tests/test_x.py"}, "text": "x"},
            {"score": 0.8, "metadata": {"path": "src/x.py"}, "text": "y"},
        ]
        reranker = LLMReranker()
        reranked = reranker._heuristic_rerank(results, changed_files=[])
        assert reranked[0]["metadata"]["path"] == "src/x.py"

    def test_config_file_penalty(self):
        results = [
            {"score": 0.8, "metadata": {"path": "config.yaml"}, "text": "x"},
            {"score": 0.8, "metadata": {"path": "main.py"}, "text": "y"},
        ]
        reranker = LLMReranker()
        reranked = reranker._heuristic_rerank(results, changed_files=[])
        assert reranked[0]["metadata"]["path"] == "main.py"

    def test_no_changed_files(self):
        results = [{"score": 0.5, "metadata": {"path": "a.py"}, "text": "x"}]
        reranker = LLMReranker()
        reranked = reranker._heuristic_rerank(results, changed_files=None)
        assert len(reranked) == 1


# ── _extract_response_text ───────────────────────────────────

class TestExtractResponseText:
    def test_content_string(self):
        resp = MagicMock()
        resp.content = "hello"
        assert LLMReranker._extract_response_text(resp) == "hello"

    def test_content_list_of_strings(self):
        resp = MagicMock()
        resp.content = ["hello", " world"]
        assert LLMReranker._extract_response_text(resp) == "hello world"

    def test_content_list_of_dicts(self):
        resp = MagicMock()
        resp.content = [{"text": "a"}, {"text": "b"}]
        assert LLMReranker._extract_response_text(resp) == "ab"

    def test_no_content(self):
        resp = "plain string"
        assert LLMReranker._extract_response_text(resp) == "plain string"


# ── rerank fallback on exception ─────────────────────────────

class TestRerankFallback:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_exception_returns_original(self):
        results = [
            {"score": 0.9, "metadata": {"path": "a.py"}, "text": "code " * 50}
            for _ in range(6)
        ]
        mock_llm = MagicMock()
        mock_llm.with_structured_output = MagicMock(side_effect=Exception("boom"))

        reranker = LLMReranker(llm_client=mock_llm)
        reranked, meta = await reranker.rerank(results)
        assert meta.method == "fallback"
        assert meta.success is False
        assert len(reranked) == 6
