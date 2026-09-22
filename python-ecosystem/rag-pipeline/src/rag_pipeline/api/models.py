"""
Pydantic request/response models for the repository-index API.

All models are defined here to avoid circular imports between routers
and to keep the router files focused on endpoint logic.
"""
import os
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


def _validate_repo_path(path: str) -> str:
    """Validate that a repo path is within the allowed root and contains no traversal."""
    allowed_root = Path(os.environ.get("ALLOWED_REPO_ROOT", "/tmp")).resolve()
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(allowed_root):
        raise ValueError(f"Path must be under {allowed_root}, got: {path}")
    return path


def _validate_source_root(path: Optional[str]) -> Optional[str]:
    if path is None or not path.strip() or path.strip() == ".":
        return None
    normalized = path.strip().replace("\\", "/")
    if (
        normalized.startswith("/")
        or normalized.endswith("/")
        or any(segment in {"", ".", ".."} for segment in normalized.split("/"))
    ):
        raise ValueError("source_root must be a normalized repository-relative directory")
    return normalized


def _validate_repository_relative_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    if (
        not normalized
        or normalized.startswith("/")
        or normalized.endswith("/")
        or any(segment in {"", ".", ".."} for segment in normalized.split("/"))
    ):
        raise ValueError("path must be a normalized repository-relative file path")
    return normalized


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
    transfer_repo_ownership: bool = False
    include_patterns: Optional[List[str]] = None
    exclude_patterns: Optional[List[str]] = None
    project_type: Optional[str] = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9-]{0,63}$",
    )
    source_root: Optional[str] = None
    base_revision: Optional[str] = Field(default=None, min_length=1, max_length=200)
    base_collection_target: Optional[str] = Field(default=None, min_length=1)
    base_generation_manifest_sha256: Optional[str] = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    changed_paths: List[str] = Field(default_factory=list, max_length=50000)
    deleted_paths: List[str] = Field(default_factory=list, max_length=50000)

    @field_validator("repo_path")
    @classmethod
    def validate_repo_path(cls, v: str) -> str:
        return _validate_repo_path(v)

    @field_validator("project_type", mode="before")
    @classmethod
    def validate_project_type(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not str(v).strip() or str(v).strip().casefold() == "auto":
            return None
        return str(v).strip().casefold()

    @field_validator("source_root")
    @classmethod
    def validate_source_root(cls, v: Optional[str]) -> Optional[str]:
        return _validate_source_root(v)

    @field_validator("changed_paths", "deleted_paths")
    @classmethod
    def validate_delta_paths(cls, value: List[str]) -> List[str]:
        return list(dict.fromkeys(
            _validate_repository_relative_path(path) for path in value
        ))

    @model_validator(mode="after")
    def validate_delta_binding(self):
        binding = (
            self.base_revision,
            self.base_collection_target,
            self.base_generation_manifest_sha256,
        )
        if any(value is not None for value in binding) and not all(
            value is not None for value in binding
        ):
            raise ValueError(
                "repository delta requires base_revision, "
                "base_collection_target, and "
                "base_generation_manifest_sha256 together"
            )
        if not self.base_collection_target and (
            self.changed_paths or self.deleted_paths
        ):
            raise ValueError(
                "changed_paths/deleted_paths require an exact base generation"
            )
        overlap = set(self.changed_paths).intersection(self.deleted_paths)
        if overlap:
            raise ValueError(
                "changed_paths and deleted_paths must be disjoint: "
                + ", ".join(sorted(overlap)[:5])
            )
        return self


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
    current_index_representation_fingerprint: str
    generation_schema: str
    generation_member_count: int = Field(gt=0)
    generation_members_sha256: str
    generation_manifest_sha256: str
    source_tree_sha256: str
    index_include_patterns: List[str]
    index_exclude_patterns: List[str]
    index_selection_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RevisionDiscoveryResponse(RevisionPreflightResponse):
    """One discoverable sealed repository generation."""

    collection_target: str = Field(min_length=1)
    document_count: int = Field(ge=0)


class RepresentationIdentityResponse(BaseModel):
    representation_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    index_representation_fingerprint: str = Field(
        pattern=r"^sha256:[0-9a-f]{64}$"
    )
    plugin_descriptor_fingerprint: str = Field(
        pattern=r"^sha256:[0-9a-f]{64}$"
    )
    plugin_implementation_fingerprint: str = Field(
        pattern=r"^sha256:[0-9a-f]{64}$"
    )
    plugin_ids: List[str]


# ── Query models ──

class CodeSearchRequest(BaseModel):
    """Exact revision-bound structural code search."""
    query: str = Field(min_length=1, max_length=1000)
    workspace: str
    project: str
    branch: str
    repository_revision: str = Field(
        min_length=1,
        max_length=200,
    )
    repository_generation_manifest_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$",
    )
    collection_target: str = Field(min_length=1)
    limit: Optional[int] = Field(
        default=None,
        ge=1,
        le=5000,
        description=(
            "Optional explicit result limit. Omit for complete matching up to "
            "the observable global matching-point safety limit."
        ),
    )


class StructuralQueryBinding(BaseModel):
    """Exact server-authorized structural generation binding."""

    workspace: str
    project: str
    branch: str
    repository_revision: str = Field(min_length=1, max_length=200)
    repository_generation_manifest_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$",
    )
    collection_target: str = Field(min_length=1)


class StructuralRelationsRequest(StructuralQueryBinding):
    """Compact one-hop relation metadata for changed repository paths."""

    paths: List[str] = Field(min_length=1, max_length=100)
    max_relations: int = Field(default=80, ge=1, le=500)


class StructuralGraphQueryRequest(StructuralQueryBinding):
    """One exact directional graph query."""

    pattern: str = Field(min_length=1, max_length=100)
    target: str = Field(min_length=1, max_length=1000)
    max_results: int = Field(default=25, ge=1, le=100)


class StructuralUnitRequest(StructuralQueryBinding):
    """Read one exact AST/plugin unit returned by a graph query."""

    unit_id: str = Field(min_length=1, max_length=200)


class ProposedTreeGenerationBinding(BaseModel):
    """Host-owned inputs that identify one exact proposed-tree generation."""

    workspace: str = Field(min_length=1, max_length=200)
    project: str = Field(min_length=1, max_length=300)
    target_branch: str = Field(min_length=1, max_length=500)
    base_revision: str = Field(min_length=1, max_length=200)
    source_revision: str = Field(min_length=1, max_length=200)
    target_repo_path: str
    review_overlay_path: str
    base_collection_target: Optional[str] = Field(default=None, min_length=1)
    base_generation_manifest_sha256: Optional[str] = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    include_patterns: Optional[List[str]] = None
    exclude_patterns: Optional[List[str]] = None
    project_type: Optional[str] = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9-]{0,63}$",
    )
    source_root: Optional[str] = None

    @field_validator("target_repo_path", "review_overlay_path")
    @classmethod
    def validate_local_path(cls, value: str) -> str:
        return _validate_repo_path(value)

    @field_validator("project_type", mode="before")
    @classmethod
    def validate_project_type(cls, value: Optional[str]) -> Optional[str]:
        if (
            value is None
            or not str(value).strip()
            or str(value).strip().casefold() == "auto"
        ):
            return None
        return str(value).strip().casefold()

    @field_validator("source_root")
    @classmethod
    def validate_source_root(cls, value: Optional[str]) -> Optional[str]:
        return _validate_source_root(value)


class ProposedTreePrepareRequest(ProposedTreeGenerationBinding):
    """Prepare and seal one review generation before Stage 1 fans out."""


class ProposedTreeQueryBinding(ProposedTreeGenerationBinding):
    """Read-only binding shared by every proposed-tree graph operation."""

    review_collection_target: str = Field(min_length=1)
    review_generation_manifest_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$",
    )
    focus_paths: List[str] = Field(min_length=1, max_length=100)

    @field_validator("focus_paths")
    @classmethod
    def validate_focus_paths(cls, value: List[str]) -> List[str]:
        return list(dict.fromkeys(
            _validate_repository_relative_path(path) for path in value
        ))


class ReviewContextRequest(ProposedTreeQueryBinding):
    """Build/query one exact proposed-tree graph for an active PR review.

    Repository and overlay paths are injected by the review host. The model-facing
    MCP tool only supplies the review question and optional symbol focus.
    """

    question: str = Field(min_length=1, max_length=4000)
    focus_symbols: List[str] = Field(default_factory=list, max_length=50)
    max_relations: int = Field(default=32, ge=1, le=200)
    max_source_windows: int = Field(default=6, ge=1, le=20)
    max_source_characters: int = Field(default=12000, ge=1000, le=60000)

    @field_validator("focus_symbols")
    @classmethod
    def validate_focus_symbols(cls, value: List[str]) -> List[str]:
        return list(dict.fromkeys(
            symbol.strip()
            for symbol in value
            if isinstance(symbol, str) and symbol.strip()
        ))


class ReviewMinimalContextRequest(ProposedTreeQueryBinding):
    """Ultra-compact starting topology for one proposed-tree review task."""

    question: str = Field(min_length=1, max_length=4000)
    focus_symbols: List[str] = Field(default_factory=list, max_length=50)
    max_relations: int = Field(default=25, ge=1, le=200)
    detail_level: Literal["minimal", "standard"] = "minimal"
    include_source: bool = True
    max_source_windows: int = Field(default=4, ge=1, le=20)
    max_source_characters: int = Field(default=8000, ge=1000, le=60000)

    @field_validator("focus_symbols")
    @classmethod
    def validate_focus_symbols(cls, value: List[str]) -> List[str]:
        return list(dict.fromkeys(
            symbol.strip()
            for symbol in value
            if isinstance(symbol, str) and symbol.strip()
        ))


class ReviewImpactRadiusRequest(ProposedTreeQueryBinding):
    """Weighted best-score impact traversal from changed or precise targets."""

    targets: List[str] = Field(default_factory=list, max_length=100)
    max_depth: int = Field(default=2, ge=0, le=6)
    max_results: int = Field(default=100, ge=1, le=500)
    detail_level: Literal["minimal", "standard"] = "standard"
    include_source: bool = True
    max_source_windows: int = Field(default=6, ge=1, le=20)
    max_source_characters: int = Field(default=12000, ge=1000, le=60000)

    @field_validator("targets")
    @classmethod
    def validate_targets(cls, value: List[str]) -> List[str]:
        return list(dict.fromkeys(
            target.strip()
            for target in value
            if isinstance(target, str) and target.strip()
        ))


class ReviewTraverseRequest(ProposedTreeQueryBinding):
    """Free-form BFS/DFS over exact proposed-tree AST and plugin relations."""

    start: str = Field(min_length=1, max_length=1000)
    strategy: Literal["bfs", "dfs"] = "bfs"
    direction: Literal["incoming", "outgoing", "both"] = "both"
    relation_kinds: List[str] = Field(default_factory=list, max_length=50)
    max_depth: int = Field(default=3, ge=0, le=6)
    max_results: int = Field(default=100, ge=1, le=500)
    token_budget: int = Field(default=2000, ge=512, le=16000)
    detail_level: Literal["minimal", "standard"] = "standard"
    include_source: bool = True
    max_source_windows: int = Field(default=6, ge=1, le=20)
    max_source_characters: int = Field(default=12000, ge=1000, le=60000)

    @field_validator("relation_kinds")
    @classmethod
    def validate_relation_kinds(cls, value: List[str]) -> List[str]:
        if any(
            isinstance(kind, str) and len(kind.strip()) > 128
            for kind in value
        ):
            raise ValueError("relation kind must be at most 128 characters")
        return list(dict.fromkeys(
            kind.strip()
            for kind in value
            if isinstance(kind, str) and kind.strip()
        ))


class ReviewGraphQueryRequest(ProposedTreeQueryBinding):
    """One exact proposed-tree query with optional bounded source windows."""

    pattern: str = Field(min_length=1, max_length=100)
    target: str = Field(min_length=1, max_length=1000)
    max_results: int = Field(default=25, ge=1, le=100)
    cursor: int = Field(default=0, ge=0)
    detail_level: Literal["minimal", "standard"] = "standard"
    include_source: bool = True
    max_source_windows: int = Field(default=6, ge=1, le=20)
    max_source_characters: int = Field(default=12000, ge=1000, le=60000)


class ReviewUnitRequest(ProposedTreeQueryBinding):
    """Read one exact AST/plugin unit from the proposed-tree graph."""

    unit_id: str = Field(min_length=1, max_length=200)
    offset: int = Field(default=0, ge=0)
    max_characters: int = Field(default=12000, ge=1, le=60000)


class ReviewFileRequest(ProposedTreeQueryBinding):
    """Exact source lines from the bound proposed or target tree."""

    path: str
    side: Literal["proposed", "target"] = "proposed"
    start_line: int = Field(default=1, ge=1)
    end_line: Optional[int] = Field(default=None, ge=1)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _validate_repository_relative_path(value)


class ReviewSearchRequest(ProposedTreeQueryBinding):
    """A paged literal read of the bound proposed tree."""

    query: str = Field(min_length=1, max_length=1000)
    cursor: int = Field(default=0, ge=0)
    max_results: int = Field(default=100, ge=1, le=500)


class ReviewContextResponse(BaseModel):
    """Bounded source-backed structural evidence for one PR review question."""

    status: str
    snapshot: Dict[str, Any]
    freshness: Dict[str, Any]
    changed: Dict[str, Any]
    evidence: Dict[str, Any]
    sourceWindows: List[Dict[str, Any]]
    coverage: Dict[str, Any]
    provenance: Dict[str, Any]
    omittedFollowups: List[Dict[str, Any]]


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
    symbol_names: List[str] = []
    parent_class: Optional[str] = None
    namespace: Optional[str] = None
    calls: List[str] = []
    success: bool = True
    error: Optional[str] = None


# ── Repository index inspection models ──

class RepositoryIndexFilters(BaseModel):
    """Bounded filters for structural repository-index inspection.

    These are internal service-to-service filters. The public web app must
    resolve workspace/project access on the Java side before forwarding them.
    """
    branches: List[str] = Field(default_factory=list, max_length=20)
    languages: List[str] = Field(default_factory=list, max_length=20)
    path: Optional[str] = Field(default=None, max_length=500)
    file_query: Optional[str] = Field(default=None, max_length=500)
    text_query: Optional[str] = Field(default=None, max_length=160)


class RepositoryIndexGraphRequest(BaseModel):
    """Request a bounded graph slice from a project structural collection."""
    collection_target: str = Field(min_length=1)
    filters: RepositoryIndexFilters = Field(default_factory=RepositoryIndexFilters)
    limit: int = Field(default=160, ge=20, le=5000)
    cursor: Optional[str] = Field(default=None, max_length=256)
    scan_limit: int = Field(default=2500, ge=100, le=100000)


class RepositoryIndexNodeRequest(BaseModel):
    """Request a point detail and bounded neighborhood."""
    collection_target: str = Field(min_length=1)
    filters: RepositoryIndexFilters = Field(default_factory=RepositoryIndexFilters)
    neighbor_limit: int = Field(default=80, ge=10, le=160)
