"""Focused tests for the exact-generation repository index API."""

import asyncio
import time
from threading import Event
from unittest.mock import MagicMock, patch

import pytest
from fastapi import BackgroundTasks, HTTPException

from rag_pipeline.api.models import IndexRequest
from rag_pipeline.api.routers.index import (
    _IndexStreamWorkerRegistry,
    _index_collection_target,
    delete_branch,
    discover_revision_preflights,
    get_revision_preflight,
    index_repository,
    index_repository_stream,
)
from rag_pipeline.core.index_manager.manager import RepositoryIndexCancelled
from rag_pipeline.core.exact_index import ExactIndexPreconditionError
from rag_pipeline.core.source_tree import RepositorySourceTreeError
from rag_pipeline.models.config import IndexStats


def _stats() -> IndexStats:
    return IndexStats(
        namespace="ws__project__main",
        document_count=2,
        chunk_count=4,
        last_updated="now",
        workspace="ws",
        project="project",
        branch="main",
        generation_manifest_sha256="b" * 64,
        source_tree_sha256="a" * 64,
        collection_target="generation-target",
    )


def _preflight_receipt():
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


@patch.dict("os.environ", {"ALLOWED_REPO_ROOT": "/tmp"})
def test_full_index_forwards_only_exact_generation_identity():
    manager = MagicMock()
    manager.index_repository.return_value = _stats()
    request = IndexRequest(
        repo_path="/tmp/repository",
        workspace="ws",
        project="project",
        branch="main",
        commit="commit",
        source_tree_sha256="a" * 64,
        collection_target="generation-target",
    )

    with patch(
        "rag_pipeline.api.routers.index._get_singletons",
        return_value=(MagicMock(), manager),
    ):
        assert index_repository(request, BackgroundTasks()) == _stats()

    assert manager.index_repository.call_args.kwargs == {
        "repo_path": "/tmp/repository",
        "workspace": "ws",
        "project": "project",
        "branch": "main",
        "commit": "commit",
        "include_patterns": None,
        "exclude_patterns": None,
        "project_type": None,
        "source_root": None,
        "source_tree_sha256": "a" * 64,
        "collection_target": "generation-target",
    }


@patch.dict("os.environ", {"ALLOWED_REPO_ROOT": "/tmp"})
def test_direct_index_allocates_fresh_opaque_target_and_delegates_attestation():
    manager = MagicMock()
    manager.index_repository.return_value = _stats()
    request = IndexRequest(
        repo_path="/tmp/repository",
        workspace="ws",
        project="project",
        branch="main",
        commit="commit",
    )

    first_target = _index_collection_target(request)
    second_target = _index_collection_target(request)
    assert first_target.startswith("cc_http_g_")
    assert second_target.startswith("cc_http_g_")
    assert first_target != second_target

    with patch(
        "rag_pipeline.api.routers.index._get_singletons",
        return_value=(MagicMock(), manager),
    ):
        assert index_repository(request, BackgroundTasks()) == _stats()

    forwarded = manager.index_repository.call_args.kwargs
    assert forwarded["source_tree_sha256"] is None
    assert forwarded["collection_target"].startswith("cc_http_g_")


@patch.dict("os.environ", {"ALLOWED_REPO_ROOT": "/tmp"})
def test_direct_index_routes_exact_base_binding_through_repository_delta():
    manager = MagicMock()
    manager.index_repository_delta.return_value = _stats()
    request = IndexRequest(
        repo_path="/tmp/repository",
        workspace="ws",
        project="project",
        branch="main",
        commit="next",
        collection_target="next-target",
        base_revision="base",
        base_collection_target="base-target",
        base_generation_manifest_sha256="a" * 64,
        changed_paths=["src/changed.py"],
        deleted_paths=["src/deleted.py"],
    )

    with patch(
        "rag_pipeline.api.routers.index._get_singletons",
        return_value=(MagicMock(), manager),
    ):
        assert index_repository(request, BackgroundTasks()) == _stats()

    forwarded = manager.index_repository_delta.call_args.kwargs
    assert forwarded["base_revision"] == "base"
    assert forwarded["base_collection_target"] == "base-target"
    assert forwarded["base_generation_manifest_sha256"] == "a" * 64
    assert forwarded["changed_paths"] == ["src/changed.py"]
    assert forwarded["deleted_paths"] == ["src/deleted.py"]
    manager.index_repository.assert_not_called()


@pytest.mark.asyncio
async def test_stream_disconnect_cancels_worker_and_cleans_owned_repository(
    tmp_path,
):
    source_repository = tmp_path / "codecrow-rag-branch-generation-disconnect"
    source_repository.mkdir()
    (source_repository / "example.py").write_text(
        "def example():\n    return True\n",
        encoding="utf-8",
    )
    manager = MagicMock()
    worker_started = Event()
    worker_stopped = Event()
    forwarded_cancellation = None

    def run_index(**kwargs):
        nonlocal forwarded_cancellation
        forwarded_cancellation = kwargs["cancellation_event"]
        kwargs["progress_callback"]({
            "stage": "indexing",
            "message": "first batch",
            "progress": 10,
        })
        worker_started.set()
        assert forwarded_cancellation.wait(timeout=1.0)
        worker_stopped.set()
        raise RepositoryIndexCancelled("cancelled by disconnected client")

    manager.index_repository.side_effect = run_index
    request = IndexRequest(
        repo_path=str(source_repository),
        workspace="ws",
        project="project",
        branch="main",
        commit="commit",
        collection_target="disconnect-target",
        transfer_repo_ownership=True,
    )

    with (
        patch.dict("os.environ", {"ALLOWED_REPO_ROOT": str(tmp_path)}),
        patch(
            "rag_pipeline.api.routers.index._get_singletons",
            return_value=(MagicMock(), manager),
        ),
    ):
        response = index_repository_stream(request)
        body = response.body_iterator
        admitted_event = await asyncio.wait_for(anext(body), timeout=1.0)
        assert '"type": "admitted"' in admitted_event
        progress_event = await asyncio.wait_for(anext(body), timeout=1.0)
        assert "first batch" in progress_event
        assert worker_started.wait(timeout=1.0)
        assert not source_repository.exists()
        owned_repositories = tuple(
            tmp_path.glob("codecrow-rag-owned-stream-*")
        )
        assert len(owned_repositories) == 1

        started_at = time.monotonic()
        await asyncio.wait_for(body.aclose(), timeout=1.0)

    assert time.monotonic() - started_at < 1.0
    assert forwarded_cancellation is not None
    assert forwarded_cancellation.is_set()
    assert manager.index_repository.call_args.kwargs[
        "source_tree_exclusively_owned"
    ] is True
    assert worker_stopped.wait(timeout=1.0)
    for _ in range(100):
        if not owned_repositories[0].exists():
            break
        await asyncio.sleep(0.01)
    assert not owned_repositories[0].exists()


@pytest.mark.asyncio
async def test_stream_polls_asgi_disconnect_and_cancels_without_generator_close(
    tmp_path,
):
    source_repository = tmp_path / "codecrow-rag-branch-generation-peer-gone"
    source_repository.mkdir()
    (source_repository / "example.py").write_text(
        "def example():\n    return True\n",
        encoding="utf-8",
    )
    manager = MagicMock()
    worker_started = Event()
    worker_stopped = Event()

    def run_index(**kwargs):
        worker_started.set()
        assert kwargs["cancellation_event"].wait(timeout=1.0)
        worker_stopped.set()
        raise RepositoryIndexCancelled("cancelled after ASGI disconnect")

    manager.index_repository.side_effect = run_index
    http_request = MagicMock()
    http_request.is_disconnected = MagicMock(
        side_effect=[asyncio.sleep(0, result=False), asyncio.sleep(0, result=True)]
    )
    request = IndexRequest(
        repo_path=str(source_repository),
        workspace="ws",
        project="project",
        branch="main",
        commit="commit",
        collection_target="peer-gone-target",
        transfer_repo_ownership=True,
    )

    with (
        patch.dict("os.environ", {"ALLOWED_REPO_ROOT": str(tmp_path)}),
        patch(
            "rag_pipeline.api.routers.index._get_singletons",
            return_value=(MagicMock(), manager),
        ),
    ):
        response = index_repository_stream(request, http_request)
        body = response.body_iterator
        assert '"type": "admitted"' in await asyncio.wait_for(
            anext(body), timeout=1.0
        )
        assert worker_started.wait(timeout=1.0)
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(body), timeout=1.0)

    assert worker_stopped.wait(timeout=1.0)
    for _ in range(100):
        if not tuple(tmp_path.glob("codecrow-rag-owned-stream-*")):
            break
        await asyncio.sleep(0.01)
    assert not tuple(tmp_path.glob("codecrow-rag-owned-stream-*"))


@pytest.mark.asyncio
async def test_stream_worker_registry_drain_cancels_active_workers():
    registry = _IndexStreamWorkerRegistry()
    cancellation_event = Event()
    worker_stopped = Event()

    def run_until_cancelled():
        assert cancellation_event.wait(timeout=1.0)
        worker_stopped.set()

    registry.start(run_until_cancelled, cancellation_event)

    await asyncio.wait_for(registry.drain(), timeout=1.0)

    assert cancellation_event.is_set()
    assert worker_stopped.is_set()
    assert registry.active_count == 0


@pytest.mark.asyncio
async def test_stream_worker_wait_cancellation_does_not_poll_cancelled_task():
    registry = _IndexStreamWorkerRegistry()
    cancellation_event = Event()
    worker_stopped = Event()

    def run_until_cancelled():
        cancellation_event.wait()
        worker_stopped.set()

    worker = registry.start(run_until_cancelled, cancellation_event)
    waiter = asyncio.create_task(registry.wait_for(worker))
    await asyncio.sleep(0)
    waiter.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(waiter, timeout=1.0)

    assert cancellation_event.is_set()
    assert worker_stopped.wait(timeout=1.0)
    worker.join(timeout=1.0)
    assert not worker.is_alive()
    assert registry.active_count == 0


@patch.dict("os.environ", {"ALLOWED_REPO_ROOT": "/tmp"})
def test_source_tree_revision_mismatch_maps_to_conflict():
    manager = MagicMock()
    manager.index_repository.side_effect = RepositorySourceTreeError(
        "repository Git HEAD does not match the supplied commit"
    )
    request = IndexRequest(
        repo_path="/tmp/repository",
        workspace="ws",
        project="project",
        branch="main",
        commit="commit",
    )

    with patch(
        "rag_pipeline.api.routers.index._get_singletons",
        return_value=(MagicMock(), manager),
    ), pytest.raises(HTTPException) as exception:
        index_repository(request, BackgroundTasks())

    assert exception.value.status_code == 409


def test_exact_revision_preflight_requires_and_forwards_target():
    manager = MagicMock()
    manager.get_revision_preflight.return_value = _preflight_receipt()
    with patch(
        "rag_pipeline.api.routers.index._get_singletons",
        return_value=(MagicMock(), manager),
    ):
        result = get_revision_preflight(
            "ws",
            "project",
            branch="main",
            commit="commit",
            collection_target="cc_http_g_exact",
        )

    assert result["generation_manifest_sha256"] == "3" * 64
    manager.get_revision_preflight.assert_called_once_with(
        "ws",
        "project",
        "main",
        "commit",
        collection_target="cc_http_g_exact",
    )


def test_exact_revision_discovery_is_tenant_branch_bound():
    manager = MagicMock()
    discovered = {
        **_preflight_receipt(),
        "collection_target": "cc_http_g_exact",
        "document_count": 7,
    }
    manager.discover_revision_preflights.return_value = [discovered]
    with patch(
        "rag_pipeline.api.routers.index._get_singletons",
        return_value=(MagicMock(), manager),
    ):
        result = discover_revision_preflights(
            "ws",
            "project",
            branch="main",
            commit="commit",
        )

    assert result == [discovered]
    manager.discover_revision_preflights.assert_called_once_with(
        "ws",
        "project",
        "main",
        commit="commit",
    )


def test_exact_revision_preflight_returns_not_found_for_absent_target():
    manager = MagicMock()
    manager.get_revision_preflight.return_value = None
    with patch(
        "rag_pipeline.api.routers.index._get_singletons",
        return_value=(MagicMock(), manager),
    ), pytest.raises(HTTPException) as exception:
        get_revision_preflight(
            "ws",
            "project",
            branch="main",
            commit="commit",
            collection_target="missing-target",
        )

    assert exception.value.status_code == 404


def test_exact_revision_preflight_maps_integrity_mismatch_to_conflict():
    manager = MagicMock()
    manager.get_revision_preflight.side_effect = ExactIndexPreconditionError(
        "repository generation membership failed integrity validation"
    )
    with patch(
        "rag_pipeline.api.routers.index._get_singletons",
        return_value=(MagicMock(), manager),
    ), pytest.raises(HTTPException) as exception:
        get_revision_preflight(
            "ws",
            "project",
            branch="main",
            commit="commit",
            collection_target="cc_http_g_exact",
        )

    assert exception.value.status_code == 409


def test_exact_generation_delete_requires_and_forwards_receipt():
    manager = MagicMock()
    manager.delete_branch.return_value = True
    with patch(
        "rag_pipeline.api.routers.index._get_singletons",
        return_value=(MagicMock(), manager),
    ):
        result = delete_branch(
            "ws",
            "project",
            "main",
            collection_target="generation-target",
            generation_revision="commit",
            generation_manifest_sha256="b" * 64,
        )

    assert result["status"] == "success"
    manager.delete_branch.assert_called_once_with(
        "ws",
        "project",
        "main",
        collection_target="generation-target",
        generation_revision="commit",
        generation_manifest_sha256="b" * 64,
    )


def test_exact_generation_delete_maps_receipt_mismatch_to_conflict():
    manager = MagicMock()
    manager.delete_branch.side_effect = ExactIndexPreconditionError(
        "receipt mismatch"
    )
    with patch(
        "rag_pipeline.api.routers.index._get_singletons",
        return_value=(MagicMock(), manager),
    ), pytest.raises(HTTPException) as exception:
        delete_branch(
            "ws",
            "project",
            "main",
            collection_target="generation-target",
            generation_revision="commit",
            generation_manifest_sha256="b" * 64,
        )

    assert exception.value.status_code == 409
