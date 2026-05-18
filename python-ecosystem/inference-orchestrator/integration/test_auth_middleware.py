"""Integration tests: ServiceSecretMiddleware auth enforcement."""
import pytest


@pytest.mark.asyncio(loop_scope="function")
async def test_protected_endpoint_rejected_without_secret(client):
    """POST /review without x-service-secret → 401."""
    resp = await client.post("/review", json={})
    assert resp.status_code == 401
    assert "service secret" in resp.json()["detail"].lower()


@pytest.mark.asyncio(loop_scope="function")
async def test_protected_endpoint_rejected_with_wrong_secret(client):
    """POST /review with wrong secret → 401."""
    resp = await client.post(
        "/review",
        json={},
        headers={"x-service-secret": "wrong-token"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio(loop_scope="function")
async def test_protected_endpoint_accepted_with_correct_secret(client, auth_headers):
    """POST /review with correct secret passes middleware (may fail on DTO validation → 422, not 401)."""
    resp = await client.post("/review", json={}, headers=auth_headers)
    # Should get past auth — either 422 (validation) or 200, not 401
    assert resp.status_code != 401


@pytest.mark.asyncio(loop_scope="function")
async def test_docs_public(client):
    """/docs is public (no auth required)."""
    resp = await client.get("/docs")
    assert resp.status_code == 200


@pytest.mark.asyncio(loop_scope="function")
async def test_openapi_public(client):
    """/openapi.json is public."""
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    data = resp.json()
    assert "paths" in data


@pytest.mark.asyncio(loop_scope="function")
async def test_summarize_rejected_without_secret(client):
    resp = await client.post("/review/summarize", json={})
    assert resp.status_code == 401


@pytest.mark.asyncio(loop_scope="function")
async def test_ask_rejected_without_secret(client):
    resp = await client.post("/review/ask", json={})
    assert resp.status_code == 401


@pytest.mark.asyncio(loop_scope="function")
async def test_qa_doc_rejected_without_secret(client):
    resp = await client.post("/qa-documentation", json={})
    assert resp.status_code == 401
