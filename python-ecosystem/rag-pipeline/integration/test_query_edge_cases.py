"""
Additional integration tests for /query endpoints — edge cases & error flows.
Complements test_query_endpoints.py for deeper coverage.
"""
import pytest
from unittest.mock import MagicMock


@pytest.mark.asyncio
class TestSemanticSearchEdgeCases:
    """Additional semantic search scenarios."""

    async def test_search_with_language_filter(self, client, auth_headers, rag_app):
        """Semantic search with filter_language passes it to service."""
        import rag_pipeline.api.api as api_module
        api_module.query_service.semantic_search.return_value = [
            {"path": "a.py", "content": "pass", "score": 0.9}
        ]

        resp = await client.post("/query/search", json={
            "query": "authentication handler",
            "workspace": "ws1",
            "project": "proj1",
            "branch": "main",
            "top_k": 3,
            "filter_language": "python",
        }, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["results"]) == 1

    async def test_search_empty_results(self, client, auth_headers, rag_app):
        """Service returns empty list → valid empty response."""
        import rag_pipeline.api.api as api_module
        api_module.query_service.semantic_search.return_value = []

        resp = await client.post("/query/search", json={
            "query": "nonexistent",
            "workspace": "ws1",
            "project": "proj1",
            "branch": "main",
        }, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["results"] == []

    async def test_search_missing_workspace(self, client, auth_headers):
        """Missing required field workspace → 422."""
        resp = await client.post("/query/search", json={
            "query": "test",
            "project": "p",
            "branch": "main",
        }, headers=auth_headers)
        assert resp.status_code == 422

    async def test_search_large_top_k(self, client, auth_headers, rag_app):
        """Large top_k is accepted and forwarded to service."""
        import rag_pipeline.api.api as api_module
        api_module.query_service.semantic_search.return_value = []

        resp = await client.post("/query/search", json={
            "query": "x",
            "workspace": "w",
            "project": "p",
            "branch": "main",
            "top_k": 200,
        }, headers=auth_headers)
        assert resp.status_code == 200


@pytest.mark.asyncio
class TestPRContextEdgeCases:
    """Additional PR context scenarios."""

    async def test_pr_context_with_all_optional_fields(self, client, auth_headers, rag_app):
        """Full request with all optional fields."""
        import rag_pipeline.api.api as api_module
        api_module.query_service.get_context_for_pr.return_value = {
            "relevant_code": [{"path": "a.py", "content": "x"}],
            "related_files": [],
        }

        resp = await client.post("/query/pr-context", json={
            "workspace": "ws1",
            "project": "proj1",
            "branch": "feature/x",
            "base_branch": "main",
            "changed_files": ["src/auth.py"],
            "diff_snippets": ["+ new line"],
            "pr_title": "Title",
            "pr_description": "Long description",
            "top_k": 20,
            "enable_priority_reranking": True,
            "min_relevance_score": 0.5,
            "deleted_files": ["old.py"],
            "pr_number": 100,
            "all_pr_changed_files": ["src/auth.py", "src/login.py"],
        }, headers=auth_headers)
        assert resp.status_code == 200
        ctx = resp.json()["context"]
        assert "_metadata" in ctx
        assert ctx["_metadata"]["hybrid_mode"] is True
        assert ctx["_metadata"]["pr_number"] == 100

    async def test_pr_context_service_error(self, client, auth_headers, rag_app):
        """Service exception → 500."""
        import rag_pipeline.api.api as api_module
        original = api_module.query_service.get_context_for_pr.side_effect
        api_module.query_service.get_context_for_pr.side_effect = RuntimeError("service down")
        try:
            resp = await client.post("/query/pr-context", json={
                "workspace": "w",
                "project": "p",
                "branch": "b",
                "changed_files": [],
            }, headers=auth_headers)
            assert resp.status_code == 500
        finally:
            api_module.query_service.get_context_for_pr.side_effect = original

    async def test_pr_context_no_diff_snippets(self, client, auth_headers, rag_app):
        """PR context without diff_snippets."""
        import rag_pipeline.api.api as api_module
        api_module.query_service.get_context_for_pr.return_value = {
            "relevant_code": [],
            "related_files": [],
        }

        resp = await client.post("/query/pr-context", json={
            "workspace": "ws1",
            "project": "proj1",
            "branch": "dev",
            "changed_files": ["x.py"],
        }, headers=auth_headers)
        assert resp.status_code == 200
        ctx = resp.json()["context"]
        assert ctx["_metadata"]["hybrid_mode"] is False

    async def test_pr_context_empty_changed_files(self, client, auth_headers, rag_app):
        """Empty changed_files list is valid."""
        import rag_pipeline.api.api as api_module
        api_module.query_service.get_context_for_pr.return_value = {
            "relevant_code": [],
            "related_files": [],
        }

        resp = await client.post("/query/pr-context", json={
            "workspace": "ws1",
            "project": "proj1",
            "branch": "main",
            "changed_files": [],
        }, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["context"]["_metadata"]["changed_files_count"] == 0


@pytest.mark.asyncio
class TestDeterministicContextEdgeCases:
    """Additional deterministic context scenarios."""

    async def test_deterministic_with_pr_number(self, client, auth_headers, rag_app):
        """Deterministic context with PR-specific filtering."""
        import rag_pipeline.api.api as api_module
        api_module.query_service.get_deterministic_context.return_value = {
            "files": [{"path": "a.py", "definitions": []}],
            "definitions": [],
        }

        resp = await client.post("/query/deterministic", json={
            "workspace": "ws1",
            "project": "proj1",
            "branches": ["main", "dev"],
            "file_paths": ["src/auth.py", "src/login.py"],
            "limit_per_file": 10,
            "pr_number": 42,
            "pr_changed_files": ["src/auth.py"],
            "additional_identifiers": ["verify_token", "User"],
        }, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "context" in data

    async def test_deterministic_service_error(self, client, auth_headers, rag_app):
        """Service error → 500."""
        import rag_pipeline.api.api as api_module
        original = api_module.query_service.get_deterministic_context.side_effect
        api_module.query_service.get_deterministic_context.side_effect = RuntimeError("fail")
        try:
            resp = await client.post("/query/deterministic", json={
                "workspace": "w",
                "project": "p",
                "branches": ["main"],
                "file_paths": ["a.py"],
            }, headers=auth_headers)
            assert resp.status_code == 500
        finally:
            api_module.query_service.get_deterministic_context.side_effect = original

    async def test_deterministic_multiple_branches(self, client, auth_headers, rag_app):
        """Multiple branches in deterministic context."""
        import rag_pipeline.api.api as api_module
        api_module.query_service.get_deterministic_context.return_value = {
            "files": [], "definitions": []
        }

        resp = await client.post("/query/deterministic", json={
            "workspace": "ws1",
            "project": "proj1",
            "branches": ["main", "develop", "release/1.0"],
            "file_paths": ["src/core.py"],
        }, headers=auth_headers)
        assert resp.status_code == 200

    async def test_deterministic_empty_file_paths(self, client, auth_headers, rag_app):
        """Empty file_paths is still a valid request."""
        import rag_pipeline.api.api as api_module
        api_module.query_service.get_deterministic_context.return_value = {
            "files": [], "definitions": []
        }

        resp = await client.post("/query/deterministic", json={
            "workspace": "ws1",
            "project": "proj1",
            "branches": ["main"],
            "file_paths": [],
        }, headers=auth_headers)
        assert resp.status_code == 200
