"""
Tests for rag_pipeline.api.api — App creation, middleware, lifespan.
"""
import os
import pytest
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
