"""
Pydantic request/response models for the RAG Pipeline API.

All models are defined here to avoid circular imports between routers
and to keep the router files focused on endpoint logic.
"""
import os
from typing import List, Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator

from ..models.snapshot import ContextSnapshotV1, EXACT_REVISION_PATTERN


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
    # Exact evaluation snapshots must remain queryable after the branch moves
    # to another revision. Normal project indexing keeps its replacement
    # behavior; callers opt in only when they need an immutable revision cache.
    retain_revisions: bool = False

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
    revision: Optional[str] = Field(default=None, pattern=EXACT_REVISION_PATTERN)
    snapshot: Optional[ContextSnapshotV1] = None
    execution_id: Optional[str] = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def bind_exact_revision_to_snapshot(self):
        if self.snapshot is None:
            return self
        if self.revision is None:
            raise ValueError("snapshot-bound semantic search requires revision")
        if self.revision not in {self.snapshot.base_sha, self.snapshot.head_sha}:
            raise ValueError("semantic search revision is outside snapshot")
        return self


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
    snapshot: Optional[ContextSnapshotV1] = None
    execution_id: Optional[str] = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def require_branch_labels_for_exact_snapshot(self):
        if self.snapshot is not None and (not self.branch or not self.base_branch):
            raise ValueError(
                "snapshot-bound PR context requires source and base branch labels"
            )
        if self.snapshot is not None and self.pr_number is not None and not self.execution_id:
            raise ValueError(
                "snapshot-bound PR overlay queries require execution_id"
            )
        return self

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
    snapshot: Optional[ContextSnapshotV1] = None
    execution_id: Optional[str] = Field(default=None, min_length=1, max_length=160)
    additional_identifiers: Optional[List[str]] = Field(
        default=None,
        description="Extra type/function names to look up (from AST enrichment: extends, implements, calls). "
                    "Injected directly into Step 2 definition lookup alongside Qdrant-extracted identifiers."
    )

    @model_validator(mode="after")
    def bind_exact_repository_coordinates(self):
        if self.snapshot is None:
            return self
        if len(self.branches) < 2 or not all(self.branches[:2]):
            raise ValueError(
                "snapshot-bound deterministic context requires source and base branch labels"
            )
        if self.pr_number is not None and not self.execution_id:
            raise ValueError(
                "snapshot-bound deterministic PR overlay queries require execution_id"
            )
        return self


# ── Parse models ──

class ParseFileRequest(BaseModel):
    """Request to parse a single file and extract AST metadata."""
    path: str
    content: str
    language: Optional[str] = None


class ParseBatchRequest(BaseModel):
    """Request to parse multiple files in batch."""
    files: List[ParseFileRequest]


class ParsedSymbol(BaseModel):
    """One definition extracted from an exact source file."""

    symbol_id: str
    path: str
    name: str
    qualified_name: str
    kind: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    parent_symbol: Optional[str] = None
    signature: Optional[str] = None
    parameters: List[str] = Field(default_factory=list)
    return_type: Optional[str] = None
    modifiers: List[str] = Field(default_factory=list)
    decorators: List[str] = Field(default_factory=list)
    extraction_method: Literal["ast", "fallback"] = "ast"


class ParsedRelationship(BaseModel):
    """Unresolved or resolved edge originating in one parsed source file."""

    relationship_id: str
    source_symbol_id: str
    source_name: str
    target_name: str
    relationship_type: Literal[
        "imports",
        "calls",
        "references",
        "extends",
        "implements",
        "contained_by",
    ]
    source_line: int = Field(ge=1)
    target_symbol_id: Optional[str] = None
    target_path: Optional[str] = None
    resolution: Literal["unresolved", "resolved", "ambiguous"] = "unresolved"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ParsedFileMetadata(BaseModel):
    """AST metadata extracted from a file."""
    path: str
    language: Optional[str] = None
    imports: List[str] = Field(default_factory=list)
    extends: List[str] = Field(default_factory=list)
    implements: List[str] = Field(default_factory=list)
    semantic_names: List[str] = Field(default_factory=list)
    parent_class: Optional[str] = None
    namespace: Optional[str] = None
    calls: List[str] = Field(default_factory=list)
    content_digest: Optional[str] = None
    parser_version: Optional[str] = None
    ast_supported: bool = False
    symbols: List[ParsedSymbol] = Field(default_factory=list)
    relationships: List[ParsedRelationship] = Field(default_factory=list)
    degraded_reason: Optional[str] = None
    success: bool = True
    error: Optional[str] = None


class ParsedRepositoryGraphV1(BaseModel):
    """Resolved symbol graph for one exact batch of parsed source files."""

    schema_version: Literal[1] = 1
    files: List[str] = Field(default_factory=list)
    symbols: List[ParsedSymbol] = Field(default_factory=list)
    relationships: List[ParsedRelationship] = Field(default_factory=list)
    resolved_count: int = Field(default=0, ge=0)
    ambiguous_count: int = Field(default=0, ge=0)
    unresolved_count: int = Field(default=0, ge=0)
    resolution_gaps: List[str] = Field(default_factory=list)


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
    snapshot: Optional[ContextSnapshotV1] = None
    execution_id: Optional[str] = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def require_execution_for_exact_overlay(self):
        if self.snapshot is not None and not self.execution_id:
            raise ValueError("snapshot-bound PR indexing requires execution_id")
        return self


# ── Vector storage inspection models ──

class VectorInspectFilters(BaseModel):
    """Bounded filters for vector storage inspection.

    These are internal service-to-service filters. The public web app must
    resolve workspace/project access on the Java side before forwarding them.
    """
    branches: List[str] = Field(default_factory=list, max_length=20)
    languages: List[str] = Field(default_factory=list, max_length=20)
    path: Optional[str] = Field(default=None, max_length=500)
    file_query: Optional[str] = Field(default=None, max_length=500)
    semantic_query: Optional[str] = Field(default=None, max_length=160)
    pr_number: Optional[int] = Field(default=None, ge=1)
    include_pr: bool = True


class VectorGraphRequest(BaseModel):
    """Request a bounded graph slice from a project vector collection."""
    filters: VectorInspectFilters = Field(default_factory=VectorInspectFilters)
    limit: int = Field(default=160, ge=20, le=5000)
    cursor: Optional[str] = Field(default=None, max_length=256)
    scan_limit: int = Field(default=2500, ge=100, le=100000)


class VectorNodeRequest(BaseModel):
    """Request a point detail and bounded neighborhood."""
    filters: VectorInspectFilters = Field(default_factory=VectorInspectFilters)
    neighbor_limit: int = Field(default=80, ge=10, le=160)
