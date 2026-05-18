"""
Shared fixtures for rag-pipeline **integration** tests.

These tests exercise the full FastAPI stack (routers → middleware → services)
with Qdrant and Redis mocked at the boundary.
"""
import os
import sys
import pytest
from unittest.mock import MagicMock, patch

# ── Ensure src/ is on sys.path ────────────────────────────────
SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, os.path.abspath(SRC_DIR))

# ── Environment variables ─────────────────────────────────────
os.environ.setdefault("SERVICE_SECRET", "test-secret-token")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
os.environ.setdefault("EMBEDDING_PROVIDER", "ollama")
os.environ.setdefault("OLLAMA_BASE_URL", "http://localhost:11434")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")


@pytest.fixture(scope="session")
def _mock_qdrant():
    """Mock qdrant_client so no real Qdrant connection is needed."""
    mock_qclient = MagicMock()
    mock_qclient.get_collections.return_value = MagicMock(collections=[])
    mock_qclient.collection_exists.return_value = False
    mock_qclient.create_collection.return_value = True
    mock_qclient.upsert.return_value = None
    mock_qclient.search.return_value = []
    mock_qclient.scroll.return_value = ([], None)
    mock_qclient.count.return_value = MagicMock(count=0)
    mock_qclient.delete_collection.return_value = True
    return mock_qclient


@pytest.fixture(scope="session")
def _mock_embedding():
    """Mock embedding model."""
    mock_embed = MagicMock()
    mock_embed.get_query_embedding.return_value = [0.1] * 384
    mock_embed.get_text_embedding.return_value = [0.1] * 384
    mock_embed.get_text_embedding_batch.return_value = [[0.1] * 384]
    mock_embed.embed_documents.return_value = [[0.1] * 384]
    mock_embed.embed_query.return_value = [0.1] * 384
    mock_embed.close = MagicMock()
    return mock_embed


@pytest.fixture(scope="session")
def rag_app(_mock_qdrant, _mock_embedding):
    """
    Create the RAG FastAPI app with mocked services.

    The RAG app is a module-level singleton (not a factory), so we:
    1. Patch service constructors at source (RAGConfig, RAGIndexManager, etc.)
    2. Set the module-level globals that routers read via _get_singletons()
    3. Patch RAGQueueConsumer at its source module (it's imported inside lifespan)
    """
    with patch("rag_pipeline.models.config.RAGConfig") as MockConfig, \
         patch("rag_pipeline.core.index_manager.RAGIndexManager") as MockIM, \
         patch("rag_pipeline.services.query_service.RAGQueryService") as MockQS, \
         patch("rag_pipeline.server.rag_queue_consumer.RAGQueueConsumer") as MockRQC:

        mock_config = MagicMock()
        mock_config.qdrant_url = "http://localhost:6333"
        mock_config.embedding_provider = "ollama"
        mock_config.max_chunks_per_index = 50000
        mock_config.max_files_per_index = 5000
        mock_config.max_file_size_bytes = 1048576
        mock_config.chunk_size = 1024
        mock_config.chunk_overlap = 128
        MockConfig.return_value = mock_config

        mock_im = MagicMock()
        mock_im.embed_model = _mock_embedding
        mock_im.qdrant_client = _mock_qdrant
        MockIM.return_value = mock_im

        mock_qs = MagicMock()
        mock_qs.embed_model = _mock_embedding
        mock_qs.semantic_search.return_value = [
            {"path": "a.py", "content": "class A: pass", "score": 0.95}
        ]
        mock_qs.get_context_for_pr.return_value = {
            "relevant_code": [{"path": "a.py", "content": "class A: pass"}],
            "related_files": [],
        }
        mock_qs.get_deterministic_context.return_value = {
            "files": [], "definitions": []
        }
        MockQS.return_value = mock_qs

        mock_rqc_instance = MagicMock()
        mock_rqc_instance.start = MagicMock()
        mock_rqc_instance.stop = MagicMock()
        MockRQC.return_value = mock_rqc_instance

        # Directly set module-level globals that routers access
        import rag_pipeline.api.api as api_module
        api_module.config = mock_config
        api_module.index_manager = mock_im
        api_module.query_service = mock_qs

        yield api_module.app


@pytest.fixture()
def client(rag_app):
    """httpx.AsyncClient bound to the RAG app."""
    import httpx
    transport = httpx.ASGITransport(app=rag_app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture()
def auth_headers():
    return {"x-service-secret": os.environ["SERVICE_SECRET"]}
