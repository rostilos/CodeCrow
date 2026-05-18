"""
Integration tests for /system/* endpoints.
Covers GC, memory, root, and health.
"""
import pytest
from unittest.mock import patch, MagicMock


@pytest.mark.asyncio
class TestSystemGC:
    """POST /system/gc — force garbage collection."""

    async def test_gc_with_psutil(self, client, auth_headers):
        """GC endpoint with psutil installed returns memory stats."""
        mock_process = MagicMock()
        mock_mem_before = MagicMock()
        mock_mem_before.rss = 200 * 1024 * 1024  # 200 MB
        mock_mem_after = MagicMock()
        mock_mem_after.rss = 190 * 1024 * 1024  # 190 MB
        mock_process.memory_info.side_effect = [mock_mem_before, mock_mem_after]

        mock_psutil = MagicMock()
        mock_psutil.Process.return_value = mock_process

        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            resp = await client.post("/system/gc", headers=auth_headers)

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "objects_collected" in body
        assert "memory_before_mb" in body
        assert "memory_after_mb" in body
        assert "memory_freed_mb" in body
        assert body["memory_before_mb"] == 200.0
        assert body["memory_after_mb"] == pytest.approx(190.0, abs=1.0)

    async def test_gc_without_psutil(self, client, auth_headers):
        """GC endpoint without psutil → simple response with objects_collected."""
        import sys
        saved = sys.modules.get("psutil")
        sys.modules["psutil"] = None  # force ImportError

        # The import-inside-endpoint pattern means we need to reload the module
        # Instead, we'll test the fallback path by making psutil raise ImportError
        with patch(
            "rag_pipeline.api.routers.system.force_garbage_collection",
            wraps=None,
        ) as _:
            pass

        # Direct test: just verify the endpoint doesn't crash
        resp = await client.post("/system/gc", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "objects_collected" in body

        # Restore
        if saved is not None:
            sys.modules["psutil"] = saved
        else:
            sys.modules.pop("psutil", None)

    async def test_gc_no_auth(self, client):
        """GC endpoint without auth → 401."""
        resp = await client.post("/system/gc")
        assert resp.status_code == 401


@pytest.mark.asyncio
class TestSystemMemory:
    """GET /system/memory — current memory usage."""

    async def test_memory_with_psutil(self, client, auth_headers):
        """Memory endpoint with psutil returns RSS/VMS/percent."""
        mock_process = MagicMock()
        mock_mem = MagicMock()
        mock_mem.rss = 150 * 1024 * 1024  # 150 MB
        mock_mem.vms = 400 * 1024 * 1024  # 400 MB
        mock_process.memory_info.return_value = mock_mem
        mock_process.memory_percent.return_value = 3.5

        mock_psutil = MagicMock()
        mock_psutil.Process.return_value = mock_process

        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            resp = await client.get("/system/memory", headers=auth_headers)

        assert resp.status_code == 200
        body = resp.json()
        assert body["rss_mb"] == 150.0
        assert body["vms_mb"] == 400.0
        assert body["percent"] == 3.5

    async def test_memory_no_auth(self, client):
        """Memory endpoint without auth → 401."""
        resp = await client.get("/system/memory")
        assert resp.status_code == 401


@pytest.mark.asyncio
class TestSystemRoot:
    """GET / — service identity."""

    async def test_root_returns_service_info(self, client, auth_headers):
        """Root endpoint returns name and version."""
        resp = await client.get("/", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "message" in body
        assert "RAG" in body["message"]
        assert "version" in body


@pytest.mark.asyncio
class TestSystemHealth:
    """GET /health — quick health check."""

    async def test_health_returns_healthy(self, client):
        """Health endpoint is public and returns healthy."""
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"
