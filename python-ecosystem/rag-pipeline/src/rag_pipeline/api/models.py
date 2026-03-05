"""
Pydantic request/response models for the RAG Pipeline API.

All models are defined here to avoid circular imports between routers
and to keep the router files focused on endpoint logic.
"""
import os
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


def _validate_repo_path(path: str) -> str:
    """Validate that a repo path is within the allowed root and contains no traversal."""
    allowed_root = os.environ.get("ALLOWED_REPO_ROOT", "/tmp")
    resolved = os.path.realpath(path)
    if not resolved.startswith(os.path.realpath(allowed_root)):
        raise ValueError(f"Path must be under {allowed_root}, got: {path}")
    return path


# ── Index models ──

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


class DeleteBranchRequest(BaseModel):
    workspace: str
    project: str
    branch: str


class CleanupStaleBranchesRequest(BaseModel):
    workspace: str
    project: str
    protected_branches: List[str] = ["main", "master", "develop"]
    branches_to_keep: Optional[List[str]] = None


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


# ── Query models ──

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
    branch: Optional[str] = None
    base_branch: Optional[str] = None
    changed_files: List[str]
    diff_snippets: Optional[List[str]] = Field(default_factory=list)
    pr_title: Optional[str] = None
    pr_description: Optional[str] = None
    top_k: Optional[int] = 15
    enable_priority_reranking: Optional[bool] = True
    min_relevance_score: Optional[float] = 0.7
    deleted_files: Optional[List[str]] = Field(default_factory=list)
    pr_number: Optional[int] = None
    all_pr_changed_files: Optional[List[str]] = Field(default_factory=list)

    @field_validator('changed_files')
    @classmethod
    def validate_changed_files(cls, v):
        max_files = int(os.getenv('RAG_MAX_FILES_PER_REQUEST', '500'))
        if len(v) > max_files:
            raise ValueError(f'Too many changed files: {len(v)} > {max_files}')
        return v

    @field_validator('diff_snippets')
    @classmethod
    def validate_snippets(cls, v):
        if v is not None:
            max_snippets = int(os.getenv('RAG_MAX_SNIPPETS_PER_REQUEST', '50'))
            if len(v) > max_snippets:
                raise ValueError(f'Too many diff snippets: {len(v)} > {max_snippets}')
        return v


class DeterministicContextRequest(BaseModel):
    """Request for deterministic metadata-based context retrieval."""
    workspace: str
    project: str
    branches: List[str]
    file_paths: List[str]
    limit_per_file: Optional[int] = 10
    pr_number: Optional[int] = None
    pr_changed_files: Optional[List[str]] = None
    additional_identifiers: Optional[List[str]] = Field(
        default=None,
        description="Extra type/function names to look up (from AST enrichment: extends, implements, calls). "
                    "Injected directly into Step 2 definition lookup alongside Qdrant-extracted identifiers."
    )


# ── Parse models ──

class ParseFileRequest(BaseModel):
    """Request to parse a single file and extract AST metadata."""
    path: str
    content: str
    language: Optional[str] = None


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
    semantic_names: List[str] = []
    parent_class: Optional[str] = None
    namespace: Optional[str] = None
    calls: List[str] = []
    success: bool = True
    error: Optional[str] = None


# ── PR indexing models ──

class PRFileInfo(BaseModel):
    """Info about a single PR file."""
    path: str
    content: str
    change_type: str  # ADDED, MODIFIED, DELETED


class PRIndexRequest(BaseModel):
    """Request to index PR files into main collection with PR metadata."""
    workspace: str
    project: str
    pr_number: int
    branch: str
    files: List[PRFileInfo]
