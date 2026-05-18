"""Integration tests: RAG pipeline ServiceSecretMiddleware."""
import pytest


@pytest.mark.asyncio
async def test_search_rejected_without_secret(client):
    resp = await client.post("/query/search", json={
        "query": "test", "workspace": "ws", "project": "p", "branch": "main"
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_search_accepted_with_secret(client, auth_headers):
    resp = await client.post("/query/search", json={
        "query": "test", "workspace": "ws", "project": "p", "branch": "main"
    }, headers=auth_headers)
    assert resp.status_code != 401


@pytest.mark.asyncio
async def test_index_rejected_without_secret(client):
    resp = await client.post("/index/repo", json={})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_parse_rejected_without_secret(client):
    resp = await client.post("/parse", json={"path": "a.py", "content": "x=1"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_wrong_secret(client):
    resp = await client.post("/query/search", json={
        "query": "test", "workspace": "ws", "project": "p", "branch": "main"
    }, headers={"x-service-secret": "wrong"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_docs_public(client):
    resp = await client.get("/docs")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_openapi_public(client):
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    assert "paths" in resp.json()
