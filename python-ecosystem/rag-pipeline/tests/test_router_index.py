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
import asyncio
import json
import logging
import os
import shutil
import threading
from pathlib import Path
from queue import Queue
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
        req.preserve_other_branches = False
        req.include_patterns = None
        req.exclude_patterns = None
        req.project_type = "magento"
        req.source_root = "magento/src/etc"

        result = index_repository(req, MagicMock())
        assert result.document_count == 10
        im.index_repository.assert_called_once_with(
            repo_path="/tmp/repo",
            workspace="ws",
            project="proj",
            branch="main",
            commit="abc",
            preserve_other_branches=False,
            include_patterns=None,
            exclude_patterns=None,
            project_type="magento",
            source_root="magento/src/etc",
        )

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
        req.project_type = None
        req.source_root = None

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
        req.project_type = None
        req.source_root = None

        with pytest.raises(HTTPException) as exc_info:
            index_repository(req, MagicMock())
        assert exc_info.value.status_code == 500

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_stream_forwards_batch_progress_and_terminal_stats(self, mock_get):
        _, im = _mock_singletons()
        stats = IndexStats(
            namespace="ns", document_count=10, chunk_count=50,
            last_updated="2024-01-01", workspace="ws", project="proj", branch="main"
        )

        def index_with_progress(**kwargs):
            kwargs["progress_callback"]({
                "stage": "indexing", "message": "Indexed batch 1/2",
                "indexedChunks": 25, "estimatedChunks": 50,
                "completedBatches": 1, "totalBatches": 2,
                "estimatedRemainingMs": 1200,
            })
            return stats

        im.index_repository.side_effect = index_with_progress
        mock_get.return_value = (_, im)
        from rag_pipeline.api.routers.index import index_repository_stream

        req = MagicMock()
        req.repo_path = "/tmp/repo"
        req.workspace = "ws"
        req.project = "proj"
        req.branch = "main"
        req.commit = "abc"
        req.preserve_other_branches = False
        req.include_patterns = None
        req.exclude_patterns = None
        req.project_type = None
        req.source_root = None
        req.source_tree_sha256 = None
        req.collection_target = "target"
        req.reuse_collection_target = "prior-target"

        response = index_repository_stream(req)

        async def consume():
            return [item async for item in response.body_iterator]

        events = [json.loads(
            (item.decode() if isinstance(item, bytes) else item)
            .removeprefix("data: ").strip()
        ) for item in asyncio.run(consume())]
        assert events[0]["indexedChunks"] == 25
        assert events[0]["estimatedRemainingMs"] == 1200
        assert events[1]["type"] == "complete"
        assert events[1]["result"]["chunk_count"] == 50
        assert im.index_repository.call_args.kwargs[
            "reuse_collection_target"
        ] == "prior-target"

    def test_stream_progress_is_bounded_and_keeps_latest_event(self):
        from rag_pipeline.api.routers.index import _coalesce_stream_progress

        events = Queue(maxsize=1)
        for batch in range(100):
            _coalesce_stream_progress(events, {"completedBatches": batch})

        assert events.qsize() == 1
        assert events.get_nowait() == {"completedBatches": 99}

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_stream_emits_heartbeat_while_indexer_is_quiet(
        self,
        mock_get,
        monkeypatch,
    ):
        _, im = _mock_singletons()
        release = threading.Event()
        stats = IndexStats(
            namespace="ns", document_count=10, chunk_count=50,
            last_updated="2024-01-01", workspace="ws", project="proj",
            branch="main",
        )

        def quiet_index(**_kwargs):
            release.wait(timeout=1)
            return stats

        im.index_repository.side_effect = quiet_index
        mock_get.return_value = (_, im)
        import rag_pipeline.api.routers.index as index_router

        monkeypatch.setattr(
            index_router,
            "INDEX_STREAM_HEARTBEAT_SECONDS",
            0.01,
        )
        req = MagicMock()
        req.repo_path = "/tmp/repo"
        req.workspace = "ws"
        req.project = "proj"
        req.branch = "main"
        req.commit = "abc"
        req.preserve_other_branches = False
        req.include_patterns = None
        req.exclude_patterns = None
        req.project_type = None
        req.source_root = None
        req.source_tree_sha256 = None
        req.collection_target = "target"
        req.reuse_collection_target = None

        response = index_router.index_repository_stream(req)

        async def consume():
            items = []
            iterator = response.body_iterator.__aiter__()
            first = await iterator.__anext__()
            items.append(first)
            release.set()
            async for item in iterator:
                items.append(item)
            return items

        events = [json.loads(
            (item.decode() if isinstance(item, bytes) else item)
            .removeprefix("data: ").strip()
        ) for item in asyncio.run(consume())]

        assert events[0]["type"] == "heartbeat"
        assert events[0]["stage"] == "starting"
        assert events[0]["elapsedMs"] >= 0
        assert events[-1]["type"] == "complete"

    def test_orphan_cleanup_removes_only_old_owned_stream_directories(
        self,
        tmp_path,
    ):
        from rag_pipeline.api.routers.index import (
            _remove_owned_stream_repository,
            _take_stream_repository_ownership,
            cleanup_orphaned_index_repository_stream_workspaces,
        )

        old_owned = tmp_path / "codecrow-rag-owned-stream-old"
        old_owned.mkdir()
        (old_owned / "source.py").write_text("old", encoding="utf-8")
        active_source = (
            tmp_path / "codecrow-rag-branch-generation-active"
        )
        active_source.mkdir()
        unrelated = tmp_path / "codecrow-rag-branch-generation-old"
        unrelated.mkdir()
        old_mtime = 1_000_000
        os.utime(old_owned, (old_mtime, old_mtime))
        os.utime(unrelated, (old_mtime, old_mtime))

        with patch.dict(
            "os.environ", {"ALLOWED_REPO_ROOT": str(tmp_path)}
        ):
            active_owned, active_lock = _take_stream_repository_ownership(
                str(active_source)
            )
            os.utime(active_owned, (old_mtime, old_mtime))
            try:
                cleaned = (
                    cleanup_orphaned_index_repository_stream_workspaces(
                        max_age_seconds=3600
                    )
                )
                active_survived_cleanup = active_owned.exists()
            finally:
                _remove_owned_stream_repository(active_owned, active_lock)

        assert cleaned == 1
        assert not old_owned.exists()
        assert active_survived_cleanup
        assert not active_owned.exists()
        assert unrelated.exists()

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_stream_cancellation_waits_for_admitted_index_worker(
        self,
        mock_get,
    ):
        _, im = _mock_singletons()
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        stats = IndexStats(
            namespace="ns", document_count=10, chunk_count=50,
            last_updated="2024-01-01", workspace="ws", project="proj",
            branch="main",
        )

        def blocking_index(**_kwargs):
            started.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test did not release indexing worker")
            finished.set()
            return stats

        im.index_repository.side_effect = blocking_index
        mock_get.return_value = (_, im)
        from rag_pipeline.api.routers.index import (
            _index_stream_workers,
            index_repository_stream,
        )

        req = MagicMock()
        req.repo_path = "/tmp/repo"
        req.workspace = "ws"
        req.project = "proj"
        req.branch = "main"
        req.commit = "abc"
        req.preserve_other_branches = False
        req.include_patterns = None
        req.exclude_patterns = None
        req.project_type = None
        req.source_root = None
        req.source_tree_sha256 = None
        req.collection_target = "target"
        response = index_repository_stream(req)

        async def scenario():
            consumer_started = asyncio.Event()

            async def consume():
                consumer_started.set()
                async for _item in response.body_iterator:
                    pass

            consumer = asyncio.create_task(consume())
            await consumer_started.wait()
            for _ in range(100):
                if started.is_set():
                    break
                await asyncio.sleep(0.01)
            assert started.is_set()
            assert _index_stream_workers.active_count == 1

            consumer.cancel()
            await asyncio.sleep(0.1)
            assert not consumer.done()

            release.set()
            with pytest.raises(asyncio.CancelledError):
                await consumer

            assert finished.is_set()
            assert _index_stream_workers.active_count == 0

        try:
            asyncio.run(scenario())
        finally:
            release.set()

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_disconnect_cannot_delete_transferred_snapshot_under_active_worker(
        self,
        mock_get,
        tmp_path,
    ):
        _, im = _mock_singletons()
        source = tmp_path / "codecrow-rag-branch-generation-disconnect"
        source.mkdir()
        (source / "source.py").write_text("value = 1", encoding="utf-8")
        started = threading.Event()
        release = threading.Event()
        worker_path: list[Path] = []
        stats = IndexStats(
            namespace="ns", document_count=1, chunk_count=1,
            last_updated="2024-01-01", workspace="ws", project="proj",
            branch="main",
        )

        def blocking_index(**kwargs):
            owned_path = Path(kwargs["repo_path"])
            worker_path.append(owned_path)
            assert owned_path != source
            assert owned_path.exists()
            started.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test did not release indexing worker")
            # Java may clean its original path after the stream disconnects,
            # but the atomically moved RAG-owned path remains valid.
            assert owned_path.exists()
            return stats

        im.index_repository.side_effect = blocking_index
        mock_get.return_value = (_, im)
        from rag_pipeline.api.models import IndexRequest
        from rag_pipeline.api.routers.index import (
            _index_stream_workers,
            index_repository_stream,
        )

        request = IndexRequest(
            repo_path=str(source),
            workspace="ws",
            project="proj",
            branch="main",
            commit="abc",
            collection_target="target",
            transfer_repo_ownership=True,
        )

        async def scenario():
            response = index_repository_stream(request)
            admitted = asyncio.Event()

            async def consume():
                async for item in response.body_iterator:
                    event = json.loads(
                        (item.decode() if isinstance(item, bytes) else item)
                        .removeprefix("data: ").strip()
                    )
                    if event["type"] == "admitted":
                        admitted.set()

            consumer = asyncio.create_task(consume())
            await asyncio.wait_for(admitted.wait(), timeout=1)
            for _ in range(100):
                if started.is_set():
                    break
                await asyncio.sleep(0.01)
            assert started.is_set()
            assert not source.exists()

            consumer.cancel()
            # Simulate the Java pre-admission fallback after a lost stream.
            # It targets only the old path, which no longer backs the worker.
            shutil.rmtree(source, ignore_errors=True)
            await asyncio.sleep(0.1)
            assert not consumer.done()
            assert worker_path[0].exists()

            release.set()
            with pytest.raises(asyncio.CancelledError):
                await consumer

            assert not worker_path[0].exists()
            assert _index_stream_workers.active_count == 0

        try:
            with patch.dict(
                "os.environ", {"ALLOWED_REPO_ROOT": str(tmp_path)}
            ):
                asyncio.run(scenario())
        finally:
            release.set()
            for path in worker_path:
                shutil.rmtree(path, ignore_errors=True)

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_stream_worker_failure_is_terminal_without_duplicate_error_log(
        self,
        mock_get,
        caplog,
    ):
        _, im = _mock_singletons()
        im.index_repository.side_effect = RuntimeError("qdrant unavailable")
        mock_get.return_value = (_, im)
        from rag_pipeline.api.routers.index import index_repository_stream

        req = MagicMock()
        req.repo_path = "/tmp/repo"
        req.workspace = "ws"
        req.project = "proj"
        req.branch = "main"
        req.commit = "abc"
        req.preserve_other_branches = False
        req.include_patterns = None
        req.exclude_patterns = None
        req.project_type = None
        req.source_root = None
        req.source_tree_sha256 = None
        req.collection_target = "target"
        req.transfer_repo_ownership = False

        response = index_repository_stream(req)

        async def consume():
            return [item async for item in response.body_iterator]

        with caplog.at_level(logging.DEBUG):
            events = [
                json.loads(
                    (item.decode() if isinstance(item, bytes) else item)
                    .removeprefix("data: ").strip()
                )
                for item in asyncio.run(consume())
            ]

        assert events[-1] == {
            "type": "error",
            "message": "qdrant unavailable",
        }
        assert not any(
            record.levelno >= logging.ERROR
            and "qdrant unavailable" in record.getMessage()
            for record in caplog.records
        )

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_exact_index_forwards_readable_alias_publication(self, mock_get):
        _, im = _mock_singletons()
        stats = IndexStats(
            namespace="ns", document_count=10, chunk_count=50,
            last_updated="2024-01-01", workspace="ws", project="proj", branch="develop"
        )
        im.index_repository.return_value = stats
        mock_get.return_value = (_, im)

        from rag_pipeline.api.routers.index import index_repository
        from rag_pipeline.api.models import IndexRequest

        request = IndexRequest(
            repo_path="/tmp/repo", workspace="ws", project="proj",
            branch="develop", commit="a" * 40,
            collection_target="exact-develop-target",
            reuse_collection_target="prior-develop-target",
            publish_branch_alias=True,
        )

        index_repository(request, MagicMock())

        assert im.index_repository.call_args.kwargs["collection_target"] == (
            "exact-develop-target"
        )
        assert im.index_repository.call_args.kwargs["publish_branch_alias"] is True
        assert im.index_repository.call_args.kwargs["reuse_collection_target"] == (
            "prior-develop-target"
        )


class TestAdvanceGeneration:

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_forwards_readable_alias_publication(self, mock_get):
        _, im = _mock_singletons()
        im.advance_generation.return_value = IndexStats(
            namespace="ns", document_count=10, chunk_count=50,
            last_updated="2024-01-01", workspace="ws", project="proj", branch="develop"
        )
        mock_get.return_value = (_, im)

        from rag_pipeline.api.routers.index import advance_generation
        from rag_pipeline.api.models import AdvanceGenerationRequest

        request = AdvanceGenerationRequest(
            workspace="ws", project="proj", branch="develop",
            source_commit="a" * 40, commit="b" * 40,
            source_tree_sha256="c" * 64,
            source_collection_target="source", collection_target="target",
            repo_base="/tmp/repo", publish_branch_alias=True,
        )

        advance_generation(request)

        assert im.advance_generation.call_args.kwargs["publish_branch_alias"] is True


class TestGenerationAliasPublication:

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_forwards_registry_manifest_receipt(self, mock_get):
        _, im = _mock_singletons()
        im.publish_generation_aliases.return_value = ["readable-main"]
        mock_get.return_value = (_, im)

        from rag_pipeline.api.models import GenerationAliasPublicationRequest
        from rag_pipeline.api.routers.index import publish_generation_aliases

        request = GenerationAliasPublicationRequest(
            workspace="ws",
            project="project",
            branch="main",
            commit="a" * 40,
            collection_target="exact-target",
            generation_manifest_sha256="b" * 64,
        )

        result = publish_generation_aliases(request)

        assert result["status"] == "published"
        assert im.publish_generation_aliases.call_args.kwargs[
            "generation_manifest_sha256"
        ] == "b" * 64

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_retryable_failure_does_not_emit_server_error_log(
        self,
        mock_get,
        caplog,
    ):
        _, im = _mock_singletons()
        im.publish_generation_aliases.side_effect = RuntimeError("timed out")
        mock_get.return_value = (_, im)

        from rag_pipeline.api.models import GenerationAliasPublicationRequest
        from rag_pipeline.api.routers.index import publish_generation_aliases

        request = GenerationAliasPublicationRequest(
            workspace="ws",
            project="project",
            branch="main",
            commit="a" * 40,
            collection_target="exact-target",
        )

        with pytest.raises(HTTPException) as exc_info:
            publish_generation_aliases(request)

        assert exc_info.value.status_code == 500
        assert not any(
            record.levelname == "ERROR"
            and "alias" in record.getMessage().lower()
            for record in caplog.records
        )


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
        req.commit = "abc123"

        result = delete_files(req)
        assert result.document_count == 5
        im.delete_files.assert_called_once_with(
            file_paths=["a.py"],
            workspace="ws",
            project="proj",
            branch="main",
            commit="abc123",
        )

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_apply_changes_forwards_complete_commit(self, mock_get):
        _, im = _mock_singletons()
        stats = IndexStats(
            namespace="ns", document_count=5, chunk_count=20,
            last_updated="2024-01-01", workspace="ws", project="proj", branch="main"
        )
        im.apply_changes.return_value = stats
        mock_get.return_value = (_, im)
        from rag_pipeline.api.models import ApplyChangesRequest
        from rag_pipeline.api.routers.index import apply_changes

        request = ApplyChangesRequest(
            updated_file_paths=["a.py"],
            deleted_file_paths=["b.py"],
            repo_base="/tmp/repository",
            workspace="ws",
            project="proj",
            branch="main",
            commit="abc123",
        )

        assert apply_changes(request) == stats
        im.apply_changes.assert_called_once_with(
            updated_file_paths=["a.py"],
            deleted_file_paths=["b.py"],
            repo_base="/tmp/repository",
            workspace="ws",
            project="proj",
            branch="main",
            commit="abc123",
        )

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_apply_changes_maps_precondition_to_409(self, mock_get):
        from rag_pipeline.api.models import ApplyChangesRequest
        from rag_pipeline.api.routers.index import apply_changes
        from rag_pipeline.core.repository_overlay import (
            IncrementalIndexPreconditionError,
        )

        _, im = _mock_singletons()
        im.apply_changes.side_effect = IncrementalIndexPreconditionError(
            "fully reindex the branch"
        )
        mock_get.return_value = (_, im)
        request = ApplyChangesRequest(
            deleted_file_paths=["b.py"],
            workspace="ws",
            project="proj",
            branch="main",
            commit="abc123",
        )

        with pytest.raises(HTTPException) as exc_info:
            apply_changes(request)
        assert exc_info.value.status_code == 409

    @patch("rag_pipeline.api.routers.index._get_singletons")
    def test_apply_changes_requires_root_for_updates(self, mock_get):
        from rag_pipeline.api.models import ApplyChangesRequest
        from rag_pipeline.api.routers.index import apply_changes

        mock_get.return_value = _mock_singletons()
        request = ApplyChangesRequest(
            updated_file_paths=["a.py"],
            workspace="ws",
            project="proj",
            branch="main",
            commit="abc123",
        )

        with pytest.raises(HTTPException) as exc_info:
            apply_changes(request)
        assert exc_info.value.status_code == 422


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
    def test_delete_exact_generation_forwards_registry_receipt(self, mock_get):
        _, im = _mock_singletons()
        im.delete_branch.return_value = True
        mock_get.return_value = (_, im)

        from rag_pipeline.api.routers.index import delete_branch
        digest = "a" * 64
        result = delete_branch(
            "ws", "proj", "develop", "target", "revision", digest
        )

        assert result["status"] == "success"
        im.delete_branch.assert_called_once_with(
            "ws",
            "proj",
            "develop",
            collection_target="target",
            generation_revision="revision",
            generation_manifest_sha256=digest,
        )

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
