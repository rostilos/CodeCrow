"""Integration coverage for structural query endpoints."""

import pytest


SEARCH_REQUEST = {
    "query": "authentication handler",
    "workspace": "ws1",
    "project": "proj1",
    "branch": "main",
    "repository_revision": "revision-1",
    "repository_generation_manifest_sha256": "a" * 64,
    "collection_target": "generation-target",
    "limit": 5,
}

STRUCTURAL_BINDING = {
    "workspace": "ws1",
    "project": "proj1",
    "branch": "main",
    "repository_revision": "revision-1",
    "repository_generation_manifest_sha256": "a" * 64,
    "collection_target": "generation-target",
}


@pytest.mark.asyncio
async def test_structural_query_endpoints(client, auth_headers):
    relations = await client.post(
        "/query/relations",
        json={**STRUCTURAL_BINDING, "paths": ["src/module.py"]},
        headers=auth_headers,
    )
    graph = await client.post(
        "/query/graph",
        json={
            **STRUCTURAL_BINDING,
            "pattern": "relations_of",
            "target": "src/module.py",
        },
        headers=auth_headers,
    )
    unit = await client.post(
        "/query/unit",
        json={
            **STRUCTURAL_BINDING,
            "unit_id": "unit:authentication-handler",
        },
        headers=auth_headers,
    )

    assert relations.status_code == 200
    assert relations.json()["coverage"]["state"] == "complete"
    assert graph.status_code == 200
    assert unit.status_code == 200
    assert unit.json()["sourceEvidence"] is True


@pytest.mark.asyncio
async def test_code_search(client, auth_headers):
    response = await client.post(
        "/query/code-search",
        json=SEARCH_REQUEST,
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["results"][0]["path"] == "a.py"
    assert response.json()["results"][0]["metadata"]["source_evidence"] is True


@pytest.mark.asyncio
async def test_code_search_no_auth(client):
    response = await client.post("/query/code-search", json=SEARCH_REQUEST)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_code_search_requires_revision(client, auth_headers):
    request = dict(SEARCH_REQUEST)
    request.pop("repository_revision")
    response = await client.post(
        "/query/code-search", json=request, headers=auth_headers
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_code_search_service_error(client, auth_headers):
    import rag_pipeline.api.api as api_module

    reader = api_module.index_manager.open_reader.return_value.__enter__.return_value
    original = reader.search_units.side_effect
    reader.search_units.side_effect = RuntimeError("oops")
    try:
        response = await client.post(
            "/query/code-search",
            json=SEARCH_REQUEST,
            headers=auth_headers,
        )
        assert response.status_code == 500
    finally:
        reader.search_units.side_effect = original
