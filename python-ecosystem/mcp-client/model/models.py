from typing import Optional, Any, Dict, List
from pydantic import BaseModel, Field
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
    targetBranchName: Optional[str] = None
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
    maxAllowedTokens: Optional[int] = Field(default=None, description="Optional per-request token limit enforced by the client before calling the AI. If provided and the estimated token count exceeds this value, the request will be rejected.")
    previousCodeAnalysisIssues: Optional[List[IssueDTO]] = Field(default_factory=list,
                                                                 description="List of issues from the previous CodeAnalysis version, if available.")
    vcsProvider: Optional[str] = Field(default=None, description="VCS provider type for MCP server selection (github, bitbucket_cloud)")

class ReviewResponseDto(BaseModel):
    result: Optional[Any] = None
    error: Optional[str] = None
    exception: Optional[str] = None