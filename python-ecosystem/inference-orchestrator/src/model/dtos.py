from typing import Optional, Any, List, Dict
from pydantic import BaseModel, Field, AliasChoices



class ReviewRequestDto(BaseModel):
    """Inputs used by the single graph-guided repository review engine."""

    projectId: int
    projectVcsWorkspace: str
    projectVcsRepoSlug: str
    projectWorkspace: str
    projectNamespace: str
    aiProvider: str
    aiModel: str
    aiApiKey: str
    aiBaseUrl: Optional[str] = None
    aiCustomParameters: Optional[Dict[str, Any]] = None
    targetBranchName: Optional[str] = None
    sourceBranchName: Optional[str] = None
    pullRequestId: Optional[int] = None
    commitHash: Optional[str] = None
    currentCommitHash: Optional[str] = None
    targetHeadCommitHash: Optional[str] = None
    baseCommitHash: Optional[str] = None
    prTitle: Optional[str] = None
    prDescription: Optional[str] = None
    taskContext: Optional[Dict[str, Any]] = None
    projectRules: Optional[str] = None
    rawDiff: Optional[str] = None
    analysisMode: Optional[str] = "FULL"
    deltaDiff: Optional[str] = None
    localRepoPath: Optional[str] = None
    localRepoTargetBranch: Optional[str] = None
    localRepoRevision: Optional[str] = None
    localRagRepoPath: Optional[str] = None
    localReviewOverlayPath: Optional[str] = None
    ragCollectionTarget: Optional[str] = None
    ragBaseGenerationManifestSha256: Optional[str] = None

    def get_target_head_commit_hash(self) -> Optional[str]:
        for value in (self.targetHeadCommitHash, self.baseCommitHash):
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None


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
    aiBaseUrl: Optional[str] = None
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
    vcsBaseUrl: Optional[str] = Field(default=None, description="GitLab instance root for MCP API calls")

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
    aiBaseUrl: Optional[str] = None
    question: str
    pullRequestId: Optional[int] = None
    repositoryRevision: Optional[str] = Field(
        default=None,
        exclude=True,
        description="Exact indexed repository revision for code search",
    )
    oAuthClient: Optional[str] = None
    oAuthSecret: Optional[str] = None
    accessToken: Optional[str] = Field(default=None, description="Bearer token for APP connections")
    maxAllowedTokens: Optional[int] = None
    vcsProvider: Optional[str] = Field(default=None, description="VCS provider type (github, bitbucket_cloud)")
    vcsBaseUrl: Optional[str] = Field(default=None, description="GitLab instance root for MCP API calls")
    # Context data that can be passed from the processor
    analysisContext: Optional[str] = Field(default=None, description="Existing analysis data for context")
    issueReferences: Optional[List[str]] = Field(default_factory=list, description="Issue IDs referenced in the question")
    branch: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("branch", "targetBranch", "targetBranchName"),
        description="Repository branch used for deterministic code search",
    )
    ragCollectionTarget: Optional[str] = Field(
        default=None,
        exclude=True,
        description="Internal opaque collection target for code search",
    )
    ragGenerationManifestSha256: Optional[str] = Field(
        default=None,
        exclude=True,
        validation_alias=AliasChoices(
            "ragGenerationManifestSha256",
            "repositoryGenerationManifestSha256",
            "ragBaseGenerationManifestSha256",
        ),
        description="Internal sealed repository-generation receipt for code search",
    )


class AskResponseDto(BaseModel):
    """Response model for ask command."""
    answer: Optional[str] = None
    error: Optional[str] = None
