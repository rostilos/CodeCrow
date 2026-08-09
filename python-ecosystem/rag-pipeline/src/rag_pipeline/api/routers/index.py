"""Index and branch management endpoints."""
import asyncio
import json
import logging
from queue import Empty, Queue
from threading import Thread
from typing import List
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

    async def event_stream():
        events: Queue[tuple[str, object]] = Queue()

        def progress(event: dict) -> None:
            events.put(("progress", event))

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
                    repo_path=request.repo_path,
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
                events.put(("complete", stats.model_dump(mode="json")))
            except Exception as exception:
                logger.error("Error indexing repository with progress: %s", exception)
                events.put(("error", {"message": str(exception)}))

        worker = Thread(
            target=run_index,
            name="rag-index-progress",
            daemon=True,
        )
        worker.start()
        while True:
            try:
                event_type, payload = events.get_nowait()
            except Empty:
                # Polling a thread-safe queue avoids nesting a blocking queue
                # consumer inside Starlette's thread pool. The short wait keeps
                # the event loop responsive and progress delivery prompt.
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
                # The terminal event is scheduled just before the producer
                # returns. Join that final unwind so a short-lived consumer
                # cannot finish while its producer thread is still active.
                worker.join(timeout=1.0)
                break

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
        logger.error("Error publishing readable generation aliases: %s", e)
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
):
    """Delete all points for a specific branch from the project collection."""
    _, index_manager = _get_singletons()
    try:
        success = index_manager.delete_branch(
            workspace, project, branch, collection_target=collection_target
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
