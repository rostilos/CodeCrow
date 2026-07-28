import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx

from service.rag.rag_client import RagClient, _http_error_detail


def test_empty_transport_error_names_timeout_and_target():
    request = httpx.Request("POST", "http://rag-pipeline:8001/index/pr-files")

    status, detail = _http_error_detail(httpx.ReadTimeout("", request=request))

    assert status is None
    assert detail == (
        "ReadTimeout for POST http://rag-pipeline:8001/index/pr-files"
    )


def test_pr_index_timeout_is_configurable(monkeypatch):
    monkeypatch.setenv("REVIEW_PR_INDEX_TIMEOUT_SECONDS", "321")
    response = MagicMock()
    response.json.return_value = {
        "status": "indexed",
        "chunks_indexed": 1,
        "files_processed": 1,
    }
    response.raise_for_status.return_value = None
    http_client = AsyncMock()
    http_client.post.return_value = response
    client = RagClient(base_url="http://rag-pipeline:8001", enabled=True)
    client._get_client = AsyncMock(return_value=http_client)

    result = asyncio.run(client.index_pr_files(
        "workspace",
        "project",
        227,
        "feature",
        [{"path": "a.py", "content": "print('ok')", "change_type": "MODIFIED"}],
    ))

    assert result["status"] == "indexed"
    assert http_client.post.await_args.kwargs["timeout"] == 321.0


def test_pr_index_timeout_default_covers_measured_large_overlay(monkeypatch):
    monkeypatch.delenv("REVIEW_PR_INDEX_TIMEOUT_SECONDS", raising=False)
    response = MagicMock()
    response.json.return_value = {
        "status": "indexed",
        "chunks_indexed": 1,
        "files_processed": 1,
    }
    response.raise_for_status.return_value = None
    http_client = AsyncMock()
    http_client.post.return_value = response
    client = RagClient(base_url="http://rag-pipeline:8001", enabled=True)
    client._get_client = AsyncMock(return_value=http_client)

    asyncio.run(client.index_pr_files(
        "workspace",
        "project",
        227,
        "feature",
        [{"path": "a.py", "content": "print('ok')", "change_type": "MODIFIED"}],
    ))

    assert http_client.post.await_args.kwargs["timeout"] == 1200.0
