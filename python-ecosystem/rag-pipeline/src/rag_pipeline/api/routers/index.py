"""Index and branch management endpoints."""
import asyncio
import fcntl
import json
import logging
import os
import shutil
import time
from queue import Empty, Full, Queue
from pathlib import Path
from threading import Lock, Thread, current_thread
from typing import BinaryIO, Callable, List
from uuid import uuid4
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from fastapi.responses import StreamingResponse

from ...models.config import IndexStats
from ..models import (
    IndexRequest, UpdateFilesRequest, DeleteFilesRequest, ApplyChangesRequest,
    AdvanceGenerationRequest,
    GenerationAliasPublicationRequest,
    DeleteBranchRequest, CleanupStaleBranchesRequest,
    EstimateRequest, EstimateResponse,
)
from ...core.repository_overlay import IncrementalIndexPreconditionError
from ...core.coordination import (
    MutationCoordinationUnavailable,
    MutationLeaseUnavailable,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["index"])


class _IndexStreamWorkerRegistry:
    """Track synchronous HTTP-stream indexing beyond request cancellation."""

    def __init__(self) -> None:
        self._workers: set[Thread] = set()
        self._lock = Lock()

    def start(self, target: Callable[[], None]) -> Thread:
        """Admit and start one worker before exposing its response stream."""

        def run_tracked() -> None:
            try:
                target()
            finally:
                with self._lock:
                    self._workers.discard(current_thread())

        worker = Thread(
            target=run_tracked,
            name="rag-index-progress",
            # Graceful shutdown drains these workers explicitly. Keeping them
            # non-daemon also prevents interpreter teardown from closing
            # shared clients while an admitted index operation is still using
            # them.
            daemon=False,
        )
        with self._lock:
            self._workers.add(worker)
        try:
            worker.start()
        except BaseException:
            with self._lock:
                self._workers.discard(worker)
            raise
        return worker

    async def wait_for(self, worker: Thread) -> None:
        """Wait for a worker, deferring cancellation until it has returned."""
        cancellation: asyncio.CancelledError | None = None
        while worker.is_alive():
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError as exception:
                # A disconnected streaming client must not unwind its server
                # handler while the admitted worker can still read the
                # caller-owned repository snapshot.
                cancellation = cancellation or exception
        worker.join()
        if cancellation is not None:
            raise cancellation

    async def drain(self) -> None:
        """Wait for every currently admitted stream worker."""
        announced = False
        while True:
            with self._lock:
                active_workers = tuple(self._workers)
            if not active_workers:
                return
            if not announced:
                logger.info(
                    "Waiting for %s HTTP RAG indexing workers before shutdown",
                    len(active_workers),
                )
                announced = True
            for worker in active_workers:
                await self.wait_for(worker)

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._workers)

_index_stream_workers = _IndexStreamWorkerRegistry()
_OWNED_STREAM_DIRECTORY_PREFIX = "codecrow-rag-owned-stream-"
_DEFAULT_OWNED_STREAM_ORPHAN_AGE_SECONDS = 24 * 60 * 60


async def drain_index_repository_stream_workers() -> None:
    """Drain admitted HTTP index operations before shared clients close."""
    await _index_stream_workers.drain()


def cleanup_orphaned_index_repository_stream_workspaces(
    max_age_seconds: int = _DEFAULT_OWNED_STREAM_ORPHAN_AGE_SECONDS,
) -> int:
    """Remove only old RAG-owned workspaces left by an earlier process."""
    allowed_root = Path(os.environ.get("ALLOWED_REPO_ROOT", "/tmp")).resolve()
    if not allowed_root.is_dir():
        return 0
    cutoff = time.time() - max(0, max_age_seconds)
    cleaned = 0
    for candidate in allowed_root.iterdir():
        if (
            not candidate.name.startswith(_OWNED_STREAM_DIRECTORY_PREFIX)
            or candidate.is_symlink()
            or not candidate.is_dir()
        ):
            continue
        try:
            if candidate.stat().st_mtime > cutoff:
                continue
            lock_path = allowed_root / f".{candidate.name}.lock"
            lock_descriptor = os.open(
                lock_path,
                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                0o600,
            )
            lock_file = os.fdopen(lock_descriptor, "a+b")
            try:
                try:
                    fcntl.flock(
                        lock_file.fileno(),
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
                except BlockingIOError:
                    # Another RAG process still owns this workspace. Its age
                    # alone is never authority to disrupt an active worker.
                    continue
                shutil.rmtree(candidate)
                cleaned += 1
            finally:
                lock_file.close()
            if not candidate.exists():
                lock_path.unlink(missing_ok=True)
        except FileNotFoundError:
            continue
        except Exception:
            logger.exception(
                "Failed to remove orphaned RAG HTTP index workspace: %s",
                candidate,
            )
    return cleaned


def _coalesce_stream_progress(
    events: Queue[dict],
    event: dict,
) -> None:
    """Retain only the latest undelivered optional progress event."""
    while True:
        try:
            events.put_nowait(event)
            return
        except Full:
            try:
                events.get_nowait()
            except Empty:
                continue


def _take_stream_repository_ownership(
    repo_path: str,
) -> tuple[Path, BinaryIO]:
    """Atomically move one Java-owned snapshot into the RAG namespace."""
    allowed_root = Path(os.environ.get("ALLOWED_REPO_ROOT", "/tmp")).resolve()
    source = Path(repo_path).resolve()
    if (
        source.parent != allowed_root
        or not source.name.startswith("codecrow-rag-branch-generation-")
        or not source.is_dir()
    ):
        raise ValueError(
            "stream repository ownership transfer requires an existing "
            "codecrow-rag-branch-generation-* directory directly under "
            f"{allowed_root}"
        )
    owned = allowed_root / f"{_OWNED_STREAM_DIRECTORY_PREFIX}{uuid4().hex}"
    lock_path = allowed_root / f".{owned.name}.lock"
    lock_file = None
    try:
        lock_descriptor = os.open(
            lock_path,
            os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
            0o600,
        )
        lock_file = os.fdopen(lock_descriptor, "a+b")
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        if lock_file is not None:
            lock_file.close()
        lock_path.unlink(missing_ok=True)
        raise
    # Both services mount the same temporary volume. A rename in that volume
    # is atomic: after this point Java may safely clean the now-absent source
    # path even if the admission SSE is lost, while RAG alone owns `owned`.
    try:
        source.rename(owned)
    except BaseException:
        lock_file.close()
        lock_path.unlink(missing_ok=True)
        raise
    return owned, lock_file


def _remove_owned_stream_repository(
    repo_path: Path,
    lock_file: BinaryIO,
) -> None:
    removed = False
    try:
        shutil.rmtree(repo_path)
        removed = True
    except FileNotFoundError:
        removed = True
    except Exception:
        logger.exception(
            "Failed to remove RAG-owned HTTP index workspace: %s",
            repo_path,
        )
    finally:
        lock_path = repo_path.parent / f".{repo_path.name}.lock"
        lock_file.close()
        if removed:
            lock_path.unlink(missing_ok=True)


def _get_singletons():
    """Get lifecycle-managed singletons from the api module."""
    from ..api import config, index_manager
    return config, index_manager


@router.get("/limits")
def get_limits():
    """Get current RAG indexing limits (for free plan info)."""
    config, _ = _get_singletons()
    return {
        "max_chunks_per_index": config.max_chunks_per_index,
        "max_files_per_index": config.max_files_per_index,
        "max_file_size_bytes": config.max_file_size_bytes,
        "chunk_size": config.chunk_size,
        "chunk_overlap": config.chunk_overlap
    }


@router.post("/index/estimate", response_model=EstimateResponse)
def estimate_repository(request: EstimateRequest):
    """Estimate repository size before indexing (file and chunk counts)."""
    config, index_manager = _get_singletons()
    try:
        file_count, estimated_chunks = index_manager.estimate_repository_size(
            repo_path=request.repo_path,
            include_patterns=request.include_patterns,
            exclude_patterns=request.exclude_patterns,
        )

        within_limits = True
        messages = []

        if config.max_files_per_index > 0 and file_count > config.max_files_per_index:
            within_limits = False
            messages.append(f"File count ({file_count}) exceeds limit ({config.max_files_per_index})")

        if config.max_chunks_per_index > 0 and estimated_chunks > config.max_chunks_per_index:
            within_limits = False
            messages.append(f"Estimated chunks ({estimated_chunks}) exceeds limit ({config.max_chunks_per_index})")

        if within_limits:
            message = "Repository is within limits"
        else:
            message = (
                ". ".join(messages) +
                ". Use exclude patterns to skip large directories (node_modules, vendor, dist, generated files). "
                "This is a free plan limitation - contact support for extended limits."
            )

        return EstimateResponse(
            file_count=file_count,
            estimated_chunks=estimated_chunks,
            max_files_allowed=config.max_files_per_index,
            max_chunks_allowed=config.max_chunks_per_index,
            within_limits=within_limits,
            message=message
        )
    except Exception as e:
        logger.error(f"Error estimating repository: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/index/repository", response_model=IndexStats)
def index_repository(request: IndexRequest, background_tasks: BackgroundTasks):
    """Index entire repository."""
    _, index_manager = _get_singletons()
    try:
        optional_generation_args = {}
        source_tree_sha256 = getattr(request, "source_tree_sha256", None)
        collection_target = getattr(request, "collection_target", None)
        if isinstance(source_tree_sha256, str) and source_tree_sha256:
            optional_generation_args["source_tree_sha256"] = source_tree_sha256
        if isinstance(collection_target, str) and collection_target:
            optional_generation_args["collection_target"] = collection_target
        if getattr(request, "publish_branch_alias", False) is True:
            optional_generation_args["publish_branch_alias"] = True
        if getattr(request, "publish_legacy_project_alias", False) is True:
            optional_generation_args["publish_legacy_project_alias"] = True
        stats = index_manager.index_repository(
            repo_path=request.repo_path,
            workspace=request.workspace,
            project=request.project,
            branch=request.branch,
            commit=request.commit,
            preserve_other_branches=request.preserve_other_branches,
            include_patterns=request.include_patterns,
            exclude_patterns=request.exclude_patterns,
            **optional_generation_args,
        )
        return stats
    except ValueError as e:
        logger.warning(f"Validation error indexing repository: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except MutationLeaseUnavailable as e:
        raise HTTPException(status_code=409, detail=str(e))
    except MutationCoordinationUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"Error indexing repository: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/index/repository/stream")
def index_repository_stream(request: IndexRequest):
    """Index one repository and stream observable batch progress as SSE.

    The ordinary endpoint remains the stable JSON contract.  This endpoint is
    intentionally only an observability transport: it runs the same index
    operation and forwards optional progress events without making progress
    delivery a prerequisite for a successful snapshot.
    """
    _, index_manager = _get_singletons()
    progress_events: Queue[dict] = Queue(maxsize=1)
    terminal_events: Queue[tuple[str, object]] = Queue(maxsize=1)
    repository_ownership_transferred = (
        getattr(request, "transfer_repo_ownership", False) is True
    )
    index_repo_path = Path(request.repo_path)
    repository_ownership_lock: BinaryIO | None = None
    if repository_ownership_transferred:
        try:
            (
                index_repo_path,
                repository_ownership_lock,
            ) = _take_stream_repository_ownership(request.repo_path)
        except ValueError as exception:
            raise HTTPException(status_code=400, detail=str(exception))
        except OSError as exception:
            raise HTTPException(
                status_code=503,
                detail=(
                    "RAG could not take repository snapshot ownership: "
                    f"{exception}"
                ),
            )

    def progress(event: dict) -> None:
        _coalesce_stream_progress(progress_events, event)

    def run_index() -> None:
        try:
            optional_generation_args = {}
            if request.source_tree_sha256:
                optional_generation_args["source_tree_sha256"] = (
                    request.source_tree_sha256
                )
            if request.collection_target:
                optional_generation_args["collection_target"] = (
                    request.collection_target
                )
            if getattr(request, "publish_branch_alias", False) is True:
                optional_generation_args["publish_branch_alias"] = True
            if getattr(request, "publish_legacy_project_alias", False) is True:
                optional_generation_args["publish_legacy_project_alias"] = True
            stats = index_manager.index_repository(
                repo_path=str(index_repo_path),
                workspace=request.workspace,
                project=request.project,
                branch=request.branch,
                commit=request.commit,
                preserve_other_branches=request.preserve_other_branches,
                include_patterns=request.include_patterns,
                exclude_patterns=request.exclude_patterns,
                progress_callback=progress,
                **optional_generation_args,
            )
            terminal_events.put(
                ("complete", stats.model_dump(mode="json"))
            )
        except Exception as exception:
            # The terminal event gives the Java job owner complete context and
            # that owner emits the rate-bounded diagnostic. Avoid logging the
            # same failure again at this transport layer.
            logger.debug(
                "RAG repository stream worker failed: %s", exception,
                exc_info=True,
            )
            terminal_events.put(("error", {"message": str(exception)}))
        finally:
            if repository_ownership_transferred:
                _remove_owned_stream_repository(
                    index_repo_path,
                    repository_ownership_lock,
                )

    try:
        # Admission happens before successful response headers. Ownership is
        # already represented by an atomic rename, so a lost admission event
        # cannot make the Java caller delete the path this worker is reading.
        worker = _index_stream_workers.start(run_index)
    except BaseException:
        if repository_ownership_transferred:
            _remove_owned_stream_repository(
                index_repo_path,
                repository_ownership_lock,
            )
        raise

    async def event_stream():
        try:
            if repository_ownership_transferred:
                admitted = {
                    "type": "admitted",
                    "repositoryOwnershipTransferred": True,
                }
                yield f"data: {json.dumps(admitted)}\n\n"
            while True:
                try:
                    payload = progress_events.get_nowait()
                    event_type = "progress"
                except Empty:
                    try:
                        event_type, payload = terminal_events.get_nowait()
                    except Empty:
                        if not worker.is_alive():
                            # run_index publishes a terminal event before it
                            # returns. This guards an unexpected BaseException
                            # in the worker without leaving the stream open.
                            event_type = "error"
                            payload = {
                                "message": (
                                    "RAG indexing worker stopped without a "
                                    "terminal result"
                                )
                            }
                        else:
                            # Polling thread-safe queues avoids nesting a
                            # blocking consumer inside Starlette's thread pool.
                            await asyncio.sleep(0.05)
                            continue
                if event_type == "progress":
                    event = {"type": "progress", **payload}
                elif event_type == "complete":
                    event = {"type": "complete", "result": payload}
                else:
                    event = {"type": "error", **payload}
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event_type in {"complete", "error"}:
                    break
        finally:
            # StreamingResponse closes this async generator when its client
            # disconnects. Defer handler teardown until the independently
            # admitted synchronous operation has returned.
            worker_drain = asyncio.create_task(
                _index_stream_workers.wait_for(worker)
            )
            try:
                await asyncio.shield(worker_drain)
            except asyncio.CancelledError:
                # Cancellation can be delivered before an awaited coroutine
                # executes its own cancellation handler. Shield admission
                # draining as a separate task, then re-raise only after it has
                # completed.
                await worker_drain
                raise

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/index/update-files", response_model=IndexStats)
def update_files(request: UpdateFilesRequest):
    """Update specific files in index."""
    _, index_manager = _get_singletons()
    try:
        stats = index_manager.update_files(
            file_paths=request.file_paths,
            repo_base=request.repo_base,
            workspace=request.workspace,
            project=request.project,
            branch=request.branch,
            commit=request.commit
        )
        return stats
    except IncrementalIndexPreconditionError as e:
        logger.warning(f"Incremental update precondition failed: {e}")
        raise HTTPException(status_code=409, detail=str(e))
    except MutationLeaseUnavailable as e:
        raise HTTPException(status_code=409, detail=str(e))
    except MutationCoordinationUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating files: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/index/delete-files", response_model=IndexStats)
def delete_files(request: DeleteFilesRequest):
    """Delete specific files from index."""
    _, index_manager = _get_singletons()
    try:
        stats = index_manager.delete_files(
            file_paths=request.file_paths,
            workspace=request.workspace,
            project=request.project,
            branch=request.branch,
            commit=request.commit,
        )
        return stats
    except IncrementalIndexPreconditionError as e:
        logger.warning(f"Incremental delete precondition failed: {e}")
        raise HTTPException(status_code=409, detail=str(e))
    except MutationLeaseUnavailable as e:
        raise HTTPException(status_code=409, detail=str(e))
    except MutationCoordinationUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"Error deleting files: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/index/apply-changes", response_model=IndexStats)
def apply_changes(request: ApplyChangesRequest):
    """Atomically apply all updated and deleted files from one commit."""
    _, index_manager = _get_singletons()
    if request.updated_file_paths and request.repo_base is None:
        raise HTTPException(
            status_code=422,
            detail="repo_base is required when updated_file_paths is not empty",
        )
    try:
        return index_manager.apply_changes(
            updated_file_paths=request.updated_file_paths,
            deleted_file_paths=request.deleted_file_paths,
            repo_base=request.repo_base,
            workspace=request.workspace,
            project=request.project,
            branch=request.branch,
            commit=request.commit,
        )
    except IncrementalIndexPreconditionError as e:
        logger.warning(f"Incremental change-set precondition failed: {e}")
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        logger.warning(f"Invalid incremental change set: {e}")
        raise HTTPException(status_code=422, detail=str(e))
    except MutationLeaseUnavailable as e:
        raise HTTPException(status_code=409, detail=str(e))
    except MutationCoordinationUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"Error applying incremental change set: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/index/advance-generation", response_model=IndexStats)
def advance_generation(request: AdvanceGenerationRequest):
    """Build one immutable target generation from an exact sealed source."""
    _, index_manager = _get_singletons()
    if request.repo_base is None:
        raise HTTPException(
            status_code=422,
            detail="repo_base is required to attest the target source tree",
        )
    try:
        return index_manager.advance_generation(
            source_collection_target=request.source_collection_target,
            target_collection_target=request.collection_target,
            source_commit=request.source_commit,
            source_tree_sha256=request.source_tree_sha256,
            updated_file_paths=request.updated_file_paths,
            deleted_file_paths=request.deleted_file_paths,
            repo_base=request.repo_base,
            workspace=request.workspace,
            project=request.project,
            branch=request.branch,
            commit=request.commit,
            publish_branch_alias=request.publish_branch_alias,
            publish_legacy_project_alias=request.publish_legacy_project_alias,
        )
    except IncrementalIndexPreconditionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except MutationLeaseUnavailable as e:
        raise HTTPException(status_code=409, detail=str(e))
    except MutationCoordinationUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"Error advancing repository generation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/index/generation-aliases")
def publish_generation_aliases(request: GenerationAliasPublicationRequest):
    """Publish or repair readable aliases for an accepted immutable generation.

    This is deliberately separate from indexing so the Java registry can reject
    a stale completed build before any mutable branch-head alias is moved.
    """
    _, index_manager = _get_singletons()
    try:
        aliases = index_manager.publish_generation_aliases(
            workspace=request.workspace,
            project=request.project,
            branch=request.branch,
            commit=request.commit,
            collection_target=request.collection_target,
            generation_manifest_sha256=request.generation_manifest_sha256,
            publish_branch_alias=request.publish_branch_alias,
            publish_legacy_project_alias=request.publish_legacy_project_alias,
        )
        return {"status": "published", "aliases": aliases}
    except IncrementalIndexPreconditionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except MutationLeaseUnavailable as e:
        raise HTTPException(status_code=409, detail=str(e))
    except MutationCoordinationUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        # Alias repair is optional and retried by the registry owner, which
        # emits the contextual transition alert. Avoid a fixed-delay ERROR
        # stream here while preserving the HTTP failure for that caller.
        logger.info("Readable generation alias publication failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/index/{workspace}/{project}/{branch}")
def delete_index(workspace: str, project: str, branch: str):
    """Delete entire index."""
    _, index_manager = _get_singletons()
    try:
        index_manager.delete_index(workspace, project, branch)
        return {"message": f"Index deleted for {workspace}/{project}/{branch}"}
    except MutationLeaseUnavailable as e:
        raise HTTPException(status_code=409, detail=str(e))
    except MutationCoordinationUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"Error deleting index: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Branch management ──

@router.delete("/index/{workspace}/{project}/branch/{branch}")
def delete_branch(
    workspace: str,
    project: str,
    branch: str,
    collection_target: str | None = Query(default=None),
    generation_revision: str | None = Query(default=None, min_length=1),
    generation_manifest_sha256: str | None = Query(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    ),
):
    """Delete all points for a specific branch from the project collection."""
    _, index_manager = _get_singletons()
    try:
        success = index_manager.delete_branch(
            workspace,
            project,
            branch,
            collection_target=collection_target,
            generation_revision=generation_revision,
            generation_manifest_sha256=generation_manifest_sha256,
        )
        if success:
            return {
                "status": "success",
                "message": f"Deleted all points for branch '{branch}' from {workspace}/{project}"
            }
        else:
            return {
                "status": "not_found",
                "message": f"Branch '{branch}' not found or collection doesn't exist"
            }
    except IncrementalIndexPreconditionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except MutationLeaseUnavailable as e:
        raise HTTPException(status_code=409, detail=str(e))
    except MutationCoordinationUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error(f"Error deleting branch '{branch}': {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/index/{workspace}/{project}/branches")
def list_branches(workspace: str, project: str):
    """List all branches that have indexed points in the project collection."""
    _, index_manager = _get_singletons()
    try:
        branches = index_manager.get_indexed_branches(workspace, project)
        branch_stats = []

        for branch in branches:
            count = index_manager.get_branch_point_count(workspace, project, branch)
            branch_stats.append({"branch": branch, "point_count": count})

        return {
            "workspace": workspace,
            "project": project,
            "branches": branch_stats,
            "total_branches": len(branches)
        }
    except Exception as e:
        logger.error(f"Error listing branches: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/index/{workspace}/{project}/cleanup-branches")
def cleanup_stale_branches(workspace: str, project: str, request: CleanupStaleBranchesRequest):
    """Delete all branch points except protected and explicitly kept branches."""
    _, index_manager = _get_singletons()
    try:
        all_branches = index_manager.get_indexed_branches(workspace, project)
        keep_branches = set(request.protected_branches)
        if request.branches_to_keep:
            keep_branches.update(request.branches_to_keep)

        branches_to_delete = [b for b in all_branches if b not in keep_branches]
        deleted_branches = []
        failed_branches = []

        for branch in branches_to_delete:
            try:
                success = index_manager.delete_branch(workspace, project, branch)
                if success:
                    deleted_branches.append(branch)
                else:
                    failed_branches.append(branch)
            except Exception as e:
                logger.error(f"Failed to delete branch '{branch}': {e}")
                failed_branches.append(branch)

        return {
            "status": "completed",
            "deleted_branches": deleted_branches,
            "failed_branches": failed_branches,
            "kept_branches": list(keep_branches & set(all_branches)),
            "total_deleted": len(deleted_branches)
        }
    except Exception as e:
        logger.error(f"Error during branch cleanup: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/index/stats/{workspace}/{project}/{branch}", response_model=IndexStats)
def get_index_stats(workspace: str, project: str, branch: str):
    """Get index statistics."""
    _, index_manager = _get_singletons()
    try:
        stats = index_manager._get_index_stats(workspace, project, branch)
        return stats
    except Exception as e:
        logger.error(f"Error getting index stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/index/list", response_model=List[IndexStats])
def list_indices():
    """List all indices."""
    _, index_manager = _get_singletons()
    try:
        indices = index_manager.list_indices()
        return indices
    except Exception as e:
        logger.error(f"Error listing indices: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Deprecated /branch/* redirects ──

@router.delete("/branch/{workspace}/{project}/{branch:path}", deprecated=True)
def delete_branch_index(workspace: str, project: str, branch: str):
    """DEPRECATED: Use DELETE /index/{workspace}/{project}/branch/{branch} instead."""
    return delete_branch(workspace, project, branch)


@router.post("/branch/delete", deprecated=True)
def delete_branch_index_post(request: DeleteBranchRequest):
    """DEPRECATED: Use DELETE /index/{workspace}/{project}/branch/{branch} instead."""
    return delete_branch(request.workspace, request.project, request.branch)


@router.get("/branch/list/{workspace}/{project}", deprecated=True)
def list_indexed_branches(workspace: str, project: str):
    """DEPRECATED: Use GET /index/{workspace}/{project}/branches instead."""
    return list_branches(workspace, project)


@router.get("/branch/stats/{workspace}/{project}/{branch:path}", deprecated=True)
def get_branch_stats(workspace: str, project: str, branch: str):
    """DEPRECATED: Use GET /index/stats/{workspace}/{project}/{branch} instead."""
    return get_index_stats(workspace, project, branch)
