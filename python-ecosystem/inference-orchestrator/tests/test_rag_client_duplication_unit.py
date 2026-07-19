import asyncio
import logging
import time

import pytest

from service.rag.rag_client import RagClient
import service.rag.rag_client as rag_client_module


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


class _RecordingSearchClient:
    def __init__(self):
        self.payloads = []

    async def post(self, *args, **kwargs):
        self.payloads.append(kwargs["json"])
        return _FakeResponse({"results": []})


def test_rag_client_initialization_does_not_log_url_credentials(caplog):
    credential = "RAG-CREDENTIAL-SENTINEL-44b1"
    caplog.set_level(logging.INFO, logger=rag_client_module.__name__)

    RagClient(
        base_url=f"https://rag-user:{credential}@rag.internal:8001",
        enabled=True,
    )

    assert credential not in caplog.text


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


@pytest.mark.asyncio(loop_scope="function")
async def test_exact_duplication_search_binds_execution_to_every_coordinate(monkeypatch):
    monkeypatch.setenv("REVIEW_DUPLICATION_RAG_QUERY_TIMEOUT_SECONDS", "1")
    recording = _RecordingSearchClient()
    client = RagClient(base_url="http://rag", enabled=True)

    async def get_client():
        return recording

    client._get_client = get_client
    snapshot = {
        "schema_version": 1,
        "base_sha": "a" * 40,
        "head_sha": "b" * 40,
        "merge_base_sha": "c" * 40,
        "parser_version": "parser-v1",
        "chunker_version": "chunker-v1",
        "embedding_version": "embedding-v1",
    }

    await client.search_for_duplicates(
        workspace="ws",
        project="proj",
        branch="feature",
        base_branch="main",
        queries=["find duplicate implementation"],
        snapshot=snapshot,
        execution_id="execution-1",
    )

    assert len(recording.payloads) == 2
    assert {payload["revision"] for payload in recording.payloads} == {
        snapshot["base_sha"], snapshot["head_sha"]
    }
    assert all(
        payload["execution_id"] == "execution-1"
        for payload in recording.payloads
    )
