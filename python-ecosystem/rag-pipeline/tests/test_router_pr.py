"""
Tests for rag_pipeline.api.routers.pr — PR file indexing endpoints.

Covers:
- index_pr_files (full flow, skipped, deleted files, error handling)
- delete_pr_files (success, collection not found, error)
"""
import pytest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace
from fastapi import HTTPException


def _make_index_manager():
    im = MagicMock()
    im._get_project_collection_name.return_value = "rag_ws__proj"
    im._collection_manager.collection_exists.return_value = True
    im.splitter.split_documents.return_value = []
    im._point_ops.embed_and_create_points.return_value = []
    im._point_ops.upsert_points.return_value = (0, 0)
    return im


# ─────────────────────────────────────────────────────────────
# index_pr_files
# ─────────────────────────────────────────────────────────────
class TestIndexPRFiles:

    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_success_with_files(self, mock_get):
        im = _make_index_manager()
        mock_get.return_value = im

        # Simulate chunks after splitting
        mock_chunk = MagicMock()
        mock_chunk.metadata = {"path": "src/Foo.java"}
        im.splitter.split_documents.return_value = [mock_chunk]
        im._point_ops.embed_and_create_points.return_value = [MagicMock()]
        im._point_ops.upsert_points.return_value = (1, 0)

        from rag_pipeline.api.routers.pr import index_pr_files

        file_info = MagicMock()
        file_info.content = "public class Foo {}"
        file_info.path = "src/Foo.java"
        file_info.change_type = "ADDED"

        req = MagicMock()
        req.workspace = "ws"
        req.project = "proj"
        req.pr_number = 42
        req.branch = "feat"
        req.files = [file_info]

        result = index_pr_files(req)
        assert result["status"] == "indexed"
        assert result["pr_number"] == 42
        assert result["files_processed"] == 1
        assert result["chunks_indexed"] == 1

    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_exact_snapshot_is_stored_on_every_pr_chunk(self, mock_get):
        from rag_pipeline.api.models import ContextSnapshotV1
        from rag_pipeline.api.routers.pr import index_pr_files

        im = _make_index_manager()
        mock_get.return_value = im
        mock_chunk = MagicMock()
        mock_chunk.text = "public class Foo {}"
        mock_chunk.metadata = {"path": "src/Foo.java"}
        im.splitter.split_documents.return_value = [mock_chunk]
        im._point_ops.generate_point_id.return_value = "exact-point-id"
        im._point_ops.embed_and_create_points.return_value = [MagicMock()]
        im._point_ops.upsert_points.return_value = (1, 0)

        snapshot = ContextSnapshotV1(
            base_sha="a" * 40,
            head_sha="b" * 40,
            merge_base_sha="c" * 40,
        )
        file_info = SimpleNamespace(
            content="public class Foo {}",
            path="src/Foo.java",
            change_type="ADDED",
        )
        req = SimpleNamespace(
            workspace="ws",
            project="proj",
            pr_number=42,
            branch="feat",
            files=[file_info],
            snapshot=snapshot,
            execution_id="execution-42",
        )

        result = index_pr_files(req)

        assert mock_chunk.metadata["snapshot_sha"] == "b" * 40
        assert mock_chunk.metadata["context_snapshot_id"] == snapshot.identity
        assert mock_chunk.metadata["execution_id"] == "execution-42"
        im._point_ops.generate_point_id.assert_called_once()
        assert result["context_snapshot_id"] == snapshot.identity

    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_skip_empty_content(self, mock_get):
        im = _make_index_manager()
        mock_get.return_value = im

        from rag_pipeline.api.routers.pr import index_pr_files

        file_info = MagicMock()
        file_info.content = ""
        file_info.path = "empty.java"
        file_info.change_type = "ADDED"

        req = MagicMock()
        req.workspace = "ws"
        req.project = "proj"
        req.pr_number = 42
        req.branch = "feat"
        req.files = [file_info]

        result = index_pr_files(req)
        assert result["status"] == "skipped"

    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_skip_deleted_files(self, mock_get):
        im = _make_index_manager()
        mock_get.return_value = im

        from rag_pipeline.api.routers.pr import index_pr_files

        file_info = MagicMock()
        file_info.content = "class Foo {}"
        file_info.path = "Deleted.java"
        file_info.change_type = "DELETED"

        req = MagicMock()
        req.workspace = "ws"
        req.project = "proj"
        req.pr_number = 42
        req.branch = "feat"
        req.files = [file_info]

        result = index_pr_files(req)
        assert result["status"] == "skipped"

    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_delete_existing_pr_points_error_handled(self, mock_get):
        im = _make_index_manager()
        # Simulate error deleting existing PR points — should not stop indexing
        im.qdrant_client.delete.side_effect = [RuntimeError("delete err"), None]
        mock_get.return_value = im

        from rag_pipeline.api.routers.pr import index_pr_files

        file_info = MagicMock()
        file_info.content = "code"
        file_info.path = "a.java"
        file_info.change_type = "MODIFIED"

        mock_chunk = MagicMock()
        mock_chunk.metadata = {"path": "a.java"}
        im.splitter.split_documents.return_value = [mock_chunk]
        im._point_ops.embed_and_create_points.return_value = [MagicMock()]
        im._point_ops.upsert_points.return_value = (1, 0)

        req = MagicMock()
        req.workspace = "ws"
        req.project = "proj"
        req.pr_number = 42
        req.branch = "feat"
        req.files = [file_info]

        result = index_pr_files(req)
        assert result["status"] == "indexed"

    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_value_error_raises_400(self, mock_get):
        im = _make_index_manager()
        im._get_project_collection_name.side_effect = ValueError("bad input")
        mock_get.return_value = im

        from rag_pipeline.api.routers.pr import index_pr_files

        req = MagicMock()
        req.workspace = "ws"
        req.project = "proj"
        req.pr_number = 42
        req.branch = "feat"
        req.files = []

        with pytest.raises(HTTPException) as exc_info:
            index_pr_files(req)
        assert exc_info.value.status_code == 400

    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_internal_error_raises_500(self, mock_get):
        im = _make_index_manager()
        im._ensure_collection_exists.side_effect = RuntimeError("qdrant down")
        mock_get.return_value = im

        from rag_pipeline.api.routers.pr import index_pr_files

        req = MagicMock()
        req.workspace = "ws"
        req.project = "proj"
        req.pr_number = 42
        req.branch = "feat"
        req.files = []

        with pytest.raises(HTTPException) as exc_info:
            index_pr_files(req)
        assert exc_info.value.status_code == 500


# ─────────────────────────────────────────────────────────────
# delete_pr_files
# ─────────────────────────────────────────────────────────────
class TestDeletePRFiles:

    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_success(self, mock_get):
        im = _make_index_manager()
        mock_get.return_value = im

        from rag_pipeline.api.routers.pr import delete_pr_files
        result = delete_pr_files("ws", "proj", 42)
        assert result["status"] == "deleted"
        assert result["pr_number"] == 42

    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_collection_not_found(self, mock_get):
        im = _make_index_manager()
        im._collection_manager.collection_exists.return_value = False
        mock_get.return_value = im

        from rag_pipeline.api.routers.pr import delete_pr_files
        result = delete_pr_files("ws", "proj", 42)
        assert result["status"] == "skipped"

    @patch("rag_pipeline.api.routers.pr._get_index_manager")
    def test_error_raises_500(self, mock_get):
        im = _make_index_manager()
        im.qdrant_client.delete.side_effect = RuntimeError("err")
        mock_get.return_value = im

        from rag_pipeline.api.routers.pr import delete_pr_files
        with pytest.raises(HTTPException):
            delete_pr_files("ws", "proj", 42)
