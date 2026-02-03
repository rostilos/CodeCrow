"""
Model package - Re-exports all models for backward compatibility.

The models are split into logical modules:
- enums: IssueCategory, AnalysisMode, RelationshipType
- enrichment: File enrichment DTOs (FileContentDto, PrEnrichmentDataDto, etc.)
- dtos: Request/Response DTOs (ReviewRequestDto, SummarizeRequestDto, etc.)
- output_schemas: MCP Agent output schemas (CodeReviewOutput, CodeReviewIssue, etc.)
- multi_stage: Multi-stage review models (ReviewPlan, FileReviewOutput, etc.)
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
    IssueDTO,
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

# Multi-stage review models
from model.multi_stage import (
    FileReviewOutput,
    FileReviewBatchOutput,
    ReviewFile,
    FileGroup,
    FileToSkip,
    ReviewPlan,
    CrossFileIssue,
    DataFlowConcern,
    ImmutabilityCheck,
    DatabaseIntegrityCheck,
    CrossFileAnalysisResult,
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
    "IssueDTO",
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
    # Multi-stage
    "FileReviewOutput",
    "FileReviewBatchOutput",
    "ReviewFile",
    "FileGroup",
    "FileToSkip",
    "ReviewPlan",
    "CrossFileIssue",
    "DataFlowConcern",
    "ImmutabilityCheck",
    "DatabaseIntegrityCheck",
    "CrossFileAnalysisResult",
]
