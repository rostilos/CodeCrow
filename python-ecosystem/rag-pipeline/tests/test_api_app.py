"""
Tests for rag_pipeline.api.api — App creation, middleware, lifespan.
"""
import asyncio
import logging
import os
import pytest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock, AsyncMock


# ─────────────────────────────────────────────────────────────
# ServiceSecretMiddleware
# ─────────────────────────────────────────────────────────────
class TestServiceSecretMiddleware:

    def test_init_reads_env(self):
        from rag_pipeline.api.middleware import ServiceSecretMiddleware

        mock_app = MagicMock()
        with patch.dict(os.environ, {"SERVICE_SECRET": "test-secret"}):
            mw = ServiceSecretMiddleware(mock_app)
            assert mw.secret == "test-secret"

    def test_init_no_secret(self):
        from rag_pipeline.api.middleware import ServiceSecretMiddleware

        mock_app = MagicMock()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SERVICE_SECRET", None)
            mw = ServiceSecretMiddleware(mock_app)
            assert mw.secret == ""

    def test_init_explicit_secret(self):
        from rag_pipeline.api.middleware import ServiceSecretMiddleware

        mock_app = MagicMock()
        mw = ServiceSecretMiddleware(mock_app, secret="explicit-secret")
        assert mw.secret == "explicit-secret"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("secret,path,provided,status", [
        ("required", "/health", None, 200),
        ("required", "/docs", None, 200),
        ("required", "/openapi.json", None, 200),
        ("required", "/redoc", None, 200),
        ("", "/query/code-search", None, 200),
        ("required", "/query/code-search", "required", 200),
        ("required", "/query/code-search", "wrong", 401),
        ("required", "/query/code-search", None, 401),
        ("required", "/docs/private", None, 401),
    ])
    async def test_auth_preserves_receive_channel(self, secret, path, provided, status):
        from rag_pipeline.api.middleware import ServiceSecretMiddleware
        from starlette.responses import JSONResponse

        receive = AsyncMock(return_value={"type": "http.disconnect"})
        sent = []
        called = False

        async def send(message):
            sent.append(message)

        async def downstream(scope, actual_receive, actual_send):
            nonlocal called
            called = True
            assert actual_receive is receive and actual_send is send
            assert await actual_receive() == {"type": "http.disconnect"}
            await JSONResponse({"ok": True})(scope, actual_receive, actual_send)

        scope = {"type": "http", "path": path, "client": ("127.0.0.1", 1234),
                 "headers": [(b"x-service-secret", provided.encode())] if provided else []}
        await ServiceSecretMiddleware(downstream, secret=secret)(scope, receive, send)
        assert next(message["status"] for message in sent if message["type"] == "http.response.start") == status
        assert called is (status == 200)
        assert receive.await_count == (1 if status == 200 else 0)

    @pytest.mark.asyncio
    async def test_non_http_scope_passes_through(self):
        from rag_pipeline.api.middleware import ServiceSecretMiddleware

        app, receive, send = AsyncMock(), AsyncMock(), AsyncMock()
        scope = {"type": "lifespan"}
        await ServiceSecretMiddleware(app, secret="required")(scope, receive, send)
        app.assert_awaited_once_with(scope, receive, send)


# ─────────────────────────────────────────────────────────────
# App creation
# ─────────────────────────────────────────────────────────────
class TestAppCreation:

    def test_app_exists(self):
        """Ensure the app object can be imported (lifespan not triggered without TestClient)."""
        from rag_pipeline.api.api import app
        assert app is not None
        assert app.title == "CodeCrow Repository Index API"

    @pytest.mark.asyncio
    async def test_shutdown_drains_http_index_workers_before_clients_close(self):
        import rag_pipeline.api.api as api_module

        order = []
        manager = MagicMock()
        manager.close.side_effect = lambda: order.append("manager-close")
        drain_workers = AsyncMock(side_effect=lambda: order.append("drain"))
        test_app = SimpleNamespace(state=SimpleNamespace())

        with (
            patch.object(api_module, "RAGConfig", return_value=SimpleNamespace(full_index_concurrency=16, model_dump=lambda **kwargs: {"full_index_concurrency": 16})),
            patch.object(
                api_module,
                "RAGIndexManager",
                return_value=manager,
            ),
            patch(
                "rag_pipeline.api.routers.index."
                "drain_index_repository_stream_workers",
                drain_workers,
            ),
        ):
            async with api_module.lifespan(test_app):
                pass

        drain_workers.assert_awaited_once()
        assert order == ["drain", "manager-close"]


class TestPendingCollectionJanitor:

    @pytest.mark.parametrize(
        ("configured", "expected"),
        [("120", 300), ("900", 900)],
    )
    def test_interval_applies_minimum(self, configured, expected):
        from rag_pipeline.api.api import _pending_janitor_interval_seconds

        with patch.dict(
            os.environ,
            {"RAG_PENDING_JANITOR_INTERVAL_SECONDS": configured},
        ):
            assert _pending_janitor_interval_seconds() == expected

    def test_malformed_interval_falls_back_to_default(self, caplog):
        from rag_pipeline.api.api import _pending_janitor_interval_seconds

        with (
            patch.dict(
                os.environ,
                {"RAG_PENDING_JANITOR_INTERVAL_SECONDS": "not-a-number"},
            ),
            caplog.at_level(logging.WARNING),
        ):
            assert _pending_janitor_interval_seconds() == 3600

        assert "Invalid RAG_PENDING_JANITOR_INTERVAL_SECONDS" in caplog.text

    @pytest.mark.asyncio
    async def test_janitor_runs_pending_and_review_generation_cleanup(self):
        from rag_pipeline.api.api import _pending_collection_janitor

        manager = MagicMock()
        manager.cleanup_expired_collections.return_value = {
            "pending": 1,
            "proposedTree": 2,
        }
        cleanup_attempt = AsyncMock(
            side_effect=lambda callback: callback(),
        )
        with (
            patch(
                "rag_pipeline.api.api.asyncio.to_thread",
                cleanup_attempt,
            ),
            patch(
                "rag_pipeline.api.api.asyncio.sleep",
                AsyncMock(side_effect=asyncio.CancelledError()),
            ),
        ):
            with pytest.raises(asyncio.CancelledError):
                await _pending_collection_janitor(manager)

        cleanup_attempt.assert_awaited_once_with(
            manager.cleanup_expired_collections,
        )
        manager.cleanup_expired_collections.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_outage_logs_once_and_reports_recovery(self, caplog):
        from rag_pipeline.api.api import _pending_collection_janitor

        cleanup_attempt = AsyncMock(side_effect=[
            RuntimeError("structural store unavailable"),
            RuntimeError("structural store unavailable"),
            0,
            asyncio.CancelledError(),
        ])
        with (
            patch(
                "rag_pipeline.api.api.asyncio.to_thread",
                cleanup_attempt,
            ),
            patch(
                "rag_pipeline.api.api.asyncio.sleep",
                AsyncMock(return_value=None),
            ),
            caplog.at_level(logging.DEBUG),
        ):
            with pytest.raises(asyncio.CancelledError):
                await _pending_collection_janitor(MagicMock())

        janitor_records = [
            record
            for record in caplog.records
            if "Pending collection janitor" in record.getMessage()
        ]
        assert sum(
            record.levelno == logging.WARNING for record in janitor_records
        ) == 1
        assert not any(
            record.levelno >= logging.ERROR for record in janitor_records
        )
        assert any(
            "recovered" in record.getMessage() for record in janitor_records
        )


@pytest.mark.asyncio
async def test_lifespan_closes_manager_when_serving_raises_and_cleanup_fails():
    import rag_pipeline.api.api as api_module
    manager = MagicMock()
    drain = AsyncMock()
    app = SimpleNamespace(state=SimpleNamespace())
    with (
        patch.object(api_module, "RAGConfig", return_value=SimpleNamespace(full_index_concurrency=16, model_dump=lambda **kwargs: {"full_index_concurrency": 16})),
        patch.object(api_module, "RAGIndexManager", return_value=manager),
        patch("rag_pipeline.api.routers.index.cleanup_orphaned_index_repository_stream_workspaces", side_effect=OSError("read only")),
        patch("rag_pipeline.api.routers.index.drain_index_repository_stream_workers", drain),
    ):
        with pytest.raises(RuntimeError, match="serving failed"):
            async with api_module.lifespan(app):
                raise RuntimeError("serving failed")
    drain.assert_awaited_once()
    manager.close.assert_called_once()
    assert api_module.index_manager is None
    assert app.state.pending_collection_janitor.done()
