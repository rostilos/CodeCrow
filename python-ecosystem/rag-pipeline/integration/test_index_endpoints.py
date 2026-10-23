"""Integration coverage for exact-generation index endpoints."""

import os

import pytest

from rag_pipeline.models.config import IndexStats


def _stats() -> IndexStats:
    return IndexStats(
        namespace="ws__project__main",
        document_count=5,
        chunk_count=20,
        last_updated="2026-01-01T00:00:00Z",
        workspace="ws",
        project="project",
        branch="main",
        generation_manifest_sha256="b" * 64,
        source_tree_sha256="a" * 64,
        collection_target="generation-target",
    )


def _preflight_receipt() -> dict:
    return {
        "workspace": "ws",
        "project": "project",
        "branch": "main",
        "commit": "commit",
        "point_count": 5,
        "repository_revision": "commit",
        "repository_facts_sha256": "1" * 64,
        "plugin_ids": ["python"],
        "plugin_fingerprint": "sha256:selection",
        "plugin_descriptor_fingerprint": "sha256:descriptor",
        "plugin_implementation_fingerprint": "sha256:implementation",
        "index_representation_fingerprint": "sha256:representation",
        "current_index_representation_fingerprint": "sha256:representation",
        "generation_schema": "codecrow.repository-generation.v2",
        "generation_member_count": 4,
        "generation_members_sha256": "2" * 64,
        "generation_manifest_sha256": "3" * 64,
        "source_tree_sha256": "4" * 64,
        "index_include_patterns": ["src/**"],
        "index_exclude_patterns": ["vendor/**"],
        "index_selection_policy_sha256": "5" * 64,
    }


@pytest.mark.asyncio
async def test_full_index_requires_and_forwards_exact_target(
    client, auth_headers, rag_app, tmp_path
):
    import rag_pipeline.api.api as api_module

    api_module.index_manager.index_repository.return_value = _stats()
    repository = tmp_path / "repository"
    repository.mkdir()
    previous_root = os.environ.get("ALLOWED_REPO_ROOT")
    os.environ["ALLOWED_REPO_ROOT"] = str(tmp_path)
    try:
        response = await client.post(
            "/index/repository",
            json={
                "repo_path": str(repository),
                "workspace": "ws",
                "project": "project",
                "branch": "main",
                "commit": "commit",
                "source_tree_sha256": "a" * 64,
                "collection_target": "generation-target",
            },
            headers=auth_headers,
        )
    finally:
        if previous_root is None:
            os.environ.pop("ALLOWED_REPO_ROOT", None)
        else:
            os.environ["ALLOWED_REPO_ROOT"] = previous_root

    assert response.status_code == 200
    assert api_module.index_manager.index_repository.call_args.kwargs[
        "collection_target"
    ] == "generation-target"


@pytest.mark.asyncio
async def test_full_index_accepts_server_owned_exact_identity(
    client, auth_headers, rag_app, tmp_path
):
    import rag_pipeline.api.api as api_module

    api_module.index_manager.index_repository.return_value = _stats()
    (tmp_path / "Example.php").write_text("<?php\n", encoding="utf-8")
    previous_root = os.environ.get("ALLOWED_REPO_ROOT")
    os.environ["ALLOWED_REPO_ROOT"] = str(tmp_path)
    try:
        response = await client.post(
            "/index/repository",
            json={
                "repo_path": str(tmp_path),
                "workspace": "ws",
                "project": "project",
                "branch": "main",
                "commit": "commit",
            },
            headers=auth_headers,
        )
    finally:
        if previous_root is None:
            os.environ.pop("ALLOWED_REPO_ROOT", None)
        else:
            os.environ["ALLOWED_REPO_ROOT"] = previous_root

    assert response.status_code == 200
    forwarded = api_module.index_manager.index_repository.call_args.kwargs
    assert forwarded["source_tree_sha256"] is None
    assert forwarded["collection_target"].startswith("cc_http_g_")


@pytest.mark.asyncio
async def test_full_index_rejects_malformed_explicit_source_identity(
    client, auth_headers, tmp_path
):
    previous_root = os.environ.get("ALLOWED_REPO_ROOT")
    os.environ["ALLOWED_REPO_ROOT"] = str(tmp_path)
    try:
        response = await client.post(
            "/index/repository",
            json={
                "repo_path": str(tmp_path),
                "workspace": "ws",
                "project": "project",
                "branch": "main",
                "commit": "commit",
                "source_tree_sha256": "not-a-digest",
            },
            headers=auth_headers,
        )
    finally:
        if previous_root is None:
            os.environ.pop("ALLOWED_REPO_ROOT", None)
        else:
            os.environ["ALLOWED_REPO_ROOT"] = previous_root

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_exact_revision_preflight_returns_sealed_target_receipt(
    client, auth_headers, rag_app
):
    import rag_pipeline.api.api as api_module

    preflight = api_module.index_manager.get_revision_preflight
    prior_return_value = preflight.return_value
    preflight.reset_mock()
    try:
        preflight.return_value = _preflight_receipt()
        response = await client.get(
            "/index/ws/project/revision",
            params={
                "branch": "main",
                "commit": "commit",
                "collection_target": "cc_http_g_exact",
            },
            headers=auth_headers,
        )

        assert response.status_code == 200
        assert response.json()["generation_manifest_sha256"] == "3" * 64
        assert response.json()["generation_member_count"] == 4
        assert response.json()["current_index_representation_fingerprint"] == (
            "sha256:representation"
        )
        preflight.assert_called_once_with(
            "ws",
            "project",
            "main",
            "commit",
            collection_target="cc_http_g_exact",
        )
    finally:
        preflight.return_value = prior_return_value
        preflight.reset_mock()


@pytest.mark.asyncio
async def test_exact_revision_preflight_rejects_target_discovery(
    client, auth_headers
):
    response = await client.get(
        "/index/ws/project/revision",
        params={"branch": "main", "commit": "commit"},
        headers=auth_headers,
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_exact_generation_delete_forwards_registry_receipt(
    client, auth_headers, rag_app
):
    import rag_pipeline.api.api as api_module

    api_module.index_manager.delete_branch.return_value = True
    response = await client.delete(
        "/index/ws/project/branch/main",
        params={
            "collection_target": "generation-target",
            "generation_revision": "commit",
            "generation_manifest_sha256": "b" * 64,
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "success"
