from typing import Optional, Any, List, Dict
from pydantic import BaseModel, Field, AliasChoices
from datetime import datetime

from model.enrichment import PrEnrichmentDataDto
from model.plugins import ProjectCapabilitiesDto


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
    # Title field used for content-based tracking (fingerprint + display)
    title: Optional[str] = None
    description: Optional[str] = None  # Legacy - use suggestedFixDescription instead
    column: Optional[int] = None
    rule: Optional[str] = None
    createdAt: Optional[datetime] = None
    resolvedAt: Optional[datetime] = None
    resolvedBy: Optional[str] = None
    aiProvider: Optional[str] = None  # OPENAI|ANTHROPIC|OPENROUTER
    confidence: Optional[float] = None
    # Content-based line anchoring — verbatim source line, Java persists it and
    # passes it back so Python reconciliation can carry it forward
    codeSnippet: Optional[str] = None


class ReviewRequestDto(BaseModel):
    projectId: int
    projectVcsWorkspace: str
    projectVcsRepoSlug: str
    projectWorkspace: str
    projectNamespace: str
    aiProvider: str
    aiModel: str
    aiApiKey: str
    aiBaseUrl: Optional[str] = None
    aiCustomParameters: Optional[Dict[str, Any]] = Field(
        default_factory=dict,
        validation_alias=AliasChoices(
            "aiCustomParameters",
            "aiExtraParameters",
            "aiModelParameters",
            "aiParams",
        ),
        description=(
            "Optional provider-specific parameters for OpenAI-compatible endpoints. "
            "Supports direct request parameters plus nested model_kwargs, extra_body, "
            "and default_headers maps."
        ),
    )
    targetBranchName: Optional[str] = Field(default=None, alias="branch", validation_alias=AliasChoices("targetBranchName", "branch"))
    pullRequestId: Optional[int] = None
    commitHash: Optional[str] = None
    oAuthClient: Optional[str] = None
    oAuthSecret: Optional[str] = None
    accessToken: Optional[str] = Field(default=None, description="Bearer token for APP connections (used instead of oAuthClient/oAuthSecret)")
    mcpServerJar: Optional[str] = None
    analysisType: Optional[str] = None
    prTitle: Optional[str] = Field(default=None, description="Pull request title")
    prDescription: Optional[str] = Field(default=None, description="Pull request description")
    taskContext: Optional[Dict[str, Any]] = Field(
        default=None,
        validation_alias=AliasChoices("taskContext", "task_context"),
        description="Optional task-management context (for example Jira issue details) for PR-wide review analysis",
    )
    taskHistoryContext: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("taskHistoryContext", "task_history_context"),
        description="Bounded server-side context from prior PRs associated with the same task key",
    )
    prAuthor: Optional[str] = Field(default=None, description="PR author username")
    sourceBranchName: Optional[str] = Field(default=None, description="Source branch name of the PR")
    changedFiles: Optional[List[str]] = Field(default_factory=list, description="List of changed file paths from diff")
    deletedFiles: Optional[List[str]] = Field(default_factory=list, description="Files deleted in this PR and excluded from review context")
    rawDiff: Optional[str] = Field(default=None, description="Full raw diff content from PR for direct analysis without MCP tool call")
    maxAllowedTokens: Optional[int] = Field(
        default=None,
        description=(
            "Optional model-context hint used to split complete review units; "
            "generated output is bounded separately by the stage inference policy."
        ),
    )
    previousCodeAnalysisIssues: Optional[List[IssueDTO]] = Field(default_factory=list,
                                                                 description="List of issues from the previous CodeAnalysis version, if available.")
    vcsProvider: Optional[str] = Field(default=None, description="VCS provider type for MCP server selection (github, bitbucket_cloud, gitlab)")
    vcsBaseUrl: Optional[str] = Field(default=None, description="GitLab instance root for MCP API calls")
    # Incremental analysis fields
    analysisMode: Optional[str] = Field(default="FULL", description="Analysis mode: FULL or INCREMENTAL")
    deltaDiff: Optional[str] = Field(default=None, description="Delta diff between previous and current commit (only for INCREMENTAL mode)")
    previousCommitHash: Optional[str] = Field(default=None, description="Previously analyzed commit hash")
    currentCommitHash: Optional[str] = Field(default=None, description="Current commit hash being analyzed")
    targetHeadCommitHash: Optional[str] = Field(
        default=None,
        description=(
            "Immutable target-branch head commit captured with pull-request metadata"
        ),
    )
    baseCommitHash: Optional[str] = Field(
        default=None,
        description="Immutable pull-request merge-base commit hash",
    )
    ragCollectionTarget: Optional[str] = Field(
        default=None,
        exclude=True,
        description="Internal opaque collection target for the selected branch generation",
    )
    ragBaseGenerationManifestSha256: Optional[str] = Field(
        default=None,
        exclude=True,
        description="Internal sealed target-generation receipt returned by RAG indexing",
    )
    ragBasePluginFingerprint: Optional[str] = Field(
        default=None,
        exclude=True,
        description="Internal plugin selection identity of the sealed target generation",
    )
    ragBasePluginDescriptorFingerprint: Optional[str] = Field(
        default=None,
        exclude=True,
        description="Internal plugin descriptor identity of the sealed target generation",
    )
    ragBasePluginImplementationFingerprint: Optional[str] = Field(
        default=None,
        exclude=True,
        description="Internal plugin implementation identity of the sealed target generation",
    )
    ragBaseIndexRepresentationFingerprint: Optional[str] = Field(
        default=None,
        exclude=True,
        description="Internal index representation identity of the sealed target generation",
    )
    ragReviewGenerationStatus: Optional[str] = Field(
        default=None,
        exclude=True,
        description="Internal request-scoped proposed-tree preparation state",
    )
    ragReviewCollectionTarget: Optional[str] = Field(
        default=None,
        exclude=True,
        description="Internal opaque collection target for the sealed review generation",
    )
    ragReviewGenerationManifestSha256: Optional[str] = Field(
        default=None,
        exclude=True,
        description="Internal sealed proposed-tree generation receipt",
    )
    ragReviewGenerationError: Optional[str] = Field(
        default=None,
        exclude=True,
        description="Internal observable reason proposed-tree preparation was unavailable",
    )
    # File enrichment data (full file contents + pre-computed dependency graph)
    enrichmentData: Optional[PrEnrichmentDataDto] = Field(default=None, description="Pre-computed file contents and dependency relationships from Java")
    projectCapabilities: Optional[ProjectCapabilitiesDto] = Field(
        default=None,
        description="Deterministically selected capabilities for the pinned repository snapshot",
    )
    promptDryRun: bool = Field(
        default=False,
        description=(
            "Testing-only request marker: execute normal context assembly with a "
            "capturing model and persist prompts instead of calling an LLM."
        ),
    )
    promptDryRunId: Optional[str] = Field(
        default=None,
        description="Opaque job identifier used only to name a prompt dry-run artifact.",
    )
    # Repository/structural MCP tools in Stage 1 and source tools in Stage 3.
    useMcpTools: Optional[bool] = Field(
        default=True,
        description=(
            "Enable agentic repository and structural tools for Stage 1 context gaps "
            "and exact-source tools for issue verification"
        ),
    )
    mcpLocalOnly: bool = Field(
        default=False,
        description=(
            "Restrict MCP to request-staged repository and structural sources. "
            "Provider tools, credentials, and provider fallbacks are disabled."
        ),
    )
    requireStructuralMcp: bool = Field(
        default=False,
        description=(
            "Require the exact proposed-tree graph and the complete Stage 1 "
            "structural MCP workflow. When enabled, the review fails before "
            "source-only model fallback if graph preparation or graph tools are "
            "unavailable. Intended for controlled Graph-RAG evaluations."
        ),
    )
    localRepoPath: Optional[str] = Field(
        default=None,
        description=(
            "Ephemeral target-branch snapshot path shared with the VCS MCP server"
        ),
    )
    localRepoTargetBranch: Optional[str] = Field(
        default=None,
        description="Target branch represented by localRepoPath",
    )
    localRepoRevision: Optional[str] = Field(
        default=None,
        description="Immutable target-head revision represented by localRepoPath",
    )
    localRagRepoPath: Optional[str] = Field(
        default=None,
        description=(
            "Ephemeral target-head snapshot selected identically to the sealed "
            "structural base generation"
        ),
    )
    localReviewOverlayPath: Optional[str] = Field(
        default=None,
        description=(
            "Ephemeral request-scoped proposed-tree overlay for PR-modified files"
        ),
    )
    ragEnabled: bool = Field(
        default=True,
        description=(
            "Whether this project review may use the structural repository index. "
            "False disables structural context for this request even when the "
            "service is globally enabled."
        ),
    )
    # Custom project review rules (JSON array of enabled rules from ProjectRulesConfig)
    projectRules: Optional[str] = Field(default=None, description="JSON array of enabled custom project review rules")
    # Pre-fetched file contents for MCP-free branch reconciliation (filePath → content)
    reconciliationFileContents: Optional[Dict[str, str]] = Field(default=None, description="Pre-fetched file contents for MCP-free reconciliation. Map of filePath to full file content.")

    def get_rag_branch(self) -> Optional[str]:
        # Structural context is always bound to the immutable target head. The
        # PR diff and changed-file source remain direct review evidence.
        return self.targetBranchName

    def get_rag_base_branch(self) -> Optional[str]:
        if self.pullRequestId:
            return self.targetBranchName
        return None

    def get_target_head_commit_hash(self) -> Optional[str]:
        """Return the pinned target head, with legacy base-field fallback."""
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
