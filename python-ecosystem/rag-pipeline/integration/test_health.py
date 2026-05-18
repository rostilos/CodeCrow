"""Integration tests: RAG pipeline health and system endpoints."""
import pytest


@pytest.mark.asyncio
async def test_root(client, auth_headers):
    resp = await client.get("/", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "CodeCrow RAG" in data.get("message", "")


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


@pytest.mark.asyncio
async def test_health_no_auth_needed(client):
    """Health endpoint should work without auth headers."""
    resp = await client.get("/health")
    assert resp.status_code == 200
