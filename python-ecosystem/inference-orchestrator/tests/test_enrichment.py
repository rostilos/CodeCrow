"""
Unit tests for model.enrichment — FileContentDto, ParsedFileMetadataDto,
FileRelationshipDto, EnrichmentStats, PrEnrichmentDataDto.
"""
import pytest
from model.enrichment import (
    FileContentDto,
    ParsedFileMetadataDto,
    FileRelationshipDto,
    EnrichmentStats,
    PrEnrichmentDataDto,
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
