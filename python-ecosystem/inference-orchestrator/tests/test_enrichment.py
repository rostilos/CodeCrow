"""
Unit tests for model.enrichment — FileContentDto, ParsedFileMetadataDto,
FileRelationshipDto, EnrichmentStats, PrEnrichmentDataDto.
"""
import pytest
from model.enrichment import (
    BoundPreviousFindingDto,
    FileContentDto,
    ParsedFileMetadataDto,
    ParsedRelationshipDto,
    ParsedSymbolDto,
    FileRelationshipDto,
    EnrichmentStats,
    PrEnrichmentDataDto,
    ReviewContextDto,
)
from model.enums import RelationshipType


class TestFileContentDto:

    def test_defaults(self):
        dto = FileContentDto(path="src/main.py")
        assert dto.content is None
        assert dto.sizeBytes == 0
        assert dto.skipped is False
        assert dto.skipReason is None

    def test_skipped(self):
        dto = FileContentDto(path="big.bin", skipped=True, skipReason="Too large")
        assert dto.skipped is True
        assert dto.skipReason == "Too large"


class TestParsedFileMetadataDto:

    def test_defaults(self):
        dto = ParsedFileMetadataDto(path="src/svc.py")
        assert dto.imports == []
        assert dto.extendsClasses == []
        assert dto.implementsInterfaces == []
        assert dto.semanticNames == []
        assert dto.parentClass is None

    def test_from_alias(self):
        """Fields can be set via their alias names."""
        dto = ParsedFileMetadataDto(
            path="A.java",
            language="java",
            **{"extends": ["Base"], "implements": ["Serializable"],
               "semantic_names": ["processOrder"], "parent_class": "Base"}
        )
        assert dto.extendsClasses == ["Base"]
        assert dto.implementsInterfaces == ["Serializable"]
        assert dto.semanticNames == ["processOrder"]
        assert dto.parentClass == "Base"

    def test_rich_symbols_relationships_and_receipt_survive_alias_round_trip(self):
        dto = ParsedFileMetadataDto.model_validate({
            "path": "src/Child.py",
            "language": "python",
            "imports": [],
            "extends": ["Base"],
            "implements": [],
            "semantic_names": ["Child"],
            "parent_class": None,
            "namespace": "example",
            "calls": [],
            "content_digest": "a" * 64,
            "parser_version": "tree-sitter-v1",
            "ast_supported": True,
            "symbols": [{
                "symbol_id": "symbol-1",
                "path": "src/Child.py",
                "name": "Child",
                "qualified_name": "example.Child",
                "kind": "class_definition",
                "start_line": 2,
                "end_line": 8,
                "parameters": [],
                "modifiers": [],
                "decorators": [],
                "extraction_method": "ast",
            }],
            "relationships": [{
                "relationship_id": "edge-1",
                "source_symbol_id": "symbol-1",
                "source_name": "example.Child",
                "target_name": "example.Base",
                "relationship_type": "extends",
                "source_line": 2,
                "target_symbol_id": "symbol-2",
                "target_path": "src/Base.py",
                "resolution": "resolved",
                "confidence": 1.0,
            }],
            "degraded_reason": None,
            "error": None,
        })

        assert dto.astSupported is True
        assert dto.symbols[0].startLine == 2
        assert dto.relationships[0].targetPath == "src/Base.py"
        serialized = dto.model_dump(mode="json", by_alias=True)
        assert serialized["content_digest"] == "a" * 64
        assert serialized["symbols"][0]["qualified_name"] == "example.Child"
        assert serialized["relationships"][0]["resolution"] == "resolved"


class TestFileRelationshipDto:

    def test_basic(self):
        dto = FileRelationshipDto(
            sourceFile="a.py",
            targetFile="b.py",
            relationshipType=RelationshipType.IMPORTS,
        )
        assert dto.strength == 0
        assert dto.matchedOn is None

    def test_with_metadata(self):
        dto = FileRelationshipDto(
            sourceFile="a.py",
            targetFile="b.py",
            relationshipType=RelationshipType.EXTENDS,
            matchedOn="BaseService",
            strength=5,
        )
        assert dto.matchedOn == "BaseService"
        assert dto.strength == 5


class TestEnrichmentStats:

    def test_defaults(self):
        stats = EnrichmentStats()
        assert stats.totalFilesRequested == 0
        assert stats.skipReasons == {}

    def test_populated(self):
        stats = EnrichmentStats(
            totalFilesRequested=10,
            filesEnriched=8,
            filesSkipped=2,
            relationshipsFound=5,
            totalContentSizeBytes=50000,
            processingTimeMs=320,
            skipReasons={"too_large": 2},
        )
        assert stats.filesSkipped == 2


class TestPrEnrichmentDataDto:

    def test_empty(self):
        dto = PrEnrichmentDataDto()
        assert dto.has_data() is False

    def test_has_data_with_contents(self):
        dto = PrEnrichmentDataDto(
            fileContents=[FileContentDto(path="x.py")],
        )
        assert dto.has_data() is True

    def test_has_data_with_relationships(self):
        dto = PrEnrichmentDataDto(
            relationships=[
                FileRelationshipDto(
                    sourceFile="a.py", targetFile="b.py",
                    relationshipType=RelationshipType.CALLS,
                )
            ],
        )
        assert dto.has_data() is True

    def test_bound_previous_findings_are_strict_and_serialized_in_review_context(self):
        dto = PrEnrichmentDataDto.model_validate({
            "reviewContext": {
                "schemaVersion": 1,
                "prTitle": "Keep authorization findings visible",
                "prDescription": None,
                "prAuthor": None,
                "taskContext": {},
                "taskHistoryContext": "",
                "projectRules": "[]",
                "sourceBranchName": "feature/auth",
                "targetBranchName": "main",
                "previousFindings": [{
                    "id": "issue-42",
                    "type": "security",
                    "severity": "HIGH",
                    "title": "Authorization bypass",
                    "reason": "The endpoint does not verify ownership.",
                    "suggestedFixDescription": "Verify the current owner.",
                    "suggestedFixDiff": None,
                    "file": "src/auth.py",
                    "line": 42,
                    "branch": "feature/auth",
                    "pullRequestId": "12",
                    "status": "open",
                    "category": "SECURITY",
                    "prVersion": 2,
                    "resolvedDescription": None,
                    "resolvedByCommit": None,
                    "resolvedInAnalysisId": None,
                    "codeSnippet": "return resource",
                }],
            }
        })

        assert isinstance(
            dto.reviewContext.previousFindings[0], BoundPreviousFindingDto
        )
        serialized = dto.model_dump(mode="json", by_alias=True)
        assert serialized["reviewContext"]["previousFindings"][0]["id"] == "issue-42"

        invalid = serialized["reviewContext"]["previousFindings"][0] | {
            "unboundField": "must fail"
        }
        with pytest.raises(ValueError, match="unboundField"):
            BoundPreviousFindingDto.model_validate(invalid)
        with pytest.raises(ValueError, match="line"):
            BoundPreviousFindingDto.model_validate({"line": "42"})

    def test_missing_previous_findings_preserves_legacy_review_context_bytes(self):
        dto = PrEnrichmentDataDto.model_validate({
            "reviewContext": {
                "schemaVersion": 1,
                "prTitle": None,
                "prDescription": None,
                "prAuthor": None,
                "taskContext": {},
                "taskHistoryContext": "",
                "projectRules": "[]",
                "sourceBranchName": "feature/legacy",
                "targetBranchName": "main",
            }
        })

        assert dto.reviewContext.previousFindings == []
        serialized = dto.model_dump(mode="json", by_alias=True)
        assert "previousFindings" not in serialized["reviewContext"]

    def test_current_review_context_requires_and_serializes_review_approach(self):
        context = ReviewContextDto.model_validate({
            "schemaVersion": 2,
            "prTitle": None,
            "prDescription": None,
            "prAuthor": None,
            "taskContext": {},
            "taskHistoryContext": "",
            "projectRules": "[]",
            "sourceBranchName": "feature/agentic",
            "targetBranchName": "main",
            "reviewApproach": "AGENTIC",
        })

        assert context.reviewApproach == "AGENTIC"
        assert context.model_dump(mode="json")["reviewApproach"] == "AGENTIC"
        with pytest.raises(ValueError, match="reviewApproach"):
            ReviewContextDto.model_validate({
                **context.model_dump(mode="json"),
                "reviewApproach": None,
            })

    def test_legacy_review_context_rejects_an_unbound_review_approach(self):
        with pytest.raises(ValueError, match="schemaVersion 1"):
            ReviewContextDto.model_validate({
                "schemaVersion": 1,
                "taskHistoryContext": "",
                "projectRules": "[]",
                "sourceBranchName": "feature/legacy",
                "targetBranchName": "main",
                "reviewApproach": "CLASSIC",
            })
