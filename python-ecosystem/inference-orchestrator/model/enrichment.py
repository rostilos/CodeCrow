from typing import Optional, Dict, List
from pydantic import BaseModel, Field

from model.enums import RelationshipType


class FileContentDto(BaseModel):
    """DTO representing the content of a single file retrieved from VCS."""
    path: str
    content: Optional[str] = None
    sizeBytes: int = 0
    skipped: bool = False
    skipReason: Optional[str] = None


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


class PrEnrichmentDataDto(BaseModel):
    """Aggregate DTO containing all file enrichment data for a PR."""
    fileContents: List[FileContentDto] = Field(default_factory=list)
    fileMetadata: List[ParsedFileMetadataDto] = Field(default_factory=list)
    relationships: List[FileRelationshipDto] = Field(default_factory=list)
    stats: Optional[EnrichmentStats] = None

    def has_data(self) -> bool:
        """Check if enrichment data is present."""
        return bool(self.fileContents) or bool(self.relationships)
