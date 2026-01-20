package org.rostilos.codecrow.core.dto.analysis.issue;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("IssueDTO")
class IssueDTOTest {

    @Test
    @DisplayName("should create with all fields")
    void shouldCreateWithAllFields() {
        OffsetDateTime now = OffsetDateTime.now();
        IssueDTO dto = new IssueDTO(
                "1",
                "SECURITY",
                "high",
                "SQL Injection vulnerability",
                "Use parameterized queries",
                "- query(sql)\n+ query(sql, params)",
                "src/UserService.java",
                42,
                10,
                "sql-injection",
                "main",
                "123",
                "open",
                now,
                "SECURITY",
                100L,
                123L,
                "abc123",
                now,
                null,
                null,
                null,
                null,
                null,
                null
        );
        
        assertThat(dto.id()).isEqualTo("1");
        assertThat(dto.type()).isEqualTo("SECURITY");
        assertThat(dto.severity()).isEqualTo("high");
        assertThat(dto.title()).isEqualTo("SQL Injection vulnerability");
        assertThat(dto.file()).isEqualTo("src/UserService.java");
        assertThat(dto.line()).isEqualTo(42);
        assertThat(dto.branch()).isEqualTo("main");
        assertThat(dto.pullRequestId()).isEqualTo("123");
        assertThat(dto.status()).isEqualTo("open");
        assertThat(dto.analysisId()).isEqualTo(100L);
        assertThat(dto.prNumber()).isEqualTo(123L);
        assertThat(dto.commitHash()).isEqualTo("abc123");
    }

    @Test
    @DisplayName("should create resolved issue")
    void shouldCreateResolvedIssue() {
        OffsetDateTime created = OffsetDateTime.now().minusDays(7);
        OffsetDateTime resolved = OffsetDateTime.now();
        
        IssueDTO dto = new IssueDTO(
                "2",
                "CODE_QUALITY",
                "medium",
                "Unused variable",
                "Remove unused variable",
                "- int unused = 0;",
                "src/Example.java",
                10,
                null,
                "unused-variable",
                "feature",
                "456",
                "resolved",
                created,
                "CODE_QUALITY",
                100L,
                456L,
                "def456",
                created,
                "Fixed by removing unused code",
                789L,
                "ghi789",
                101L,
                resolved,
                "user@example.com"
        );
        
        assertThat(dto.status()).isEqualTo("resolved");
        assertThat(dto.resolvedDescription()).isEqualTo("Fixed by removing unused code");
        assertThat(dto.resolvedByPr()).isEqualTo(789L);
        assertThat(dto.resolvedCommitHash()).isEqualTo("ghi789");
        assertThat(dto.resolvedAnalysisId()).isEqualTo(101L);
        assertThat(dto.resolvedAt()).isEqualTo(resolved);
        assertThat(dto.resolvedBy()).isEqualTo("user@example.com");
    }

    @Test
    @DisplayName("should handle null optional fields")
    void shouldHandleNullOptionalFields() {
        IssueDTO dto = new IssueDTO(
                "3",
                "STYLE",
                "low",
                "Missing semicolon",
                null,
                null,
                "src/Test.java",
                5,
                null,
                null,
                null,
                null,
                "open",
                null,
                "STYLE",
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null
        );
        
        assertThat(dto.suggestedFixDescription()).isNull();
        assertThat(dto.suggestedFixDiff()).isNull();
        assertThat(dto.column()).isNull();
        assertThat(dto.branch()).isNull();
        assertThat(dto.pullRequestId()).isNull();
        assertThat(dto.analysisId()).isNull();
    }
}
