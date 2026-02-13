import logging
import gc
import os
import uuid
from typing import Dict, List, Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel, field_validator
from llama_index.core.schema import TextNode
from qdrant_client.models import PointStruct

from ..models.config import RAGConfig, IndexStats
from ..core.index_manager import RAGIndexManager
from ..services.query_service import RAGQueryService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="CodeCrow RAG API", version="2.0.0")

# Service-to-service auth
from .middleware import ServiceSecretMiddleware
app.add_middleware(ServiceSecretMiddleware)

config = RAGConfig()
index_manager = RAGIndexManager(config)
query_service = RAGQueryService(config)


# Allowed base directory for all repo paths (set via env or default to /tmp)
_ALLOWED_REPO_ROOT = os.environ.get("ALLOWED_REPO_ROOT", "/tmp")


def _validate_repo_path(path: str) -> str:
    """Validate that a repo path is within the allowed root and contains no traversal."""
    resolved = os.path.realpath(path)
    if not resolved.startswith(os.path.realpath(_ALLOWED_REPO_ROOT)):
        raise ValueError(
            f"Path must be under {_ALLOWED_REPO_ROOT}, got: {path}"
        )
    return path


class IndexRequest(BaseModel):
    repo_path: str
    workspace: str
    project: str
    branch: str
    commit: str
    include_patterns: Optional[List[str]] = None
    exclude_patterns: Optional[List[str]] = None

    @field_validator("repo_path")
    @classmethod
    def validate_repo_path(cls, v: str) -> str:
        return _validate_repo_path(v)


class UpdateFilesRequest(BaseModel):
    file_paths: List[str]
    repo_base: str
    workspace: str
    project: str
    branch: str
    commit: str

    @field_validator("repo_base")
    @classmethod
    def validate_repo_base(cls, v: str) -> str:
        return _validate_repo_path(v)


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
    # NEW: PR-specific hybrid query mode
    pr_number: Optional[int] = None  # If set, enables hybrid query with PR data priority
    all_pr_changed_files: Optional[List[str]] = []  # All files in PR (for exclusion from branch query)


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


# =============================================================================
# PARSE ENDPOINTS (AST metadata extraction without indexing)
# =============================================================================

class ParseFileRequest(BaseModel):
    """Request to parse a single file and extract AST metadata."""
    path: str
    content: str
    language: Optional[str] = None  # Auto-detected if not provided


class ParseBatchRequest(BaseModel):
    """Request to parse multiple files in batch."""
    files: List[ParseFileRequest]


class ParsedFileMetadata(BaseModel):
    """AST metadata extracted from a file."""
    path: str
    language: Optional[str] = None
    imports: List[str] = []
    extends: List[str] = []
    implements: List[str] = []
    semantic_names: List[str] = []  # Function/class/method names
    parent_class: Optional[str] = None
    namespace: Optional[str] = None
    calls: List[str] = []  # Called functions/methods
    success: bool = True
    error: Optional[str] = None


@app.post("/parse", response_model=ParsedFileMetadata)
def parse_file(request: ParseFileRequest):
    """
    Parse a single file and extract AST metadata WITHOUT indexing.
    
    Returns tree-sitter extracted metadata:
    - imports: Import statements
    - extends: Parent classes/interfaces
    - implements: Implemented interfaces
    - semantic_names: Function/class/method names defined
    - namespace: Package/namespace
    - calls: Called functions/methods
    
    Used by Java pipeline-agent to build dependency graph.
    """
    try:
        from ..core.splitter import ASTCodeSplitter
        from ..core.splitter.languages import get_language_from_path, EXTENSION_TO_LANGUAGE
        
        # Detect language
        language = request.language
        if not language:
            lang_enum = get_language_from_path(request.path)
            language = lang_enum.value if lang_enum else None
        
        if not language:
            # Try to infer from extension
            ext = '.' + request.path.rsplit('.', 1)[-1] if '.' in request.path else ''
            language = EXTENSION_TO_LANGUAGE.get(ext, {}).get('name')
        
        splitter = ASTCodeSplitter(
            max_chunk_size=50000,  # Large to avoid splitting
            enrich_embedding_text=False
        )
        
        # Create a minimal document for parsing
        from llama_index.core.schema import Document as LlamaDocument
        doc = LlamaDocument(text=request.content, metadata={'path': request.path})
        
        # Parse and extract chunks
        nodes = splitter.split_documents([doc])
        
        # Aggregate metadata from all chunks
        imports = set()
        extends = set()
        implements = set()
        semantic_names = set()
        calls = set()
        namespace = None
        parent_classes = set()
        
        for node in nodes:
            meta = node.metadata
            
            if meta.get('imports'):
                imports.update(meta['imports'])
            if meta.get('extends'):
                extends.update(meta['extends'])
            if meta.get('implements'):
                implements.update(meta['implements'])
            if meta.get('semantic_names'):
                semantic_names.update(meta['semantic_names'])
            if meta.get('calls'):
                calls.update(meta['calls'])
            if meta.get('namespace') and not namespace:
                namespace = meta['namespace']
            if meta.get('parent_class'):
                parent_classes.add(meta['parent_class'])
        
        # Primary parent class (first one found)
        parent_class = list(parent_classes)[0] if parent_classes else None
        
        return ParsedFileMetadata(
            path=request.path,
            language=language,
            imports=sorted(list(imports)),
            extends=sorted(list(extends)),
            implements=sorted(list(implements)),
            semantic_names=sorted(list(semantic_names)),
            parent_class=parent_class,
            namespace=namespace,
            calls=sorted(list(calls)),
            success=True
        )
        
    except Exception as e:
        logger.warning(f"Error parsing file {request.path}: {e}")
        return ParsedFileMetadata(
            path=request.path,
            success=False,
            error=str(e)
        )


@app.post("/parse/batch")
def parse_files_batch(request: ParseBatchRequest):
    """
    Parse multiple files and extract AST metadata in batch.
    
    Returns list of ParsedFileMetadata for each file.
    Continues processing even if individual files fail.
    """
    results = []
    
    for file_req in request.files:
        result = parse_file(file_req)
        results.append(result)
    
    successful = sum(1 for r in results if r.success)
    failed = len(results) - successful
    
    logger.info(f"Batch parse: {successful} successful, {failed} failed out of {len(results)} files")
    
    return {
        "results": results,
        "summary": {
            "total": len(results),
            "successful": successful,
            "failed": failed
        }
    }


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
    include_patterns: Optional[List[str]] = None
    exclude_patterns: Optional[List[str]] = None

    @field_validator("repo_path")
    @classmethod
    def validate_repo_path(cls, v: str) -> str:
        return _validate_repo_path(v)


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
            include_patterns=request.include_patterns,
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
    Get context for PR review with multi-branch support and optional PR-specific hybrid mode.
    
    When pr_number is provided, uses HYBRID query:
    1. Query PR-indexed chunks (pr=true, pr_number=X) - these are the actual changed files
    2. Query branch data, excluding files that are in the PR (to get unchanged dependencies)
    3. Merge results with PR data taking priority
    
    Args:
        branch: Target branch (PR source branch)
        base_branch: Base branch (PR target, e.g., 'main'). Auto-detected if not provided.
        deleted_files: Files deleted in target branch (excluded from results)
        pr_number: If set, enables hybrid query with PR data priority
        all_pr_changed_files: Files to exclude from branch query (PR files already indexed separately)
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
        
        pr_results = []
        
        # HYBRID MODE: Query PR-indexed data first if pr_number is provided
        if request.pr_number:
            pr_results = _query_pr_indexed_data(
                workspace=request.workspace,
                project=request.project,
                pr_number=request.pr_number,
                query_texts=request.diff_snippets or [],
                pr_title=request.pr_title,
                top_k=request.top_k or 15
            )
            logger.info(f"Hybrid mode: Found {len(pr_results)} PR-specific chunks for PR #{request.pr_number}")
        
        # Get branch context (with optional exclusion of PR files)
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
            deleted_files=request.deleted_files or [],
            # Pass PR files to exclude if in hybrid mode (guard against None)
            exclude_pr_files=(request.all_pr_changed_files or []) if request.pr_number else []
        )
        
        # Merge PR results with branch results (PR first, then branch)
        if pr_results:
            pr_paths = set()
            merged_code = []
            
            # PR results first (highest priority - fresh data)
            # Include ALL chunks from PR (multiple chunks per file allowed)
            for pr_chunk in pr_results:
                merged_code.append(pr_chunk)
                path = pr_chunk.get("path", "")
                if path:
                    pr_paths.add(path)
            
            # Then branch results (excluding files already covered by PR)
            for branch_chunk in context.get("relevant_code", []):
                path = branch_chunk.get("path", "")
                if path not in pr_paths:
                    merged_code.append(branch_chunk)
            
            context["relevant_code"] = merged_code
            context["_pr_chunks_count"] = len(pr_results)
        
        # Add metadata about processing
        context["_metadata"] = {
            "priority_reranking_enabled": request.enable_priority_reranking,
            "min_relevance_score": request.min_relevance_score,
            "changed_files_count": len(request.changed_files),
            "result_count": len(context.get("relevant_code", [])),
            "branches_searched": context.get("_branches_searched", [request.branch]),
            "hybrid_mode": request.pr_number is not None,
            "pr_number": request.pr_number
        }
        
        return {"context": context}
    except Exception as e:
        logger.error(f"Error getting PR context: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _query_pr_indexed_data(
    workspace: str,
    project: str,
    pr_number: int,
    query_texts: List[str],
    pr_title: Optional[str],
    top_k: int = 15
) -> List[Dict]:
    """
    Query PR-indexed chunks from the main collection.
    
    Filters by pr=true and pr_number to get only PR-specific data.
    """
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    
    collection_name = index_manager._get_project_collection_name(workspace, project)
    
    if not index_manager._collection_manager.collection_exists(collection_name):
        return []
    
    # Build query from snippets and title
    query_parts = []
    if pr_title:
        query_parts.append(pr_title)
    if query_texts:
        query_parts.extend(query_texts[:5])  # Limit snippets
    
    if not query_parts:
        # If no query, just get all PR chunks
        query_text = f"code changes for PR {pr_number}"
    else:
        query_text = " ".join(query_parts)[:1000]
    
    try:
        # Generate embedding for query
        query_embedding = index_manager.embed_model.get_text_embedding(query_text)
        
        # Search with PR filter
        results = index_manager.qdrant_client.search(
            collection_name=collection_name,
            query_vector=query_embedding,
            query_filter=Filter(
                must=[
                    FieldCondition(key="pr", match=MatchValue(value=True)),
                    FieldCondition(key="pr_number", match=MatchValue(value=pr_number))
                ]
            ),
            limit=top_k,
            with_payload=True
        )
        
        formatted = []
        for r in results:
            formatted.append({
                "path": r.payload.get("path", "unknown"),
                "content": r.payload.get("text", ""),
                "score": r.score,
                "semantic_name": r.payload.get("semantic_name", ""),
                "semantic_type": r.payload.get("semantic_type", ""),
                "branch": r.payload.get("pr_branch", ""),
                "_source": "pr_indexed"
            })
        
        return formatted
        
    except Exception as e:
        logger.warning(f"Error querying PR-indexed data: {e}")
        return []


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


# =============================================================================
# PR-SPECIFIC RAG ENDPOINTS (for PR file indexing with metadata)
# =============================================================================

class PRFileInfo(BaseModel):
    """Info about a single PR file."""
    path: str
    content: str  # Full file content (not just diff)
    change_type: str  # ADDED, MODIFIED, DELETED


class PRIndexRequest(BaseModel):
    """Request to index PR files into main collection with PR metadata."""
    workspace: str
    project: str
    pr_number: int
    branch: str  # Source branch
    files: List[PRFileInfo]


@app.post("/index/pr-files")
def index_pr_files(request: PRIndexRequest):
    """
    Index PR files into the main collection with PR-specific metadata.
    
    Files are indexed with metadata:
    - pr: true
    - pr_number: <pr_number>
    - pr_branch: <branch>
    
    This allows hybrid queries that prioritize PR data over branch data.
    Existing PR points for the same pr_number are deleted first.
    """
    try:
        from datetime import datetime, timezone
        from llama_index.core import Document as LlamaDocument
        
        collection_name = index_manager._get_project_collection_name(
            request.workspace, request.project
        )
        
        # Ensure collection exists
        index_manager._ensure_collection_exists(collection_name)
        
        # Delete existing points for this PR first (handles re-analysis)
        try:
            from qdrant_client.models import Filter, FieldCondition, MatchValue
            index_manager.qdrant_client.delete(
                collection_name=collection_name,
                points_selector=Filter(
                    must=[
                        FieldCondition(key="pr_number", match=MatchValue(value=request.pr_number))
                    ]
                )
            )
            logger.info(f"Deleted existing PR points for PR #{request.pr_number}")
        except Exception as e:
            logger.warning(f"Error deleting existing PR points: {e}")
        
        # Convert files to LlamaIndex documents
        documents = []
        for file_info in request.files:
            if not file_info.content or not file_info.content.strip():
                continue
            if file_info.change_type == "DELETED":
                continue  # Don't index deleted files
                
            doc = LlamaDocument(
                text=file_info.content,
                metadata={
                    "path": file_info.path,
                    "change_type": file_info.change_type,
                }
            )
            documents.append(doc)
        
        if not documents:
            return {
                "status": "skipped",
                "message": "No files to index",
                "chunks_indexed": 0
            }
        
        # Split documents into chunks
        chunks = index_manager.splitter.split_documents(documents)
        
        # Add PR metadata to all chunks
        for chunk in chunks:
            chunk.metadata["pr"] = True
            chunk.metadata["pr_number"] = request.pr_number
            chunk.metadata["pr_branch"] = request.branch
            chunk.metadata["workspace"] = request.workspace
            chunk.metadata["project"] = request.project
            chunk.metadata["branch"] = request.branch  # For compatibility
            chunk.metadata["indexed_at"] = datetime.now(timezone.utc).isoformat()
        
        # Embed and upsert using point_ops (with PR-specific IDs)
        chunk_data = []
        chunks_by_file = {}
        for chunk in chunks:
            path = chunk.metadata.get("path", str(uuid.uuid4()))
            if path not in chunks_by_file:
                chunks_by_file[path] = []
            chunks_by_file[path].append(chunk)
        
        for path, file_chunks in chunks_by_file.items():
            for chunk_index, chunk in enumerate(file_chunks):
                # Use PR-specific ID format to avoid collision with branch data
                key = f"pr:{request.pr_number}:{request.workspace}:{request.project}:{path}:{chunk_index}"
                point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, key))
                chunk_data.append((point_id, chunk))
        
        # Embed and create points
        points = index_manager._point_ops.embed_and_create_points(chunk_data)
        
        # Upsert to collection
        successful, failed = index_manager._point_ops.upsert_points(collection_name, points)
        
        logger.info(f"Indexed PR #{request.pr_number}: {successful} chunks from {len(documents)} files")
        
        return {
            "status": "indexed",
            "pr_number": request.pr_number,
            "files_processed": len(documents),
            "chunks_indexed": successful,
            "chunks_failed": failed
        }
    
    except ValueError as e:
        logger.warning(f"Invalid request for PR indexing: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Internal error indexing PR files: {e}")
        raise HTTPException(status_code=500, detail="Internal indexing error")


@app.delete("/index/pr-files/{workspace}/{project}/{pr_number}")
def delete_pr_files(workspace: str, project: str, pr_number: int):
    """
    Delete all indexed points for a specific PR.
    
    Called after analysis completes to clean up PR-specific data.
    """
    try:
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        
        collection_name = index_manager._get_project_collection_name(workspace, project)
        
        # Check if collection exists
        if not index_manager._collection_manager.collection_exists(collection_name):
            return {
                "status": "skipped",
                "message": f"Collection does not exist"
            }
        
        # Delete points with matching pr_number
        result = index_manager.qdrant_client.delete(
            collection_name=collection_name,
            points_selector=Filter(
                must=[
                    FieldCondition(key="pr_number", match=MatchValue(value=pr_number))
                ]
            )
        )
        
        logger.info(f"Deleted PR #{pr_number} points from {collection_name}")
        
        return {
            "status": "deleted",
            "pr_number": pr_number,
            "collection": collection_name
        }
        
    except Exception as e:
        logger.error(f"Error deleting PR files: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)

