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
    async def test_public_paths_skip_auth(self):
        from rag_pipeline.api.middleware import ServiceSecretMiddleware

        mock_app = MagicMock()
        mw = ServiceSecretMiddleware(mock_app, secret="required-secret")

        mock_request = MagicMock()
        mock_request.url.path = "/health"
        mock_call_next = AsyncMock(return_value=MagicMock(status_code=200))

        result = await mw.dispatch(mock_request, mock_call_next)
        mock_call_next.assert_called_once_with(mock_request)

    @pytest.mark.asyncio
    async def test_no_secret_allows_all(self):
        from rag_pipeline.api.middleware import ServiceSecretMiddleware

        mock_app = MagicMock()
        mw = ServiceSecretMiddleware(mock_app, secret="")

        mock_request = MagicMock()
        mock_request.url.path = "/query/search"
        mock_call_next = AsyncMock(return_value=MagicMock(status_code=200))

        result = await mw.dispatch(mock_request, mock_call_next)
        mock_call_next.assert_called_once_with(mock_request)

    @pytest.mark.asyncio
    async def test_valid_secret_passes(self):
        from rag_pipeline.api.middleware import ServiceSecretMiddleware

        mock_app = MagicMock()
        mw = ServiceSecretMiddleware(mock_app, secret="my-secret")

        mock_request = MagicMock()
        mock_request.url.path = "/query/search"
        mock_request.headers = {"x-service-secret": "my-secret"}
        mock_call_next = AsyncMock(return_value=MagicMock(status_code=200))

        result = await mw.dispatch(mock_request, mock_call_next)
        mock_call_next.assert_called_once_with(mock_request)

    @pytest.mark.asyncio
    async def test_invalid_secret_returns_401(self):
        from rag_pipeline.api.middleware import ServiceSecretMiddleware

        mock_app = MagicMock()
        mw = ServiceSecretMiddleware(mock_app, secret="my-secret")

        mock_request = MagicMock()
        mock_request.url.path = "/query/search"
        mock_request.headers = {"x-service-secret": "wrong-secret"}
        mock_request.client.host = "127.0.0.1"
        mock_call_next = AsyncMock()

        result = await mw.dispatch(mock_request, mock_call_next)
        assert result.status_code == 401
        mock_call_next.assert_not_called()


# ─────────────────────────────────────────────────────────────
# App creation
# ─────────────────────────────────────────────────────────────
class TestAppCreation:

    def test_app_exists(self):
        """Ensure the app object can be imported (lifespan not triggered without TestClient)."""
        from rag_pipeline.api.api import app
        assert app is not None
        assert app.title == "CodeCrow RAG API"

    @pytest.mark.asyncio
    async def test_shutdown_drains_http_index_workers_before_clients_close(self):
        import rag_pipeline.api.api as api_module

        order = []
        manager = MagicMock()
        manager.embed_model.close.side_effect = lambda: order.append(
            "manager-embed-close"
        )
        manager.close.side_effect = lambda: order.append("manager-close")
        query_service = MagicMock()
        query_service.embed_model.close.side_effect = lambda: order.append(
            "query-embed-close"
        )
        query_service.close.side_effect = lambda: order.append("query-close")
        queue_consumer = MagicMock()
        queue_consumer.start = AsyncMock()
        queue_consumer.stop = AsyncMock()
        drain_workers = AsyncMock(side_effect=lambda: order.append("drain"))
        test_app = SimpleNamespace(state=SimpleNamespace())

        with (
            patch.object(api_module, "RAGConfig", return_value=MagicMock()),
            patch.object(
                api_module,
                "RAGIndexManager",
                return_value=manager,
            ),
            patch.object(
                api_module,
                "RAGQueryService",
                return_value=query_service,
            ),
            patch(
                "rag_pipeline.server.rag_queue_consumer.RAGQueueConsumer",
                return_value=queue_consumer,
            ),
            patch(
                "rag_pipeline.api.routers.index."
                "drain_index_repository_stream_workers",
                drain_workers,
            ),
        ):
            async with api_module.lifespan(test_app):
                pass

        queue_consumer.stop.assert_awaited_once()
        drain_workers.assert_awaited_once()
        assert order == [
            "drain",
            "manager-embed-close",
            "query-embed-close",
            "query-close",
            "manager-close",
        ]


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
    async def test_outage_logs_once_and_reports_recovery(self, caplog):
        from rag_pipeline.api.api import _pending_collection_janitor

        cleanup_attempt = AsyncMock(side_effect=[
            RuntimeError("qdrant unavailable"),
            RuntimeError("qdrant unavailable"),
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
