"""Shared FastAPI fixtures backed by a mocked structural generation reader."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest


SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, os.path.abspath(SRC_DIR))

os.environ.setdefault("SERVICE_SECRET", "test-secret-token")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")


async def _run_in_threadpool_inline(function, *args, **kwargs):
    return function(*args, **kwargs)


@pytest.fixture(scope="session")
def rag_app():
    """Create the module-level app with one exact mocked SQLite reader."""
    with patch(
        "fastapi.routing.run_in_threadpool",
        new=_run_in_threadpool_inline,
    ):
        import rag_pipeline.api.api as api_module

        config = MagicMock(
            structural_index_root="/tmp/codecrow-structural-integration",
            max_file_size_bytes=512 * 1024,
            max_files_per_index=5000,
            max_chunks_per_index=1_000_000,
            chunk_size=8000,
            chunk_overlap=200,
        )
        manager = MagicMock()
        reader = manager.open_reader.return_value.__enter__.return_value
        reader.snapshot.return_value = {
            "workspace": "ws1",
            "project": "proj1",
            "branch": "main",
            "revision": "revision-1",
            "generationManifestSha256": "a" * 64,
        }
        reader.search_units.return_value = [{
            "unitId": "unit:authentication-handler",
            "recordType": "source_unit",
            "path": "a.py",
            "language": "python",
            "kind": "class",
            "name": "AuthenticationHandler",
            "qualifiedName": "AuthenticationHandler",
            "startLine": 1,
            "endLine": 1,
        }]
        reader.get_unit.return_value = {
            "unit": {"content": "class AuthenticationHandler: pass"},
            "sourceEvidence": True,
        }
        reader.query_graph.return_value = {"results": []}
        reader.relations_for_paths.return_value = {
            "snapshot": reader.snapshot.return_value,
            "anchors": [{"path": "src/module.py", "symbols": []}],
            "relations": [],
            "coverage": {
                "state": "complete",
                "totalRelations": 0,
                "omittedRelations": 0,
            },
        }
        manager.get_revision_preflight.return_value = None

        api_module.config = config
        api_module.index_manager = manager
        yield api_module.app


@pytest.fixture()
def client(rag_app):
    import httpx

    transport = httpx.ASGITransport(app=rag_app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.fixture()
def auth_headers():
    return {"x-service-secret": os.environ["SERVICE_SECRET"]}
