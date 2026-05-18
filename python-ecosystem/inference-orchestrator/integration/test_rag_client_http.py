"""
Integration tests: RagClient HTTP calls via respx.

Tests the real RagClient code making actual HTTP calls,
intercepted by respx to verify correct URLs, headers, payloads.
"""
import os
import pytest
import respx
import httpx
from unittest.mock import patch

os.environ.setdefault("RAG_ENABLED", "true")
os.environ.setdefault("RAG_API_URL", "http://rag-pipeline:8001")
os.environ.setdefault("SERVICE_SECRET", "test-secret-token")

from service.rag.rag_client import RagClient


@pytest.fixture
def rag_client():
    client = RagClient(base_url="http://rag-pipeline:8001", enabled=True)
    yield client


# ── get_pr_context ────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_get_pr_context_sends_correct_payload(rag_client):
    route = respx.post("http://rag-pipeline:8001/query/pr-context").mock(
        return_value=httpx.Response(200, json={
            "context": {
                "relevant_code": [{"path": "a.py", "content": "class A: pass"}],
                "related_files": [],
                "_metadata": {"result_count": 1},
            }
        })
    )
    result = await rag_client.get_pr_context(
        workspace="ws",
        project="proj",
        branch="feature/x",
        changed_files=["a.py"],
        diff_snippets=["def foo():"],
        pr_title="Add foo",
    )
    assert route.called
    req_json = route.calls[0].request.read()
    import json
    payload = json.loads(req_json)
    assert payload["workspace"] == "ws"
    assert payload["branch"] == "feature/x"
    assert "a.py" in payload["changed_files"]

    assert "relevant_code" in result.get("context", {})
    await rag_client.close()


@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_get_pr_context_sends_service_secret_header(rag_client):
    route = respx.post("http://rag-pipeline:8001/query/pr-context").mock(
        return_value=httpx.Response(200, json={"context": {"relevant_code": []}})
    )
    await rag_client.get_pr_context(
        workspace="ws", project="p", branch="main", changed_files=["a.py"]
    )
    assert route.called
    headers = route.calls[0].request.headers
    assert headers.get("x-service-secret") == "test-secret-token"
    await rag_client.close()


@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_get_pr_context_handles_timeout(rag_client):
    respx.post("http://rag-pipeline:8001/query/pr-context").mock(
        side_effect=httpx.ReadTimeout("timed out")
    )
    result = await rag_client.get_pr_context(
        workspace="ws", project="p", branch="main", changed_files=["a.py"]
    )
    # Should return empty context on failure, not raise
    assert isinstance(result, dict)
    await rag_client.close()


@pytest.mark.asyncio(loop_scope="function")
async def test_rag_client_disabled_returns_empty():
    client = RagClient(enabled=False)
    result = await client.get_pr_context(
        workspace="ws", project="p", branch="main", changed_files=["a.py"]
    )
    assert result == {"context": {"relevant_code": []}}
    await client.close()


# ── search ────────────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_semantic_search_sends_correct_payload(rag_client):
    route = respx.post("http://rag-pipeline:8001/query/search").mock(
        return_value=httpx.Response(200, json={"results": [{"path": "b.py", "score": 0.9}]})
    )
    result = await rag_client.semantic_search(
        query="def bar()",
        workspace="ws", project="p", branch="main",
        top_k=5,
    )
    assert route.called
    assert isinstance(result, dict)
    assert "results" in result
    await rag_client.close()


# ── health ────────────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_is_healthy(rag_client):
    route = respx.get("http://rag-pipeline:8001/health").mock(
        return_value=httpx.Response(200, json={"status": "healthy"})
    )
    result = await rag_client.is_healthy()
    assert route.called
    assert result is True
    await rag_client.close()


@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_is_healthy_failure(rag_client):
    respx.get("http://rag-pipeline:8001/health").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    result = await rag_client.is_healthy()
    assert result is False
    await rag_client.close()
