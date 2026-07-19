"""
Tests for rag_pipeline.api.routers.index — Index & branch management endpoints.

Covers:
- get_limits
- estimate_repository
- index_repository
- update_files, delete_files
- delete_index
- delete_branch, list_branches, cleanup_stale_branches
- get_index_stats, list_indices
- deprecated branch redirects
"""
import pytest
from unittest.mock import patch, MagicMock
from fastapi import HTTPException

from rag_pipeline.models.config import IndexStats


def _mock_singletons(config_overrides=None, index_manager=None):
    """Return (config_mock, index_manager_mock)."""
    config = MagicMock()
    config.max_chunks_per_index = 100000
    config.max_files_per_index = 50000
    config.max_file_size_bytes = 1048576
    config.chunk_size = 8000
    config.chunk_overlap = 200
    if config_overrides:
        for k, v in config_overrides.items():
            setattr(config, k, v)

    im = index_manager or MagicMock()
    return config, im


# ─────────────────────────────────────────────────────────────
# get_limits
# ─────────────────────────────────────────────────────────────
class TestGetLimits:

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_returns_limits(self, mock_get):
        config, im = _mock_singletons()
        mock_get.return_value = (config, im)

        from rag_pipeline.api.routers.index import get_limits
        result = get_limits()

        assert result["max_chunks_per_index"] == 100000
        assert result["max_files_per_index"] == 50000
        assert result["chunk_size"] == 8000


# ─────────────────────────────────────────────────────────────
# estimate_repository
# ─────────────────────────────────────────────────────────────
class TestEstimateRepository:

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_within_limits(self, mock_get):
        config, im = _mock_singletons()
        im.estimate_repository_size.return_value = (100, 500)
        mock_get.return_value = (config, im)

        from rag_pipeline.api.routers.index import estimate_repository
        from rag_pipeline.api.models import EstimateRequest

        req = MagicMock(spec=EstimateRequest)
        req.repo_path = "/tmp/repo"
        req.include_patterns = None
        req.exclude_patterns = None

        result = estimate_repository(req)
        assert result.within_limits is True
        assert result.file_count == 100

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_exceeds_limits(self, mock_get):
        config, im = _mock_singletons(config_overrides={"max_files_per_index": 50})
        im.estimate_repository_size.return_value = (100, 500)
        mock_get.return_value = (config, im)

        from rag_pipeline.api.routers.index import estimate_repository

        req = MagicMock()
        req.repo_path = "/tmp/repo"
        req.include_patterns = None
        req.exclude_patterns = None

        result = estimate_repository(req)
        assert result.within_limits is False
        assert "exceeds limit" in result.message

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_error_raises_500(self, mock_get):
        config, im = _mock_singletons()
        im.estimate_repository_size.side_effect = RuntimeError("disk error")
        mock_get.return_value = (config, im)

        from rag_pipeline.api.routers.index import estimate_repository

        req = MagicMock()
        req.repo_path = "/tmp/repo"
        req.include_patterns = None
        req.exclude_patterns = None

        with pytest.raises(HTTPException) as exc_info:
            estimate_repository(req)
        assert exc_info.value.status_code == 500


# ─────────────────────────────────────────────────────────────
# index_repository
# ─────────────────────────────────────────────────────────────
class TestIndexRepository:

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_success(self, mock_get):
        _, im = _mock_singletons()
        stats = IndexStats(
            namespace="ns", document_count=10, chunk_count=50,
            last_updated="2024-01-01", workspace="ws", project="proj", branch="main"
        )
        im.index_repository.return_value = stats
        mock_get.return_value = (_, im)

        from rag_pipeline.api.routers.index import index_repository

        req = MagicMock()
        req.repo_path = "/tmp/repo"
        req.workspace = "ws"
        req.project = "proj"
        req.branch = "main"
        req.commit = "abc"
        req.include_patterns = None
        req.exclude_patterns = None
        req.retain_revisions = True

        result = index_repository(req, MagicMock())
        assert result.document_count == 10
        assert im.index_repository.call_args.kwargs["retain_revisions"] is True

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_validation_error_raises_400(self, mock_get):
        _, im = _mock_singletons()
        im.index_repository.side_effect = ValueError("exceeds file limit")
        mock_get.return_value = (_, im)

        from rag_pipeline.api.routers.index import index_repository

        req = MagicMock()
        req.repo_path = "/tmp/repo"
        req.workspace = "ws"
        req.project = "proj"
        req.branch = "main"
        req.commit = "abc"
        req.include_patterns = None
        req.exclude_patterns = None

        with pytest.raises(HTTPException) as exc_info:
            index_repository(req, MagicMock())
        assert exc_info.value.status_code == 400

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_internal_error_raises_500(self, mock_get):
        _, im = _mock_singletons()
        im.index_repository.side_effect = RuntimeError("qdrant down")
        mock_get.return_value = (_, im)

        from rag_pipeline.api.routers.index import index_repository

        req = MagicMock()
        req.repo_path = "/tmp/repo"
        req.workspace = "ws"
        req.project = "proj"
        req.branch = "main"
        req.commit = "abc"
        req.include_patterns = None
        req.exclude_patterns = None

        with pytest.raises(HTTPException) as exc_info:
            index_repository(req, MagicMock())
        assert exc_info.value.status_code == 500


# ─────────────────────────────────────────────────────────────
# update_files / delete_files
# ─────────────────────────────────────────────────────────────
class TestFileEndpoints:

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_update_files_success(self, mock_get):
        _, im = _mock_singletons()
        stats = IndexStats(
            namespace="ns", document_count=10, chunk_count=50,
            last_updated="2024-01-01", workspace="ws", project="proj", branch="main"
        )
        im.update_files.return_value = stats
        mock_get.return_value = (_, im)

        from rag_pipeline.api.routers.index import update_files

        req = MagicMock()
        req.file_paths = ["a.py"]
        req.repo_base = "/tmp/repo"
        req.workspace = "ws"
        req.project = "proj"
        req.branch = "main"
        req.commit = "abc"

        result = update_files(req)
        assert result.document_count == 10

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_update_files_error(self, mock_get):
        _, im = _mock_singletons()
        im.update_files.side_effect = RuntimeError("err")
        mock_get.return_value = (_, im)

        from rag_pipeline.api.routers.index import update_files
        req = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            update_files(req)
        assert exc_info.value.status_code == 500

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_delete_files_success(self, mock_get):
        _, im = _mock_singletons()
        stats = IndexStats(
            namespace="ns", document_count=5, chunk_count=20,
            last_updated="2024-01-01", workspace="ws", project="proj", branch="main"
        )
        im.delete_files.return_value = stats
        mock_get.return_value = (_, im)

        from rag_pipeline.api.routers.index import delete_files

        req = MagicMock()
        req.file_paths = ["a.py"]
        req.workspace = "ws"
        req.project = "proj"
        req.branch = "main"

        result = delete_files(req)
        assert result.document_count == 5


# ─────────────────────────────────────────────────────────────
# delete_index
# ─────────────────────────────────────────────────────────────
class TestDeleteIndex:

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_success(self, mock_get):
        _, im = _mock_singletons()
        mock_get.return_value = (_, im)

        from rag_pipeline.api.routers.index import delete_index
        result = delete_index("ws", "proj", "main")
        assert "message" in result
        im.delete_index.assert_called_once_with("ws", "proj", "main")

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_error(self, mock_get):
        _, im = _mock_singletons()
        im.delete_index.side_effect = RuntimeError("fail")
        mock_get.return_value = (_, im)

        from rag_pipeline.api.routers.index import delete_index
        with pytest.raises(HTTPException):
            delete_index("ws", "proj", "main")


# ─────────────────────────────────────────────────────────────
# Branch management
# ─────────────────────────────────────────────────────────────
class TestBranchEndpoints:

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_delete_branch_success(self, mock_get):
        _, im = _mock_singletons()
        im.delete_branch.return_value = True
        mock_get.return_value = (_, im)

        from rag_pipeline.api.routers.index import delete_branch
        result = delete_branch("ws", "proj", "feat")
        assert result["status"] == "success"

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_delete_branch_not_found(self, mock_get):
        _, im = _mock_singletons()
        im.delete_branch.return_value = False
        mock_get.return_value = (_, im)

        from rag_pipeline.api.routers.index import delete_branch
        result = delete_branch("ws", "proj", "feat")
        assert result["status"] == "not_found"

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_list_branches(self, mock_get):
        _, im = _mock_singletons()
        im.get_indexed_branches.return_value = ["main", "dev"]
        im.get_branch_point_count.side_effect = [100, 50]
        mock_get.return_value = (_, im)

        from rag_pipeline.api.routers.index import list_branches
        result = list_branches("ws", "proj")
        assert result["total_branches"] == 2
        assert len(result["branches"]) == 2

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_get_exact_revision_status(self, mock_get):
        _, im = _mock_singletons()
        im.get_revision_point_count.return_value = 73
        mock_get.return_value = (_, im)

        from rag_pipeline.api.routers.index import get_revision_status

        result = get_revision_status("ws", "proj", "main", "a" * 40)

        assert result["point_count"] == 73
        assert result["indexed"] is True
        im.get_revision_point_count.assert_called_once_with(
            "ws", "proj", "main", "a" * 40
        )

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_cleanup_stale_branches(self, mock_get):
        _, im = _mock_singletons()
        im.get_indexed_branches.return_value = ["main", "stale1", "stale2"]
        im.delete_branch.return_value = True
        mock_get.return_value = (_, im)

        from rag_pipeline.api.routers.index import cleanup_stale_branches

        req = MagicMock()
        req.protected_branches = ["main"]
        req.branches_to_keep = None

        result = cleanup_stale_branches("ws", "proj", req)
        assert result["status"] == "completed"
        assert "stale1" in result["deleted_branches"]
        assert "stale2" in result["deleted_branches"]
        assert result["total_deleted"] == 2


# ─────────────────────────────────────────────────────────────
# Stats & list
# ─────────────────────────────────────────────────────────────
class TestStatsEndpoints:

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_get_index_stats(self, mock_get):
        _, im = _mock_singletons()
        stats = IndexStats(
            namespace="ns", document_count=10, chunk_count=50,
            last_updated="2024-01-01", workspace="ws", project="proj", branch="main"
        )
        im._get_index_stats.return_value = stats
        mock_get.return_value = (_, im)

        from rag_pipeline.api.routers.index import get_index_stats
        result = get_index_stats("ws", "proj", "main")
        assert result.chunk_count == 50

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_list_indices(self, mock_get):
        _, im = _mock_singletons()
        im.list_indices.return_value = []
        mock_get.return_value = (_, im)

        from rag_pipeline.api.routers.index import list_indices
        result = list_indices()
        assert result == []

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_list_indices_error(self, mock_get):
        _, im = _mock_singletons()
        im.list_indices.side_effect = RuntimeError("err")
        mock_get.return_value = (_, im)

        from rag_pipeline.api.routers.index import list_indices
        with pytest.raises(HTTPException):
            list_indices()


# ─────────────────────────────────────────────────────────────
# Deprecated redirects
# ─────────────────────────────────────────────────────────────
class TestDeprecatedEndpoints:

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_deprecated_delete_branch_index(self, mock_get):
        _, im = _mock_singletons()
        im.delete_branch.return_value = True
        mock_get.return_value = (_, im)

        from rag_pipeline.api.routers.index import delete_branch_index
        result = delete_branch_index("ws", "proj", "feat")
        assert result["status"] == "success"

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_deprecated_delete_branch_index_post(self, mock_get):
        _, im = _mock_singletons()
        im.delete_branch.return_value = True
        mock_get.return_value = (_, im)

        from rag_pipeline.api.routers.index import delete_branch_index_post
        req = MagicMock()
        req.workspace = "ws"
        req.project = "proj"
        req.branch = "feat"

        result = delete_branch_index_post(req)
        assert result["status"] == "success"

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_deprecated_list_indexed_branches(self, mock_get):
        _, im = _mock_singletons()
        im.get_indexed_branches.return_value = ["main"]
        im.get_branch_point_count.return_value = 10
        mock_get.return_value = (_, im)

        from rag_pipeline.api.routers.index import list_indexed_branches
        result = list_indexed_branches("ws", "proj")
        assert result["total_branches"] == 1

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_deprecated_get_branch_stats(self, mock_get):
        _, im = _mock_singletons()
        stats = IndexStats(
            namespace="ns", document_count=5, chunk_count=20,
            last_updated="2024-01-01", workspace="ws", project="proj", branch="main"
        )
        im._get_index_stats.return_value = stats
        mock_get.return_value = (_, im)

        from rag_pipeline.api.routers.index import get_branch_stats
        result = get_branch_stats("ws", "proj", "main")
        assert result.chunk_count == 20
