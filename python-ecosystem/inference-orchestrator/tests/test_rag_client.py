"""
Unit tests for service.rag.rag_client — RagClient (all async methods).
"""
import pytest
import httpx
import respx
from unittest.mock import AsyncMock, patch, MagicMock
from service.rag.rag_client import RagClient


@pytest.fixture
def disabled_client():
    return RagClient(base_url="http://rag:8001", enabled=False)


@pytest.fixture
def enabled_client():
    return RagClient(base_url="http://rag:8001", enabled=True)


# ── Disabled client short-circuits ───────────────────────────

class TestRagClientDisabled:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_get_pr_context_disabled(self, disabled_client):
        r = await disabled_client.get_pr_context("ws", "proj", "main", ["a.py"])
        assert r == {"context": {"relevant_code": []}}

    @pytest.mark.asyncio(loop_scope="function")
    async def test_semantic_search_disabled(self, disabled_client):
        r = await disabled_client.semantic_search("q", "ws", "proj", "main")
        assert r == {"results": []}

    @pytest.mark.asyncio(loop_scope="function")
    async def test_is_healthy_disabled(self, disabled_client):
        assert await disabled_client.is_healthy() is False

    @pytest.mark.asyncio(loop_scope="function")
    async def test_search_for_duplicates_disabled(self, disabled_client):
        r = await disabled_client.search_for_duplicates("ws", "proj", "main", ["q"])
        assert r == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_deterministic_context_disabled(self, disabled_client):
        r = await disabled_client.get_deterministic_context("ws", "proj", ["main"], ["a.py"])
        assert "context" in r

    @pytest.mark.asyncio(loop_scope="function")
    async def test_index_pr_files_disabled(self, disabled_client):
        r = await disabled_client.index_pr_files("ws", "proj", 1, "main", [])
        assert r["status"] == "skipped"

    @pytest.mark.asyncio(loop_scope="function")
    async def test_delete_pr_files_disabled(self, disabled_client):
        r = await disabled_client.delete_pr_files("ws", "proj", 1)
        assert r is True


# ── No-branch short-circuit ──────────────────────────────────

class TestRagClientNoBranch:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_get_pr_context_no_branch(self, enabled_client):
        r = await enabled_client.get_pr_context("ws", "proj", None, ["a.py"])
        assert r == {"context": {"relevant_code": []}}


# ── Successful HTTP calls (mocked with respx) ───────────────

class TestRagClientSuccess:
    @pytest.mark.asyncio(loop_scope="function")
    @respx.mock
    async def test_get_pr_context_ok(self):
        respx.post("http://rag:8001/query/pr-context").mock(
            return_value=httpx.Response(200, json={
                "context": {
                    "relevant_code": [{"text": "x"}],
                    "_branches_searched": ["main"],
                }
            })
        )
        c = RagClient(base_url="http://rag:8001", enabled=True)
        r = await c.get_pr_context("ws", "proj", "main", ["a.py"], pr_title="fix")
        assert len(r["context"]["relevant_code"]) == 1
        await c.close()

    @pytest.mark.asyncio(loop_scope="function")
    @respx.mock
    async def test_semantic_search_ok(self):
        respx.post("http://rag:8001/query/search").mock(
            return_value=httpx.Response(200, json={"results": [{"score": 0.9}]})
        )
        c = RagClient(base_url="http://rag:8001", enabled=True)
        r = await c.semantic_search("query", "ws", "proj", "main", filter_language="python")
        assert len(r["results"]) == 1
        await c.close()

    @pytest.mark.asyncio(loop_scope="function")
    @respx.mock
    async def test_is_healthy_ok(self):
        respx.get("http://rag:8001/health").mock(return_value=httpx.Response(200))
        c = RagClient(base_url="http://rag:8001", enabled=True)
        assert await c.is_healthy() is True
        await c.close()

    @pytest.mark.asyncio(loop_scope="function")
    @respx.mock
    async def test_search_for_duplicates_ok(self):
        respx.post("http://rag:8001/query/search").mock(
            return_value=httpx.Response(200, json={"results": [{"text": "dup"}]})
        )
        c = RagClient(base_url="http://rag:8001", enabled=True)
        r = await c.search_for_duplicates("ws", "proj", "main", ["find duplicate of X"])
        assert len(r) == 1
        assert r[0]["_source"] == "duplication"
        await c.close()

    @pytest.mark.asyncio(loop_scope="function")
    @respx.mock
    async def test_get_deterministic_context_ok(self):
        respx.post("http://rag:8001/query/deterministic").mock(
            return_value=httpx.Response(200, json={
                "context": {"chunks": [{"text": "c"}], "changed_files": {}, "related_definitions": {}}
            })
        )
        c = RagClient(base_url="http://rag:8001", enabled=True)
        r = await c.get_deterministic_context("ws", "proj", ["main"], ["a.py"], pr_number=42)
        assert len(r["context"]["chunks"]) == 1
        await c.close()

    @pytest.mark.asyncio(loop_scope="function")
    @respx.mock
    async def test_index_pr_files_ok(self):
        respx.post("http://rag:8001/index/pr-files").mock(
            return_value=httpx.Response(200, json={"status": "ok", "chunks_indexed": 5, "files_processed": 2})
        )
        c = RagClient(base_url="http://rag:8001", enabled=True)
        files = [{"path": "a.py", "content": "code", "change_type": "MODIFIED"}]
        r = await c.index_pr_files("ws", "proj", 1, "main", files)
        assert r["chunks_indexed"] == 5
        await c.close()

    @pytest.mark.asyncio(loop_scope="function")
    @respx.mock
    async def test_delete_pr_files_ok(self):
        respx.delete("http://rag:8001/index/pr-files/ws/proj/1").mock(
            return_value=httpx.Response(200, json={"status": "deleted"})
        )
        c = RagClient(base_url="http://rag:8001", enabled=True)
        assert await c.delete_pr_files("ws", "proj", 1) is True
        await c.close()


# ── Error handling ───────────────────────────────────────────

class TestRagClientErrors:
    @pytest.mark.asyncio(loop_scope="function")
    @respx.mock
    async def test_get_pr_context_http_error(self):
        respx.post("http://rag:8001/query/pr-context").mock(
            return_value=httpx.Response(500)
        )
        c = RagClient(base_url="http://rag:8001", enabled=True)
        r = await c.get_pr_context("ws", "proj", "main", ["a.py"])
        assert r == {"context": {"relevant_code": []}}
        await c.close()

    @pytest.mark.asyncio(loop_scope="function")
    @respx.mock
    async def test_semantic_search_error(self):
        respx.post("http://rag:8001/query/search").mock(side_effect=httpx.ConnectError("fail"))
        c = RagClient(base_url="http://rag:8001", enabled=True)
        r = await c.semantic_search("q", "ws", "proj", "main")
        assert r == {"results": []}
        await c.close()

    @pytest.mark.asyncio(loop_scope="function")
    @respx.mock
    async def test_is_healthy_error(self):
        respx.get("http://rag:8001/health").mock(side_effect=Exception("down"))
        c = RagClient(base_url="http://rag:8001", enabled=True)
        assert await c.is_healthy() is False
        await c.close()

    @pytest.mark.asyncio(loop_scope="function")
    @respx.mock
    async def test_deterministic_context_error(self):
        respx.post("http://rag:8001/query/deterministic").mock(
            return_value=httpx.Response(503)
        )
        c = RagClient(base_url="http://rag:8001", enabled=True)
        r = await c.get_deterministic_context("ws", "proj", ["main"], ["a.py"])
        assert "context" in r
        await c.close()

    @pytest.mark.asyncio(loop_scope="function")
    @respx.mock
    async def test_index_pr_files_error(self):
        respx.post("http://rag:8001/index/pr-files").mock(
            return_value=httpx.Response(500)
        )
        c = RagClient(base_url="http://rag:8001", enabled=True)
        r = await c.index_pr_files("ws", "proj", 1, "main", [{"path": "a.py", "content": "x", "change_type": "M"}])
        assert r["status"] == "error"
        await c.close()

    @pytest.mark.asyncio(loop_scope="function")
    @respx.mock
    async def test_delete_pr_files_error(self):
        respx.delete("http://rag:8001/index/pr-files/ws/proj/1").mock(
            return_value=httpx.Response(500)
        )
        c = RagClient(base_url="http://rag:8001", enabled=True)
        assert await c.delete_pr_files("ws", "proj", 1) is False
        await c.close()


# ── Client lifecycle ─────────────────────────────────────────

class TestRagClientLifecycle:
    @pytest.mark.asyncio(loop_scope="function")
    async def test_close_noop_when_no_client(self, enabled_client):
        await enabled_client.close()  # Should not raise

    @pytest.mark.asyncio(loop_scope="function")
    @respx.mock
    async def test_get_client_reuses(self):
        respx.get("http://rag:8001/health").mock(return_value=httpx.Response(200))
        c = RagClient(base_url="http://rag:8001", enabled=True)
        await c.is_healthy()
        client1 = c._client
        await c.is_healthy()
        client2 = c._client
        assert client1 is client2
        await c.close()

    @pytest.mark.asyncio(loop_scope="function")
    async def test_empty_queries_duplicates(self, enabled_client):
        r = await enabled_client.search_for_duplicates("ws", "proj", "main", [])
        assert r == []

    @pytest.mark.asyncio(loop_scope="function")
    async def test_empty_files_index(self, enabled_client):
        r = await enabled_client.index_pr_files("ws", "proj", 1, "main", [])
        assert r["status"] == "skipped"
