"""
Pydantic request/response models for the RAG Pipeline API.

All models are defined here to avoid circular imports between routers
and to keep the router files focused on endpoint logic.
"""
import os
from typing import Dict, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator


def _validate_repo_path(path: str) -> str:
    """Validate that a repo path is within the allowed root and contains no traversal."""
    allowed_root = os.environ.get("ALLOWED_REPO_ROOT", "/tmp")
    resolved = os.path.realpath(path)
    if not resolved.startswith(os.path.realpath(allowed_root)):
        raise ValueError(f"Path must be under {allowed_root}, got: {path}")
    return path


def _validate_file_paths(paths: List[str]) -> List[str]:
    for original in paths:
        path = original.replace("\\", "/") if isinstance(original, str) else ""
        if (
            not path
            or path.startswith("/")
            or path.endswith("/")
            or any(segment in {"", ".", ".."} for segment in path.split("/"))
        ):
            raise ValueError(f"Invalid repository-relative file path: {original!r}")
    return paths


# ── Index models ──

class IndexRequest(BaseModel):
    repo_path: str
    workspace: str
    project: str
    branch: str
    commit: str
    source_tree_sha256: Optional[str] = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    collection_target: Optional[str] = Field(default=None, min_length=1)
    publish_branch_alias: bool = False
    publish_legacy_project_alias: bool = False
    preserve_other_branches: bool = False
    cleanup_repo_path: bool = False
    transfer_repo_ownership: bool = False
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

    @field_validator("file_paths")
    @classmethod
    def validate_file_paths(cls, v: List[str]) -> List[str]:
        return _validate_file_paths(v)


class DeleteFilesRequest(BaseModel):
    file_paths: List[str]
    workspace: str
    project: str
    branch: str
    commit: Optional[str] = None

    @field_validator("file_paths")
    @classmethod
    def validate_file_paths(cls, v: List[str]) -> List[str]:
        return _validate_file_paths(v)


class ApplyChangesRequest(BaseModel):
    updated_file_paths: List[str] = Field(default_factory=list)
    deleted_file_paths: List[str] = Field(default_factory=list)
    repo_base: Optional[str] = None
    workspace: str
    project: str
    branch: str
    commit: str

    @field_validator("repo_base")
    @classmethod
    def validate_repo_base(cls, v: Optional[str]) -> Optional[str]:
        return _validate_repo_path(v) if v is not None else None

    @field_validator("updated_file_paths", "deleted_file_paths")
    @classmethod
    def validate_file_paths(cls, v: List[str]) -> List[str]:
        return _validate_file_paths(v)


class AdvanceGenerationRequest(BaseModel):
    updated_file_paths: List[str] = Field(default_factory=list)
    deleted_file_paths: List[str] = Field(default_factory=list)
    repo_base: Optional[str] = None
    workspace: str
    project: str
    branch: str
    source_commit: str
    commit: str
    source_tree_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_collection_target: str = Field(min_length=1)
    collection_target: str = Field(min_length=1)
    publish_branch_alias: bool = False
    publish_legacy_project_alias: bool = False

    @field_validator("repo_base")
    @classmethod
    def validate_repo_base(cls, v: Optional[str]) -> Optional[str]:
        return _validate_repo_path(v) if v is not None else None

    @field_validator("updated_file_paths", "deleted_file_paths")
    @classmethod
    def validate_file_paths(cls, v: List[str]) -> List[str]:
        return _validate_file_paths(v)


class GenerationAliasPublicationRequest(BaseModel):
    """Repair the readable aliases of one already sealed generation."""
    workspace: str
    project: str
    branch: str
    commit: str
    collection_target: str = Field(min_length=1)
    generation_manifest_sha256: Optional[str] = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    publish_branch_alias: bool = True
    publish_legacy_project_alias: bool = False


class DeleteBranchRequest(BaseModel):
    workspace: str
    project: str
    branch: str


class CleanupStaleBranchesRequest(BaseModel):
    workspace: str
    project: str
    protected_branches: List[str] = Field(min_length=1)
    branches_to_keep: Optional[List[str]] = None

    @field_validator("protected_branches", "branches_to_keep")
    @classmethod
    def validate_branch_names(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        if value is None:
            return value
        if any(not branch or branch != branch.strip() for branch in value):
            raise ValueError("Branch names must be non-blank exact repository identities")
        if len(set(value)) != len(value):
            raise ValueError("Branch names must be unique")
        return value


class RevisionPreflightResponse(BaseModel):
    workspace: str
    project: str
    branch: str
    commit: str
    point_count: int = Field(gt=0)
    repository_revision: str
    repository_facts_sha256: str
    plugin_ids: List[str]
    plugin_fingerprint: str
    plugin_descriptor_fingerprint: str
    plugin_implementation_fingerprint: str
    index_representation_fingerprint: str
    generation_schema: str
    generation_member_count: int = Field(gt=0)
    generation_members_sha256: str
    generation_manifest_sha256: str
    source_tree_sha256: str
    index_include_patterns: List[str]
    index_exclude_patterns: List[str]
    index_selection_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


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
    repository_revision: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=200,
    )
    repository_generation_manifest_sha256: Optional[str] = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    collection_target: Optional[str] = Field(default=None, min_length=1)


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
    source_revision: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=200,
    )
    base_revision: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=200,
    )
    base_generation_manifest_sha256: Optional[str] = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    pr_generation_fingerprint: Optional[str] = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    pr_overlay_generation_manifest_sha256: Optional[str] = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    collection_target: Optional[str] = Field(default=None, min_length=1)

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
    source_revision: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=200,
    )
    base_revision: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=200,
    )
    base_generation_manifest_sha256: Optional[str] = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    pr_generation_fingerprint: Optional[str] = Field(
        default=None,
        pattern=r"^sha256:[0-9a-f]{64}$",
    )
    pr_overlay_generation_manifest_sha256: Optional[str] = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    collection_target: Optional[str] = Field(default=None, min_length=1)


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
    """Info about a single PR file.

    ``partial_diff`` content is review evidence, not a complete repository
    artifact. It must never be parsed or embedded as source code.
    """
    path: str
    content: str
    change_type: str  # ADDED, MODIFIED, DELETED
    content_state: Literal["complete", "partial_diff"] = "complete"

    @field_validator("change_type")
    @classmethod
    def normalize_change_type(cls, value: str) -> str:
        normalized = str(value or "").strip().upper()
        if normalized not in {
            "ADDED",
            "MODIFIED",
            "DELETED",
            "RENAMED",
            "BINARY",
        }:
            raise ValueError(f"Unsupported PR change type: {value!r}")
        return normalized


class PRIndexRequest(BaseModel):
    """Request to index PR files into main collection with PR metadata."""
    workspace: str
    project: str
    pr_number: int
    branch: str
    base_branch: Optional[str] = None
    source_revision: Optional[str] = Field(default=None, min_length=1, max_length=200)
    base_revision: Optional[str] = Field(default=None, min_length=1, max_length=200)
    repository_plugins: List[str] = Field(default_factory=list)
    plugin_detection_evidence: Dict[str, List[str]] = Field(default_factory=dict)
    plugin_fingerprint: str = "sha256:" + "0" * 64
    plugin_descriptor_fingerprint: str = "sha256:" + "0" * 64
    files: List[PRFileInfo]
    base_generation_manifest_sha256: Optional[str] = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    collection_target: Optional[str] = Field(default=None, min_length=1)


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
