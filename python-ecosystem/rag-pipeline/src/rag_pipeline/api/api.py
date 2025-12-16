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

app = FastAPI(title="CodeCrow RAG API", version="1.0.0")

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
    branch: str
    changed_files: List[str]
    diff_snippets: Optional[List[str]] = []
    pr_title: Optional[str] = None
    pr_description: Optional[str] = None
    top_k: Optional[int] = 10


@app.get("/")
def root():
    return {"message": "CodeCrow RAG Pipeline API", "version": "1.0.0"}


@app.get("/health")
def health():
    return {"status": "healthy"}


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
    """Get context for PR review"""
    try:
        context = query_service.get_context_for_pr(
            workspace=request.workspace,
            project=request.project,
            branch=request.branch,
            changed_files=request.changed_files,
            diff_snippets=request.diff_snippets or [],
            pr_title=request.pr_title,
            pr_description=request.pr_description,
            top_k=request.top_k
        )
        return {"context": context}
    except Exception as e:
        logger.error(f"Error getting PR context: {e}")
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

