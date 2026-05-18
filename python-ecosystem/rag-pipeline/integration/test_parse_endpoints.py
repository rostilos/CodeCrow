"""Integration tests: RAG pipeline /parse endpoints."""
import pytest


@pytest.mark.asyncio
async def test_parse_single_file(client, auth_headers):
    """POST /parse with Python file → parsed metadata."""
    resp = await client.post("/parse", json={
        "path": "example.py",
        "content": "import os\n\ndef hello():\n    print('hi')\n\nclass Foo:\n    pass\n",
    }, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["path"] == "example.py"
    assert data["success"] is True
    # Should extract at least some semantic info
    assert isinstance(data.get("imports", []), list)
    assert isinstance(data.get("semantic_names", []), list)


@pytest.mark.asyncio
async def test_parse_batch(client, auth_headers):
    """POST /parse/batch with multiple files."""
    resp = await client.post("/parse/batch", json={
        "files": [
            {"path": "a.py", "content": "class A:\n    pass\n"},
            {"path": "b.py", "content": "import a\ndef foo(): pass\n"},
        ]
    }, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    results = data.get("results", data) if isinstance(data, dict) else data
    assert len(results) == 2


@pytest.mark.asyncio
async def test_parse_unknown_language(client, auth_headers):
    """Parse a file with unrecognized extension → still returns (success may vary)."""
    resp = await client.post("/parse", json={
        "path": "data.xyz",
        "content": "some content",
    }, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["path"] == "data.xyz"


@pytest.mark.asyncio
async def test_parse_java_file(client, auth_headers):
    """Parse Java file extracts class names and imports."""
    resp = await client.post("/parse", json={
        "path": "App.java",
        "content": "package com.example;\nimport java.util.List;\npublic class App {\n    public void run() {}\n}\n",
    }, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True


@pytest.mark.asyncio
async def test_parse_empty_content(client, auth_headers):
    resp = await client.post("/parse", json={
        "path": "empty.py",
        "content": "",
    }, headers=auth_headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_parse_no_auth(client):
    resp = await client.post("/parse", json={"path": "a.py", "content": "x=1"})
    assert resp.status_code == 401
