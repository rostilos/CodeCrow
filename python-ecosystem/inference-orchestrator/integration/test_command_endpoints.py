"""Integration tests: POST /review/summarize and POST /review/ask."""
import pytest
from unittest.mock import AsyncMock


def _minimal_summarize_payload():
    return {
        "projectId": 1,
        "projectVcsWorkspace": "ws",
        "projectVcsRepoSlug": "repo",
        "projectWorkspace": "ws",
        "projectNamespace": "ns",
        "aiProvider": "openai",
        "aiModel": "gpt-4",
        "aiApiKey": "sk-test",
        "pullRequestId": 42,
    }


def _minimal_ask_payload():
    return {
        "projectId": 1,
        "projectVcsWorkspace": "ws",
        "projectVcsRepoSlug": "repo",
        "projectWorkspace": "ws",
        "projectNamespace": "ns",
        "aiProvider": "openai",
        "aiModel": "gpt-4",
        "aiApiKey": "sk-test",
        "question": "What does this PR change?",
    }


# ── /review/summarize ────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
async def test_summarize_success(client, auth_headers, mock_services):
    resp = await client.post("/review/summarize", json=_minimal_summarize_payload(), headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("summary") == "Test summary"
    assert data.get("diagramType") == "MERMAID"


@pytest.mark.asyncio(loop_scope="function")
async def test_summarize_validation_error(client, auth_headers):
    resp = await client.post("/review/summarize", json={"projectId": 1}, headers=auth_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio(loop_scope="function")
async def test_summarize_service_error(client, auth_headers, mock_services):
    mock_services["command_service"].process_summarize = AsyncMock(
        side_effect=Exception("timeout")
    )
    try:
        resp = await client.post("/review/summarize", json=_minimal_summarize_payload(), headers=auth_headers)
        data = resp.json()
        assert data.get("error") is not None or resp.status_code == 500
    finally:
        mock_services["command_service"].process_summarize = AsyncMock(
            return_value={"summary": "Test summary", "diagram": "graph LR; A-->B", "diagramType": "MERMAID"}
        )


@pytest.mark.asyncio(loop_scope="function")
async def test_summarize_streaming(client, auth_headers, mock_services):
    headers = {**auth_headers, "accept": "application/x-ndjson"}
    resp = await client.post("/review/summarize", json=_minimal_summarize_payload(), headers=headers)
    assert resp.status_code == 200
    assert "ndjson" in resp.headers.get("content-type", "")


# ── /review/ask ───────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
async def test_ask_success(client, auth_headers, mock_services):
    resp = await client.post("/review/ask", json=_minimal_ask_payload(), headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("answer") == "42"


@pytest.mark.asyncio(loop_scope="function")
async def test_ask_validation_error(client, auth_headers):
    resp = await client.post("/review/ask", json={"projectId": 1}, headers=auth_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio(loop_scope="function")
async def test_ask_streaming(client, auth_headers, mock_services):
    headers = {**auth_headers, "accept": "application/x-ndjson"}
    resp = await client.post("/review/ask", json=_minimal_ask_payload(), headers=headers)
    assert resp.status_code == 200
