"""
Additional integration tests for /review, /review/summarize, /review/ask endpoints.
Covers edge cases, streaming body verification, and more error flows.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock


def _minimal_review_payload(**overrides):
    base = {
        "projectId": 1,
        "projectVcsWorkspace": "ws",
        "projectVcsRepoSlug": "repo",
        "projectWorkspace": "ws",
        "projectNamespace": "ns",
        "aiProvider": "openai",
        "aiModel": "gpt-4",
        "aiApiKey": "sk-test",
        "pullRequestId": 42,
        "commitHash": "abc123",
        "rawDiff": "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new",
        "changedFiles": ["file.py"],
    }
    base.update(overrides)
    return base


def _minimal_summarize_payload(**overrides):
    base = {
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
    base.update(overrides)
    return base


def _minimal_ask_payload(**overrides):
    base = {
        "projectId": 1,
        "projectVcsWorkspace": "ws",
        "projectVcsRepoSlug": "repo",
        "projectWorkspace": "ws",
        "projectNamespace": "ns",
        "aiProvider": "openai",
        "aiModel": "gpt-4",
        "aiApiKey": "sk-test",
        "question": "What changed?",
    }
    base.update(overrides)
    return base


# ── /review edge cases ───────────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
async def test_review_no_auth(client):
    """Missing auth header → 401."""
    resp = await client.post("/review", json=_minimal_review_payload())
    assert resp.status_code == 401


@pytest.mark.asyncio(loop_scope="function")
async def test_review_wrong_secret(client):
    """Wrong secret → 401."""
    resp = await client.post(
        "/review",
        json=_minimal_review_payload(),
        headers={"x-service-secret": "wrong-secret"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio(loop_scope="function")
async def test_review_with_optional_fields(client, auth_headers, mock_services):
    """Review with optional fields like prTitle, prDescription, rules."""
    payload = _minimal_review_payload(
        prTitle="Add authentication",
        prDescription="Implements JWT-based auth for the API",
    )
    resp = await client.post("/review", json=payload, headers=auth_headers)
    assert resp.status_code == 200


@pytest.mark.asyncio(loop_scope="function")
async def test_review_empty_changed_files(client, auth_headers, mock_services):
    """Review with empty changedFiles list."""
    payload = _minimal_review_payload(changedFiles=[])
    resp = await client.post("/review", json=payload, headers=auth_headers)
    assert resp.status_code == 200


@pytest.mark.asyncio(loop_scope="function")
async def test_review_streaming_body_content(client, auth_headers, mock_services):
    """Streaming response body should contain NDJSON events."""
    headers = {**auth_headers, "accept": "application/x-ndjson"}
    resp = await client.post("/review", json=_minimal_review_payload(), headers=headers)
    assert resp.status_code == 200
    # Body should contain newline-delimited JSON
    body = resp.text
    assert len(body.strip()) > 0
    # At least one line should be valid JSON
    first_line = body.strip().split("\n")[0]
    parsed = json.loads(first_line)
    assert "type" in parsed or "result" in parsed or "status" in parsed or "error" in parsed


@pytest.mark.asyncio(loop_scope="function")
async def test_review_result_structure(client, auth_headers, mock_services):
    """Review result should have expected structure."""
    resp = await client.post("/review", json=_minimal_review_payload(), headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    # The mocked service returns {"result": {"issues": []}}
    assert "result" in data
    assert "issues" in data["result"]


# ── /review/summarize edge cases ─────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
async def test_summarize_no_auth(client):
    """Missing auth → 401."""
    resp = await client.post("/review/summarize", json=_minimal_summarize_payload())
    assert resp.status_code == 401


@pytest.mark.asyncio(loop_scope="function")
async def test_summarize_result_structure(client, auth_headers, mock_services):
    """Summarize result contains summary, diagram, diagramType."""
    resp = await client.post(
        "/review/summarize", json=_minimal_summarize_payload(), headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "summary" in data
    assert "diagram" in data
    assert "diagramType" in data


@pytest.mark.asyncio(loop_scope="function")
async def test_summarize_streaming_body_content(client, auth_headers, mock_services):
    """Streaming summarize response body check."""
    headers = {**auth_headers, "accept": "application/x-ndjson"}
    resp = await client.post(
        "/review/summarize", json=_minimal_summarize_payload(), headers=headers,
    )
    assert resp.status_code == 200
    body = resp.text
    assert len(body.strip()) > 0
    first_line = body.strip().split("\n")[0]
    parsed = json.loads(first_line)
    assert isinstance(parsed, dict)


@pytest.mark.asyncio(loop_scope="function")
async def test_summarize_with_different_providers(client, auth_headers, mock_services):
    """Summarize accepts different AI providers."""
    for provider in ["openai", "anthropic", "google"]:
        payload = _minimal_summarize_payload(aiProvider=provider, aiModel="test-model")
        resp = await client.post("/review/summarize", json=payload, headers=auth_headers)
        assert resp.status_code == 200


# ── /review/ask edge cases ───────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
async def test_ask_no_auth(client):
    """Missing auth → 401."""
    resp = await client.post("/review/ask", json=_minimal_ask_payload())
    assert resp.status_code == 401


@pytest.mark.asyncio(loop_scope="function")
async def test_ask_result_structure(client, auth_headers, mock_services):
    """Ask result contains answer."""
    resp = await client.post(
        "/review/ask", json=_minimal_ask_payload(), headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "answer" in data


@pytest.mark.asyncio(loop_scope="function")
async def test_ask_service_error(client, auth_headers, mock_services):
    """Service error returns error field."""
    mock_services["command_service"].process_ask = AsyncMock(
        side_effect=Exception("model overloaded")
    )
    try:
        resp = await client.post(
            "/review/ask", json=_minimal_ask_payload(), headers=auth_headers,
        )
        data = resp.json()
        assert data.get("error") is not None or resp.status_code == 500
    finally:
        mock_services["command_service"].process_ask = AsyncMock(return_value={"answer": "42"})


@pytest.mark.asyncio(loop_scope="function")
async def test_ask_streaming_body_content(client, auth_headers, mock_services):
    """Streaming ask response body check."""
    headers = {**auth_headers, "accept": "application/x-ndjson"}
    resp = await client.post(
        "/review/ask", json=_minimal_ask_payload(), headers=headers,
    )
    assert resp.status_code == 200
    body = resp.text
    assert len(body.strip()) > 0
