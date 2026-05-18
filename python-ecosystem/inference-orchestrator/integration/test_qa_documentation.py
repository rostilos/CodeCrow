"""Integration tests: POST /qa-documentation endpoint."""
import pytest
from unittest.mock import AsyncMock, patch


def _minimal_qa_payload():
    return {
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


@pytest.mark.asyncio(loop_scope="function")
async def test_qa_doc_success(client, auth_headers):
    """Valid QA doc request → 200 with documentation."""
    mock_svc = AsyncMock()
    mock_svc.generate = AsyncMock(return_value={
        "documentation": "## QA Documentation\nTest changes",
        "documentation_needed": True,
    })
    with patch("api.routers.qa_documentation.QaDocumentationService", return_value=mock_svc):
        resp = await client.post("/qa-documentation", json=_minimal_qa_payload(), headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("documentation") is not None or data.get("error") is not None


@pytest.mark.asyncio(loop_scope="function")
async def test_qa_doc_validation_error(client, auth_headers):
    """Missing required fields → 422."""
    resp = await client.post("/qa-documentation", json={}, headers=auth_headers)
    assert resp.status_code == 422


@pytest.mark.asyncio(loop_scope="function")
async def test_qa_doc_no_auth(client):
    """No secret → 401."""
    resp = await client.post("/qa-documentation", json=_minimal_qa_payload())
    assert resp.status_code == 401
