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
os.environ.setdefault("RAG_API_URL", "http://codecrow-rag-pipeline:8001")
os.environ.setdefault("SERVICE_SECRET", "test-secret-token")

from service.rag.rag_client import RagClient


@pytest.fixture
def rag_client():
    client = RagClient(base_url="http://codecrow-rag-pipeline:8001", enabled=True)
    yield client


# ── code search ───────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_search_code_sends_correct_payload(rag_client):
    route = respx.post("http://codecrow-rag-pipeline:8001/query/code-search").mock(
        return_value=httpx.Response(200, json={
            "results": [{
                "path": "a.py",
                "text": "class A: pass",
                "score": 12,
                "match_reasons": ["path_token:a"],
            }]
        })
    )
    result = await rag_client.search_code(
        query="class A",
        workspace="ws",
        project="proj",
        branch="feature/x",
        top_k=5,
        repository_revision="abc123",
        repository_generation_manifest_sha256="f" * 64,
        collection_target="cc_ws_proj_feature_x_generation",
    )
    assert route.called
    req_json = route.calls[0].request.read()
    import json
    payload = json.loads(req_json)
    assert payload["workspace"] == "ws"
    assert payload["branch"] == "feature/x"
    assert payload["limit"] == 5
    assert payload["repository_revision"] == "abc123"
    assert payload["repository_generation_manifest_sha256"] == "f" * 64
    assert payload["collection_target"] == "cc_ws_proj_feature_x_generation"

    assert result["results"][0]["match_reasons"] == ["path_token:a"]
    await rag_client.close()


@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_search_code_sends_service_secret_header(rag_client):
    route = respx.post("http://codecrow-rag-pipeline:8001/query/code-search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    await rag_client.search_code(
        query="class A", workspace="ws", project="p", branch="main"
    )
    assert route.called
    headers = route.calls[0].request.headers
    assert headers.get("x-service-secret") == "test-secret-token"
    await rag_client.close()


@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_search_code_handles_timeout(rag_client):
    respx.post("http://codecrow-rag-pipeline:8001/query/code-search").mock(
        side_effect=httpx.ReadTimeout("timed out")
    )
    result = await rag_client.search_code(
        query="class A", workspace="ws", project="p", branch="main"
    )
    assert result["results"] == []
    assert result["status"] == "error"
    await rag_client.close()


@pytest.mark.asyncio(loop_scope="function")
async def test_rag_client_disabled_returns_empty():
    client = RagClient(enabled=False)
    result = await client.search_code(
        query="class A", workspace="ws", project="p", branch="main"
    )
    assert result == {"results": []}
    await client.close()


# ── health ────────────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_is_healthy(rag_client):
    route = respx.get("http://codecrow-rag-pipeline:8001/health").mock(
        return_value=httpx.Response(200, json={"status": "healthy"})
    )
    result = await rag_client.is_healthy()
    assert route.called
    assert result is True
    await rag_client.close()


@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_is_healthy_failure(rag_client):
    respx.get("http://codecrow-rag-pipeline:8001/health").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    result = await rag_client.is_healthy()
    assert result is False
    await rag_client.close()
