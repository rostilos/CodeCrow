"""
Integration tests for /index/pr-files endpoints.
Covers POST (index PR files) and DELETE (remove PR data).
"""
import pytest
from unittest.mock import MagicMock, patch, call


@pytest.mark.asyncio
class TestIndexPRFiles:
    """POST /index/pr-files — index PR file content with metadata."""

    async def test_index_pr_files_success(self, client, auth_headers, rag_app):
        """Valid request indexes files and returns chunk counts."""
        import rag_pipeline.api.api as api_module
        im = api_module.index_manager

        # Configure mocks for the indexing pipeline
        im._get_project_collection_name.return_value = "col_ws_proj"
        im._ensure_collection_exists.return_value = None
        im.qdrant_client.delete.return_value = None

        mock_chunk = MagicMock()
        mock_chunk.metadata = {"path": "src/main.py"}
        im.splitter.split_documents.return_value = [mock_chunk]

        im._point_ops.embed_and_create_points.return_value = [MagicMock()]
        im._point_ops.upsert_points.return_value = (1, 0)

        resp = await client.post(
            "/index/pr-files",
            json={
                "workspace": "ws",
                "project": "proj",
                "pr_number": 42,
                "branch": "feature/x",
                "files": [
                    {"path": "src/main.py", "content": "print('hello')", "change_type": "MODIFIED"},
                ],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "indexed"
        assert body["pr_number"] == 42
        assert body["files_processed"] == 1
        assert body["chunks_indexed"] == 1
        assert body["chunks_failed"] == 0

    async def test_index_pr_files_empty_files(self, client, auth_headers, rag_app):
        """Request with no files returns skipped status."""
        import rag_pipeline.api.api as api_module
        im = api_module.index_manager
        im._get_project_collection_name.return_value = "col_ws_proj"
        im._ensure_collection_exists.return_value = None
        im.qdrant_client.delete.return_value = None

        resp = await client.post(
            "/index/pr-files",
            json={
                "workspace": "ws",
                "project": "proj",
                "pr_number": 10,
                "branch": "fix/bug",
                "files": [],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "skipped"
        assert body["chunks_indexed"] == 0

    async def test_index_pr_files_all_deleted(self, client, auth_headers, rag_app):
        """Only DELETED files → skipped (they are filtered out)."""
        import rag_pipeline.api.api as api_module
        im = api_module.index_manager
        im._get_project_collection_name.return_value = "col_ws_proj"
        im._ensure_collection_exists.return_value = None
        im.qdrant_client.delete.return_value = None

        resp = await client.post(
            "/index/pr-files",
            json={
                "workspace": "ws",
                "project": "proj",
                "pr_number": 11,
                "branch": "cleanup",
                "files": [
                    {"path": "old.py", "content": "x = 1", "change_type": "DELETED"},
                ],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "skipped"

    async def test_index_pr_files_empty_content_skipped(self, client, auth_headers, rag_app):
        """Files with blank content are skipped."""
        import rag_pipeline.api.api as api_module
        im = api_module.index_manager
        im._get_project_collection_name.return_value = "col_ws_proj"
        im._ensure_collection_exists.return_value = None
        im.qdrant_client.delete.return_value = None

        resp = await client.post(
            "/index/pr-files",
            json={
                "workspace": "ws",
                "project": "proj",
                "pr_number": 12,
                "branch": "b",
                "files": [
                    {"path": "blank.py", "content": "   ", "change_type": "ADDED"},
                ],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "skipped"

    async def test_index_pr_files_multiple_files(self, client, auth_headers, rag_app):
        """Multiple files produce multiple chunks."""
        import rag_pipeline.api.api as api_module
        im = api_module.index_manager
        im._get_project_collection_name.return_value = "col_ws_proj"
        im._ensure_collection_exists.return_value = None
        im.qdrant_client.delete.return_value = None

        chunk1 = MagicMock()
        chunk1.metadata = {"path": "a.py"}
        chunk2 = MagicMock()
        chunk2.metadata = {"path": "b.py"}
        im.splitter.split_documents.return_value = [chunk1, chunk2]
        im._point_ops.embed_and_create_points.return_value = [MagicMock(), MagicMock()]
        im._point_ops.upsert_points.return_value = (2, 0)

        resp = await client.post(
            "/index/pr-files",
            json={
                "workspace": "ws",
                "project": "proj",
                "pr_number": 13,
                "branch": "feat",
                "files": [
                    {"path": "a.py", "content": "def a(): pass", "change_type": "ADDED"},
                    {"path": "b.py", "content": "def b(): pass", "change_type": "MODIFIED"},
                ],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["files_processed"] == 2
        assert body["chunks_indexed"] == 2

    async def test_index_pr_files_upsert_failures(self, client, auth_headers, rag_app):
        """Partial upsert failure is reported in chunks_failed."""
        import rag_pipeline.api.api as api_module
        im = api_module.index_manager
        im._get_project_collection_name.return_value = "col_ws_proj"
        im._ensure_collection_exists.return_value = None
        im.qdrant_client.delete.return_value = None

        chunk = MagicMock()
        chunk.metadata = {"path": "fail.py"}
        im.splitter.split_documents.return_value = [chunk]
        im._point_ops.embed_and_create_points.return_value = [MagicMock()]
        im._point_ops.upsert_points.return_value = (0, 1)

        resp = await client.post(
            "/index/pr-files",
            json={
                "workspace": "ws",
                "project": "proj",
                "pr_number": 14,
                "branch": "b",
                "files": [
                    {"path": "fail.py", "content": "x=1", "change_type": "ADDED"},
                ],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["chunks_indexed"] == 0
        assert body["chunks_failed"] == 1

    async def test_index_pr_files_value_error_returns_400(self, client, auth_headers, rag_app):
        """ValueError from index_manager → 400."""
        import rag_pipeline.api.api as api_module
        im = api_module.index_manager
        im._get_project_collection_name.side_effect = ValueError("bad workspace")

        resp = await client.post(
            "/index/pr-files",
            json={
                "workspace": "ws",
                "project": "proj",
                "pr_number": 15,
                "branch": "b",
                "files": [{"path": "a.py", "content": "x=1", "change_type": "ADDED"}],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400
        im._get_project_collection_name.side_effect = None  # reset

    async def test_index_pr_files_internal_error_returns_500(self, client, auth_headers, rag_app):
        """Unexpected exception from index_manager → 500."""
        import rag_pipeline.api.api as api_module
        im = api_module.index_manager
        im._get_project_collection_name.side_effect = RuntimeError("boom")

        resp = await client.post(
            "/index/pr-files",
            json={
                "workspace": "ws",
                "project": "proj",
                "pr_number": 16,
                "branch": "b",
                "files": [{"path": "a.py", "content": "x=1", "change_type": "ADDED"}],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 500
        im._get_project_collection_name.side_effect = None

    async def test_index_pr_files_no_auth(self, client, rag_app):
        """Missing auth → 401."""
        resp = await client.post(
            "/index/pr-files",
            json={
                "workspace": "ws",
                "project": "proj",
                "pr_number": 1,
                "branch": "b",
                "files": [],
            },
        )
        assert resp.status_code == 401

    async def test_index_pr_files_missing_required_fields(self, client, auth_headers):
        """Missing required fields → 422."""
        resp = await client.post(
            "/index/pr-files",
            json={"workspace": "ws"},
            headers=auth_headers,
        )
        assert resp.status_code == 422


@pytest.mark.asyncio
class TestDeletePRFiles:
    """DELETE /index/pr-files/{workspace}/{project}/{pr_number}."""

    async def test_delete_pr_files_success(self, client, auth_headers, rag_app):
        """Successful deletion returns status=deleted."""
        import rag_pipeline.api.api as api_module
        im = api_module.index_manager
        im._get_project_collection_name.return_value = "col_ws_proj"
        im._collection_manager.collection_exists.return_value = True
        im.qdrant_client.delete.return_value = None

        resp = await client.delete(
            "/index/pr-files/ws/proj/42",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "deleted"
        assert body["pr_number"] == 42
        assert body["collection"] == "col_ws_proj"

    async def test_delete_pr_files_collection_not_found(self, client, auth_headers, rag_app):
        """Collection doesn't exist → skipped."""
        import rag_pipeline.api.api as api_module
        im = api_module.index_manager
        im._get_project_collection_name.return_value = "col_ws_proj"
        im._collection_manager.collection_exists.return_value = False

        resp = await client.delete(
            "/index/pr-files/ws/proj/99",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "skipped"

    async def test_delete_pr_files_qdrant_error(self, client, auth_headers, rag_app):
        """Qdrant error → 500."""
        import rag_pipeline.api.api as api_module
        im = api_module.index_manager
        im._get_project_collection_name.return_value = "col_ws_proj"
        im._collection_manager.collection_exists.return_value = True
        im.qdrant_client.delete.side_effect = RuntimeError("qdrant down")

        resp = await client.delete(
            "/index/pr-files/ws/proj/50",
            headers=auth_headers,
        )
        assert resp.status_code == 500
        im.qdrant_client.delete.side_effect = None

    async def test_delete_pr_files_no_auth(self, client, rag_app):
        """Missing auth → 401."""
        resp = await client.delete("/index/pr-files/ws/proj/42")
        assert resp.status_code == 401
