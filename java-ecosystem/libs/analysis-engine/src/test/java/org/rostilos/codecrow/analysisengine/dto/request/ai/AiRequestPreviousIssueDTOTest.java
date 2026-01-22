package org.rostilos.codecrow.analysisengine.dto.request.ai;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueCategory;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

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

    @Nested
    @DisplayName("fromEntity()")
    class FromEntityTests {

        @Test
        @DisplayName("should convert entity with all fields")
        void shouldConvertEntityWithAllFields() {
            CodeAnalysis analysis = mock(CodeAnalysis.class);
            when(analysis.getBranchName()).thenReturn("feature-branch");
            when(analysis.getPrNumber()).thenReturn(42L);

            CodeAnalysisIssue issue = mock(CodeAnalysisIssue.class);
            when(issue.getId()).thenReturn(123L);
            when(issue.getAnalysis()).thenReturn(analysis);
            when(issue.getIssueCategory()).thenReturn(IssueCategory.SECURITY);
            when(issue.getSeverity()).thenReturn(IssueSeverity.HIGH);
            when(issue.getReason()).thenReturn("Security vulnerability found");
            when(issue.getSuggestedFixDescription()).thenReturn("Fix the security issue");
            when(issue.getSuggestedFixDiff()).thenReturn("- old\n+ new");
            when(issue.getFilePath()).thenReturn("src/Main.java");
            when(issue.getLineNumber()).thenReturn(50);
            when(issue.isResolved()).thenReturn(false);

            AiRequestPreviousIssueDTO dto = AiRequestPreviousIssueDTO.fromEntity(issue);

            assertThat(dto.id()).isEqualTo("123");
            assertThat(dto.type()).isEqualTo("SECURITY");
            assertThat(dto.severity()).isEqualTo("HIGH");
            assertThat(dto.reason()).isEqualTo("Security vulnerability found");
            assertThat(dto.suggestedFixDescription()).isEqualTo("Fix the security issue");
            assertThat(dto.suggestedFixDiff()).isEqualTo("- old\n+ new");
            assertThat(dto.file()).isEqualTo("src/Main.java");
            assertThat(dto.line()).isEqualTo(50);
            assertThat(dto.branch()).isEqualTo("feature-branch");
            assertThat(dto.pullRequestId()).isEqualTo("42");
            assertThat(dto.status()).isEqualTo("open");
            assertThat(dto.category()).isEqualTo("SECURITY");
        }

        @Test
        @DisplayName("should convert resolved entity")
        void shouldConvertResolvedEntity() {
            CodeAnalysis analysis = mock(CodeAnalysis.class);
            when(analysis.getBranchName()).thenReturn("main");
            when(analysis.getPrNumber()).thenReturn(10L);

            CodeAnalysisIssue issue = mock(CodeAnalysisIssue.class);
            when(issue.getId()).thenReturn(456L);
            when(issue.getAnalysis()).thenReturn(analysis);
            when(issue.getIssueCategory()).thenReturn(IssueCategory.CODE_QUALITY);
            when(issue.getSeverity()).thenReturn(IssueSeverity.LOW);
            when(issue.getReason()).thenReturn("Minor code issue");
            when(issue.getFilePath()).thenReturn("src/Utils.java");
            when(issue.getLineNumber()).thenReturn(10);
            when(issue.isResolved()).thenReturn(true);

            AiRequestPreviousIssueDTO dto = AiRequestPreviousIssueDTO.fromEntity(issue);

            assertThat(dto.status()).isEqualTo("resolved");
        }

        @Test
        @DisplayName("should handle null issueCategory with default")
        void shouldHandleNullIssueCategoryWithDefault() {
            CodeAnalysis analysis = mock(CodeAnalysis.class);
            when(analysis.getBranchName()).thenReturn("main");
            when(analysis.getPrNumber()).thenReturn(1L);

            CodeAnalysisIssue issue = mock(CodeAnalysisIssue.class);
            when(issue.getId()).thenReturn(1L);
            when(issue.getAnalysis()).thenReturn(analysis);
            when(issue.getIssueCategory()).thenReturn(null); // null category
            when(issue.getSeverity()).thenReturn(IssueSeverity.MEDIUM);
            when(issue.getFilePath()).thenReturn("test.java");

            AiRequestPreviousIssueDTO dto = AiRequestPreviousIssueDTO.fromEntity(issue);

            assertThat(dto.type()).isEqualTo("CODE_QUALITY"); // default
            assertThat(dto.category()).isEqualTo("CODE_QUALITY");
        }

        @Test
        @DisplayName("should handle null severity")
        void shouldHandleNullSeverity() {
            CodeAnalysis analysis = mock(CodeAnalysis.class);
            when(analysis.getBranchName()).thenReturn("main");

            CodeAnalysisIssue issue = mock(CodeAnalysisIssue.class);
            when(issue.getId()).thenReturn(2L);
            when(issue.getAnalysis()).thenReturn(analysis);
            when(issue.getIssueCategory()).thenReturn(IssueCategory.PERFORMANCE);
            when(issue.getSeverity()).thenReturn(null);
            when(issue.getFilePath()).thenReturn("test.java");

            AiRequestPreviousIssueDTO dto = AiRequestPreviousIssueDTO.fromEntity(issue);

            assertThat(dto.severity()).isNull();
        }

        @Test
        @DisplayName("should handle null analysis")
        void shouldHandleNullAnalysis() {
            CodeAnalysisIssue issue = mock(CodeAnalysisIssue.class);
            when(issue.getId()).thenReturn(3L);
            when(issue.getAnalysis()).thenReturn(null);
            when(issue.getIssueCategory()).thenReturn(IssueCategory.STYLE);
            when(issue.getSeverity()).thenReturn(IssueSeverity.INFO);
            when(issue.getFilePath()).thenReturn("test.java");

            AiRequestPreviousIssueDTO dto = AiRequestPreviousIssueDTO.fromEntity(issue);

            assertThat(dto.branch()).isNull();
            assertThat(dto.pullRequestId()).isNull();
        }

        @Test
        @DisplayName("should handle analysis with null prNumber")
        void shouldHandleAnalysisWithNullPrNumber() {
            CodeAnalysis analysis = mock(CodeAnalysis.class);
            when(analysis.getBranchName()).thenReturn("develop");
            when(analysis.getPrNumber()).thenReturn(null);

            CodeAnalysisIssue issue = mock(CodeAnalysisIssue.class);
            when(issue.getId()).thenReturn(4L);
            when(issue.getAnalysis()).thenReturn(analysis);
            when(issue.getIssueCategory()).thenReturn(IssueCategory.CODE_QUALITY);
            when(issue.getSeverity()).thenReturn(IssueSeverity.MEDIUM);
            when(issue.getFilePath()).thenReturn("test.java");

            AiRequestPreviousIssueDTO dto = AiRequestPreviousIssueDTO.fromEntity(issue);

            assertThat(dto.branch()).isEqualTo("develop");
            assertThat(dto.pullRequestId()).isNull();
        }
    }
}
