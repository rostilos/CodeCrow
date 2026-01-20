import logging
import gc
from typing import List, Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel

from ..models.config import RAGConfig, IndexStats
from ..core.index_manager import RAGIndexManager
from ..core.delta_index_manager import DeltaIndexManager, DeltaIndexStats, DeltaIndexStatus
from ..services.query_service import RAGQueryService
from ..services.hybrid_query_service import HybridQueryService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="CodeCrow RAG API", version="1.1.0")

config = RAGConfig()
index_manager = RAGIndexManager(config)
delta_index_manager = DeltaIndexManager(config)
query_service = RAGQueryService(config)
hybrid_query_service = HybridQueryService(config)


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
    branch: Optional[str] = None  # Optional - if None, return empty context
    changed_files: List[str]
    diff_snippets: Optional[List[str]] = []
    pr_title: Optional[str] = None
    pr_description: Optional[str] = None
    top_k: Optional[int] = 10
    enable_priority_reranking: Optional[bool] = True  # Enable Lost-in-Middle protection
    min_relevance_score: Optional[float] = 0.7  # Minimum relevance threshold


class HybridPRContextRequest(BaseModel):
    """Extended PR context request supporting hybrid retrieval."""
    workspace: str
    project: str
    base_branch: str  # The indexed base branch (e.g., "master")
    target_branch: str  # PR target branch (e.g., "release/1.0")
    changed_files: List[str]
    diff_snippets: Optional[List[str]] = []
    pr_title: Optional[str] = None
    pr_description: Optional[str] = None
    top_k: Optional[int] = 15
    enable_priority_reranking: Optional[bool] = True
    min_relevance_score: Optional[float] = 0.7
    delta_boost: Optional[float] = 1.3  # Score multiplier for delta results


class DeltaIndexRequest(BaseModel):
    """Request to create a delta index."""
    workspace: str
    project: str
    base_branch: str  # e.g., "master"
    delta_branch: str  # e.g., "release/1.0"
    repo_path: str
    base_commit: Optional[str] = None
    delta_commit: Optional[str] = None
    raw_diff: Optional[str] = None  # Alternative to commits
    exclude_patterns: Optional[List[str]] = None


class DeltaIndexUpdateRequest(BaseModel):
    """Request to update an existing delta index."""
    workspace: str
    project: str
    delta_branch: str
    repo_path: str
    delta_commit: str
    raw_diff: str
    exclude_patterns: Optional[List[str]] = None


class DeltaIndexResponse(BaseModel):
    """Response for delta index operations."""
    workspace: str
    project: str
    branch_name: str
    base_branch: str
    collection_name: str
    status: str
    chunk_count: int
    file_count: int
    base_commit_hash: Optional[str] = None
    delta_commit_hash: Optional[str] = None
    error_message: Optional[str] = None


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
    Get context for PR review with Lost-in-the-Middle protection.
    
    Implements:
    - Query decomposition for comprehensive coverage
    - Priority-based reranking (core files boosted)
    - Relevance threshold filtering
    - Deduplication
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
            min_relevance_score=request.min_relevance_score
        )
        
        # Add metadata about processing
        context["_metadata"] = {
            "priority_reranking_enabled": request.enable_priority_reranking,
            "min_relevance_score": request.min_relevance_score,
            "changed_files_count": len(request.changed_files),
            "result_count": len(context.get("relevant_code", []))
        }
        
        return {"context": context}
    except Exception as e:
        logger.error(f"Error getting PR context: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# DELTA INDEX ENDPOINTS (Hierarchical RAG)
# =============================================================================

@app.post("/delta/index", response_model=DeltaIndexResponse)
def create_delta_index(request: DeltaIndexRequest):
    """
    Create a delta index containing only the differences between delta_branch and base_branch.
    
    Delta indexes enable efficient hybrid RAG queries for release branches and similar use cases
    where full re-indexing would be expensive but branch-specific context is valuable.
    """
    try:
        stats = delta_index_manager.create_delta_index(
            workspace=request.workspace,
            project=request.project,
            base_branch=request.base_branch,
            delta_branch=request.delta_branch,
            repo_path=request.repo_path,
            base_commit=request.base_commit,
            delta_commit=request.delta_commit,
            raw_diff=request.raw_diff,
            exclude_patterns=request.exclude_patterns
        )
        
        return DeltaIndexResponse(
            workspace=stats.workspace,
            project=stats.project,
            branch_name=stats.branch_name,
            base_branch=stats.base_branch,
            collection_name=stats.collection_name,
            status=stats.status.value,
            chunk_count=stats.chunk_count,
            file_count=stats.file_count,
            base_commit_hash=stats.base_commit_hash,
            delta_commit_hash=stats.delta_commit_hash,
            error_message=stats.error_message
        )
    except Exception as e:
        logger.error(f"Error creating delta index: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/delta/index", response_model=DeltaIndexResponse)
def update_delta_index(request: DeltaIndexUpdateRequest):
    """
    Incrementally update an existing delta index with new changes.
    """
    try:
        stats = delta_index_manager.update_delta_index(
            workspace=request.workspace,
            project=request.project,
            delta_branch=request.delta_branch,
            repo_path=request.repo_path,
            delta_commit=request.delta_commit,
            raw_diff=request.raw_diff,
            exclude_patterns=request.exclude_patterns
        )
        
        return DeltaIndexResponse(
            workspace=stats.workspace,
            project=stats.project,
            branch_name=stats.branch_name,
            base_branch=stats.base_branch,
            collection_name=stats.collection_name,
            status=stats.status.value,
            chunk_count=stats.chunk_count,
            file_count=stats.file_count,
            base_commit_hash=stats.base_commit_hash,
            delta_commit_hash=stats.delta_commit_hash,
            error_message=stats.error_message
        )
    except Exception as e:
        logger.error(f"Error updating delta index: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/delta/index/{workspace}/{project}/{branch:path}")
def delete_delta_index(workspace: str, project: str, branch: str):
    """Delete a delta index."""
    try:
        success = delta_index_manager.delete_delta_index(workspace, project, branch)
        if success:
            return {"message": f"Delta index deleted for {workspace}/{project}/{branch}"}
        else:
            raise HTTPException(status_code=404, detail="Delta index not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting delta index: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/delta/index/{workspace}/{project}/{branch:path}")
def get_delta_index_stats(workspace: str, project: str, branch: str):
    """Get statistics about a delta index."""
    try:
        stats = delta_index_manager.get_delta_index_stats(workspace, project, branch)
        if stats is None:
            raise HTTPException(status_code=404, detail="Delta index not found")
        
        return DeltaIndexResponse(
            workspace=stats.workspace,
            project=stats.project,
            branch_name=stats.branch_name,
            base_branch=stats.base_branch,
            collection_name=stats.collection_name,
            status=stats.status.value,
            chunk_count=stats.chunk_count,
            file_count=stats.file_count,
            base_commit_hash=stats.base_commit_hash,
            delta_commit_hash=stats.delta_commit_hash,
            error_message=stats.error_message
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting delta index stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/delta/list/{workspace}/{project}")
def list_delta_indexes(workspace: str, project: str):
    """List all delta indexes for a project."""
    try:
        indexes = delta_index_manager.list_delta_indexes(workspace, project)
        return {
            "indexes": [
                DeltaIndexResponse(
                    workspace=s.workspace,
                    project=s.project,
                    branch_name=s.branch_name,
                    base_branch=s.base_branch,
                    collection_name=s.collection_name,
                    status=s.status.value,
                    chunk_count=s.chunk_count,
                    file_count=s.file_count,
                    base_commit_hash=s.base_commit_hash,
                    delta_commit_hash=s.delta_commit_hash,
                    error_message=s.error_message
                ) for s in indexes
            ]
        }
    except Exception as e:
        logger.error(f"Error listing delta indexes: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/delta/exists/{workspace}/{project}/{branch:path}")
def check_delta_index_exists(workspace: str, project: str, branch: str):
    """Check if a delta index exists for a branch."""
    exists = delta_index_manager.delta_index_exists(workspace, project, branch)
    return {"exists": exists, "branch": branch}


# =============================================================================
# HYBRID QUERY ENDPOINTS
# =============================================================================

@app.post("/query/pr-context-hybrid")
def get_pr_context_hybrid(request: HybridPRContextRequest):
    """
    Get PR context using hybrid retrieval from base + delta indexes.
    
    This endpoint combines results from:
    1. Base index (e.g., master) - for general repository context
    2. Delta index (e.g., release/1.0) - for branch-specific changes
    
    Results from delta index receive a score boost (configurable via delta_boost).
    Use this when PR targets a branch that differs from the base RAG index.
    """
    try:
        # Check if hybrid should be used
        should_use, reason = hybrid_query_service.should_use_hybrid(
            workspace=request.workspace,
            project=request.project,
            base_branch=request.base_branch,
            target_branch=request.target_branch
        )
        
        if not should_use and reason == "no_base_index":
            logger.warning(f"No base index available for hybrid query")
            return {
                "context": {
                    "relevant_code": [],
                    "related_files": [],
                    "changed_files": request.changed_files,
                    "_hybrid_metadata": {
                        "skipped_reason": reason,
                        "base_branch": request.base_branch,
                        "target_branch": request.target_branch
                    }
                }
            }
        
        result = hybrid_query_service.get_hybrid_context_for_pr(
            workspace=request.workspace,
            project=request.project,
            base_branch=request.base_branch,
            target_branch=request.target_branch,
            changed_files=request.changed_files,
            diff_snippets=request.diff_snippets or [],
            pr_title=request.pr_title,
            pr_description=request.pr_description,
            top_k=request.top_k,
            enable_priority_reranking=request.enable_priority_reranking,
            min_relevance_score=request.min_relevance_score,
            delta_boost=request.delta_boost
        )
        
        return {
            "context": {
                "relevant_code": result.relevant_code,
                "related_files": result.related_files,
                "changed_files": result.changed_files,
                "_hybrid_metadata": result.hybrid_metadata
            }
        }
    except Exception as e:
        logger.error(f"Error getting hybrid PR context: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/query/should-use-hybrid/{workspace}/{project}")
def should_use_hybrid_query(
    workspace: str, 
    project: str, 
    base_branch: str, 
    target_branch: str
):
    """
    Check if hybrid query should be used for a PR.
    
    Returns recommendation based on:
    - Whether base index exists
    - Whether delta index exists for target branch
    """
    should_use, reason = hybrid_query_service.should_use_hybrid(
        workspace=workspace,
        project=project,
        base_branch=base_branch,
        target_branch=target_branch
    )
    
    return {
        "should_use_hybrid": should_use,
        "reason": reason,
        "base_branch": base_branch,
        "target_branch": target_branch
    }


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

