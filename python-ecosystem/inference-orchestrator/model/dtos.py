from typing import Optional, Any, List
from pydantic import BaseModel, Field, AliasChoices
from datetime import datetime

from model.enrichment import PrEnrichmentDataDto


class IssueDTO(BaseModel):
    """
    Maps to Java's AiRequestPreviousIssueDTO.
    Fields match exactly what Java sends for previousCodeAnalysisIssues.
    """
    id: Optional[str] = None
    type: Optional[str] = None  # security|quality|performance|style
    category: Optional[str] = None  # SECURITY|PERFORMANCE|CODE_QUALITY|BUG_RISK|STYLE|DOCUMENTATION|BEST_PRACTICES|ERROR_HANDLING|TESTING|ARCHITECTURE
    severity: Optional[str] = None  # HIGH|MEDIUM|LOW|INFO
    reason: Optional[str] = None  # Issue description/title (from Java)
    suggestedFixDescription: Optional[str] = None  # Suggested fix text (from Java)
    suggestedFixDiff: Optional[str] = None  # Diff for suggested fix (from Java)
    file: Optional[str] = None
    line: Optional[int] = None
    branch: Optional[str] = None
    pullRequestId: Optional[str] = None
    status: Optional[str] = None  # open|resolved|ignored
    # Resolution tracking fields (for full PR issue history)
    prVersion: Optional[int] = None  # Which PR iteration this issue was found in
    resolvedDescription: Optional[str] = None  # How the issue was resolved
    resolvedByCommit: Optional[str] = None  # Commit hash that resolved the issue
    resolvedInPrVersion: Optional[int] = None  # PR version where this was resolved
    # Legacy fields for backwards compatibility
    title: Optional[str] = None  # Legacy - use reason instead
    description: Optional[str] = None  # Legacy - use suggestedFixDescription instead
    column: Optional[int] = None
    rule: Optional[str] = None
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
    vcsProvider: Optional[str] = Field(default=None, description="VCS provider type for MCP server selection (github, bitbucket_cloud, gitlab)")
    # Incremental analysis fields
    analysisMode: Optional[str] = Field(default="FULL", description="Analysis mode: FULL or INCREMENTAL")
    deltaDiff: Optional[str] = Field(default=None, description="Delta diff between previous and current commit (only for INCREMENTAL mode)")
    previousCommitHash: Optional[str] = Field(default=None, description="Previously analyzed commit hash")
    currentCommitHash: Optional[str] = Field(default=None, description="Current commit hash being analyzed")
    # File enrichment data (full file contents + pre-computed dependency graph)
    enrichmentData: Optional[PrEnrichmentDataDto] = Field(default=None, description="Pre-computed file contents and dependency relationships from Java")
    # MCP tools for enhanced context in Stage 1 and issue verification in Stage 3
    useMcpTools: Optional[bool] = Field(default=False, description="Enable LLM to call VCS tools for context gaps and issue verification")


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
