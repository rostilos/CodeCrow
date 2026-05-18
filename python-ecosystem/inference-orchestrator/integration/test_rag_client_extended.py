"""
Additional RagClient HTTP integration tests via respx.
Covers: search_for_duplicates, get_deterministic_context, index_pr_files, delete_pr_files.
"""
import os
import json
import pytest
import respx
import httpx

os.environ.setdefault("RAG_ENABLED", "true")
os.environ.setdefault("RAG_API_URL", "http://rag-pipeline:8001")
os.environ.setdefault("SERVICE_SECRET", "test-secret-token")

from service.rag.rag_client import RagClient


@pytest.fixture
def rag_client():
    client = RagClient(base_url="http://rag-pipeline:8001", enabled=True)
    yield client


# ── search_for_duplicates ────────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_search_for_duplicates_sends_correct_payloads(rag_client):
    """Each query produces a separate POST to /query/search."""
    route = respx.post("http://rag-pipeline:8001/query/search").mock(
        return_value=httpx.Response(200, json={
            "results": [{"path": "dup.py", "text": "x = 1", "score": 0.8}]
        })
    )
    results = await rag_client.search_for_duplicates(
        workspace="ws", project="proj", branch="main",
        queries=["def validate_input(data):", "class UserValidator:"],
        top_k=5,
    )
    # Should make 2 calls (one per query)
    assert route.call_count == 2
    # Each result tagged with _source="duplication"
    assert len(results) == 2
    assert all(r["_source"] == "duplication" for r in results)
    assert all("_query" in r for r in results)
    await rag_client.close()


@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_search_for_duplicates_skips_short_queries(rag_client):
    """Queries shorter than 10 chars are skipped."""
    route = respx.post("http://rag-pipeline:8001/query/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    results = await rag_client.search_for_duplicates(
        workspace="ws", project="proj", branch="main",
        queries=["tiny", "small", "this query is long enough to be sent"],
    )
    # Only the third query should be sent (first two are < 10 chars stripped)
    assert route.call_count == 1
    await rag_client.close()


@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_search_for_duplicates_max_8_queries(rag_client):
    """Only first 8 queries are sent."""
    route = respx.post("http://rag-pipeline:8001/query/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    queries = [f"query number {i} with enough text" for i in range(12)]
    await rag_client.search_for_duplicates(
        workspace="ws", project="proj", branch="main",
        queries=queries,
    )
    assert route.call_count == 8
    await rag_client.close()


@pytest.mark.asyncio(loop_scope="function")
async def test_search_for_duplicates_disabled():
    """Disabled client returns empty list."""
    client = RagClient(enabled=False)
    results = await client.search_for_duplicates(
        workspace="ws", project="proj", branch="main",
        queries=["def foo(): pass"],
    )
    assert results == []
    await client.close()


@pytest.mark.asyncio(loop_scope="function")
async def test_search_for_duplicates_empty_queries(rag_client):
    """Empty queries list returns empty list."""
    results = await rag_client.search_for_duplicates(
        workspace="ws", project="proj", branch="main",
        queries=[],
    )
    assert results == []
    await rag_client.close()


@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_search_for_duplicates_partial_failure(rag_client):
    """One query fails, others succeed → partial results returned."""
    call_count = 0

    def side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(200, json={
                "results": [{"path": "a.py", "text": "ok", "score": 0.8}]
            })
        raise httpx.ConnectError("connection lost")

    respx.post("http://rag-pipeline:8001/query/search").mock(side_effect=side_effect)

    results = await rag_client.search_for_duplicates(
        workspace="ws", project="proj", branch="main",
        queries=["first query is long enough", "second query is long enough"],
    )
    # First succeeds, second fails silently
    assert len(results) == 1
    assert results[0]["_source"] == "duplication"
    await rag_client.close()


# ── get_deterministic_context ────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_get_deterministic_context_success(rag_client):
    """Basic deterministic context retrieval."""
    route = respx.post("http://rag-pipeline:8001/query/deterministic").mock(
        return_value=httpx.Response(200, json={
            "context": {
                "chunks": [{"path": "a.py", "text": "class A:"}],
                "changed_files": {"a.py": [{"text": "class A:"}]},
                "related_definitions": {},
            }
        })
    )
    result = await rag_client.get_deterministic_context(
        workspace="ws", project="proj",
        branches=["main"],
        file_paths=["src/a.py"],
    )
    assert route.called
    payload = json.loads(route.calls[0].request.read())
    assert payload["branches"] == ["main"]
    assert payload["file_paths"] == ["src/a.py"]
    assert "context" in result
    await rag_client.close()


@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_get_deterministic_context_with_pr(rag_client):
    """Deterministic context with PR-specific parameters."""
    route = respx.post("http://rag-pipeline:8001/query/deterministic").mock(
        return_value=httpx.Response(200, json={
            "context": {"chunks": [], "changed_files": {}, "related_definitions": {}}
        })
    )
    result = await rag_client.get_deterministic_context(
        workspace="ws", project="proj",
        branches=["main", "develop"],
        file_paths=["auth.py"],
        pr_number=42,
        pr_changed_files=["auth.py", "login.py"],
        additional_identifiers=["verify_token"],
    )
    assert route.called
    payload = json.loads(route.calls[0].request.read())
    assert payload["pr_number"] == 42
    assert "verify_token" in payload["additional_identifiers"]
    await rag_client.close()


@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_get_deterministic_context_timeout(rag_client):
    """Timeout → graceful fallback."""
    respx.post("http://rag-pipeline:8001/query/deterministic").mock(
        side_effect=httpx.ReadTimeout("timed out")
    )
    result = await rag_client.get_deterministic_context(
        workspace="ws", project="proj",
        branches=["main"],
        file_paths=["a.py"],
    )
    assert result["context"]["chunks"] == []
    await rag_client.close()


@pytest.mark.asyncio(loop_scope="function")
async def test_get_deterministic_context_disabled():
    """Disabled client returns empty deterministic context."""
    client = RagClient(enabled=False)
    result = await client.get_deterministic_context(
        workspace="ws", project="proj",
        branches=["main"],
        file_paths=["a.py"],
    )
    assert result["context"]["chunks"] == []
    await client.close()


# ── index_pr_files ───────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_index_pr_files_success(rag_client):
    """Successful PR file indexing."""
    route = respx.post("http://rag-pipeline:8001/index/pr-files").mock(
        return_value=httpx.Response(200, json={
            "status": "indexed",
            "pr_number": 42,
            "files_processed": 2,
            "chunks_indexed": 10,
            "chunks_failed": 0,
        })
    )
    result = await rag_client.index_pr_files(
        workspace="ws", project="proj", pr_number=42, branch="feat",
        files=[
            {"path": "a.py", "content": "x=1", "change_type": "ADDED"},
            {"path": "b.py", "content": "y=2", "change_type": "MODIFIED"},
        ],
    )
    assert route.called
    assert result["status"] == "indexed"
    assert result["chunks_indexed"] == 10
    await rag_client.close()


@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_index_pr_files_empty_files(rag_client):
    """Empty files list → skipped without HTTP call."""
    result = await rag_client.index_pr_files(
        workspace="ws", project="proj", pr_number=1, branch="b",
        files=[],
    )
    assert result["status"] == "skipped"
    await rag_client.close()


@pytest.mark.asyncio(loop_scope="function")
async def test_index_pr_files_disabled():
    """Disabled client → skipped."""
    client = RagClient(enabled=False)
    result = await client.index_pr_files(
        workspace="ws", project="proj", pr_number=1, branch="b",
        files=[{"path": "a.py", "content": "x=1", "change_type": "ADDED"}],
    )
    assert result["status"] == "skipped"
    await client.close()


@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_index_pr_files_server_error(rag_client):
    """Server error → graceful error status."""
    respx.post("http://rag-pipeline:8001/index/pr-files").mock(
        return_value=httpx.Response(500, json={"detail": "boom"})
    )
    result = await rag_client.index_pr_files(
        workspace="ws", project="proj", pr_number=1, branch="b",
        files=[{"path": "a.py", "content": "x=1", "change_type": "ADDED"}],
    )
    assert result["status"] == "error"
    await rag_client.close()


@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_index_pr_files_connection_error(rag_client):
    """Connection error → graceful error status."""
    respx.post("http://rag-pipeline:8001/index/pr-files").mock(
        side_effect=httpx.ConnectError("refused")
    )
    result = await rag_client.index_pr_files(
        workspace="ws", project="proj", pr_number=1, branch="b",
        files=[{"path": "a.py", "content": "x=1", "change_type": "ADDED"}],
    )
    assert result["status"] == "error"
    await rag_client.close()


# ── delete_pr_files ──────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_delete_pr_files_success(rag_client):
    """Successful PR file deletion."""
    route = respx.delete("http://rag-pipeline:8001/index/pr-files/ws/proj/42").mock(
        return_value=httpx.Response(200, json={"status": "deleted", "pr_number": 42})
    )
    result = await rag_client.delete_pr_files(
        workspace="ws", project="proj", pr_number=42,
    )
    assert route.called
    assert result is True
    await rag_client.close()


@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_delete_pr_files_not_found(rag_client):
    """Collection doesn't exist → skipped status → False."""
    respx.delete("http://rag-pipeline:8001/index/pr-files/ws/proj/99").mock(
        return_value=httpx.Response(200, json={"status": "skipped"})
    )
    result = await rag_client.delete_pr_files(
        workspace="ws", project="proj", pr_number=99,
    )
    assert result is False  # status != "deleted"
    await rag_client.close()


@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_delete_pr_files_server_error(rag_client):
    """Server error → False."""
    respx.delete("http://rag-pipeline:8001/index/pr-files/ws/proj/1").mock(
        return_value=httpx.Response(500, json={"detail": "error"})
    )
    result = await rag_client.delete_pr_files(
        workspace="ws", project="proj", pr_number=1,
    )
    assert result is False
    await rag_client.close()


@pytest.mark.asyncio(loop_scope="function")
async def test_delete_pr_files_disabled():
    """Disabled client → True (no-op)."""
    client = RagClient(enabled=False)
    result = await client.delete_pr_files(
        workspace="ws", project="proj", pr_number=1,
    )
    assert result is True
    await client.close()


# ── get_pr_context edge cases ────────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
async def test_get_pr_context_no_branch(rag_client):
    """Missing branch → returns empty context without HTTP call."""
    result = await rag_client.get_pr_context(
        workspace="ws", project="proj", branch=None, changed_files=["a.py"],
    )
    assert result == {"context": {"relevant_code": []}}
    await rag_client.close()


@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_get_pr_context_with_hybrid_params(rag_client):
    """PR context with all hybrid parameters."""
    route = respx.post("http://rag-pipeline:8001/query/pr-context").mock(
        return_value=httpx.Response(200, json={
            "context": {"relevant_code": [], "_metadata": {}}
        })
    )
    await rag_client.get_pr_context(
        workspace="ws", project="proj", branch="feat",
        changed_files=["a.py"],
        base_branch="main",
        deleted_files=["old.py"],
        pr_number=42,
        all_pr_changed_files=["a.py", "b.py"],
    )
    payload = json.loads(route.calls[0].request.read())
    assert payload["base_branch"] == "main"
    assert payload["pr_number"] == 42
    assert payload["deleted_files"] == ["old.py"]
    assert payload["all_pr_changed_files"] == ["a.py", "b.py"]
    await rag_client.close()


@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_get_pr_context_http_500(rag_client):
    """Server 500 → graceful empty context."""
    respx.post("http://rag-pipeline:8001/query/pr-context").mock(
        return_value=httpx.Response(500, json={"detail": "Internal error"})
    )
    result = await rag_client.get_pr_context(
        workspace="ws", project="proj", branch="main", changed_files=["a.py"],
    )
    assert "relevant_code" in result.get("context", {})
    await rag_client.close()


# ── semantic_search edge cases ───────────────────────────────

@pytest.mark.asyncio(loop_scope="function")
async def test_semantic_search_disabled():
    """Disabled client returns empty results."""
    client = RagClient(enabled=False)
    result = await client.semantic_search(
        query="test", workspace="ws", project="proj", branch="main",
    )
    assert result == {"results": []}
    await client.close()


@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_semantic_search_with_language_filter(rag_client):
    """Language filter included in payload."""
    route = respx.post("http://rag-pipeline:8001/query/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    await rag_client.semantic_search(
        query="test", workspace="ws", project="proj", branch="main",
        filter_language="python",
    )
    payload = json.loads(route.calls[0].request.read())
    assert payload["filter_language"] == "python"
    await rag_client.close()


@pytest.mark.asyncio(loop_scope="function")
@respx.mock
async def test_semantic_search_server_error(rag_client):
    """Server error → empty results."""
    respx.post("http://rag-pipeline:8001/query/search").mock(
        return_value=httpx.Response(500, json={"detail": "error"})
    )
    result = await rag_client.semantic_search(
        query="test", workspace="ws", project="proj", branch="main",
    )
    assert result == {"results": []}
    await rag_client.close()
