package org.rostilos.codecrow.mcp.bitbucket.pullrequest.diff;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

@DisplayName("FileDiff")
class FileDiffTest {

    @Test
    @DisplayName("should set and get filePath")
    void shouldSetAndGetFilePath() {
        FileDiff fileDiff = new FileDiff();
        fileDiff.setFilePath("src/main/java/Service.java");
        
        assertThat(fileDiff.getFilePath()).isEqualTo("src/main/java/Service.java");
    }

    @Test
    @DisplayName("should set and get diffType")
    void shouldSetAndGetDiffType() {
        FileDiff fileDiff = new FileDiff();
        fileDiff.setDiffType(FileDiff.DiffType.MODIFIED);
        
        assertThat(fileDiff.getDiffType()).isEqualTo(FileDiff.DiffType.MODIFIED);
    }

    @Test
    @DisplayName("should set and get rawContent")
    void shouldSetAndGetRawContent() {
        FileDiff fileDiff = new FileDiff();
        String content = "public class Test {}";
        fileDiff.setRawContent(content);
        
        assertThat(fileDiff.getRawContent()).isEqualTo(content);
    }

    @Test
    @DisplayName("should set and get sha")
    void shouldSetAndGetSha() {
        FileDiff fileDiff = new FileDiff();
        fileDiff.setSha("abc123def456");
        
        assertThat(fileDiff.getSha()).isEqualTo("abc123def456");
    }

    @Test
    @DisplayName("should set and get changes")
    void shouldSetAndGetChanges() {
        FileDiff fileDiff = new FileDiff();
        fileDiff.setChanges("+10 -5");
        
        assertThat(fileDiff.getChanges()).isEqualTo("+10 -5");
    }

    @Test
    @DisplayName("should handle null values")
    void shouldHandleNullValues() {
        FileDiff fileDiff = new FileDiff();
        
        assertThat(fileDiff.getFilePath()).isNull();
        assertThat(fileDiff.getDiffType()).isNull();
        assertThat(fileDiff.getRawContent()).isNull();
        assertThat(fileDiff.getSha()).isNull();
        assertThat(fileDiff.getChanges()).isNull();
    }

    @DisplayName("FileDiff.DiffType enum")
    @org.junit.jupiter.api.Nested
    class FileDiffDiffTypeTest {
        
        @Test
        @DisplayName("should have all expected values")
        void shouldHaveAllExpectedValues() {
            FileDiff.DiffType[] values = FileDiff.DiffType.values();
            
            assertThat(values).hasSize(4);
            assertThat(values).contains(
                    FileDiff.DiffType.ADDED,
                    FileDiff.DiffType.MODIFIED,
                    FileDiff.DiffType.REMOVED,
                    FileDiff.DiffType.RENAMED
            );
        }
        
        @Test
        @DisplayName("fromValue should parse lowercase value")
        void fromValueShouldParseLowercaseValue() {
            assertThat(FileDiff.DiffType.fromValue("added")).isEqualTo(FileDiff.DiffType.ADDED);
            assertThat(FileDiff.DiffType.fromValue("modified")).isEqualTo(FileDiff.DiffType.MODIFIED);
        }
        
        @Test
        @DisplayName("fromValue should parse uppercase value")
        void fromValueShouldParseUppercaseValue() {
            assertThat(FileDiff.DiffType.fromValue("ADDED")).isEqualTo(FileDiff.DiffType.ADDED);
            assertThat(FileDiff.DiffType.fromValue("REMOVED")).isEqualTo(FileDiff.DiffType.REMOVED);
        }
        
        @Test
        @DisplayName("fromValue should parse mixed case value")
        void fromValueShouldParseMixedCaseValue() {
            assertThat(FileDiff.DiffType.fromValue("Added")).isEqualTo(FileDiff.DiffType.ADDED);
            assertThat(FileDiff.DiffType.fromValue("rEmOvEd")).isEqualTo(FileDiff.DiffType.REMOVED);
        }
        
        @Test
        @DisplayName("fromValue should throw for invalid value")
        void fromValueShouldThrowForInvalidValue() {
            assertThatThrownBy(() -> FileDiff.DiffType.fromValue("invalid"))
                    .isInstanceOf(IllegalArgumentException.class);
        }
    }
}
