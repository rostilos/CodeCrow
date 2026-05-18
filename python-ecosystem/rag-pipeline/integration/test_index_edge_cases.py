"""
Additional integration tests for /index endpoints — edge cases & error flows.
Complements test_index_endpoints.py for deeper coverage.
"""
import os
import pytest
from unittest.mock import MagicMock
from rag_pipeline.models.config import IndexStats


@pytest.mark.asyncio
class TestIndexRepositoryEdgeCases:
    """POST /index/repository edge cases."""

    async def test_index_repository_value_error(self, client, auth_headers, rag_app, tmp_path):
        """ValueError from index_manager → 400."""
        import rag_pipeline.api.api as api_module
        api_module.index_manager.index_repository.side_effect = ValueError("bad input")

        repo_path = str(tmp_path / "repo")
        os.makedirs(repo_path, exist_ok=True)

        old_root = os.environ.get("ALLOWED_REPO_ROOT")
        os.environ["ALLOWED_REPO_ROOT"] = str(tmp_path)
        try:
            resp = await client.post("/index/repository", json={
                "repo_path": repo_path,
                "workspace": "ws1",
                "project": "proj1",
                "branch": "main",
                "commit": "abc",
            }, headers=auth_headers)
            assert resp.status_code == 400
        finally:
            api_module.index_manager.index_repository.side_effect = None
            if old_root is None:
                os.environ.pop("ALLOWED_REPO_ROOT", None)
            else:
                os.environ["ALLOWED_REPO_ROOT"] = old_root

    async def test_index_repository_internal_error(self, client, auth_headers, rag_app, tmp_path):
        """RuntimeError from index_manager → 500."""
        import rag_pipeline.api.api as api_module
        api_module.index_manager.index_repository.side_effect = RuntimeError("crash")

        repo_path = str(tmp_path / "repo")
        os.makedirs(repo_path, exist_ok=True)

        old_root = os.environ.get("ALLOWED_REPO_ROOT")
        os.environ["ALLOWED_REPO_ROOT"] = str(tmp_path)
        try:
            resp = await client.post("/index/repository", json={
                "repo_path": repo_path,
                "workspace": "ws1",
                "project": "proj1",
                "branch": "main",
                "commit": "abc",
            }, headers=auth_headers)
            assert resp.status_code == 500
        finally:
            api_module.index_manager.index_repository.side_effect = None
            if old_root is None:
                os.environ.pop("ALLOWED_REPO_ROOT", None)
            else:
                os.environ["ALLOWED_REPO_ROOT"] = old_root

    async def test_index_repository_with_patterns(self, client, auth_headers, rag_app, tmp_path):
        """Include/exclude patterns forwarded to service."""
        import rag_pipeline.api.api as api_module
        fake_stats = IndexStats(
            namespace="ws__p__main", document_count=3, chunk_count=15,
            last_updated="2025-01-01T00:00:00Z", workspace="ws", project="p", branch="main",
        )
        api_module.index_manager.index_repository.return_value = fake_stats

        repo_path = str(tmp_path / "repo")
        os.makedirs(repo_path, exist_ok=True)

        old_root = os.environ.get("ALLOWED_REPO_ROOT")
        os.environ["ALLOWED_REPO_ROOT"] = str(tmp_path)
        try:
            resp = await client.post("/index/repository", json={
                "repo_path": repo_path,
                "workspace": "ws",
                "project": "p",
                "branch": "main",
                "commit": "sha1",
                "include_patterns": ["*.py", "*.java"],
                "exclude_patterns": ["**/node_modules/**"],
            }, headers=auth_headers)
            assert resp.status_code == 200
            body = resp.json()
            assert body["document_count"] == 3
        finally:
            if old_root is None:
                os.environ.pop("ALLOWED_REPO_ROOT", None)
            else:
                os.environ["ALLOWED_REPO_ROOT"] = old_root


@pytest.mark.asyncio
class TestUpdateFilesEdgeCases:
    """POST /index/update-files edge cases."""

    async def test_update_files_success(self, client, auth_headers, rag_app, tmp_path):
        """Update specific files succeeds."""
        import rag_pipeline.api.api as api_module
        fake_stats = IndexStats(
            namespace="ws__p__dev", document_count=2, chunk_count=10,
            last_updated="2025-01-01T00:00:00Z", workspace="ws", project="p", branch="dev",
        )
        api_module.index_manager.update_files.return_value = fake_stats

        repo_base = str(tmp_path / "repo")
        os.makedirs(repo_base, exist_ok=True)

        old_root = os.environ.get("ALLOWED_REPO_ROOT")
        os.environ["ALLOWED_REPO_ROOT"] = str(tmp_path)
        try:
            resp = await client.post("/index/update-files", json={
                "file_paths": ["src/a.py", "src/b.py"],
                "repo_base": repo_base,
                "workspace": "ws",
                "project": "p",
                "branch": "dev",
                "commit": "abc",
            }, headers=auth_headers)
            assert resp.status_code == 200
            assert resp.json()["document_count"] == 2
        finally:
            if old_root is None:
                os.environ.pop("ALLOWED_REPO_ROOT", None)
            else:
                os.environ["ALLOWED_REPO_ROOT"] = old_root

    async def test_update_files_service_error(self, client, auth_headers, rag_app, tmp_path):
        """Service error → 500."""
        import rag_pipeline.api.api as api_module
        api_module.index_manager.update_files.side_effect = RuntimeError("fail")

        repo_base = str(tmp_path / "repo")
        os.makedirs(repo_base, exist_ok=True)

        old_root = os.environ.get("ALLOWED_REPO_ROOT")
        os.environ["ALLOWED_REPO_ROOT"] = str(tmp_path)
        try:
            resp = await client.post("/index/update-files", json={
                "file_paths": ["x.py"],
                "repo_base": repo_base,
                "workspace": "ws",
                "project": "p",
                "branch": "main",
                "commit": "abc",
            }, headers=auth_headers)
            assert resp.status_code == 500
        finally:
            api_module.index_manager.update_files.side_effect = None
            if old_root is None:
                os.environ.pop("ALLOWED_REPO_ROOT", None)
            else:
                os.environ["ALLOWED_REPO_ROOT"] = old_root

    async def test_update_files_no_auth(self, client, tmp_path):
        """Missing auth → 401."""
        resp = await client.post("/index/update-files", json={
            "file_paths": ["x.py"],
            "repo_base": str(tmp_path),
            "workspace": "w",
            "project": "p",
            "branch": "main",
            "commit": "abc",
        })
        assert resp.status_code == 401


@pytest.mark.asyncio
class TestDeleteFilesEdgeCases:
    """POST /index/delete-files edge cases."""

    async def test_delete_files_success(self, client, auth_headers, rag_app):
        """Delete specific files succeeds."""
        import rag_pipeline.api.api as api_module
        fake_stats = IndexStats(
            namespace="ws__p__main", document_count=0, chunk_count=0,
            last_updated="2025-01-01T00:00:00Z", workspace="ws", project="p", branch="main",
        )
        api_module.index_manager.delete_files.return_value = fake_stats

        resp = await client.post("/index/delete-files", json={
            "file_paths": ["old.py", "removed.py"],
            "workspace": "ws",
            "project": "p",
            "branch": "main",
        }, headers=auth_headers)
        assert resp.status_code == 200

    async def test_delete_files_service_error(self, client, auth_headers, rag_app):
        """Service error → 500."""
        import rag_pipeline.api.api as api_module
        api_module.index_manager.delete_files.side_effect = RuntimeError("fail")

        resp = await client.post("/index/delete-files", json={
            "file_paths": ["x.py"],
            "workspace": "ws",
            "project": "p",
            "branch": "main",
        }, headers=auth_headers)
        assert resp.status_code == 500
        api_module.index_manager.delete_files.side_effect = None


@pytest.mark.asyncio
class TestDeleteIndexEdgeCases:
    """DELETE /index/{w}/{p}/{b} edge cases."""

    async def test_delete_index_error(self, client, auth_headers, rag_app):
        """Service error → 500."""
        import rag_pipeline.api.api as api_module
        api_module.index_manager.delete_index.side_effect = RuntimeError("fail")

        resp = await client.request("DELETE", "/index/ws/p/main", headers=auth_headers)
        assert resp.status_code == 500
        api_module.index_manager.delete_index.side_effect = None


@pytest.mark.asyncio
class TestBranchManagementEdgeCases:
    """Branch management endpoint edge cases."""

    async def test_list_branches_error(self, client, auth_headers, rag_app):
        """Service error on branch listing → 500."""
        import rag_pipeline.api.api as api_module
        api_module.index_manager.get_indexed_branches.side_effect = RuntimeError("fail")

        resp = await client.get("/index/ws/p/branches", headers=auth_headers)
        assert resp.status_code == 500
        api_module.index_manager.get_indexed_branches.side_effect = None

    async def test_list_branches_empty(self, client, auth_headers, rag_app):
        """No branches → empty list."""
        import rag_pipeline.api.api as api_module
        api_module.index_manager.get_indexed_branches.return_value = []

        resp = await client.get("/index/ws/p/branches", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_branches"] == 0
        assert data["branches"] == []

    async def test_delete_branch_error(self, client, auth_headers, rag_app):
        """Service error on branch delete → 500."""
        import rag_pipeline.api.api as api_module
        api_module.index_manager.delete_branch.side_effect = RuntimeError("fail")

        resp = await client.request("DELETE", "/index/ws/p/branch/x", headers=auth_headers)
        assert resp.status_code == 500
        api_module.index_manager.delete_branch.side_effect = None

    async def test_cleanup_partial_failure(self, client, auth_headers, rag_app):
        """Cleanup where some branches fail to delete."""
        import rag_pipeline.api.api as api_module
        api_module.index_manager.get_indexed_branches.return_value = [
            "main", "stale-1", "stale-2"
        ]

        def side_effect_fn(ws, proj, branch):
            if branch == "stale-2":
                raise RuntimeError("fail to delete stale-2")
            return True

        api_module.index_manager.delete_branch.side_effect = side_effect_fn

        resp = await client.post(
            "/index/ws/p/cleanup-branches",
            json={
                "workspace": "ws",
                "project": "p",
                "protected_branches": ["main"],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "stale-1" in data["deleted_branches"]
        assert "stale-2" in data["failed_branches"]
        api_module.index_manager.delete_branch.side_effect = None

    async def test_cleanup_no_stale_branches(self, client, auth_headers, rag_app):
        """All branches are protected → nothing deleted."""
        import rag_pipeline.api.api as api_module
        api_module.index_manager.get_indexed_branches.return_value = ["main", "dev"]

        resp = await client.post(
            "/index/ws/p/cleanup-branches",
            json={
                "workspace": "ws",
                "project": "p",
                "protected_branches": ["main", "dev"],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_deleted"] == 0

    async def test_get_index_stats_error(self, client, auth_headers, rag_app):
        """Stats service error → 500."""
        import rag_pipeline.api.api as api_module
        api_module.index_manager._get_index_stats.side_effect = RuntimeError("fail")

        resp = await client.get("/index/stats/ws/p/main", headers=auth_headers)
        assert resp.status_code == 500
        api_module.index_manager._get_index_stats.side_effect = None

    async def test_list_indices_with_data(self, client, auth_headers, rag_app):
        """List indices returns populated list."""
        import rag_pipeline.api.api as api_module
        api_module.index_manager.list_indices.return_value = [
            IndexStats(
                namespace="ws__p__main", document_count=5, chunk_count=50,
                last_updated="2025-01-01T00:00:00Z", workspace="ws", project="p", branch="main",
            ),
            IndexStats(
                namespace="ws__p__dev", document_count=3, chunk_count=30,
                last_updated="2025-01-02T00:00:00Z", workspace="ws", project="p", branch="dev",
            ),
        ]

        resp = await client.get("/index/list", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["branch"] == "main"
        assert data[1]["branch"] == "dev"

    async def test_list_indices_error(self, client, auth_headers, rag_app):
        """Service error → 500."""
        import rag_pipeline.api.api as api_module
        api_module.index_manager.list_indices.side_effect = RuntimeError("fail")

        resp = await client.get("/index/list", headers=auth_headers)
        assert resp.status_code == 500
        api_module.index_manager.list_indices.side_effect = None


@pytest.mark.asyncio
class TestEstimateEdgeCases:
    """POST /index/estimate edge cases."""

    async def test_estimate_exceeds_file_limit(self, client, auth_headers, rag_app, tmp_path):
        """Estimate where file count exceeds limit."""
        import rag_pipeline.api.api as api_module
        api_module.index_manager.estimate_repository_size.return_value = (10000, 50000)

        repo_path = str(tmp_path / "repo")
        os.makedirs(repo_path, exist_ok=True)

        old_root = os.environ.get("ALLOWED_REPO_ROOT")
        os.environ["ALLOWED_REPO_ROOT"] = str(tmp_path)
        try:
            resp = await client.post("/index/estimate", json={
                "repo_path": repo_path,
            }, headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["within_limits"] is False
            assert "exceeds limit" in data["message"].lower() or "limit" in data["message"].lower()
        finally:
            if old_root is None:
                os.environ.pop("ALLOWED_REPO_ROOT", None)
            else:
                os.environ["ALLOWED_REPO_ROOT"] = old_root

    async def test_estimate_within_limits(self, client, auth_headers, rag_app, tmp_path):
        """Estimate within limits returns success message."""
        import rag_pipeline.api.api as api_module
        api_module.index_manager.estimate_repository_size.return_value = (10, 100)

        repo_path = str(tmp_path / "repo")
        os.makedirs(repo_path, exist_ok=True)

        old_root = os.environ.get("ALLOWED_REPO_ROOT")
        os.environ["ALLOWED_REPO_ROOT"] = str(tmp_path)
        try:
            resp = await client.post("/index/estimate", json={
                "repo_path": repo_path,
            }, headers=auth_headers)
            assert resp.status_code == 200
            data = resp.json()
            assert data["within_limits"] is True
        finally:
            if old_root is None:
                os.environ.pop("ALLOWED_REPO_ROOT", None)
            else:
                os.environ["ALLOWED_REPO_ROOT"] = old_root

    async def test_estimate_service_error(self, client, auth_headers, rag_app, tmp_path):
        """Service error → 500."""
        import rag_pipeline.api.api as api_module
        api_module.index_manager.estimate_repository_size.side_effect = RuntimeError("fail")

        repo_path = str(tmp_path / "repo")
        os.makedirs(repo_path, exist_ok=True)

        old_root = os.environ.get("ALLOWED_REPO_ROOT")
        os.environ["ALLOWED_REPO_ROOT"] = str(tmp_path)
        try:
            resp = await client.post("/index/estimate", json={
                "repo_path": repo_path,
            }, headers=auth_headers)
            assert resp.status_code == 500
        finally:
            api_module.index_manager.estimate_repository_size.side_effect = None
            if old_root is None:
                os.environ.pop("ALLOWED_REPO_ROOT", None)
            else:
                os.environ["ALLOWED_REPO_ROOT"] = old_root


@pytest.mark.asyncio
class TestDeprecatedEndpoints:
    """Deprecated /branch/* redirects still work."""

    async def test_deprecated_delete_branch(self, client, auth_headers, rag_app):
        """DELETE /branch/{w}/{p}/{b} — deprecated redirect."""
        import rag_pipeline.api.api as api_module
        api_module.index_manager.delete_branch.return_value = True

        resp = await client.request(
            "DELETE", "/branch/ws/p/main", headers=auth_headers
        )
        assert resp.status_code == 200

    async def test_deprecated_delete_branch_post(self, client, auth_headers, rag_app):
        """POST /branch/delete — deprecated redirect."""
        import rag_pipeline.api.api as api_module
        api_module.index_manager.delete_branch.return_value = True

        resp = await client.post("/branch/delete", json={
            "workspace": "ws",
            "project": "p",
            "branch": "old",
        }, headers=auth_headers)
        assert resp.status_code == 200

    async def test_deprecated_list_branches(self, client, auth_headers, rag_app):
        """GET /branch/list/{w}/{p} — deprecated redirect."""
        import rag_pipeline.api.api as api_module
        api_module.index_manager.get_indexed_branches.return_value = ["main"]
        api_module.index_manager.get_branch_point_count.return_value = 10

        resp = await client.get("/branch/list/ws/p", headers=auth_headers)
        assert resp.status_code == 200

    async def test_deprecated_branch_stats(self, client, auth_headers, rag_app):
        """GET /branch/stats/{w}/{p}/{b} — deprecated redirect."""
        import rag_pipeline.api.api as api_module
        fake_stats = IndexStats(
            namespace="ws__p__main", document_count=5, chunk_count=50,
            last_updated="2025-01-01T00:00:00Z", workspace="ws", project="p", branch="main",
        )
        api_module.index_manager._get_index_stats.return_value = fake_stats

        resp = await client.get("/branch/stats/ws/p/main", headers=auth_headers)
        assert resp.status_code == 200
