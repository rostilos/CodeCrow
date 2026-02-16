package org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("Enrichment DTOs")
class EnrichmentDtoTest {

    @Nested
    @DisplayName("FileContentDto")
    class FileContentDtoTests {
        @Test void of_createsWithContent() {
            FileContentDto dto = FileContentDto.of("src/Main.java", "public class Main {}");
            assertThat(dto.path()).isEqualTo("src/Main.java");
            assertThat(dto.content()).isEqualTo("public class Main {}");
            assertThat(dto.sizeBytes()).isEqualTo("public class Main {}".getBytes().length);
            assertThat(dto.skipped()).isFalse();
            assertThat(dto.skipReason()).isNull();
        }

        @Test void skipped_createsWithReason() {
            FileContentDto dto = FileContentDto.skipped("big.bin", "unsupported_extension");
            assertThat(dto.path()).isEqualTo("big.bin");
            assertThat(dto.content()).isNull();
            assertThat(dto.sizeBytes()).isZero();
            assertThat(dto.skipped()).isTrue();
            assertThat(dto.skipReason()).isEqualTo("unsupported_extension");
        }

        @Test void skippedDueToSize_formatsMessage() {
            FileContentDto dto = FileContentDto.skippedDueToSize("huge.java", 200_000, 100_000);
            assertThat(dto.skipped()).isTrue();
            assertThat(dto.skipReason()).contains("200000").contains("100000");
            assertThat(dto.sizeBytes()).isEqualTo(200_000);
        }
    }

    @Nested
    @DisplayName("FileRelationshipDto")
    class FileRelationshipDtoTests {
        @Test void imports_strength10() {
            FileRelationshipDto r = FileRelationshipDto.imports("A.java", "B.java", "import B");
            assertThat(r.sourceFile()).isEqualTo("A.java");
            assertThat(r.targetFile()).isEqualTo("B.java");
            assertThat(r.relationshipType()).isEqualTo(FileRelationshipDto.RelationshipType.IMPORTS);
            assertThat(r.matchedOn()).isEqualTo("import B");
            assertThat(r.strength()).isEqualTo(10);
        }

        @Test void extendsClass_strength15() {
            FileRelationshipDto r = FileRelationshipDto.extendsClass("Child.java", "Parent.java", "Parent");
            assertThat(r.relationshipType()).isEqualTo(FileRelationshipDto.RelationshipType.EXTENDS);
            assertThat(r.strength()).isEqualTo(15);
        }

        @Test void implementsInterface_strength15() {
            FileRelationshipDto r = FileRelationshipDto.implementsInterface("Impl.java", "Api.java", "Api");
            assertThat(r.relationshipType()).isEqualTo(FileRelationshipDto.RelationshipType.IMPLEMENTS);
            assertThat(r.strength()).isEqualTo(15);
        }

        @Test void calls_strength8() {
            FileRelationshipDto r = FileRelationshipDto.calls("Caller.java", "Callee.java", "doWork");
            assertThat(r.relationshipType()).isEqualTo(FileRelationshipDto.RelationshipType.CALLS);
            assertThat(r.strength()).isEqualTo(8);
        }

        @Test void samePackage_strength3() {
            FileRelationshipDto r = FileRelationshipDto.samePackage("A.java", "B.java", "com.example");
            assertThat(r.relationshipType()).isEqualTo(FileRelationshipDto.RelationshipType.SAME_PACKAGE);
            assertThat(r.strength()).isEqualTo(3);
        }

        @Test void relationshipType_allValues() {
            assertThat(FileRelationshipDto.RelationshipType.values()).hasSize(6);
        }
    }

    @Nested
    @DisplayName("ParsedFileMetadataDto")
    class ParsedFileMetadataDtoTests {
        @Test void minimal_createsWithDefaults() {
            ParsedFileMetadataDto dto = ParsedFileMetadataDto.minimal(
                    "Main.java", List.of("import java.util.List"), List.of("BaseClass"));
            assertThat(dto.path()).isEqualTo("Main.java");
            assertThat(dto.imports()).containsExactly("import java.util.List");
            assertThat(dto.extendsClasses()).containsExactly("BaseClass");
            assertThat(dto.error()).isNull();
        }

        @Test void error_createsWithError() {
            ParsedFileMetadataDto dto = ParsedFileMetadataDto.error("bad.java", "Parse failed");
            assertThat(dto.path()).isEqualTo("bad.java");
            assertThat(dto.error()).isEqualTo("Parse failed");
        }

        @Test void hasRelationships_trueWithImports() {
            ParsedFileMetadataDto dto = ParsedFileMetadataDto.minimal("A.java", List.of("import B"), List.of());
            assertThat(dto.hasRelationships()).isTrue();
        }

        @Test void hasRelationships_trueWithExtends() {
            ParsedFileMetadataDto dto = ParsedFileMetadataDto.minimal("A.java", List.of(), List.of("Base"));
            assertThat(dto.hasRelationships()).isTrue();
        }

        @Test void hasRelationships_falseWhenEmpty() {
            ParsedFileMetadataDto dto = ParsedFileMetadataDto.minimal("A.java", List.of(), List.of());
            assertThat(dto.hasRelationships()).isFalse();
        }

        @Test void fullRecord() {
            ParsedFileMetadataDto dto = new ParsedFileMetadataDto(
                    "Main.java", "java",
                    List.of("import a"), List.of("Base"), List.of("Iface"),
                    List.of("main"), "BaseClass", "com.example",
                    List.of("doWork"), null);
            assertThat(dto.language()).isEqualTo("java");
            assertThat(dto.implementsInterfaces()).containsExactly("Iface");
            assertThat(dto.semanticNames()).containsExactly("main");
            assertThat(dto.parentClass()).isEqualTo("BaseClass");
            assertThat(dto.namespace()).isEqualTo("com.example");
            assertThat(dto.calls()).containsExactly("doWork");
            assertThat(dto.hasRelationships()).isTrue();
        }
    }

    @Nested
    @DisplayName("PrEnrichmentDataDto")
    class PrEnrichmentDataDtoTests {
        @Test void empty_returnsEmptyDto() {
            PrEnrichmentDataDto dto = PrEnrichmentDataDto.empty();
            assertThat(dto.fileContents()).isEmpty();
            assertThat(dto.fileMetadata()).isEmpty();
            assertThat(dto.relationships()).isEmpty();
            assertThat(dto.hasData()).isFalse();
        }

        @Test void hasData_trueWithFileContents() {
            PrEnrichmentDataDto dto = new PrEnrichmentDataDto(
                    List.of(FileContentDto.of("a.java", "content")),
                    List.of(), List.of(),
                    PrEnrichmentDataDto.EnrichmentStats.empty());
            assertThat(dto.hasData()).isTrue();
        }

        @Test void hasData_trueWithRelationships() {
            PrEnrichmentDataDto dto = new PrEnrichmentDataDto(
                    List.of(), List.of(),
                    List.of(FileRelationshipDto.imports("A.java", "B.java", "B")),
                    PrEnrichmentDataDto.EnrichmentStats.empty());
            assertThat(dto.hasData()).isTrue();
        }

        @Test void getTotalContentSize_sumsSizes() {
            PrEnrichmentDataDto dto = new PrEnrichmentDataDto(
                    List.of(
                            FileContentDto.of("a.java", "abc"),
                            FileContentDto.of("b.java", "defgh"),
                            FileContentDto.skipped("c.bin", "unsupported")),
                    List.of(), List.of(),
                    PrEnrichmentDataDto.EnrichmentStats.empty());
            assertThat(dto.getTotalContentSize()).isEqualTo(3 + 5);
        }

        @Test void enrichmentStats_empty() {
            var stats = PrEnrichmentDataDto.EnrichmentStats.empty();
            assertThat(stats.totalFilesRequested()).isZero();
            assertThat(stats.filesEnriched()).isZero();
            assertThat(stats.filesSkipped()).isZero();
            assertThat(stats.relationshipsFound()).isZero();
            assertThat(stats.totalContentSizeBytes()).isZero();
            assertThat(stats.processingTimeMs()).isZero();
            assertThat(stats.skipReasons()).isEmpty();
        }

        @Test void enrichmentStats_withValues() {
            var stats = new PrEnrichmentDataDto.EnrichmentStats(
                    10, 8, 2, 5, 50000L, 100L, Map.of("size_limit", 2));
            assertThat(stats.totalFilesRequested()).isEqualTo(10);
            assertThat(stats.filesEnriched()).isEqualTo(8);
            assertThat(stats.skipReasons()).containsEntry("size_limit", 2);
        }
    }
}
