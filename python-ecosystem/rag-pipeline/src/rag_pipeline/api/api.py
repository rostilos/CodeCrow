import logging
import gc
from typing import List, Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel

from ..models.config import RAGConfig, IndexStats
from ..core.index_manager import RAGIndexManager
from ..services.query_service import RAGQueryService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="CodeCrow RAG API", version="2.0.0")

config = RAGConfig()
index_manager = RAGIndexManager(config)
query_service = RAGQueryService(config)


class IndexRequest(BaseModel):
    repo_path: str
    workspace: str
    project: str
    branch: str
    commit: str
    exclude_patterns: Optional[List[str]] = None


class UpdateFilesRequest(BaseModel):
    file_paths: List[str]
    repo_base: str
    workspace: str
    project: str
    branch: str
    commit: str


class DeleteFilesRequest(BaseModel):
    file_paths: List[str]
    workspace: str
    project: str
    branch: str


class QueryRequest(BaseModel):
    query: str
    workspace: str
    project: str
    branch: str
    top_k: Optional[int] = 10
    filter_language: Optional[str] = None


class PRContextRequest(BaseModel):
    workspace: str
    project: str
    branch: Optional[str] = None  # Target branch (PR source)
    base_branch: Optional[str] = None  # Base branch (PR target, e.g., 'main')
    changed_files: List[str]
    diff_snippets: Optional[List[str]] = []
    pr_title: Optional[str] = None
    pr_description: Optional[str] = None
    top_k: Optional[int] = 15
    enable_priority_reranking: Optional[bool] = True
    min_relevance_score: Optional[float] = 0.7
    deleted_files: Optional[List[str]] = []  # Files deleted in target branch


class DeterministicContextRequest(BaseModel):
    """Request for deterministic metadata-based context retrieval.
    
    TWO-STEP process leveraging tree-sitter metadata:
    1. Get chunks for the changed file_paths
    2. Extract semantic_names/imports/extends from those chunks
    3. Find related definitions using extracted identifiers
    
    NO language-specific parsing needed - tree-sitter already did it during indexing!
    """
    workspace: str
    project: str
    branches: List[str]  # Branches to search (e.g., ['release/1.29', 'master'])
    file_paths: List[str]  # Changed file paths from diff
    limit_per_file: Optional[int] = 10  # Max chunks per file


class DeleteBranchRequest(BaseModel):
    workspace: str
    project: str
    branch: str


@app.get("/")
def root():
    return {"message": "CodeCrow RAG Pipeline API", "version": "1.0.0"}


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/limits")
def get_limits():
    """Get current RAG indexing limits (for free plan info)"""
    return {
        "max_chunks_per_index": config.max_chunks_per_index,
        "max_files_per_index": config.max_files_per_index,
        "max_file_size_bytes": config.max_file_size_bytes,
        "chunk_size": config.chunk_size,
        "chunk_overlap": config.chunk_overlap
    }


class EstimateRequest(BaseModel):
    repo_path: str
    exclude_patterns: Optional[List[str]] = None


class EstimateResponse(BaseModel):
    file_count: int
    estimated_chunks: int
    max_files_allowed: int
    max_chunks_allowed: int
    within_limits: bool
    message: str


@app.post("/index/estimate", response_model=EstimateResponse)
def estimate_repository(request: EstimateRequest):
    """Estimate repository size before indexing (file and chunk counts)"""
    try:
        file_count, estimated_chunks = index_manager.estimate_repository_size(
            repo_path=request.repo_path,
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


@app.post("/index/repository", response_model=IndexStats)
def index_repository(request: IndexRequest, background_tasks: BackgroundTasks):
    """Index entire repository"""
    try:
        stats = index_manager.index_repository(
            repo_path=request.repo_path,
            workspace=request.workspace,
            project=request.project,
            branch=request.branch,
            commit=request.commit,
            exclude_patterns=request.exclude_patterns
        )
        return stats
    except ValueError as e:
        # Validation errors (e.g., exceeding limits) return 400
        logger.warning(f"Validation error indexing repository: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error indexing repository: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/index/update-files", response_model=IndexStats)
def update_files(request: UpdateFilesRequest):
    """Update specific files in index"""
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


@app.post("/index/delete-files", response_model=IndexStats)
def delete_files(request: DeleteFilesRequest):
    """Delete specific files from index"""
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


@app.delete("/index/{workspace}/{project}/{branch}")
def delete_index(workspace: str, project: str, branch: str):
    """Delete entire index"""
    try:
        index_manager.delete_index(workspace, project, branch)
        return {"message": f"Index deleted for {workspace}/{project}/{branch}"}
    except Exception as e:
        logger.error(f"Error deleting index: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# BRANCH MANAGEMENT ENDPOINTS
# =============================================================================

@app.delete("/index/{workspace}/{project}/branch/{branch}")
def delete_branch(workspace: str, project: str, branch: str):
    """Delete all points for a specific branch from the project collection.
    
    This removes all indexed data for a branch without affecting other branches.
    Use this when a branch is deleted or merged and no longer needed.
    """
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


@app.get("/index/{workspace}/{project}/branches")
def list_branches(workspace: str, project: str):
    """List all branches that have indexed points in the project collection."""
    try:
        branches = index_manager.get_indexed_branches(workspace, project)
        branch_stats = []
        
        for branch in branches:
            count = index_manager.get_branch_point_count(workspace, project, branch)
            branch_stats.append({
                "branch": branch,
                "point_count": count
            })
        
        return {
            "workspace": workspace,
            "project": project,
            "branches": branch_stats,
            "total_branches": len(branches)
        }
    except Exception as e:
        logger.error(f"Error listing branches: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class CleanupStaleBranchesRequest(BaseModel):
    workspace: str
    project: str
    protected_branches: List[str] = ["main", "master", "develop"]
    branches_to_keep: Optional[List[str]] = None  # Explicit list of branches to keep


@app.post("/index/{workspace}/{project}/cleanup-branches")
def cleanup_stale_branches(workspace: str, project: str, request: CleanupStaleBranchesRequest):
    """Delete all branch points except protected and explicitly kept branches.
    
    This is useful for cleaning up after branches are merged/deleted.
    Protected branches (main, master, develop) are never deleted unless explicitly overridden.
    """
    try:
        all_branches = index_manager.get_indexed_branches(workspace, project)
        
        # Determine which branches to keep
        keep_branches = set(request.protected_branches)
        if request.branches_to_keep:
            keep_branches.update(request.branches_to_keep)
        
        # Find branches to delete
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


@app.get("/index/stats/{workspace}/{project}/{branch}", response_model=IndexStats)
def get_index_stats(workspace: str, project: str, branch: str):
    """Get index statistics"""
    try:
        stats = index_manager._get_index_stats(workspace, project, branch)
        return stats
    except Exception as e:
        logger.error(f"Error getting index stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/index/list", response_model=List[IndexStats])
def list_indices():
    """List all indices"""
    try:
        indices = index_manager.list_indices()
        return indices
    except Exception as e:
        logger.error(f"Error listing indices: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/query/search")
def semantic_search(request: QueryRequest):
    """Perform semantic search"""
    try:
        results = query_service.semantic_search(
            query=request.query,
            workspace=request.workspace,
            project=request.project,
            branch=request.branch,
            top_k=request.top_k,
            filter_language=request.filter_language
        )
        return {"results": results}
    except Exception as e:
        logger.error(f"Error performing search: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/query/pr-context")
def get_pr_context(request: PRContextRequest):
    """
    Get context for PR review with multi-branch support.
    
    Queries both target branch and base branch to preserve cross-file relationships.
    Results are deduplicated with target branch taking priority.
    
    Args:
        branch: Target branch (PR source branch)
        base_branch: Base branch (PR target, e.g., 'main'). Auto-detected if not provided.
        deleted_files: Files deleted in target branch (excluded from results)
    """
    try:
        # If branch is not provided, return empty context
        if not request.branch:
            logger.warning("Branch not provided in PR context request, returning empty context")
            return {
                "context": {
                    "relevant_code": [],
                    "related_files": [],
                    "changed_files": request.changed_files,
                    "_metadata": {
                        "skipped_reason": "branch_not_provided",
                        "changed_files_count": len(request.changed_files),
                        "result_count": 0
                    }
                }
            }
        
        context = query_service.get_context_for_pr(
            workspace=request.workspace,
            project=request.project,
            branch=request.branch,
            changed_files=request.changed_files,
            diff_snippets=request.diff_snippets or [],
            pr_title=request.pr_title,
            pr_description=request.pr_description,
            top_k=request.top_k,
            enable_priority_reranking=request.enable_priority_reranking,
            min_relevance_score=request.min_relevance_score,
            base_branch=request.base_branch,
            deleted_files=request.deleted_files or []
        )
        
        # Add metadata about processing
        context["_metadata"] = {
            "priority_reranking_enabled": request.enable_priority_reranking,
            "min_relevance_score": request.min_relevance_score,
            "changed_files_count": len(request.changed_files),
            "result_count": len(context.get("relevant_code", [])),
            "branches_searched": context.get("_branches_searched", [request.branch])
        }
        
        return {"context": context}
    except Exception as e:
        logger.error(f"Error getting PR context: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/query/deterministic")
def get_deterministic_context(request: DeterministicContextRequest):
    """
    Get context using DETERMINISTIC metadata-based retrieval.
    
    Two-step process:
    1. Get chunks for changed file_paths
    2. Extract semantic_names/imports/extends from those chunks (tree-sitter metadata!)
    3. Find related definitions using extracted identifiers
    
    No language-specific parsing needed - tree-sitter already did it during indexing.
    Predictable: same input = same output.
    """
    try:
        context = query_service.get_deterministic_context(
            workspace=request.workspace,
            project=request.project,
            branches=request.branches,
            file_paths=request.file_paths,
            limit_per_file=request.limit_per_file or 10
        )
        
        return {"context": context}
    except Exception as e:
        logger.error(f"Error getting deterministic context: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# BRANCH MANAGEMENT ENDPOINTS
# =============================================================================

@app.delete("/branch/{workspace}/{project}/{branch:path}")
def delete_branch_index(workspace: str, project: str, branch: str):
    """Delete all index data for a specific branch.
    
    This removes all points for the branch from the project collection.
    The collection itself is NOT deleted - only the branch's data.
    """
    try:
        success = index_manager.delete_branch(workspace, project, branch)
        if success:
            return {"message": f"Branch data deleted for {workspace}/{project}/{branch}"}
        else:
            return {"message": f"No data found for branch {branch}", "status": "not_found"}
    except Exception as e:
        logger.error(f"Error deleting branch index: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/branch/delete")
def delete_branch_index_post(request: DeleteBranchRequest):
    """Delete all index data for a specific branch (POST version for complex branch names)."""
    try:
        success = index_manager.delete_branch(
            request.workspace, 
            request.project, 
            request.branch
        )
        if success:
            return {"message": f"Branch data deleted for {request.workspace}/{request.project}/{request.branch}"}
        else:
            return {"message": f"No data found for branch {request.branch}", "status": "not_found"}
    except Exception as e:
        logger.error(f"Error deleting branch index: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/branch/list/{workspace}/{project}")
def list_indexed_branches(workspace: str, project: str):
    """List all branches that have indexed data for a project."""
    try:
        branches = index_manager.get_indexed_branches(workspace, project)
        return {
            "workspace": workspace,
            "project": project,
            "branches": branches,
            "count": len(branches)
        }
    except Exception as e:
        logger.error(f"Error listing branches: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/branch/stats/{workspace}/{project}/{branch:path}")
def get_branch_stats(workspace: str, project: str, branch: str):
    """Get statistics for a specific branch within a project."""
    try:
        stats = index_manager._get_branch_index_stats(workspace, project, branch)
        return stats
    except Exception as e:
        logger.error(f"Error getting branch stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/system/gc")
def force_garbage_collection():
    """Force garbage collection to free memory"""
    try:
        # Get memory info before
        import psutil
        process = psutil.Process()
        memory_before = process.memory_info().rss / 1024 / 1024  # MB
        
        # Run garbage collection
        collected = gc.collect()
        
        # Get memory info after
        memory_after = process.memory_info().rss / 1024 / 1024  # MB
        freed = memory_before - memory_after
        
        logger.info(f"Garbage collection: collected {collected} objects, freed {freed:.2f} MB")
        
        return {
            "status": "ok",
            "objects_collected": collected,
            "memory_before_mb": round(memory_before, 2),
            "memory_after_mb": round(memory_after, 2),
            "memory_freed_mb": round(freed, 2)
        }
    except ImportError:
        # psutil not available, just run gc
        collected = gc.collect()
        return {
            "status": "ok",
            "objects_collected": collected
        }
    except Exception as e:
        logger.error(f"Error during garbage collection: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/system/memory")
def get_memory_usage():
    """Get current memory usage"""
    try:
        import psutil
        process = psutil.Process()
        memory_info = process.memory_info()
        
        return {
            "rss_mb": round(memory_info.rss / 1024 / 1024, 2),
            "vms_mb": round(memory_info.vms / 1024 / 1024, 2),
            "percent": round(process.memory_percent(), 2)
        }
    except ImportError:
        return {"error": "psutil not installed"}
    except Exception as e:
        logger.error(f"Error getting memory info: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)

