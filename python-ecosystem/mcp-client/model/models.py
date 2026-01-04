from typing import Optional, Any, Dict, List
from pydantic import BaseModel, Field, AliasChoices
from datetime import datetime
from enum import Enum


class IssueCategory(str, Enum):
    """Valid issue categories for code analysis."""
    SECURITY = "SECURITY"
    PERFORMANCE = "PERFORMANCE"
    CODE_QUALITY = "CODE_QUALITY"
    BUG_RISK = "BUG_RISK"
    STYLE = "STYLE"
    DOCUMENTATION = "DOCUMENTATION"
    BEST_PRACTICES = "BEST_PRACTICES"
    ERROR_HANDLING = "ERROR_HANDLING"
    TESTING = "TESTING"
    ARCHITECTURE = "ARCHITECTURE"


class AnalysisMode(str, Enum):
    """Analysis mode for PR reviews."""
    FULL = "FULL"  # Full PR diff analysis (first review or escalation)
    INCREMENTAL = "INCREMENTAL"  # Delta diff analysis (subsequent reviews)


class IssueDTO(BaseModel):
    id: Optional[str] = None
    type: Optional[str] = None  # security|quality|performance|style
    category: Optional[str] = None  # SECURITY|PERFORMANCE|CODE_QUALITY|BUG_RISK|STYLE|DOCUMENTATION|BEST_PRACTICES|ERROR_HANDLING|TESTING|ARCHITECTURE
    severity: Optional[str] = None  # critical|high|medium|low
    title: Optional[str] = None
    description: Optional[str] = None  # Typically holds the suggestedFix
    file: Optional[str] = None
    line: Optional[int] = None
    column: Optional[int] = None
    rule: Optional[str] = None
    branch: Optional[str] = None
    pullRequestId: Optional[str] = None
    status: Optional[str] = None  # open|resolved|ignored
    createdAt: Optional[datetime] = None
    resolvedAt: Optional[datetime] = None
    resolvedBy: Optional[str] = None
    aiProvider: Optional[str] = None  # OPENAI|ANTHROPIC|OPENROUTER
    confidence: Optional[float] = None


class ReviewRequestDto(BaseModel):
    projectId: int
    projectVcsWorkspace: str
    projectVcsRepoSlug: str
    projectWorkspace: str
    projectNamespace: str
    aiProvider: str
    aiModel: str
    aiApiKey: str
    targetBranchName: Optional[str] = Field(default=None, alias="branch", validation_alias=AliasChoices("targetBranchName", "branch"))
    pullRequestId: Optional[int] = None
    commitHash: Optional[str] = None
    oAuthClient: Optional[str] = None
    oAuthSecret: Optional[str] = None
    accessToken: Optional[str] = Field(default=None, description="Bearer token for APP connections (used instead of oAuthClient/oAuthSecret)")
    mcpServerJar: Optional[str] = None
    analysisType: Optional[str] = None
    prTitle: Optional[str] = Field(default=None, description="PR title for RAG context")
    prDescription: Optional[str] = Field(default=None, description="PR description for RAG context")
    changedFiles: Optional[List[str]] = Field(default_factory=list, description="List of changed file paths from diff")
    diffSnippets: Optional[List[str]] = Field(default_factory=list, description="Code snippets from diff for RAG semantic search")
    rawDiff: Optional[str] = Field(default=None, description="Full raw diff content from PR for direct analysis without MCP tool call")
    maxAllowedTokens: Optional[int] = Field(default=None, description="Optional per-request token limit enforced by the client before calling the AI. If provided and the estimated token count exceeds this value, the request will be rejected.")
    previousCodeAnalysisIssues: Optional[List[IssueDTO]] = Field(default_factory=list,
                                                                 description="List of issues from the previous CodeAnalysis version, if available.")
    vcsProvider: Optional[str] = Field(default=None, description="VCS provider type for MCP server selection (github, bitbucket_cloud)")
    # Incremental analysis fields
    analysisMode: Optional[str] = Field(default="FULL", description="Analysis mode: FULL or INCREMENTAL")
    deltaDiff: Optional[str] = Field(default=None, description="Delta diff between previous and current commit (only for INCREMENTAL mode)")
    previousCommitHash: Optional[str] = Field(default=None, description="Previously analyzed commit hash")
    currentCommitHash: Optional[str] = Field(default=None, description="Current commit hash being analyzed")

class ReviewResponseDto(BaseModel):
    result: Optional[Any] = None
    error: Optional[str] = None
    exception: Optional[str] = None


class SummarizeRequestDto(BaseModel):
    """Request model for PR summarization command."""
    projectId: int
    projectVcsWorkspace: str
    projectVcsRepoSlug: str
    projectWorkspace: str
    projectNamespace: str
    aiProvider: str
    aiModel: str
    aiApiKey: str
    pullRequestId: int
    sourceBranch: Optional[str] = None
    targetBranch: Optional[str] = None
    commitHash: Optional[str] = None
    oAuthClient: Optional[str] = None
    oAuthSecret: Optional[str] = None
    accessToken: Optional[str] = Field(default=None, description="Bearer token for APP connections")
    supportsMermaid: bool = Field(default=True, description="Whether the VCS supports Mermaid diagrams")
    maxAllowedTokens: Optional[int] = None
    vcsProvider: Optional[str] = Field(default=None, description="VCS provider type (github, bitbucket_cloud)")


class SummarizeResponseDto(BaseModel):
    """Response model for PR summarization command."""
    summary: Optional[str] = None
    diagram: Optional[str] = None
    diagramType: Optional[str] = Field(default="MERMAID", description="MERMAID or ASCII")
    error: Optional[str] = None


class AskRequestDto(BaseModel):
    """Request model for ask command."""
    projectId: int
    projectVcsWorkspace: str
    projectVcsRepoSlug: str
    projectWorkspace: str
    projectNamespace: str
    aiProvider: str
    aiModel: str
    aiApiKey: str
    question: str
    pullRequestId: Optional[int] = None
    commitHash: Optional[str] = None
    oAuthClient: Optional[str] = None
    oAuthSecret: Optional[str] = None
    accessToken: Optional[str] = Field(default=None, description="Bearer token for APP connections")
    maxAllowedTokens: Optional[int] = None
    vcsProvider: Optional[str] = Field(default=None, description="VCS provider type (github, bitbucket_cloud)")
    # Context data that can be passed from the processor
    analysisContext: Optional[str] = Field(default=None, description="Existing analysis data for context")
    issueReferences: Optional[List[str]] = Field(default_factory=list, description="Issue IDs referenced in the question")


class AskResponseDto(BaseModel):
    """Response model for ask command."""
    answer: Optional[str] = None
    error: Optional[str] = None


# ==================== Output Schemas for MCP Agent ====================
# These Pydantic models are used with MCPAgent's output_schema parameter
# to ensure structured JSON output from the LLM.

class CodeReviewIssue(BaseModel):
    """Schema for a single code review issue."""
    # Optional issue identifier (preserve DB/client-side ids for reconciliation)
    id: Optional[str] = Field(default=None, description="Optional issue id to link to existing issues")
    severity: str = Field(description="Issue severity: HIGH, MEDIUM, or LOW")
    category: str = Field(description="Issue category: SECURITY, PERFORMANCE, CODE_QUALITY, BUG_RISK, STYLE, DOCUMENTATION, BEST_PRACTICES, ERROR_HANDLING, TESTING, or ARCHITECTURE")
    file: str = Field(description="File path where the issue is located")
    line: str = Field(description="Line number or range (e.g., '42' or '42-45')")
    reason: str = Field(description="Clear explanation of the issue")
    suggestedFixDescription: str = Field(description="Description of the suggested fix")
    suggestedFixDiff: Optional[str] = Field(default=None, description="Optional unified diff format patch for the fix")
    isResolved: bool = Field(default=False, description="Whether this issue from previous analysis is resolved")


class CodeReviewOutput(BaseModel):
    """Schema for the complete code review output."""
    comment: str = Field(description="High-level summary of the PR analysis with key findings and recommendations")
    issues: List[CodeReviewIssue] = Field(default_factory=list, description="List of identified issues in the code")


class SummarizeOutput(BaseModel):
    """Schema for PR summarization output."""
    summary: str = Field(description="Comprehensive summary of the PR changes, purpose, and impact")
    diagram: str = Field(default="", description="Visual diagram of the changes (Mermaid or ASCII format)")
    diagramType: str = Field(default="MERMAID", description="Type of diagram: MERMAID or ASCII")


class AskOutput(BaseModel):
    """Schema for ask command output."""
    answer: str = Field(description="Well-formatted markdown answer to the user's question")