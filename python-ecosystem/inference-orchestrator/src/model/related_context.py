"""Structured, provenance-bearing context supplied to review stages."""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ContextSnapshotReceiptV1(BaseModel):
    """Immutable repository and processing coordinates observed by inference."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_sha: str = Field(pattern=r"^[0-9a-fA-F]{40,64}$")
    head_sha: str = Field(pattern=r"^[0-9a-fA-F]{40,64}$")
    merge_base_sha: str = Field(pattern=r"^[0-9a-fA-F]{40,64}$")
    parser_version: str = Field(min_length=1, max_length=128)
    chunker_version: str = Field(min_length=1, max_length=128)
    embedding_version: str = Field(min_length=1, max_length=128)


class ContextAnchorV1(BaseModel):
    """Changed source location for which related context was assembled."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1)
    revision: Optional[str] = Field(
        default=None,
        pattern=r"^[0-9a-fA-F]{40,64}$",
    )
    content_digest: Optional[str] = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )


class RelatedContextItemV1(BaseModel):
    """One context item plus the reason and evidence behind its selection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    path: str = Field(min_length=1)
    revision: Optional[str] = Field(
        default=None,
        pattern=r"^[0-9a-fA-F]{40,64}$",
    )
    content_digest: Optional[str] = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    start_line: Optional[int] = Field(default=None, ge=1)
    end_line: Optional[int] = Field(default=None, ge=1)
    symbol: Optional[str] = None
    relationship_type: str = Field(min_length=1, max_length=64)
    direction: Literal[
        "local",
        "outbound_dependency",
        "inbound_dependent",
        "peer",
        "similarity",
    ]
    retrieval_method: Literal[
        "deterministic",
        "semantic",
        "duplication",
        "pr_overlay",
    ]
    score: float = Field(ge=0.0, le=1.0)
    evidence_strength: Literal[
        "exact_source",
        "structural_lead",
        "semantic_lead",
    ]
    selection_reason: str = Field(min_length=1, max_length=1000)
    snapshot_verified: bool
    content: str = Field(min_length=1)


class ContextGapV1(BaseModel):
    """An explicit limitation the reviewing model must not silently infer past."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(pattern=r"^[a-z0-9_]{1,64}$")
    detail: str = Field(min_length=1, max_length=2000)
    affected_paths: List[str] = Field(default_factory=list)


class RelatedContextPackV1(BaseModel):
    """The only structured related-code context accepted by exact reviews."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    mode: Literal["exact", "legacy"]
    execution_id: Optional[str] = None
    receipt: Optional[ContextSnapshotReceiptV1] = None
    anchors: List[ContextAnchorV1] = Field(default_factory=list)
    items: List[RelatedContextItemV1] = Field(default_factory=list)
    gaps: List[ContextGapV1] = Field(default_factory=list)
    rejected_chunk_count: int = Field(default=0, ge=0)
    truncated_chunk_count: int = Field(default=0, ge=0)
