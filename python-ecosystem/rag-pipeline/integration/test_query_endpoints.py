"""Integration tests: RAG pipeline /query endpoints."""
import pytest


@pytest.mark.asyncio
async def test_semantic_search(client, auth_headers):
    """POST /query/search → semantic results from mocked query_service."""
    resp = await client.post("/query/search", json={
        "query": "how does authentication work",
        "workspace": "ws1",
        "project": "proj1",
        "branch": "main",
        "top_k": 5,
    }, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "results" in data
    assert isinstance(data["results"], list)


@pytest.mark.asyncio
async def test_semantic_search_no_auth(client):
    resp = await client.post("/query/search", json={
        "query": "x",
        "workspace": "w",
        "project": "p",
        "branch": "main",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_semantic_search_validation_error(client, auth_headers):
    """Missing required field 'query'."""
    resp = await client.post("/query/search", json={
        "workspace": "w",
        "project": "p",
        "branch": "main",
    }, headers=auth_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_pr_context(client, auth_headers):
    """POST /query/pr-context → context dict from mocked query_service."""
    resp = await client.post("/query/pr-context", json={
        "workspace": "ws1",
        "project": "proj1",
        "branch": "feature/xyz",
        "changed_files": ["src/auth.py", "src/login.py"],
        "diff_snippets": ["+ def verify_token(tok):"],
        "pr_title": "Add JWT auth",
        "top_k": 10,
    }, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "context" in data
    ctx = data["context"]
    assert "relevant_code" in ctx


@pytest.mark.asyncio
async def test_pr_context_no_branch(client, auth_headers):
    """When branch is omitted / None, returns empty context with skip reason."""
    resp = await client.post("/query/pr-context", json={
        "workspace": "ws1",
        "project": "proj1",
        "changed_files": ["a.py"],
    }, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    ctx = data["context"]
    assert ctx["_metadata"]["skipped_reason"] == "branch_not_provided"
    assert ctx["_metadata"]["result_count"] == 0


@pytest.mark.asyncio
async def test_pr_context_with_pr_number(client, auth_headers):
    """Hybrid mode: when pr_number provided, should include PR-indexed results."""
    resp = await client.post("/query/pr-context", json={
        "workspace": "ws1",
        "project": "proj1",
        "branch": "feature/abc",
        "changed_files": ["main.py"],
        "pr_number": 42,
        "pr_title": "Fix bug",
    }, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "context" in data


@pytest.mark.asyncio
async def test_deterministic_context(client, auth_headers):
    """POST /query/deterministic → deterministic retrieval."""
    resp = await client.post("/query/deterministic", json={
        "workspace": "ws1",
        "project": "proj1",
        "branches": ["main"],
        "file_paths": ["src/module.py"],
        "limit_per_file": 5,
    }, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "context" in data


@pytest.mark.asyncio
async def test_deterministic_context_no_auth(client):
    resp = await client.post("/query/deterministic", json={
        "workspace": "ws1",
        "project": "proj1",
        "branches": ["main"],
        "file_paths": ["x.py"],
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_semantic_search_service_error(client, auth_headers, rag_app):
    """When query_service raises, endpoint returns 500."""
    import rag_pipeline.api.api as api_module
    original = api_module.query_service.semantic_search.side_effect
    api_module.query_service.semantic_search.side_effect = RuntimeError("oops")
    try:
        resp = await client.post("/query/search", json={
            "query": "fail", "workspace": "w", "project": "p", "branch": "main",
        }, headers=auth_headers)
        assert resp.status_code == 500
    finally:
        api_module.query_service.semantic_search.side_effect = original
