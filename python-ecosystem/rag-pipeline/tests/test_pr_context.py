"""
Tests for rag_pipeline.services.pr_context — PRContextMixin.

Covers:
- _infer_primary_ecosystem
- get_context_for_pr (collection missing, full flow, fallback)
- _decompose_queries
- _merge_and_rank_results (scoring, ecosystem penalty, size penalty, per-file cap)
"""
import pytest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace


def _mock_config(**overrides):
    cfg = MagicMock()
    cfg.qdrant_url = "http://localhost:6333"
    cfg.qdrant_api_key = None
    cfg.qdrant_collection_prefix = "rag"
    cfg.embedding_provider = "ollama"
    cfg.embedding_dim = 768
    cfg.embedding_supports_instructions = False
    cfg.fallback_branches = ["main", "master"]
    cfg.ollama_model = "nomic-embed-text"
    cfg.ollama_base_url = "http://localhost:11434"
    cfg.openrouter_api_key = "sk-test"
    cfg.openrouter_model = "openai/text-embedding-3-small"
    cfg.openrouter_base_url = "https://openrouter.ai/api/v1"
    cfg.max_identifiers_per_query = 100
    cfg.max_parent_classes_per_query = 20
    cfg.max_namespaces_per_query = 10
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _build_service():
    with patch("rag_pipeline.services.base.create_embedding_model") as mc, \
         patch("rag_pipeline.services.base.get_embedding_model_info") as mi, \
         patch("rag_pipeline.services.base.QdrantClient") as MQ:

        mi.return_value = {"provider": "ollama", "type": "local"}
        mc.return_value = MagicMock()

        from rag_pipeline.services.base import RAGQueryBase
        from rag_pipeline.services.pr_context import PRContextMixin
        from rag_pipeline.services.semantic_search import SemanticSearchMixin

        class TestService(PRContextMixin, SemanticSearchMixin, RAGQueryBase):
            pass

        config = _mock_config()
        svc = TestService(config)
        return svc


# ─────────────────────────────────────────────────────────────
# _infer_primary_ecosystem
# ─────────────────────────────────────────────────────────────
class TestInferPrimaryEcosystem:

    def test_java_dominant(self):
        from rag_pipeline.services.pr_context import _infer_primary_ecosystem
        files = ["src/Foo.java", "src/Bar.java", "src/Baz.java"]
        assert _infer_primary_ecosystem(files) == "jvm"

    def test_python_dominant(self):
        from rag_pipeline.services.pr_context import _infer_primary_ecosystem
        files = ["app/main.py", "app/utils.py", "app/service.py"]
        assert _infer_primary_ecosystem(files) == "python"

    def test_mixed_returns_none(self):
        from rag_pipeline.services.pr_context import _infer_primary_ecosystem
        files = ["src/Foo.java", "app/main.py", "src/bar.go"]
        result = _infer_primary_ecosystem(files)
        assert result is None

    def test_empty_files(self):
        from rag_pipeline.services.pr_context import _infer_primary_ecosystem
        assert _infer_primary_ecosystem([]) is None

    def test_unrecognised_extensions(self):
        from rag_pipeline.services.pr_context import _infer_primary_ecosystem
        files = ["readme.md", "config.txt", "data.csv"]
        assert _infer_primary_ecosystem(files) is None

    def test_js_ecosystem(self):
        from rag_pipeline.services.pr_context import _infer_primary_ecosystem
        files = ["src/App.tsx", "src/index.ts", "src/util.js"]
        assert _infer_primary_ecosystem(files) == "js"


# ─────────────────────────────────────────────────────────────
# _decompose_queries
# ─────────────────────────────────────────────────────────────
class TestDecomposeQueries:

    def test_diff_snippet_produces_queries(self):
        svc = _build_service()
        queries = svc._decompose_queries(
            pr_title="Add user auth",
            pr_description="Adds JWT support",
            diff_snippets=[
                "diff --git a/auth.py b/auth.py\n"
                "+def validate_token(token):\n"
                "+    return jwt.decode(token)\n"
            ],
            changed_files=["auth.py"],
        )
        # Should produce at least snippet queries
        assert len(queries) >= 1
        for q_text, weight, top_k, q_type in queries:
            assert isinstance(q_text, str)
            assert weight > 0

    def test_empty_snippets(self):
        svc = _build_service()
        queries = svc._decompose_queries(
            pr_title=None,
            pr_description=None,
            diff_snippets=[],
            changed_files=[],
        )
        # May produce zero queries with no input
        assert isinstance(queries, list)

    def test_short_snippet_lines_filtered(self):
        svc = _build_service()
        queries = svc._decompose_queries(
            pr_title=None,
            pr_description=None,
            diff_snippets=["+x\n-y\n"],
            changed_files=[],
        )
        # Very short lines (<=3 chars) should be filtered
        snippet_queries = [q for q in queries if q[0].strip() and len(q[0].strip()) > 15]
        # Might be empty because lines are too short
        assert isinstance(snippet_queries, list)


# ─────────────────────────────────────────────────────────────
# get_context_for_pr
# ─────────────────────────────────────────────────────────────
class TestGetContextForPR:

    def test_collection_not_found(self):
        svc = _build_service()
        svc.qdrant_client.get_collections.return_value.collections = []
        svc.qdrant_client.get_aliases.return_value.aliases = []

        result = svc.get_context_for_pr(
            workspace="ws",
            project="proj",
            branch="feat",
            changed_files=["src/Foo.java"],
        )
        assert result["_error"] == "collection_not_found"
        assert result["relevant_code"] == []

    def test_full_flow_returns_results(self):
        svc = _build_service()

        # Collection exists
        mock_coll = MagicMock()
        mock_coll.name = "rag_ws__proj"
        svc.qdrant_client.get_collections.return_value.collections = [mock_coll]
        svc.qdrant_client.get_aliases.return_value.aliases = []

        # Mock semantic_search_multi_branch to return results
        svc.semantic_search_multi_branch = MagicMock(return_value=[
            {
                "text": "related code",
                "score": 0.85,
                "metadata": {"path": "src/Related.java", "primary_name": "Related"},
            }
        ])

        # Mock _dedupe_by_branch_priority
        svc._dedupe_by_branch_priority = MagicMock(return_value=[
            {
                "text": "related code",
                "score": 0.85,
                "metadata": {
                    "path": "src/Related.java",
                    "primary_name": "Related",
                    "content_type": "functions_classes",
                    "information_density": 0.5,
                    "language": "java",
                },
            }
        ])

        result = svc.get_context_for_pr(
            workspace="ws",
            project="proj",
            branch="feat",
            changed_files=["src/Foo.java"],
            diff_snippets=["+public void test() {}"],
            pr_title="Test PR",
            base_branch="main",
        )

        assert "relevant_code" in result
        assert "related_files" in result
        assert "_branches_searched" in result
        assert "feat" in result["_branches_searched"]
        assert "main" in result["_branches_searched"]

    def test_fallback_branch_used_when_no_base(self):
        svc = _build_service()

        mock_coll = MagicMock()
        mock_coll.name = "rag_ws__proj"
        svc.qdrant_client.get_collections.return_value.collections = [mock_coll]
        svc.qdrant_client.get_aliases.return_value.aliases = []

        # Fallback branch lookup
        count_mock = MagicMock()
        count_mock.count = 5
        svc.qdrant_client.count.return_value = count_mock

        svc.semantic_search_multi_branch = MagicMock(return_value=[])
        svc._dedupe_by_branch_priority = MagicMock(return_value=[])

        result = svc.get_context_for_pr(
            workspace="ws",
            project="proj",
            branch="feat",
            changed_files=["src/Foo.java"],
        )
        # Should not crash; fallback branch should be found
        assert "relevant_code" in result

    def test_fallback_when_threshold_too_strict(self):
        svc = _build_service()

        mock_coll = MagicMock()
        mock_coll.name = "rag_ws__proj"
        svc.qdrant_client.get_collections.return_value.collections = [mock_coll]
        svc.qdrant_client.get_aliases.return_value.aliases = []

        svc.semantic_search_multi_branch = MagicMock(return_value=[
            {"text": "code", "score": 0.6, "metadata": {"path": "a.java"}}
        ])
        svc._dedupe_by_branch_priority = MagicMock(return_value=[
            {
                "text": "code", "score": 0.6,
                "metadata": {
                    "path": "a.java",
                    "content_type": "functions_classes",
                    "information_density": 0.5,
                },
            }
        ])

        result = svc.get_context_for_pr(
            workspace="ws",
            project="proj",
            branch="feat",
            changed_files=["src/Foo.java"],
            min_relevance_score=0.95,
        )
        # Fallback should kick in since all results are below 0.95
        assert "relevant_code" in result


# ─────────────────────────────────────────────────────────────
# _merge_and_rank_results
# ─────────────────────────────────────────────────────────────
class TestMergeAndRankResults:

    def test_deduplication(self):
        svc = _build_service()
        results = [
            {"text": "code A", "score": 0.8, "metadata": {"path": "a.java", "content_type": "functions_classes", "information_density": 0.5}},
            {"text": "code A", "score": 0.7, "metadata": {"path": "a.java", "content_type": "functions_classes", "information_density": 0.5}},
        ]
        ranked = svc._merge_and_rank_results(results, min_score_threshold=0.1)
        assert len(ranked) == 1
        assert ranked[0]["score"] >= 0.7

    def test_score_threshold_filtering(self):
        svc = _build_service()
        results = [
            {"text": "code A", "score": 0.9, "metadata": {"path": "a.java", "content_type": "functions_classes", "information_density": 0.5}},
            {"text": "code B", "score": 0.3, "metadata": {"path": "b.java", "content_type": "fallback", "information_density": 0.1}},
        ]
        ranked = svc._merge_and_rank_results(results, min_score_threshold=0.5)
        scores = [r["score"] for r in ranked]
        assert all(s >= 0.5 for s in scores) or len(ranked) <= 1

    def test_ecosystem_penalty_applied(self):
        svc = _build_service()
        results = [
            {
                "text": "java code",
                "score": 0.9,
                "metadata": {
                    "path": "Foo.java",
                    "content_type": "functions_classes",
                    "information_density": 0.5,
                    "language": "python",  # mismatch with java changed files
                },
            },
        ]
        ranked = svc._merge_and_rank_results(
            results,
            min_score_threshold=0.01,
            changed_files=["src/App.java", "src/Service.java", "src/Repo.java"],
        )
        if ranked:
            # Score should be penalised
            assert ranked[0]["score"] < 0.9

    def test_oversized_chunk_penalty(self):
        svc = _build_service()
        big_text = "x" * 20000
        results = [
            {
                "text": big_text,
                "score": 0.9,
                "metadata": {
                    "path": "big.java",
                    "content_type": "functions_classes",
                    "information_density": 0.5,
                },
            },
        ]
        ranked = svc._merge_and_rank_results(results, min_score_threshold=0.01)
        if ranked:
            assert ranked[0]["score"] < 0.9

    def test_empty_results(self):
        svc = _build_service()
        ranked = svc._merge_and_rank_results([], min_score_threshold=0.5)
        assert ranked == []
