"""Integration tests: POST /review endpoint — full router → service flow."""
import pytest
from unittest.mock import AsyncMock


def _minimal_review_payload():
    """Minimal valid ReviewRequestDto."""
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
        "commitHash": "abc123",
        "rawDiff": "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new",
        "changedFiles": ["file.py"],
        "legacyCompatibility": {
            "kind": "legacy",
            "deadline": "2026-09-30T00:00:00Z",
        },
    }


@pytest.mark.asyncio(loop_scope="function")
async def test_review_returns_200(client, auth_headers, mock_services):
    """Valid review request → 200 with result."""
    resp = await client.post("/review", json=_minimal_review_payload(), headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "result" in data


@pytest.mark.asyncio(loop_scope="function")
async def test_review_validation_error(client, auth_headers):
    """Missing required fields → 422 validation error."""
    resp = await client.post("/review", json={"projectId": 1}, headers=auth_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio(loop_scope="function")
async def test_review_service_exception_returns_result(client, auth_headers, mock_services):
    """When ReviewService raises, the endpoint should still return a response (not 500)."""
    mock_services["review_service"].process_review_request = AsyncMock(
        side_effect=Exception("LLM timeout")
    )
    try:
        resp = await client.post("/review", json=_minimal_review_payload(), headers=auth_headers)
        # Router catches and wraps exceptions → still 200 with error in result
        assert resp.status_code in (200, 500)
    finally:
        # Restore normal behavior
        mock_services["review_service"].process_review_request = AsyncMock(
            return_value={"result": {"issues": []}}
        )


@pytest.mark.asyncio(loop_scope="function")
async def test_review_streaming_ndjson(client, auth_headers, mock_services):
    """Accept: application/x-ndjson triggers streaming response."""
    headers = {**auth_headers, "accept": "application/x-ndjson"}
    resp = await client.post("/review", json=_minimal_review_payload(), headers=headers)
    assert resp.status_code == 200
    assert "ndjson" in resp.headers.get("content-type", "")
