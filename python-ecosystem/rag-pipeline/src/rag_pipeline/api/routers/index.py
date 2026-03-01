"""Index and branch management endpoints."""
import logging
from typing import List
from fastapi import APIRouter, HTTPException, BackgroundTasks

from ...models.config import IndexStats
from ..models import (
    IndexRequest, UpdateFilesRequest, DeleteFilesRequest,
    DeleteBranchRequest, CleanupStaleBranchesRequest,
    EstimateRequest, EstimateResponse,
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
            exclude_patterns=request.exclude_patterns
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
        stats = index_manager.index_repository(
            repo_path=request.repo_path,
            workspace=request.workspace,
            project=request.project,
            branch=request.branch,
            commit=request.commit,
            include_patterns=request.include_patterns,
            exclude_patterns=request.exclude_patterns
        )
        return stats
    except ValueError as e:
        logger.warning(f"Validation error indexing repository: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error indexing repository: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
            branch=request.branch
        )
        return stats
    except Exception as e:
        logger.error(f"Error deleting files: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/index/{workspace}/{project}/{branch}")
def delete_index(workspace: str, project: str, branch: str):
    """Delete entire index."""
    _, index_manager = _get_singletons()
    try:
        index_manager.delete_index(workspace, project, branch)
        return {"message": f"Index deleted for {workspace}/{project}/{branch}"}
    except Exception as e:
        logger.error(f"Error deleting index: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Branch management ──

@router.delete("/index/{workspace}/{project}/branch/{branch}")
def delete_branch(workspace: str, project: str, branch: str):
    """Delete all points for a specific branch from the project collection."""
    _, index_manager = _get_singletons()
    try:
        success = index_manager.delete_branch(workspace, project, branch)
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
