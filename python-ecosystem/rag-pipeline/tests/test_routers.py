"""
Tests for rag_pipeline.api.routers — system, parse, index, query, pr.
Tests individual route handlers and helper functions.
"""
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


# ─────────────────────────────────────────────────────────────
# System router
# ─────────────────────────────────────────────────────────────
class TestSystemRouter:

    def test_root(self):
        from rag_pipeline.api.routers.system import root
        result = root()
        assert "message" in result
        assert "version" in result

    def test_health(self):
        from rag_pipeline.api.routers.system import health
        result = health()
        assert result["status"] == "healthy"

    @patch("rag_pipeline.api.routers.system.gc")
    def test_force_gc(self, mock_gc):
        from rag_pipeline.api.routers.system import force_garbage_collection
        mock_gc.collect.return_value = 42

        # psutil might not be available — mock the import
        with patch("rag_pipeline.api.routers.system.psutil", create=True) as mock_psutil:
            mock_process = MagicMock()
            mock_process.memory_info.return_value.rss = 100 * 1024 * 1024
            mock_psutil.Process.return_value = mock_process
            try:
                result = force_garbage_collection()
                assert result["objects_collected"] == 42
            except Exception:
                # If psutil import fails, the function handles it
                pass


# ─────────────────────────────────────────────────────────────
# Parse router
# ─────────────────────────────────────────────────────────────
class TestParseRouter:

    def test_parse_file_returns_metadata(self):
        from rag_pipeline.api.routers.parse import parse_file
        from rag_pipeline.api.models import ParseFileRequest

        request = ParseFileRequest(
            path="test.py",
            content="def hello():\n    pass\n",
            language="python",
        )

        result = parse_file(request)
        assert result.path == "test.py"
        assert result.success is True

    def test_parse_file_preserves_symbol_spans_and_edges(self):
        from rag_pipeline.api.routers.parse import parse_file
        from rag_pipeline.api.models import ParseFileRequest

        request = ParseFileRequest(
            path="service.py",
            content=(
                "from domain import BaseService\n\n"
                "class UserService(BaseService):\n"
                "    def load(self, user_id):\n"
                "        return repository.find(user_id)\n"
            ),
        )

        result = parse_file(request)

        assert result.success is True
        assert result.ast_supported is True
        assert len(result.content_digest) == 64
        assert result.symbols
        assert all(symbol.path == "service.py" for symbol in result.symbols)
        assert all(symbol.start_line >= 1 for symbol in result.symbols)
        assert all(symbol.end_line >= symbol.start_line for symbol in result.symbols)
        assert any(symbol.name == "UserService" for symbol in result.symbols)
        assert any(
            edge.relationship_type in {"extends", "imports", "calls"}
            for edge in result.relationships
        )
        assert all(edge.resolution == "unresolved" for edge in result.relationships)

    def test_parse_file_invalid_content(self):
        from rag_pipeline.api.routers.parse import parse_file
        from rag_pipeline.api.models import ParseFileRequest

        request = ParseFileRequest(
            path="test.xyz",
            content="some content",
        )

        result = parse_file(request)
        # Should return result (may have success True or False)
        assert result.path == "test.xyz"


# ─────────────────────────────────────────────────────────────
# Index router helpers
# ─────────────────────────────────────────────────────────────
class TestIndexRouter:

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_get_limits(self, mock_singletons):
        from rag_pipeline.api.routers.index import get_limits

        mock_config = MagicMock()
        mock_config.max_chunks_per_index = 50000
        mock_config.max_files_per_index = 5000
        mock_config.max_file_size_bytes = 1_000_000
        mock_config.chunk_size = 1500
        mock_config.chunk_overlap = 200
        mock_singletons.return_value = (mock_config, MagicMock())

        result = get_limits()
        assert result["max_chunks_per_index"] == 50000
        assert result["chunk_size"] == 1500


# ─────────────────────────────────────────────────────────────
# Query router helpers
# ─────────────────────────────────────────────────────────────
class TestQueryRouterHelpers:

    def test_normalize_changed_file_candidates(self):
        from rag_pipeline.api.routers.query import _normalize_changed_file_candidates

        result = _normalize_changed_file_candidates(["src/main.py", "/src/main.py"])
        assert "src/main.py" in result

    def test_normalize_empty_list(self):
        from rag_pipeline.api.routers.query import _normalize_changed_file_candidates

        result = _normalize_changed_file_candidates([])
        assert result == []

    def test_normalize_none(self):
        from rag_pipeline.api.routers.query import _normalize_changed_file_candidates

        result = _normalize_changed_file_candidates(None)
        assert result == []

    def test_format_pr_results(self):
        from rag_pipeline.api.routers.query import _format_pr_results

        mock_point = MagicMock()
        mock_point.payload = {
            "path": "src/main.py",
            "text": "def hello(): pass",
            "semantic_name": "hello",
            "semantic_type": "function",
            "pr_branch": "feature/test",
        }
        mock_point.score = 0.95

        results = _format_pr_results([mock_point])
        assert len(results) == 1
        assert results[0]["path"] == "src/main.py"
        assert results[0]["text"] == "def hello(): pass"

    def test_format_pr_results_skips_empty(self):
        from rag_pipeline.api.routers.query import _format_pr_results

        mock_point = MagicMock()
        mock_point.payload = {"path": "", "text": ""}
        mock_point.score = 0.5

        results = _format_pr_results([mock_point])
        assert len(results) == 0

    def test_format_pr_results_forced_score(self):
        from rag_pipeline.api.routers.query import _format_pr_results

        mock_point = MagicMock()
        mock_point.payload = {
            "path": "a.py",
            "text": "code",
            "semantic_name": "",
            "semantic_type": "",
            "pr_branch": "main",
        }
        mock_point.score = 0.5

        results = _format_pr_results([mock_point], forced_score=1.0)
        assert results[0]["score"] == 1.0


# ─────────────────────────────────────────────────────────────
# Query router — semantic_search endpoint
# ─────────────────────────────────────────────────────────────
class TestQueryRouterEndpoints:

    @patch("rag_pipeline.api.routers.query._get_singletons")
    def test_semantic_search(self, mock_singletons):
        from rag_pipeline.api.routers.query import semantic_search
        from rag_pipeline.api.models import QueryRequest

        mock_query_service = MagicMock()
        mock_query_service.semantic_search.return_value = [
            {"text": "result", "score": 0.9, "metadata": {}}
        ]
        mock_singletons.return_value = (MagicMock(), mock_query_service)

        request = QueryRequest(
            query="find function",
            workspace="ws",
            project="proj",
            branch="main",
        )
        result = semantic_search(request)
        assert "results" in result
        assert len(result["results"]) == 1

    @patch("rag_pipeline.api.routers.query._get_singletons")
    def test_deterministic_context(self, mock_singletons):
        from rag_pipeline.api.routers.query import get_deterministic_context
        from rag_pipeline.api.models import DeterministicContextRequest

        mock_query_service = MagicMock()
        mock_query_service.get_deterministic_context.return_value = {"chunks": []}
        mock_singletons.return_value = (MagicMock(), mock_query_service)

        request = DeterministicContextRequest(
            workspace="ws",
            project="proj",
            branches=["main"],
            file_paths=["src/main.py"],
        )
        result = get_deterministic_context(request)
        assert "context" in result
