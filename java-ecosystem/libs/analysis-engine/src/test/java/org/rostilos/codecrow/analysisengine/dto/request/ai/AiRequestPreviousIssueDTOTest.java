package org.rostilos.codecrow.analysisengine.dto.request.ai;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("AiRequestPreviousIssueDTO")
class AiRequestPreviousIssueDTOTest {

    @Test
    @DisplayName("should create record with all fields")
    void shouldCreateRecordWithAllFields() {
        AiRequestPreviousIssueDTO dto = new AiRequestPreviousIssueDTO(
                "123",
                "SECURITY",
                "HIGH",
                "SQL injection vulnerability",
                "Use parameterized queries",
                "- executeQuery(sql)\n+ executeQuery(sql, params)",
                "src/main/java/Service.java",
                42,
                "main",
                "100",
                "open",
                "SECURITY"
        );
        
        assertThat(dto.id()).isEqualTo("123");
        assertThat(dto.type()).isEqualTo("SECURITY");
        assertThat(dto.severity()).isEqualTo("HIGH");
        assertThat(dto.reason()).isEqualTo("SQL injection vulnerability");
        assertThat(dto.suggestedFixDescription()).isEqualTo("Use parameterized queries");
        assertThat(dto.suggestedFixDiff()).contains("executeQuery");
        assertThat(dto.file()).isEqualTo("src/main/java/Service.java");
        assertThat(dto.line()).isEqualTo(42);
        assertThat(dto.branch()).isEqualTo("main");
        assertThat(dto.pullRequestId()).isEqualTo("100");
        assertThat(dto.status()).isEqualTo("open");
        assertThat(dto.category()).isEqualTo("SECURITY");
    }

    @Test
    @DisplayName("should handle null values")
    void shouldHandleNullValues() {
        AiRequestPreviousIssueDTO dto = new AiRequestPreviousIssueDTO(
                null, null, null, null, null, null, null, null, null, null, null, null
        );
        
        assertThat(dto.id()).isNull();
        assertThat(dto.type()).isNull();
        assertThat(dto.severity()).isNull();
        assertThat(dto.reason()).isNull();
    }

    @Test
    @DisplayName("should implement equals correctly")
    void shouldImplementEqualsCorrectly() {
        AiRequestPreviousIssueDTO dto1 = new AiRequestPreviousIssueDTO(
                "1", "type", "HIGH", "reason", "fix", "diff", "file.java", 10, "main", "1", "open", "cat"
        );
        AiRequestPreviousIssueDTO dto2 = new AiRequestPreviousIssueDTO(
                "1", "type", "HIGH", "reason", "fix", "diff", "file.java", 10, "main", "1", "open", "cat"
        );
        AiRequestPreviousIssueDTO dto3 = new AiRequestPreviousIssueDTO(
                "2", "type", "HIGH", "reason", "fix", "diff", "file.java", 10, "main", "1", "open", "cat"
        );
        
        assertThat(dto1).isEqualTo(dto2);
        assertThat(dto1).isNotEqualTo(dto3);
    }

    @Test
    @DisplayName("should implement hashCode correctly")
    void shouldImplementHashCodeCorrectly() {
        AiRequestPreviousIssueDTO dto1 = new AiRequestPreviousIssueDTO(
                "1", "type", "HIGH", "reason", "fix", "diff", "file.java", 10, "main", "1", "open", "cat"
        );
        AiRequestPreviousIssueDTO dto2 = new AiRequestPreviousIssueDTO(
                "1", "type", "HIGH", "reason", "fix", "diff", "file.java", 10, "main", "1", "open", "cat"
        );
        
        assertThat(dto1.hashCode()).isEqualTo(dto2.hashCode());
    }

    @Test
    @DisplayName("should support resolved status")
    void shouldSupportResolvedStatus() {
        AiRequestPreviousIssueDTO dto = new AiRequestPreviousIssueDTO(
                "1", "type", "LOW", "reason", null, null, "file.java", 5, "dev", "2", "resolved", "CODE_QUALITY"
        );
        
        assertThat(dto.status()).isEqualTo("resolved");
    }
}
