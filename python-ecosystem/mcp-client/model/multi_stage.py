"""
Multi-Stage Review Models.

These models are used for the multi-stage PR review process:
- Stage 0: Planning (ReviewPlan)
- Stage 1: File-by-file review (FileReviewOutput, FileReviewBatchOutput)
- Stage 2: Cross-file analysis (CrossFileAnalysisResult)
"""

from typing import Optional, List
from pydantic import BaseModel, Field

from model.output_schemas import CodeReviewIssue


class FileReviewOutput(BaseModel):
    """Stage 1 Output: Single file review result."""
    file: str
    analysis_summary: str
    issues: List[CodeReviewIssue] = Field(default_factory=list)
    confidence: str = Field(description="Confidence level (HIGH/MEDIUM/LOW)")
    note: str = Field(default="", description="Optional analysis note")


class FileReviewBatchOutput(BaseModel):
    """Stage 1 Output: Batch of file reviews."""
    reviews: List[FileReviewOutput] = Field(description="List of review results for the files in the batch")


class ReviewFile(BaseModel):
    """File details for review planning."""
    path: str
    focus_areas: List[str] = Field(default_factory=list, description="Specific areas to focus on (SECURITY, ARCHITECTURE, etc.)")
    risk_level: str = Field(description="CRITICAL, HIGH, MEDIUM, or LOW")
    estimated_issues: Optional[int] = Field(default=0)


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
    """Issue spanning multiple files (Stage 2)."""
    id: str
    severity: str
    category: str
    title: str
    affected_files: List[str]
    description: str
    evidence: str
    business_impact: str
    suggestion: str


class DataFlowConcern(BaseModel):
    """Stage 2: Data flow gap analysis."""
    flow: str
    gap: str
    files_involved: List[str]
    severity: str


class ImmutabilityCheck(BaseModel):
    """Stage 2: Immutability usage check."""
    rule: str
    check_pass: bool = Field(alias="check_pass")
    evidence: str


class DatabaseIntegrityCheck(BaseModel):
    """Stage 2: DB integrity check."""
    concerns: List[str]
    findings: List[str]


class CrossFileAnalysisResult(BaseModel):
    """Stage 2 Output: Cross-file architectural analysis."""
    pr_risk_level: str
    cross_file_issues: List[CrossFileIssue]
    data_flow_concerns: List[DataFlowConcern] = Field(default_factory=list)
    immutability_enforcement: Optional[ImmutabilityCheck] = None
    database_integrity: Optional[DatabaseIntegrityCheck] = None
    pr_recommendation: str
    confidence: str
