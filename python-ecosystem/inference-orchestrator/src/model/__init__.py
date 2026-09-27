"""
Public request, enrichment, and command-output models.

The models are split into logical modules:
- enums: IssueCategory, AnalysisMode, RelationshipType
- enrichment: File enrichment DTOs (FileContentDto, PrEnrichmentDataDto, etc.)
- dtos: Request/Response DTOs (ReviewRequestDto, SummarizeRequestDto, etc.)
- output_schemas: MCP Agent output schemas (CodeReviewOutput, CodeReviewIssue, etc.)
"""

# Enums
from model.enums import (
    IssueCategory,
    AnalysisMode,
    RelationshipType,
)

# Enrichment models
from model.enrichment import (
    FileContentDto,
    ParsedFileMetadataDto,
    FileRelationshipDto,
    EnrichmentStats,
    PrEnrichmentDataDto,
)

# DTOs
from model.dtos import (
    ReviewRequestDto,
    ReviewResponseDto,
    SummarizeRequestDto,
    SummarizeResponseDto,
    AskRequestDto,
    AskResponseDto,
)

# Output schemas
from model.output_schemas import (
    CodeReviewIssue,
    CodeReviewOutput,
    SummarizeOutput,
    AskOutput,
)

__all__ = [
    # Enums
    "IssueCategory",
    "AnalysisMode",
    "RelationshipType",
    # Enrichment
    "FileContentDto",
    "ParsedFileMetadataDto",
    "FileRelationshipDto",
    "EnrichmentStats",
    "PrEnrichmentDataDto",
    # DTOs
    "ReviewRequestDto",
    "ReviewResponseDto",
    "SummarizeRequestDto",
    "SummarizeResponseDto",
    "AskRequestDto",
    "AskResponseDto",
    # Output schemas
    "CodeReviewIssue",
    "CodeReviewOutput",
    "SummarizeOutput",
    "AskOutput",
]
