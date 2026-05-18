"""
Tests for rag_pipeline.api.routers.query — semantic_search, get_pr_context, get_deterministic_context.

Covers:
- semantic_search endpoint
- get_pr_context (branch missing, normal, hybrid mode with PR data, merging)
- _query_pr_indexed_data (scroll fallback, semantic query)
- _normalize_changed_file_candidates
- _fetch_direct_pr_file_chunks
- _format_pr_results
- _merge_pr_results
- get_deterministic_context endpoint
"""
import pytest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace
from fastapi import HTTPException


# ─────────────────────────────────────────────────────────────
# semantic_search
# ─────────────────────────────────────────────────────────────
class TestSemanticSearch:

    @patch("rag_pipeline.api.routers.query._get_singletons")
    def test_success(self, mock_get):
        _, qs = MagicMock(), MagicMock()
        qs.semantic_search.return_value = [{"text": "code", "score": 0.9}]
        mock_get.return_value = (_, qs)

        from rag_pipeline.api.routers.query import semantic_search

        req = MagicMock()
        req.query = "find user auth"
        req.workspace = "ws"
        req.project = "proj"
        req.branch = "main"
        req.top_k = 5
        req.filter_language = None

        result = semantic_search(req)
        assert "results" in result
        assert len(result["results"]) == 1

    @patch("rag_pipeline.api.routers.query._get_singletons")
    def test_error_raises_500(self, mock_get):
        _, qs = MagicMock(), MagicMock()
        qs.semantic_search.side_effect = RuntimeError("embed error")
        mock_get.return_value = (_, qs)

        from rag_pipeline.api.routers.query import semantic_search

        req = MagicMock()
        req.query = "test"
        req.workspace = "ws"
        req.project = "proj"
        req.branch = "main"
        req.top_k = 5
        req.filter_language = None

        with pytest.raises(HTTPException) as exc_info:
            semantic_search(req)
        assert exc_info.value.status_code == 500


# ─────────────────────────────────────────────────────────────
# _normalize_changed_file_candidates
# ─────────────────────────────────────────────────────────────
class TestNormalizeChangedFileCandidates:

    def test_strips_leading_slash(self):
        from rag_pipeline.api.routers.query import _normalize_changed_file_candidates
        result = _normalize_changed_file_candidates(["/src/Foo.java", "Bar.java"])
        assert "src/Foo.java" in result
        assert "/src/Foo.java" in result
        assert "Bar.java" in result

    def test_empty_list(self):
        from rag_pipeline.api.routers.query import _normalize_changed_file_candidates
        assert _normalize_changed_file_candidates([]) == []

    def test_none_input(self):
        from rag_pipeline.api.routers.query import _normalize_changed_file_candidates
        assert _normalize_changed_file_candidates(None) == []

    def test_empty_string_filtered(self):
        from rag_pipeline.api.routers.query import _normalize_changed_file_candidates
        result = _normalize_changed_file_candidates(["", "a.py"])
        assert "" not in result
        assert "a.py" in result


# ─────────────────────────────────────────────────────────────
# _format_pr_results
# ─────────────────────────────────────────────────────────────
class TestFormatPRResults:

    def test_formats_valid_results(self):
        from rag_pipeline.api.routers.query import _format_pr_results

        pt = SimpleNamespace(
            payload={"path": "src/Foo.java", "text": "code", "semantic_name": "Foo", "semantic_type": "class", "pr_branch": "feat"},
            score=0.85
        )
        result = _format_pr_results([pt])
        assert len(result) == 1
        assert result[0]["path"] == "src/Foo.java"
        assert result[0]["score"] == 0.85
        assert result[0]["_source"] == "pr_indexed"

    def test_skips_empty_text(self):
        from rag_pipeline.api.routers.query import _format_pr_results

        pt = SimpleNamespace(payload={"path": "a.java", "text": ""}, score=0.5)
        result = _format_pr_results([pt])
        assert result == []

    def test_skips_unknown_path(self):
        from rag_pipeline.api.routers.query import _format_pr_results

        pt = SimpleNamespace(payload={"path": "unknown", "text": "code"}, score=0.5)
        result = _format_pr_results([pt])
        assert result == []

    def test_forced_score_and_match_type(self):
        from rag_pipeline.api.routers.query import _format_pr_results

        pt = SimpleNamespace(payload={"path": "a.java", "text": "code"}, score=0.5)
        result = _format_pr_results([pt], forced_match_type="changed_file", forced_score=1.0)
        assert result[0]["score"] == 1.0
        assert result[0]["_match_type"] == "changed_file"


# ─────────────────────────────────────────────────────────────
# _merge_pr_results
# ─────────────────────────────────────────────────────────────
class TestMergePRResults:

    def test_deduplicates_by_path_and_text(self):
        from rag_pipeline.api.routers.query import _merge_pr_results

        a = [{"path": "a.java", "text": "code A"}]
        b = [{"path": "a.java", "text": "code A"}]
        result = _merge_pr_results(a, b)
        assert len(result) == 1

    def test_priority_first(self):
        from rag_pipeline.api.routers.query import _merge_pr_results

        a = [{"path": "a.java", "text": "priority", "score": 1.0}]
        b = [{"path": "a.java", "text": "semantic", "score": 0.8}]
        result = _merge_pr_results(a, b)
        assert len(result) == 2  # Different text, both kept

    def test_empty_inputs(self):
        from rag_pipeline.api.routers.query import _merge_pr_results
        assert _merge_pr_results([], []) == []
        assert _merge_pr_results(None, None) == []


# ─────────────────────────────────────────────────────────────
# get_pr_context endpoint
# ─────────────────────────────────────────────────────────────
class TestGetPRContext:

    @patch("rag_pipeline.api.routers.query._get_singletons")
    def test_branch_not_provided(self, mock_get):
        im, qs = MagicMock(), MagicMock()
        mock_get.return_value = (im, qs)

        from rag_pipeline.api.routers.query import get_pr_context

        req = MagicMock()
        req.branch = None
        req.changed_files = ["a.java"]

        result = get_pr_context(req)
        ctx = result["context"]
        assert ctx["_metadata"]["skipped_reason"] == "branch_not_provided"

    @patch("rag_pipeline.api.routers.query._get_singletons")
    def test_normal_flow(self, mock_get):
        im, qs = MagicMock(), MagicMock()
        qs.get_context_for_pr.return_value = {
            "relevant_code": [{"text": "code", "score": 0.9, "metadata": {"path": "a.java"}}],
            "related_files": ["a.java"],
            "changed_files": ["b.java"],
            "_branches_searched": ["feat", "main"],
        }
        mock_get.return_value = (im, qs)

        from rag_pipeline.api.routers.query import get_pr_context

        req = MagicMock()
        req.branch = "feat"
        req.workspace = "ws"
        req.project = "proj"
        req.changed_files = ["b.java"]
        req.diff_snippets = []
        req.pr_title = "Test"
        req.pr_description = None
        req.top_k = 10
        req.enable_priority_reranking = True
        req.min_relevance_score = 0.7
        req.base_branch = "main"
        req.deleted_files = []
        req.pr_number = None
        req.all_pr_changed_files = []

        result = get_pr_context(req)
        assert "context" in result
        assert result["context"]["_metadata"]["result_count"] == 1

    @patch("rag_pipeline.api.routers.query._query_pr_indexed_data")
    @patch("rag_pipeline.api.routers.query._get_singletons")
    def test_hybrid_mode_merges_pr_results(self, mock_get, mock_pr_query):
        im, qs = MagicMock(), MagicMock()
        qs.get_context_for_pr.return_value = {
            "relevant_code": [{"text": "branch code", "score": 0.8, "metadata": {"path": "b.java"}}],
            "related_files": [],
            "changed_files": [],
            "_branches_searched": ["feat"],
        }
        mock_get.return_value = (im, qs)
        mock_pr_query.return_value = [
            {"text": "pr code", "path": "a.java", "score": 1.0}
        ]

        from rag_pipeline.api.routers.query import get_pr_context

        req = MagicMock()
        req.branch = "feat"
        req.workspace = "ws"
        req.project = "proj"
        req.changed_files = ["a.java"]
        req.diff_snippets = []
        req.pr_title = "Test"
        req.pr_description = None
        req.top_k = 10
        req.enable_priority_reranking = True
        req.min_relevance_score = 0.7
        req.base_branch = "main"
        req.deleted_files = []
        req.pr_number = 42
        req.all_pr_changed_files = ["a.java"]

        result = get_pr_context(req)
        ctx = result["context"]
        assert ctx["_metadata"]["hybrid_mode"] is True
        assert ctx["_pr_chunks_count"] == 1

    @patch("rag_pipeline.api.routers.query._get_singletons")
    def test_error_raises_500(self, mock_get):
        im, qs = MagicMock(), MagicMock()
        qs.get_context_for_pr.side_effect = RuntimeError("err")
        mock_get.return_value = (im, qs)

        from rag_pipeline.api.routers.query import get_pr_context

        req = MagicMock()
        req.branch = "feat"
        req.workspace = "ws"
        req.project = "proj"
        req.changed_files = []
        req.diff_snippets = []
        req.pr_title = None
        req.pr_description = None
        req.top_k = 10
        req.enable_priority_reranking = True
        req.min_relevance_score = 0.7
        req.base_branch = None
        req.deleted_files = []
        req.pr_number = None
        req.all_pr_changed_files = []

        with pytest.raises(HTTPException):
            get_pr_context(req)


# ─────────────────────────────────────────────────────────────
# _query_pr_indexed_data
# ─────────────────────────────────────────────────────────────
class TestQueryPRIndexedData:

    @patch("rag_pipeline.api.routers.query._fetch_direct_pr_file_chunks")
    def test_scroll_fallback_when_no_queries(self, mock_fetch):
        from rag_pipeline.api.routers.query import _query_pr_indexed_data

        mock_fetch.return_value = []
        im = MagicMock()
        im._get_project_collection_name.return_value = "coll"
        im._collection_manager.collection_exists.return_value = True

        pt = SimpleNamespace(
            payload={"path": "a.java", "text": "pr code", "pr_branch": "feat"},
            score=None,
        )
        im.qdrant_client.scroll.return_value = ([pt], None)

        result = _query_pr_indexed_data(
            index_manager=im,
            workspace="ws",
            project="proj",
            pr_number=42,
            changed_files=["a.java"],
            query_texts=[],
            pr_title=None,
        )
        assert len(result) == 1

    @patch("rag_pipeline.api.routers.query._fetch_direct_pr_file_chunks")
    def test_semantic_query_when_text_provided(self, mock_fetch):
        from rag_pipeline.api.routers.query import _query_pr_indexed_data

        mock_fetch.return_value = []
        im = MagicMock()
        im._get_project_collection_name.return_value = "coll"
        im._collection_manager.collection_exists.return_value = True
        im.embed_model.get_text_embedding.return_value = [0.1] * 768

        pt = SimpleNamespace(
            payload={"path": "a.java", "text": "pr code", "pr_branch": "feat"},
            score=0.9,
        )
        im.qdrant_client.query_points.return_value = SimpleNamespace(points=[pt])

        result = _query_pr_indexed_data(
            index_manager=im,
            workspace="ws",
            project="proj",
            pr_number=42,
            changed_files=[],
            query_texts=["find auth logic"],
            pr_title="Auth PR",
        )
        assert len(result) == 1

    @patch("rag_pipeline.api.routers.query._fetch_direct_pr_file_chunks")
    def test_collection_not_found(self, mock_fetch):
        from rag_pipeline.api.routers.query import _query_pr_indexed_data

        im = MagicMock()
        im._get_project_collection_name.return_value = "coll"
        im._collection_manager.collection_exists.return_value = False

        result = _query_pr_indexed_data(
            index_manager=im, workspace="ws", project="proj",
            pr_number=42, changed_files=[], query_texts=[], pr_title=None,
        )
        assert result == []


# ─────────────────────────────────────────────────────────────
# get_deterministic_context endpoint
# ─────────────────────────────────────────────────────────────
class TestGetDeterministicContext:

    @patch("rag_pipeline.api.routers.query._get_singletons")
    def test_success(self, mock_get):
        _, qs = MagicMock(), MagicMock()
        qs.get_deterministic_context.return_value = {"chunks": [], "changed_files": {}}
        mock_get.return_value = (_, qs)

        from rag_pipeline.api.routers.query import get_deterministic_context

        req = MagicMock()
        req.workspace = "ws"
        req.project = "proj"
        req.branches = ["main"]
        req.file_paths = ["a.java"]
        req.limit_per_file = 10
        req.pr_number = None
        req.pr_changed_files = None
        req.additional_identifiers = None

        result = get_deterministic_context(req)
        assert "context" in result

    @patch("rag_pipeline.api.routers.query._get_singletons")
    def test_error_raises_500(self, mock_get):
        _, qs = MagicMock(), MagicMock()
        qs.get_deterministic_context.side_effect = RuntimeError("err")
        mock_get.return_value = (_, qs)

        from rag_pipeline.api.routers.query import get_deterministic_context

        req = MagicMock()
        req.workspace = "ws"
        req.project = "proj"
        req.branches = ["main"]
        req.file_paths = []
        req.limit_per_file = 10
        req.pr_number = None
        req.pr_changed_files = None
        req.additional_identifiers = None

        with pytest.raises(HTTPException):
            get_deterministic_context(req)
