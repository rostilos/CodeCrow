"""
Additional QA documentation endpoint integration tests.
Covers template modes, error flows, and edge cases.
"""
import pytest
from unittest.mock import AsyncMock, patch


def _full_qa_payload(**overrides):
    """Full QA documentation payload with all optional fields."""
    base = {
        "project_id": 1,
        "project_name": "test-project",
        "pr_number": 42,
        "issues_found": 3,
        "files_analyzed": 5,
        "ai_provider": "openai",
        "ai_model": "gpt-4",
        "ai_api_key": "sk-test",
        "diff": "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio(loop_scope="function")
async def test_qa_doc_with_all_fields(client, auth_headers):
    """QA doc with full payload including optional fields."""
    mock_svc = AsyncMock()
    mock_svc.generate = AsyncMock(return_value={
        "documentation": "## QA Report\nFull analysis",
        "documentation_needed": True,
    })
    with patch("api.routers.qa_documentation.QaDocumentationService", return_value=mock_svc):
        resp = await client.post("/qa-documentation", json=_full_qa_payload(
            pr_title="Add auth",
            pr_description="JWT implementation",
        ), headers=auth_headers)
        assert resp.status_code == 200


@pytest.mark.asyncio(loop_scope="function")
async def test_qa_doc_service_error(client, auth_headers):
    """Service raises → exception propagates (no try/except in endpoint)."""
    mock_svc = AsyncMock()
    mock_svc.generate = AsyncMock(side_effect=Exception("LLM unavailable"))
    with patch("api.routers.qa_documentation.QaDocumentationService", return_value=mock_svc):
        with pytest.raises(Exception, match="LLM unavailable"):
            await client.post("/qa-documentation", json=_full_qa_payload(), headers=auth_headers)


@pytest.mark.asyncio(loop_scope="function")
async def test_qa_doc_wrong_secret(client):
    """Wrong service secret → 401."""
    resp = await client.post(
        "/qa-documentation",
        json=_full_qa_payload(),
        headers={"x-service-secret": "wrong"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio(loop_scope="function")
async def test_qa_doc_missing_required_fields(client, auth_headers):
    """Partial payload with missing required fields → 422."""
    resp = await client.post(
        "/qa-documentation",
        json={"project_id": 1, "ai_provider": "openai"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio(loop_scope="function")
async def test_qa_doc_documentation_not_needed(client, auth_headers):
    """Service returns documentation_needed=False."""
    mock_svc = AsyncMock()
    mock_svc.generate = AsyncMock(return_value={
        "documentation": "",
        "documentation_needed": False,
    })
    with patch("api.routers.qa_documentation.QaDocumentationService", return_value=mock_svc):
        resp = await client.post("/qa-documentation", json=_full_qa_payload(), headers=auth_headers)
        assert resp.status_code == 200


@pytest.mark.asyncio(loop_scope="function")
async def test_qa_doc_different_providers(client, auth_headers):
    """QA doc works with different AI providers."""
    mock_svc = AsyncMock()
    mock_svc.generate = AsyncMock(return_value={
        "documentation": "Report",
        "documentation_needed": True,
    })
    for provider in ["openai", "anthropic", "google"]:
        with patch("api.routers.qa_documentation.QaDocumentationService", return_value=mock_svc):
            resp = await client.post(
                "/qa-documentation",
                json=_full_qa_payload(ai_provider=provider),
                headers=auth_headers,
            )
            assert resp.status_code == 200
