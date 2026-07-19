import asyncio
import time

import pytest

from service.rag.rag_client import RagClient


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _SlowSearchClient:
    async def post(self, *args, **kwargs):
        await asyncio.sleep(1)
        return _FakeResponse({"results": [{"text": "late"}]})


class _FastSearchClient:
    def __init__(self):
        self.started = 0

    async def post(self, *args, **kwargs):
        self.started += 1
        await asyncio.sleep(0.05)
        return _FakeResponse({
            "results": [
                {"text": "duplicate", "metadata": {"path": f"src/{self.started}.py"}}
            ]
        })


@pytest.mark.asyncio(loop_scope="function")
async def test_duplication_search_times_out_slow_queries(monkeypatch):
    monkeypatch.setenv("REVIEW_DUPLICATION_RAG_QUERY_TIMEOUT_SECONDS", "0.1")
    monkeypatch.setenv("REVIEW_DUPLICATION_RAG_QUERY_CONCURRENCY", "8")

    client = RagClient(base_url="http://rag", enabled=True)
    async def get_client():
        return _SlowSearchClient()
    client._get_client = get_client

    started = time.perf_counter()
    result = await client.search_for_duplicates(
        workspace="ws",
        project="proj",
        branch="main",
        queries=[f"find duplicate implementation {i}" for i in range(8)],
    )

    assert result == []
    assert time.perf_counter() - started < 0.5


@pytest.mark.asyncio(loop_scope="function")
async def test_duplication_search_runs_queries_concurrently(monkeypatch):
    monkeypatch.setenv("REVIEW_DUPLICATION_RAG_QUERY_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("REVIEW_DUPLICATION_RAG_QUERY_CONCURRENCY", "4")

    search_client = _FastSearchClient()
    client = RagClient(base_url="http://rag", enabled=True)
    async def get_client():
        return search_client
    client._get_client = get_client

    started = time.perf_counter()
    result = await client.search_for_duplicates(
        workspace="ws",
        project="proj",
        branch="main",
        queries=[f"find duplicate implementation {i}" for i in range(4)],
    )

    assert len(result) == 4
    assert all(item["_source"] == "duplication" for item in result)
    assert time.perf_counter() - started < 0.2
