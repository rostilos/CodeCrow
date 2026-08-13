"""
Multi-Stage Review Models.

These models are used for the multi-stage PR review process:
- Stage 0: Planning (ReviewPlan)
- Stage 1: File-by-file review (FileReviewOutput, FileReviewBatchOutput)
- Stage 2: Cross-file analysis (CrossFileAnalysisResult)
"""

from typing import Optional, List
from pydantic import BaseModel, Field, field_validator, model_validator

from model.output_schemas import CodeReviewIssue


class FileReviewOutput(BaseModel):
    """Stage 1 Output: Single file review result."""
    file: str
    analysis_summary: str
    issues: List[CodeReviewIssue] = Field(
        default_factory=list,
        description=(
            "Fresh current actionable defects only; omit historical issues, "
            "successful fixes, praise, INFO notes, and speculative advice"
        ),
    )
    confidence: str = Field(description="Confidence level (HIGH/MEDIUM/LOW)")
    note: str = Field(default="", description="Optional analysis note")


class ReviewContextRequest(BaseModel):
    """One falsifiable evidence request emitted by Stage 1 discovery."""

    requestId: str = Field(
        min_length=1,
        max_length=80,
        description="Batch-unique stable identifier, such as ctx-1.",
    )
    kind: str = Field(
        default="LOCAL_EXACT",
        description=(
            "LOCAL_EXACT for one exact source lookup or CROSS_FILE for a "
            "repository-interaction investigation handled by the next stage."
        ),
    )
    question: str = Field(
        min_length=8,
        max_length=500,
        description="A concrete question whose answer can confirm or reject a claim.",
    )
    targetPath: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Repository-relative path when it is visible in supplied evidence.",
    )
    targetSymbol: Optional[str] = Field(
        default=None,
        max_length=300,
        description="Exact symbol to locate when a path or range is not yet known.",
    )
    relationship: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Specific caller/callee/import/contract relationship under test.",
    )
    requiredEvidence: str = Field(
        min_length=8,
        max_length=500,
        description="The exact source fact that would confirm or refute the claim.",
    )
    startLine: Optional[int] = Field(default=None, ge=1)
    endLine: Optional[int] = Field(default=None, ge=1)
    relatedIssueIndexes: List[int] = Field(
        default_factory=list,
        max_length=20,
        description=(
            "Zero-based indexes in the flattened provisional issue list that "
            "depend on this request."
        ),
    )
    originatingPaths: List[str] = Field(
        default_factory=list,
        max_length=20,
        exclude=True,
        description=(
            "Host-bound changed paths for an admitted CROSS_FILE request. "
            "Model-supplied values are ignored."
        ),
    )

    @field_validator("kind", mode="before")
    @classmethod
    def normalize_kind(cls, value) -> str:
        normalized = str(value or "LOCAL_EXACT").strip().upper()
        return normalized if normalized in {"LOCAL_EXACT", "CROSS_FILE"} else "LOCAL_EXACT"

    @field_validator("requestId", "question", "requiredEvidence", mode="before")
    @classmethod
    def strip_required_text(cls, value) -> str:
        return str(value or "").strip()

    @field_validator("targetPath", "targetSymbol", "relationship", mode="before")
    @classmethod
    def strip_optional_text(cls, value):
        normalized = str(value or "").strip()
        return normalized or None

    @field_validator("relatedIssueIndexes", mode="after")
    @classmethod
    def normalize_issue_indexes(cls, value: List[int]) -> List[int]:
        return sorted({index for index in value if index >= 0})

    @field_validator("originatingPaths", mode="after")
    @classmethod
    def normalize_originating_paths(cls, value: List[str]) -> List[str]:
        return list(dict.fromkeys(
            str(path).strip()
            for path in value
            if str(path).strip()
        ))

    @model_validator(mode="after")
    def require_navigable_target(self):
        if not (self.targetPath or self.targetSymbol or self.relationship):
            raise ValueError(
                "context request requires targetPath, targetSymbol, or relationship"
            )
        if (
            self.startLine is not None
            and self.endLine is not None
            and self.endLine < self.startLine
        ):
            raise ValueError("context request endLine cannot precede startLine")
        return self


class FileReviewBatchOutput(BaseModel):
    """Stage 1 Output: Batch of file reviews."""
    reviews: List[FileReviewOutput] = Field(description="List of review results for the files in the batch")
    contextRequests: List[ReviewContextRequest] = Field(
        default_factory=list,
        max_length=4,
        description=(
            "At most four falsifiable evidence requests. Do not request generic "
            "context and do not repeat evidence already present in the prompt."
        ),
    )

    @model_validator(mode="after")
    def require_unique_context_request_ids(self):
        request_ids = [item.requestId for item in self.contextRequests]
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("context request IDs must be unique within a batch")
        issue_count = sum(len(review.issues) for review in self.reviews)
        for request in self.contextRequests:
            if not request.relatedIssueIndexes:
                raise ValueError(
                    "every context request must identify at least one provisional issue"
                )
            if any(index >= issue_count for index in request.relatedIssueIndexes):
                raise ValueError(
                    "context request references an out-of-range provisional issue"
                )
        return self


class ReviewFile(BaseModel):
    """File details for review planning."""
    path: str
    focus_areas: List[str] = Field(default_factory=list, description="Specific areas to focus on (SECURITY, ARCHITECTURE, etc.)")
    risk_level: str = Field(default="MEDIUM", description="CRITICAL, HIGH, MEDIUM, or LOW")


class FileGroup(BaseModel):
    """Group of files to be reviewed together."""
    group_id: str
    priority: str = Field(description="CRITICAL, HIGH, MEDIUM, LOW")
    rationale: str
    files: List[ReviewFile]


class FileToSkip(BaseModel):
    """File skipped from deep review."""
    path: str
    reason: str


class ReviewPlan(BaseModel):
    """Stage 0 Output: Plan for the review scanning."""
    analysis_summary: str
    file_groups: List[FileGroup]
    files_to_skip: List[FileToSkip] = Field(default_factory=list)
    cross_file_concerns: List[str] = Field(default_factory=list, description="Hypotheses to verify in Stage 2")


class CrossFileIssue(BaseModel):
    """Concrete actionable defect that remains across changed files (Stage 2)."""
    id: str
    severity: str
    category: str
    title: str
    primary_file: str = Field(default="", description="The single most relevant file where this issue should be annotated")
    line: Optional[int] = Field(default=None, description="Line number in primary_file where the issue is most evident")
    codeSnippet: Optional[str] = Field(default=None, description="Verbatim code line from primary_file that anchors this issue")
    affected_files: List[str]
    description: str = Field(description="Concrete defect that remains in the post-change code; never a successful fix, praise, or optional standardization")
    evidence: str = Field(description="Visible post-change evidence proving the current harmful interaction")
    evidenceRefs: List[str] = Field(
        default_factory=list,
        description="Stable Evidence ID values copied from retrieved context used by this issue",
    )
    claimKind: str = Field(
        default="",
        description=(
            "Exact analysis-plugin evidence class named in the prompt; "
            "requires matching evidenceRefs and plugin approval when non-empty. "
            "Structural relationship presence alone is not defect proof."
        ),
    )
    findingScope: str = Field(
        default="CONCRETE_DEFECT",
        description=(
            "CONCRETE_DEFECT, DUPLICATION, or TASK_COVERAGE_GAP. "
            "TASK_COVERAGE_GAP is publication-gated against the complete PR "
            "state and cannot be inferred from an incremental delta or a RAG miss."
        ),
    )
    coverageEvidenceRefs: List[str] = Field(
        default_factory=list,
        description=(
            "PRF### or DELTA### references copied from the bounded PR evidence "
            "ledger when findingScope is TASK_COVERAGE_GAP."
        ),
    )
    coverageRegression: bool = Field(
        default=False,
        description=(
            "True only when the current incremental delta visibly removes task "
            "behavior; it requires a cited DELTA### excerpt containing removal evidence."
        ),
    )
    business_impact: str = Field(description="Concrete behavior or operation that is currently broken")
    suggestion: str = Field(description="Code change still required; never work already present in the diff")


class CrossFileAnalysisResult(BaseModel):
    """Stage 2 Output: Cross-file architectural analysis."""
    pr_risk_level: str
    cross_file_issues: List[CrossFileIssue]
    pr_recommendation: str
    confidence: str
