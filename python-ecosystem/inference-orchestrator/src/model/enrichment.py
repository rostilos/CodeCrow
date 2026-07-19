from typing import Optional, Dict, List, Literal
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
    model_serializer,
)

from model.enums import RelationshipType


class FileContentDto(BaseModel):
    """DTO representing the content of a single file retrieved from VCS."""
    path: str
    content: Optional[str] = None
    sizeBytes: int = 0
    skipped: bool = False
    skipReason: Optional[str] = None


class ParsedSymbolDto(BaseModel):
    """One source symbol with exact line-span and parser provenance."""
    symbolId: str = Field(alias="symbol_id")
    path: str
    name: str
    qualifiedName: str = Field(alias="qualified_name")
    kind: str
    startLine: int = Field(alias="start_line", ge=1)
    endLine: int = Field(alias="end_line", ge=1)
    parentSymbol: Optional[str] = Field(default=None, alias="parent_symbol")
    signature: Optional[str] = None
    parameters: List[str] = Field(default_factory=list)
    returnType: Optional[str] = Field(default=None, alias="return_type")
    modifiers: List[str] = Field(default_factory=list)
    decorators: List[str] = Field(default_factory=list)
    extractionMethod: str = Field(default="ast", alias="extraction_method")


class ParsedRelationshipDto(BaseModel):
    """A typed symbol edge whose resolution can be trusted or treated as a gap."""
    relationshipId: str = Field(alias="relationship_id")
    sourceSymbolId: str = Field(alias="source_symbol_id")
    sourceName: str = Field(alias="source_name")
    targetName: str = Field(alias="target_name")
    relationshipType: str = Field(alias="relationship_type")
    sourceLine: int = Field(alias="source_line", ge=1)
    targetSymbolId: Optional[str] = Field(default=None, alias="target_symbol_id")
    targetPath: Optional[str] = Field(default=None, alias="target_path")
    resolution: Literal["unresolved", "resolved", "ambiguous"] = "unresolved"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ParsedFileMetadataDto(BaseModel):
    """DTO representing parsed AST metadata for a single file."""
    path: str
    language: Optional[str] = None
    imports: List[str] = Field(default_factory=list)
    extendsClasses: List[str] = Field(default_factory=list, alias="extends")
    implementsInterfaces: List[str] = Field(default_factory=list, alias="implements")
    semanticNames: List[str] = Field(default_factory=list, alias="semantic_names")
    parentClass: Optional[str] = Field(default=None, alias="parent_class")
    namespace: Optional[str] = None
    calls: List[str] = Field(default_factory=list)
    contentDigest: Optional[str] = Field(default=None, alias="content_digest")
    parserVersion: Optional[str] = Field(default=None, alias="parser_version")
    astSupported: Optional[bool] = Field(default=None, alias="ast_supported")
    symbols: List[ParsedSymbolDto] = Field(default_factory=list)
    relationships: List[ParsedRelationshipDto] = Field(default_factory=list)
    degradedReason: Optional[str] = Field(default=None, alias="degraded_reason")
    error: Optional[str] = None


class FileRelationshipDto(BaseModel):
    """DTO representing a relationship between two files in the PR."""
    sourceFile: str
    targetFile: str
    relationshipType: RelationshipType
    matchedOn: Optional[str] = None
    strength: int = 0


class EnrichmentStats(BaseModel):
    """Statistics about the enrichment process."""
    totalFilesRequested: int = 0
    filesEnriched: int = 0
    filesSkipped: int = 0
    relationshipsFound: int = 0
    totalContentSizeBytes: int = 0
    processingTimeMs: int = 0
    skipReasons: Dict[str, int] = Field(default_factory=dict)


class BoundPreviousFindingDto(BaseModel):
    """One prior finding frozen into the manifest-bound enrichment artifact.

    Fields mirror the Java previous-issue wire record.  They remain optional to
    accept historical findings that predate newer tracking fields, while strict
    scalar types and ``extra=forbid`` prevent the prompt from receiving
    unbound or silently coerced data.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: Optional[StrictStr] = None
    type: Optional[StrictStr] = None
    severity: Optional[StrictStr] = None
    title: Optional[StrictStr] = None
    reason: Optional[StrictStr] = None
    suggestedFixDescription: Optional[StrictStr] = None
    suggestedFixDiff: Optional[StrictStr] = None
    file: Optional[StrictStr] = None
    line: Optional[StrictInt] = None
    branch: Optional[StrictStr] = None
    pullRequestId: Optional[StrictStr] = None
    status: Optional[StrictStr] = None
    category: Optional[StrictStr] = None
    prVersion: Optional[StrictInt] = None
    resolvedDescription: Optional[StrictStr] = None
    resolvedByCommit: Optional[StrictStr] = None
    resolvedInAnalysisId: Optional[StrictInt] = None
    codeSnippet: Optional[StrictStr] = None


class ReviewContextDto(BaseModel):
    """Useful PR context whose exact JSON bytes are manifest-bound."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal[1, 2]
    prTitle: Optional[StrictStr] = None
    prDescription: Optional[StrictStr] = None
    prAuthor: Optional[StrictStr] = None
    taskContext: Dict[StrictStr, StrictStr] = Field(default_factory=dict)
    taskHistoryContext: StrictStr
    projectRules: StrictStr
    sourceBranchName: StrictStr
    targetBranchName: StrictStr
    previousFindings: List[BoundPreviousFindingDto] = Field(default_factory=list)
    reviewApproach: Optional[Literal["CLASSIC", "AGENTIC"]] = None

    @field_validator("sourceBranchName", "targetBranchName")
    @classmethod
    def require_branch_label(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("bound review branch label cannot be blank")
        return value

    @model_validator(mode="after")
    def require_schema_bound_review_approach(self) -> "ReviewContextDto":
        if self.schemaVersion == 2 and self.reviewApproach is None:
            raise ValueError(
                "reviewApproach is required for reviewContext schemaVersion 2"
            )
        if self.schemaVersion == 1 and self.reviewApproach is not None:
            raise ValueError(
                "reviewContext schemaVersion 1 cannot bind reviewApproach"
            )
        return self

    @model_serializer(mode="wrap")
    def preserve_previous_context_schema_bytes(self, handler):
        serialized = handler(self)
        if "previousFindings" not in self.model_fields_set:
            serialized.pop("previousFindings", None)
        if self.schemaVersion == 1:
            serialized.pop("reviewApproach", None)
        return serialized


class PrEnrichmentDataDto(BaseModel):
    """Aggregate DTO containing all file enrichment data for a PR."""
    fileContents: List[FileContentDto] = Field(default_factory=list)
    fileMetadata: List[ParsedFileMetadataDto] = Field(default_factory=list)
    relationships: List[FileRelationshipDto] = Field(default_factory=list)
    stats: Optional[EnrichmentStats] = None
    reviewContext: Optional[ReviewContextDto] = None

    @model_serializer(mode="wrap")
    def preserve_context_free_artifact_bytes(self, handler):
        serialized = handler(self)
        if self.reviewContext is None:
            serialized.pop("reviewContext", None)
        return serialized

    def has_data(self) -> bool:
        """Check if enrichment data is present."""
        return bool(self.fileContents) or bool(self.relationships)
