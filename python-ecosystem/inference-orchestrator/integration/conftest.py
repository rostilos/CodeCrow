"""
Shared fixtures for inference-orchestrator **integration** tests.

These tests exercise the full FastAPI stack (routers → middleware → service)
with external dependencies (LLM, RAG, Redis, MCP) stubbed at the boundary
using respx, fakeredis, and unittest.mock.

Contrast with tests/conftest.py which mocks at sys.modules level for
pure-logic unit tests.
"""
import os
import sys
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

# ── Ensure the src/ directory is on sys.path ──────────────────
SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, os.path.abspath(SRC_DIR))

# ── Pre-mock heavy third-party deps (same as unit tests) ──────
# Must happen BEFORE importing any service module.
from tests.conftest import _ensure_mock  # reuse the mock helper


# ── Environment variables for test mode ───────────────────────
os.environ.setdefault("SERVICE_SECRET", "test-secret-token")
os.environ.setdefault("RAG_ENABLED", "false")
os.environ.setdefault("RAG_API_URL", "http://rag-pipeline:8001")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")


@pytest.fixture(scope="session")
def _patch_services():
    """
    Patch ReviewService and CommandService so the lifespan doesn't
    try to connect to Redis, spawn MCP servers, or create real LLM clients.
    """
    mock_review_svc = MagicMock()
    mock_review_svc.process_review_request = AsyncMock(return_value={"result": {"issues": []}})
    mock_review_svc.rag_client = MagicMock()
    mock_review_svc.rag_client.close = AsyncMock()

    mock_command_svc = MagicMock()
    mock_command_svc.process_summarize = AsyncMock(return_value={
        "summary": "Test summary",
        "diagram": "graph LR; A-->B",
        "diagramType": "MERMAID",
    })
    mock_command_svc.process_ask = AsyncMock(return_value={"answer": "42"})
    mock_command_svc.rag_client = MagicMock()
    mock_command_svc.rag_client.close = AsyncMock()

    # Patch the service constructors + Redis queue consumers
    # Queue consumers are imported inside lifespan(), so patch at source module
    with patch("api.app.ReviewService", return_value=mock_review_svc), \
         patch("api.app.CommandService", return_value=mock_command_svc), \
         patch("server.queue_consumer.RedisQueueConsumer") as mock_rqc, \
         patch("server.command_queue_consumer.CommandQueueConsumer") as mock_cqc:
        mock_rqc.return_value.start = AsyncMock()
        mock_rqc.return_value.stop = AsyncMock()
        mock_cqc.return_value.start = AsyncMock()
        mock_cqc.return_value.stop = AsyncMock()
        yield {
            "review_service": mock_review_svc,
            "command_service": mock_command_svc,
        }


@pytest.fixture(scope="session")
def io_app(_patch_services):
    """
    Create the FastAPI app with mocked services.

    The ASGI transport does not run the lifespan automatically, so we
    manually attach the mocked services to app.state (mimicking what
    lifespan() normally does).
    """
    from api.app import create_app
    app = create_app()
    # Manually populate app.state — routers read from request.app.state
    app.state.review_service = _patch_services["review_service"]
    app.state.command_service = _patch_services["command_service"]
    return app


@pytest.fixture()
def client(io_app):
    """httpx.AsyncClient bound to the app (ASGI transport)."""
    import httpx
    transport = httpx.ASGITransport(app=io_app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture()
def auth_headers():
    """Headers with valid service secret."""
    return {"x-service-secret": os.environ["SERVICE_SECRET"]}


@pytest.fixture()
def mock_services(_patch_services):
    """Direct access to the mock service objects."""
    return _patch_services
