import json
import re
from datetime import datetime
from hashlib import sha256
from hmac import compare_digest
from typing import Optional, Any, List, Dict, Literal, Tuple

from pydantic import (
    AliasChoices,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from model.enrichment import PrEnrichmentDataDto
from model.coverage import CoverageLedgerV1


_EXECUTION_IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$"
_REPOSITORY_IDENTIFIER = (
    r"^[a-z0-9][a-z0-9._-]{0,31}:"
    r"[A-Za-z0-9._-]{1,128}(?:/[A-Za-z0-9._-]{1,128})+$"
)
_EXACT_REVISION = r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"
_SHA_256 = r"^[0-9a-f]{64}$"
_VERSION = r"^[a-z0-9][a-z0-9._-]{0,63}$"
_CANONICAL_INSTANT = (
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{3}|\.[0-9]{6})?Z$"
)
_JAVA_LONG_MAX = 9_223_372_036_854_775_807
_RAW_DIFF_CONTENT_KEY = "pull-request.diff"
_PR_ENRICHMENT_CONTENT_KEY = "pr-enrichment.json"
_RAG_EXECUTION_CONFIG_CONTENT_KEY = "rag-execution-config-v1.json"


class AgenticRepositoryArchiveV1(BaseModel):
    """Immutable coordinates for an exact-head archive on ephemeral storage."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal[1]
    workspaceKey: StrictStr = Field(pattern=_SHA_256)
    snapshotSha: StrictStr = Field(pattern=_EXACT_REVISION)
    contentDigest: StrictStr = Field(pattern=_SHA_256)
    byteLength: StrictInt = Field(gt=0, le=_JAVA_LONG_MAX)


class RagExecutionConfigV1(BaseModel):
    """Manifest-bound RAG selection and processing identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal[1]
    indexVersion: StrictStr = Field(
        pattern=r"^(?:rag-disabled|rag-commit-(?:[0-9a-f]{40}|[0-9a-f]{64}))$"
    )
    parserVersion: StrictStr = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}$"
    )
    chunkerVersion: StrictStr = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}$"
    )
    embeddingVersion: StrictStr = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}$"
    )

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")


def _canonical_sha256(document: Dict[str, Any]) -> str:
    encoded = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


class InputArtifactV1(BaseModel):
    """One exact input artifact owned by an immutable execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    executionId: StrictStr = Field(pattern=_EXECUTION_IDENTIFIER)
    artifactId: StrictStr = Field(pattern=_EXECUTION_IDENTIFIER)
    contentKey: StrictStr = Field(min_length=1)
    snapshotSha: StrictStr = Field(pattern=_EXACT_REVISION)
    contentDigest: StrictStr = Field(pattern=_SHA_256)
    byteLength: StrictInt = Field(ge=0, le=_JAVA_LONG_MAX)
    kind: Literal[
        "raw-diff", "source-file", "pr-enrichment", "execution-config"
    ]
    artifactSchemaVersion: Literal["review-artifact-v1"]
    producer: StrictStr = Field(pattern=_EXECUTION_IDENTIFIER)
    producerVersion: StrictStr = Field(pattern=_VERSION)

    @field_validator("contentKey")
    @classmethod
    def require_content_key(cls, value: str) -> str:
        # Java's String.length() limit is measured in UTF-16 code units, not
        # Python Unicode code points. Keep the queue boundary byte-for-byte
        # compatible even for non-BMP source paths.
        utf16_units = len(value.encode("utf-16-le")) // 2
        if not value.strip() or "\x00" in value or utf16_units > 1024:
            raise ValueError("contentKey is invalid")
        return value


class ExecutionManifestV1(BaseModel):
    """Immutable, self-verifying coordinates for one exact PR execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: StrictInt = Field(ge=1, le=1)
    executionId: StrictStr = Field(pattern=_EXECUTION_IDENTIFIER)
    projectId: StrictInt = Field(gt=0, le=_JAVA_LONG_MAX)
    repositoryId: StrictStr = Field(pattern=_REPOSITORY_IDENTIFIER)
    pullRequestId: StrictInt = Field(gt=0, le=_JAVA_LONG_MAX)
    baseSha: StrictStr = Field(pattern=_EXACT_REVISION)
    headSha: StrictStr = Field(pattern=_EXACT_REVISION)
    mergeBaseSha: StrictStr = Field(pattern=_EXACT_REVISION)
    diffArtifactId: StrictStr = Field(pattern=_EXECUTION_IDENTIFIER)
    diffDigest: StrictStr = Field(pattern=_SHA_256)
    diffByteLength: StrictInt = Field(ge=0, le=_JAVA_LONG_MAX)
    diffArtifactKind: Literal["raw-diff"]
    diffArtifactProducer: StrictStr = Field(pattern=_EXECUTION_IDENTIFIER)
    diffArtifactProducerVersion: StrictStr = Field(pattern=_VERSION)
    artifactSchemaVersion: Literal["review-artifact-v1"]
    policyVersion: StrictStr = Field(pattern=_VERSION)
    creationFence: StrictStr = Field(pattern=_EXECUTION_IDENTIFIER)
    createdAt: StrictStr = Field(pattern=_CANONICAL_INSTANT)
    inputArtifacts: Tuple[InputArtifactV1, ...] = Field(min_length=1)
    artifactManifestDigest: StrictStr = Field(pattern=_SHA_256)

    @field_validator("createdAt", mode="before")
    @classmethod
    def require_wire_timestamp(cls, value: Any) -> Any:
        if not isinstance(value, str):
            raise ValueError("createdAt must be an ISO-8601 string")
        if re.fullmatch(_CANONICAL_INSTANT, value) is None:
            raise ValueError("createdAt must use canonical UTC Z notation")
        try:
            parsed = datetime.fromisoformat(value[:-1] + "+00:00")
        except ValueError as error:
            raise ValueError("createdAt is not a valid instant") from error
        if parsed.microsecond == 0:
            canonical = parsed.strftime("%Y-%m-%dT%H:%M:%SZ")
        elif parsed.microsecond % 1_000 == 0:
            canonical = parsed.strftime("%Y-%m-%dT%H:%M:%S.%f")[:23] + "Z"
        else:
            canonical = parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        if canonical != value:
            raise ValueError("createdAt is not canonically encoded")
        return value

    @model_validator(mode="after")
    def verify_artifact_manifest_digest(self) -> "ExecutionManifestV1":
        artifacts = self.inputArtifacts
        if tuple(sorted(artifacts, key=lambda item: item.artifactId)) != artifacts:
            raise ValueError("inputArtifacts must use canonical artifactId order")
        artifact_ids = [artifact.artifactId for artifact in artifacts]
        content_keys = [artifact.contentKey for artifact in artifacts]
        if len(set(artifact_ids)) != len(artifact_ids):
            raise ValueError("inputArtifacts contain a duplicate artifactId")
        if len(set(content_keys)) != len(content_keys):
            raise ValueError("inputArtifacts contain a duplicate contentKey")
        for artifact in artifacts:
            if artifact.executionId != self.executionId:
                raise ValueError("input artifact belongs to another execution")
            if artifact.snapshotSha != self.headSha:
                raise ValueError("input artifact belongs to another snapshot")
            if artifact.artifactSchemaVersion != self.artifactSchemaVersion:
                raise ValueError("input artifact schema conflicts with manifest")
        raw_diffs = [item for item in artifacts if item.kind == "raw-diff"]
        if len(raw_diffs) != 1:
            raise ValueError("inputArtifacts must contain exactly one raw diff")
        raw_diff = raw_diffs[0]
        if (
            raw_diff.artifactId != self.diffArtifactId
            or raw_diff.contentKey != _RAW_DIFF_CONTENT_KEY
            or raw_diff.contentDigest != self.diffDigest
            or raw_diff.byteLength != self.diffByteLength
            or raw_diff.producer != self.diffArtifactProducer
            or raw_diff.producerVersion != self.diffArtifactProducerVersion
        ):
            raise ValueError("raw diff input artifact conflicts with manifest")
        if sum(item.kind == "pr-enrichment" for item in artifacts) > 1:
            raise ValueError("inputArtifacts contain multiple enrichment documents")
        execution_configs = [
            item for item in artifacts if item.kind == "execution-config"
        ]
        if len(execution_configs) > 1:
            raise ValueError("inputArtifacts contain multiple execution config documents")
        if execution_configs and (
            execution_configs[0].contentKey != _RAG_EXECUTION_CONFIG_CONTENT_KEY
        ):
            raise ValueError("execution config input artifact has an invalid contentKey")
        coordinates = self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"artifactManifestDigest"},
        )
        expected = _canonical_sha256(coordinates)
        if not compare_digest(expected, self.artifactManifestDigest):
            raise ValueError(
                "artifactManifestDigest does not match immutable coordinates"
            )
        return self


class LegacyCompatibility(BaseModel):
    """Explicit, expiring permission to read the pre-manifest queue shape."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["legacy"]
    deadline: AwareDatetime

    @field_validator("deadline", mode="before")
    @classmethod
    def require_wire_timestamp(cls, value: Any) -> Any:
        if not isinstance(value, str):
            raise ValueError("deadline must be an ISO-8601 string")
        return value


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
    prTitle: Optional[str] = Field(default=None, description="PR title for RAG context")
    prDescription: Optional[str] = Field(default=None, description="PR description for RAG context")
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
    deletedFiles: Optional[List[str]] = Field(default_factory=list, description="Files deleted in this PR (excluded from review, used for RAG filtering)")
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
    reviewApproach: Literal["CLASSIC", "AGENTIC"] = "CLASSIC"
    agenticRepository: Optional[AgenticRepositoryArchiveV1] = Field(
        default=None,
        description=(
            "Exact-head repository archive coordinates for the execution-scoped "
            "agentic workspace"
        ),
    )
    # Custom project review rules (JSON array of enabled rules from ProjectRulesConfig)
    projectRules: Optional[str] = Field(default=None, description="JSON array of enabled custom project review rules")
    # Pre-fetched file contents for MCP-free branch reconciliation (filePath → content)
    reconciliationFileContents: Optional[Dict[str, str]] = Field(default=None, description="Pre-fetched file contents for MCP-free reconciliation. Map of filePath to full file content.")
    # P0-04/P0-06 execution context. Python records the policy selected and
    # frozen by Java; it never recomputes rollout assignment from source data.
    # P1-01 replaces the legacy revision inputs with the durable immutable
    # execution identity.
    executionManifest: Optional[ExecutionManifestV1] = None
    ragContext: Optional[RagExecutionConfigV1] = None
    coverageLedger: Optional[CoverageLedgerV1] = None
    legacyCompatibility: Optional[LegacyCompatibility] = None
    executionId: Optional[str] = None
    baseRevision: Optional[str] = None
    headRevision: Optional[str] = None
    # Prompt/rule identities are derived from the active Python templates and
    # effective projectRules at execution time. These legacy wire fields remain
    # additive/optional but cannot override observed attribution.
    promptVersion: Optional[str] = None
    rulesVersion: Optional[str] = None
    policyVersion: str = "legacy-review-v1"
    indexVersion: Optional[str] = None
    inputPricePerMillion: Optional[str] = None
    outputPricePerMillion: Optional[str] = None
    executionMode: Literal["legacy", "shadow", "active"] = "legacy"
    policySelectionReason: str = Field(
        default="legacy_configured",
        pattern=r"^[a-z0-9_]{1,64}$",
    )
    publicationAllowed: bool = True

    @model_validator(mode="after")
    def validate_execution_manifest_binding(self) -> "ReviewRequestDto":
        manifest = self.executionManifest
        if manifest is None:
            if self.coverageLedger is not None:
                raise ValueError("coverageLedger requires executionManifest")
            if self.reviewApproach == "AGENTIC" or self.agenticRepository is not None:
                raise ValueError("AGENTIC review requires executionManifest")
            if self.ragContext is not None:
                raise ValueError("ragContext requires executionManifest")
            return self
        if self.legacyCompatibility is not None:
            raise ValueError(
                "executionManifest and legacyCompatibility are mutually exclusive"
            )
        if self.projectId != manifest.projectId:
            raise ValueError("projectId conflicts with executionManifest")
        if self.pullRequestId != manifest.pullRequestId:
            raise ValueError("pullRequestId conflicts with executionManifest")
        if self.analysisType != "PR_REVIEW":
            raise ValueError(
                "analysisType conflicts with executionManifest"
            )
        if self.vcsProvider is None or not self.vcsProvider.strip():
            raise ValueError("vcsProvider is required for executionManifest")
        expected_repository_id = (
            f"{self.vcsProvider.lower()}:"
            f"{self.projectVcsWorkspace}/{self.projectVcsRepoSlug}"
        )
        if expected_repository_id != manifest.repositoryId:
            raise ValueError("repositoryId conflicts with executionManifest")

        aliases = {
            "executionId": (self.executionId, manifest.executionId),
            "baseRevision": (self.baseRevision, manifest.baseSha),
            "headRevision": (self.headRevision, manifest.headSha),
            "previousCommitHash": (self.previousCommitHash, manifest.baseSha),
            "currentCommitHash": (self.currentCommitHash, manifest.headSha),
            "commitHash": (self.commitHash, manifest.headSha),
        }
        for field, (observed, expected) in aliases.items():
            if observed is not None and observed != expected:
                raise ValueError(f"{field} conflicts with executionManifest")

        if (
            "policyVersion" in self.model_fields_set
            and self.policyVersion != manifest.policyVersion
        ):
            raise ValueError("policyVersion conflicts with executionManifest")
        if self.rawDiff is None:
            raise ValueError("rawDiff is required for executionManifest")
        if self.analysisMode != "FULL":
            raise ValueError("executionManifest requires FULL analysisMode")
        if self.deltaDiff is not None:
            raise ValueError("deltaDiff is not bound by executionManifest")
        if self.previousCodeAnalysisIssues:
            raise ValueError(
                "previousCodeAnalysisIssues are not bound by executionManifest"
            )
        if self.diffSnippets:
            raise ValueError("diffSnippets are not bound by executionManifest")
        if self.deletedFiles and self.coverageLedger is None:
            raise ValueError("deletedFiles are not bound by executionManifest")
        review_context = (
            self.enrichmentData.reviewContext
            if self.enrichmentData is not None
            else None
        )
        if review_context is None:
            if self.prTitle is not None and self.prTitle.strip():
                raise ValueError("prTitle is not bound by executionManifest")
            if self.prDescription is not None and self.prDescription.strip():
                raise ValueError("prDescription is not bound by executionManifest")
            if self.prAuthor is not None and self.prAuthor.strip():
                raise ValueError("prAuthor is not bound by executionManifest")
            if self.taskContext:
                raise ValueError("taskContext is not bound by executionManifest")
            if self.taskHistoryContext is not None and self.taskHistoryContext.strip():
                raise ValueError(
                    "taskHistoryContext is not bound by executionManifest"
                )
            if self.sourceBranchName is not None and self.sourceBranchName.strip():
                raise ValueError("sourceBranchName is not bound by executionManifest")
            if self.targetBranchName is not None and self.targetBranchName.strip():
                raise ValueError("targetBranchName is not bound by executionManifest")
            if self.projectRules is not None and self.projectRules.strip():
                raise ValueError("projectRules are not bound by executionManifest")
        else:
            bound_context_fields = {
                "prTitle": (self.prTitle, review_context.prTitle),
                "prDescription": (
                    self.prDescription,
                    review_context.prDescription,
                ),
                "prAuthor": (self.prAuthor, review_context.prAuthor),
                "taskContext": (self.taskContext or {}, review_context.taskContext),
                "taskHistoryContext": (
                    self.taskHistoryContext,
                    review_context.taskHistoryContext,
                ),
                "sourceBranchName": (
                    self.sourceBranchName,
                    review_context.sourceBranchName,
                ),
                "targetBranchName": (
                    self.targetBranchName,
                    review_context.targetBranchName,
                ),
                "projectRules": (self.projectRules, review_context.projectRules),
            }
            for field, (observed, expected) in bound_context_fields.items():
                if observed != expected:
                    raise ValueError(
                        f"{field} conflicts with bound reviewContext"
                    )
        if review_context is None or review_context.schemaVersion == 1:
            if self.reviewApproach != "CLASSIC":
                raise ValueError(
                    "AGENTIC review requires a manifest-bound reviewApproach"
                )
        elif self.reviewApproach != review_context.reviewApproach:
            raise ValueError(
                "reviewApproach conflicts with bound reviewContext"
            )
        if self.useMcpTools:
            raise ValueError("useMcpTools is not bound by executionManifest")
        if self.reviewApproach == "AGENTIC":
            if self.agenticRepository is None:
                raise ValueError("AGENTIC review requires agenticRepository")
            if self.agenticRepository.snapshotSha != manifest.headSha:
                raise ValueError("agenticRepository conflicts with manifest headSha")
        elif self.agenticRepository is not None:
            raise ValueError("CLASSIC review cannot carry agenticRepository")
        observed_diff_digest = sha256(self.rawDiff.encode("utf-8")).hexdigest()
        observed_diff_byte_length = len(self.rawDiff.encode("utf-8"))
        if observed_diff_byte_length != manifest.diffByteLength:
            raise ValueError("rawDiff byte length does not match executionManifest")
        if not compare_digest(observed_diff_digest, manifest.diffDigest):
            raise ValueError("rawDiff digest does not match executionManifest")
        ledger = self.coverageLedger
        if ledger is not None:
            if ledger.executionId != manifest.executionId:
                raise ValueError("coverageLedger executionId conflicts with executionManifest")
            if not compare_digest(
                ledger.artifactManifestDigest,
                manifest.artifactManifestDigest,
            ):
                raise ValueError(
                    "coverageLedger provenance conflicts with artifactManifestDigest"
                )
            if not compare_digest(ledger.diffDigest, manifest.diffDigest):
                raise ValueError("coverageLedger diffDigest conflicts with executionManifest")
            if ledger.diffByteLength != manifest.diffByteLength:
                raise ValueError(
                    "coverageLedger diffByteLength conflicts with executionManifest"
                )
            if any(
                anchor.sourceArtifactId != manifest.diffArtifactId
                for anchor in ledger.anchors
            ):
                raise ValueError(
                    "coverageLedger sourceArtifactId conflicts with executionManifest"
                )
            declared_deleted = list(self.deletedFiles or [])
            if len(set(declared_deleted)) != len(declared_deleted):
                raise ValueError("deletedFiles contain a duplicate path")
            ledger_deleted = {
                anchor.oldPath
                for anchor in ledger.anchors
                if anchor.changeStatus == "DELETE" and anchor.oldPath is not None
            }
            if set(declared_deleted) != ledger_deleted:
                raise ValueError(
                    "deletedFiles conflict with coverageLedger deletion inventory"
                )
        if self.reconciliationFileContents:
            raise ValueError(
                "reconciliationFileContents are not bound by executionManifest"
            )
        expected_exact_index = f"rag-commit-{manifest.baseSha}"
        if self.indexVersion not in {"rag-disabled", expected_exact_index}:
            raise ValueError(
                "executionManifest indexVersion must be disabled or match baseSha"
            )
        rag_context = self.ragContext
        if rag_context is None:
            raise ValueError("executionManifest requires manifest-bound ragContext")
        if rag_context.indexVersion != self.indexVersion:
            raise ValueError("indexVersion conflicts with manifest-bound ragContext")
        if rag_context.indexVersion not in {"rag-disabled", expected_exact_index}:
            raise ValueError("ragContext indexVersion conflicts with manifest baseSha")
        self._verify_input_artifacts(manifest)
        return self

    def _verify_input_artifacts(self, manifest: ExecutionManifestV1) -> None:
        artifacts = manifest.inputArtifacts
        raw_entry = next(item for item in artifacts if item.kind == "raw-diff")
        self._verify_artifact_bytes(
            raw_entry,
            (self.rawDiff or "").encode("utf-8"),
            "rawDiff",
        )

        config_entries = [
            item for item in artifacts if item.kind == "execution-config"
        ]
        if len(config_entries) != 1:
            raise ValueError(
                "executionManifest requires exactly one RAG execution config artifact"
            )
        if self.ragContext is None:
            raise ValueError("ragContext is required for executionManifest")
        self._verify_artifact_bytes(
            config_entries[0],
            self.ragContext.canonical_bytes(),
            "ragContext",
        )

        source_entries = {
            item.contentKey: item
            for item in artifacts
            if item.kind == "source-file"
        }
        seen_paths: set[str] = set()
        observed_source_paths: set[str] = set()
        enriched_count = 0
        skipped_count = 0
        total_content_bytes = 0
        enrichment = self.enrichmentData
        if enrichment is not None:
            for file_content in enrichment.fileContents:
                path = file_content.path
                if path in seen_paths:
                    raise ValueError("enrichmentData contains a duplicate source path")
                if not path.strip() or "\x00" in path:
                    raise ValueError("enrichmentData source path is invalid")
                seen_paths.add(path)
                if file_content.skipped:
                    if file_content.content is not None:
                        raise ValueError("skipped source cannot carry content")
                    if not file_content.skipReason or not file_content.skipReason.strip():
                        raise ValueError("skipped source requires an explicit reason")
                    skipped_count += 1
                    continue
                if file_content.content is None:
                    raise ValueError("non-skipped source must carry content")
                observed_source_paths.add(path)
                entry = source_entries.get(path)
                if entry is None:
                    raise ValueError("source content is missing from inputArtifacts")
                self._verify_generated_artifact_identity(
                    entry,
                    prefix="source",
                    manifest=manifest,
                )
                content = file_content.content.encode("utf-8")
                if file_content.sizeBytes != len(content):
                    raise ValueError("source sizeBytes is not UTF-8 exact")
                enriched_count += 1
                total_content_bytes += len(content)
                self._verify_artifact_bytes(entry, content, f"source:{path}")

            enrichment_entries = [
                item for item in artifacts if item.kind == "pr-enrichment"
            ]
            if len(enrichment_entries) != 1:
                raise ValueError("enrichmentData requires one manifest artifact")
            canonical_enrichment = json.dumps(
                enrichment.model_dump(mode="json", by_alias=True),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
            enrichment_entry = enrichment_entries[0]
            if enrichment_entry.contentKey != _PR_ENRICHMENT_CONTENT_KEY:
                raise ValueError("enrichment artifact contentKey is invalid")
            self._verify_generated_artifact_identity(
                enrichment_entry,
                prefix="enrichment",
                manifest=manifest,
            )
            self._verify_artifact_bytes(
                enrichment_entry,
                canonical_enrichment,
                "enrichmentData",
            )
            stats = enrichment.stats
            if stats is None or (
                stats.totalFilesRequested != len(seen_paths)
                or stats.filesEnriched != enriched_count
                or stats.filesSkipped != skipped_count
                or stats.totalContentSizeBytes != total_content_bytes
            ):
                raise ValueError("enrichmentData has incomplete file accounting")
        elif any(item.kind == "pr-enrichment" for item in artifacts):
            raise ValueError("manifest enrichment artifact has no request payload")

        changed_files = list(self.changedFiles or [])
        if (
            len(changed_files) != len(set(changed_files))
            or any(not path.strip() or "\x00" in path for path in changed_files)
        ):
            raise ValueError("changedFiles inventory is invalid")
        if set(changed_files) != seen_paths:
            raise ValueError("changedFiles conflict with enrichmentData inventory")
        if set(source_entries) != observed_source_paths:
            raise ValueError("inputArtifacts contain untransmitted source content")

    @staticmethod
    def _verify_generated_artifact_identity(
        artifact: InputArtifactV1,
        *,
        prefix: str,
        manifest: ExecutionManifestV1,
    ) -> None:
        identity = f"{manifest.executionId}\x00{artifact.contentKey}".encode("utf-8")
        expected_id = f"{prefix}:{sha256(identity).hexdigest()}"
        if artifact.artifactId != expected_id:
            raise ValueError(f"{prefix} artifactId is not canonical")
        if (
            artifact.producer != manifest.diffArtifactProducer
            or artifact.producerVersion != manifest.diffArtifactProducerVersion
        ):
            raise ValueError(f"{prefix} artifact producer conflicts with manifest")

    @staticmethod
    def _verify_artifact_bytes(
        artifact: InputArtifactV1,
        content: bytes,
        field: str,
    ) -> None:
        if len(content) != artifact.byteLength:
            raise ValueError(f"{field} byte length does not match input artifact")
        if not compare_digest(sha256(content).hexdigest(), artifact.contentDigest):
            raise ValueError(f"{field} digest does not match input artifact")

    def get_rag_branch(self) -> Optional[str]:
        if self.executionManifest is not None:
            return self.executionManifest.headSha
        if self.pullRequestId:
            return self.sourceBranchName or self.targetBranchName
        return self.targetBranchName

    def get_rag_base_branch(self) -> Optional[str]:
        if self.executionManifest is not None:
            return self.executionManifest.baseSha
        if self.pullRequestId:
            return self.targetBranchName
        return None


class ReviewQueueEnvelopeV2(BaseModel):
    """Strict candidate queue envelope with mandatory coverage work."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: StrictInt = Field(ge=2, le=2)
    job_id: StrictStr = Field(pattern=_EXECUTION_IDENTIFIER)
    request: ReviewRequestDto

    @model_validator(mode="after")
    def require_coverage_ledger(self) -> "ReviewQueueEnvelopeV2":
        if self.request.coverageLedger is None:
            raise ValueError("queue schemaVersion 2 requires coverageLedger")
        return self


def parse_review_queue_envelope(
    payload: Dict[str, Any],
) -> ReviewQueueEnvelopeV2:
    """Parse the sole versioned candidate shape without downgrade fallback."""

    version = payload.get("schemaVersion")
    if version == 2:
        return ReviewQueueEnvelopeV2.model_validate(payload)
    raise ValueError(f"unsupported queue schemaVersion: {version!r}")


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

    def get_rag_branch(self) -> Optional[str]:
        if self.pullRequestId:
            return self.sourceBranch or self.targetBranch
        return self.targetBranch

    def get_rag_base_branch(self) -> Optional[str]:
        if self.pullRequestId:
            return self.targetBranch
        return None


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
