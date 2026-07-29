"""Integration tests: RAG pipeline /index and /limits endpoints."""
import os
import pytest
from unittest.mock import MagicMock


@pytest.mark.asyncio
async def test_get_limits(client, auth_headers):
    """GET /limits → current RAG limits from mocked config."""
    resp = await client.get("/limits", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    for key in ("max_chunks_per_index", "max_files_per_index",
                "max_file_size_bytes", "chunk_size", "chunk_overlap"):
        assert key in data


@pytest.mark.asyncio
async def test_index_repository(client, auth_headers, rag_app, tmp_path):
    """POST /index/repository succeeds with mocked index_manager."""
    import rag_pipeline.api.api as api_module
    from rag_pipeline.models.config import IndexStats

    fake_stats = IndexStats(
        namespace="ws1__proj1__main",
        document_count=5,
        chunk_count=20,
        last_updated="2025-01-01T00:00:00Z",
        workspace="ws1",
        project="proj1",
        branch="main",
    )
    api_module.index_manager.index_repository.return_value = fake_stats

    repo_path = str(tmp_path / "repo")
    os.makedirs(repo_path, exist_ok=True)

    # Ensure ALLOWED_REPO_ROOT includes tmp_path
    old_root = os.environ.get("ALLOWED_REPO_ROOT")
    os.environ["ALLOWED_REPO_ROOT"] = str(tmp_path)
    try:
        resp = await client.post("/index/repository", json={
            "repo_path": repo_path,
            "workspace": "ws1",
            "project": "proj1",
            "branch": "main",
            "commit": "abc123",
            "source_tree_sha256": "c" * 64,
        }, headers=auth_headers)
        assert resp.status_code == 200
    finally:
        if old_root is None:
            os.environ.pop("ALLOWED_REPO_ROOT", None)
        else:
            os.environ["ALLOWED_REPO_ROOT"] = old_root


@pytest.mark.asyncio
async def test_index_repository_no_auth(client, tmp_path):
    resp = await client.post("/index/repository", json={
        "repo_path": str(tmp_path),
        "workspace": "w",
        "project": "p",
        "branch": "main",
        "commit": "x",
        "source_tree_sha256": "c" * 64,
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_delete_index(client, auth_headers, rag_app):
    """DELETE /index/{w}/{p}/{b} → 200."""
    import rag_pipeline.api.api as api_module
    api_module.index_manager.delete_index.return_value = None

    resp = await client.request(
        "DELETE", "/index/ws1/proj1/main", headers=auth_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "deleted" in data.get("message", "").lower() or "Index deleted" in data.get("message", "")


@pytest.mark.asyncio
async def test_list_branches(client, auth_headers, rag_app):
    """GET /index/{w}/{p}/branches → list of branches."""
    import rag_pipeline.api.api as api_module
    api_module.index_manager.get_indexed_branches.return_value = ["main", "dev"]
    api_module.index_manager.get_branch_point_count.return_value = 42

    resp = await client.get("/index/ws1/proj1/branches", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_branches"] == 2
    assert len(data["branches"]) == 2


@pytest.mark.asyncio
async def test_exact_revision_preflight(client, auth_headers, rag_app):
    """GET /index/{w}/{p}/revision verifies one full commit snapshot."""
    import rag_pipeline.api.api as api_module

    commit = "a" * 40
    api_module.index_manager.get_revision_preflight.return_value = {
        "workspace": "ws1",
        "project": "proj1",
        "branch": "main",
        "commit": commit,
        "point_count": 42,
        "repository_revision": commit,
        "repository_facts_sha256": "b" * 64,
        "plugin_ids": ["php", "magento"],
        "plugin_fingerprint": "sha256:selection",
        "plugin_descriptor_fingerprint": "sha256:descriptor",
        "plugin_implementation_fingerprint": "sha256:implementation",
        "index_representation_fingerprint": "sha256:representation",
        "generation_schema": "codecrow.repository-index-generation",
        "generation_member_count": 41,
        "generation_members_sha256": "c" * 64,
        "generation_manifest_sha256": "d" * 64,
        "source_tree_sha256": "e" * 64,
    }

    resp = await client.get(
        "/index/ws1/proj1/revision",
        params={"branch": "main", "commit": commit},
        headers=auth_headers,
    )

    assert resp.status_code == 200
    assert resp.json()["point_count"] == 42
    assert resp.json()["repository_revision"] == commit
    api_module.index_manager.get_revision_preflight.assert_called_with(
        "ws1",
        "proj1",
        "main",
        commit,
    )


@pytest.mark.asyncio
async def test_exact_revision_preflight_rejects_abbreviated_sha(
    client,
    auth_headers,
    rag_app,
):
    import rag_pipeline.api.api as api_module

    api_module.index_manager.get_revision_preflight.reset_mock()
    resp = await client.get(
        "/index/ws1/proj1/revision",
        params={"branch": "main", "commit": "abc123"},
        headers=auth_headers,
    )

    assert resp.status_code == 422
    api_module.index_manager.get_revision_preflight.assert_not_called()


@pytest.mark.asyncio
async def test_delete_branch(client, auth_headers, rag_app):
    """DELETE /index/{w}/{p}/branch/{b} → success."""
    import rag_pipeline.api.api as api_module
    api_module.index_manager.delete_branch.return_value = True

    resp = await client.request(
        "DELETE", "/index/ws1/proj1/branch/feature-x", headers=auth_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"


@pytest.mark.asyncio
async def test_delete_branch_not_found(client, auth_headers, rag_app):
    import rag_pipeline.api.api as api_module
    api_module.index_manager.delete_branch.return_value = False

    resp = await client.request(
        "DELETE", "/index/ws1/proj1/branch/gone", headers=auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "not_found"


@pytest.mark.asyncio
async def test_cleanup_stale_branches(client, auth_headers, rag_app):
    """POST /index/{w}/{p}/cleanup-branches → cleanup result."""
    import rag_pipeline.api.api as api_module
    api_module.index_manager.get_indexed_branches.return_value = [
        "main", "dev", "stale-1", "stale-2"
    ]
    api_module.index_manager.delete_branch.return_value = True

    resp = await client.post(
        "/index/ws1/proj1/cleanup-branches",
        json={
            "workspace": "ws1",
            "project": "proj1",
            "protected_branches": ["main"],
            "branches_to_keep": ["dev"],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    # stale-1 and stale-2 should be in deleted_branches (not protected or kept)
    assert "stale-1" in data["deleted_branches"]
    assert "stale-2" in data["deleted_branches"]


@pytest.mark.asyncio
async def test_get_index_stats(client, auth_headers, rag_app):
    """GET /index/stats/{w}/{p}/{b} → IndexStats."""
    import rag_pipeline.api.api as api_module
    from rag_pipeline.models.config import IndexStats

    fake_stats = IndexStats(
        namespace="ws1__proj1__main",
        document_count=10,
        chunk_count=50,
        last_updated="2025-01-01T00:00:00Z",
        workspace="ws1",
        project="proj1",
        branch="main",
    )
    api_module.index_manager._get_index_stats.return_value = fake_stats

    resp = await client.get("/index/stats/ws1/proj1/main", headers=auth_headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_indices(client, auth_headers, rag_app):
    import rag_pipeline.api.api as api_module
    api_module.index_manager.list_indices.return_value = []
    resp = await client.get("/index/list", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_estimate_repository(client, auth_headers, rag_app, tmp_path):
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
        assert data["file_count"] == 10
        assert data["estimated_chunks"] == 100
    finally:
        if old_root is None:
            os.environ.pop("ALLOWED_REPO_ROOT", None)
        else:
            os.environ["ALLOWED_REPO_ROOT"] = old_root


@pytest.mark.asyncio
async def test_apply_changes_endpoint_forwards_one_commit(
    client,
    auth_headers,
    rag_app,
    tmp_path,
):
    import rag_pipeline.api.api as api_module
    from rag_pipeline.models.config import IndexStats

    api_module.index_manager.apply_changes.return_value = IndexStats(
        namespace="ws1__proj1__main",
        document_count=2,
        chunk_count=4,
        last_updated="2025-01-01T00:00:00Z",
        workspace="ws1",
        project="proj1",
        branch="main",
    )
    repository = tmp_path / "repository"
    repository.mkdir()
    old_root = os.environ.get("ALLOWED_REPO_ROOT")
    os.environ["ALLOWED_REPO_ROOT"] = str(tmp_path)
    try:
        response = await client.post(
            "/index/apply-changes",
            json={
                "updated_file_paths": ["src/A.py"],
                "deleted_file_paths": ["src/B.py"],
                "repo_base": str(repository),
                "workspace": "ws1",
                "project": "proj1",
                "branch": "main",
                "commit": "abc123",
            },
            headers=auth_headers,
        )
    finally:
        if old_root is None:
            os.environ.pop("ALLOWED_REPO_ROOT", None)
        else:
            os.environ["ALLOWED_REPO_ROOT"] = old_root

    assert response.status_code == 200
    api_module.index_manager.apply_changes.assert_called_once_with(
        updated_file_paths=["src/A.py"],
        deleted_file_paths=["src/B.py"],
        repo_base=str(repository),
        workspace="ws1",
        project="proj1",
        branch="main",
        commit="abc123",
    )
