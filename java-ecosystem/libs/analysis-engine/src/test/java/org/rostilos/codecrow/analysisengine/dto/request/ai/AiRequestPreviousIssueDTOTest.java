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
                "SECURITY",
                1,  // prVersion
                null,  // resolvedDescription
                null,  // resolvedByCommit
                null   // resolvedInPrVersion
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
        assertThat(dto.prVersion()).isEqualTo(1);
    }

    @Test
    @DisplayName("should handle null values")
    void shouldHandleNullValues() {
        AiRequestPreviousIssueDTO dto = new AiRequestPreviousIssueDTO(
                null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null
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
                "1", "type", "HIGH", "reason", "fix", "diff", "file.java", 10, "main", "1", "open", "cat", 1, null, null, null
        );
        AiRequestPreviousIssueDTO dto2 = new AiRequestPreviousIssueDTO(
                "1", "type", "HIGH", "reason", "fix", "diff", "file.java", 10, "main", "1", "open", "cat", 1, null, null, null
        );
        AiRequestPreviousIssueDTO dto3 = new AiRequestPreviousIssueDTO(
                "2", "type", "HIGH", "reason", "fix", "diff", "file.java", 10, "main", "1", "open", "cat", 1, null, null, null
        );
        
        assertThat(dto1).isEqualTo(dto2);
        assertThat(dto1).isNotEqualTo(dto3);
    }

    @Test
    @DisplayName("should implement hashCode correctly")
    void shouldImplementHashCodeCorrectly() {
        AiRequestPreviousIssueDTO dto1 = new AiRequestPreviousIssueDTO(
                "1", "type", "HIGH", "reason", "fix", "diff", "file.java", 10, "main", "1", "open", "cat", 1, null, null, null
        );
        AiRequestPreviousIssueDTO dto2 = new AiRequestPreviousIssueDTO(
                "1", "type", "HIGH", "reason", "fix", "diff", "file.java", 10, "main", "1", "open", "cat", 1, null, null, null
        );
        
        assertThat(dto1.hashCode()).isEqualTo(dto2.hashCode());
    }

    @Test
    @DisplayName("should support resolved status")
    void shouldSupportResolvedStatus() {
        AiRequestPreviousIssueDTO dto = new AiRequestPreviousIssueDTO(
                "1", "type", "LOW", "reason", null, null, "file.java", 5, "dev", "2", "resolved", "CODE_QUALITY", 
                1, "Fixed by adding null check", "abc123", 2L
        );
        
        assertThat(dto.status()).isEqualTo("resolved");
        assertThat(dto.resolvedDescription()).isEqualTo("Fixed by adding null check");
        assertThat(dto.resolvedByCommit()).isEqualTo("abc123");
        assertThat(dto.resolvedInAnalysisId()).isEqualTo(2L);
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
            when(analysis.getPrVersion()).thenReturn(2);

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
            when(issue.getResolvedDescription()).thenReturn(null);
            when(issue.getResolvedCommitHash()).thenReturn(null);
            when(issue.getResolvedAnalysisId()).thenReturn(null);

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
            assertThat(dto.prVersion()).isEqualTo(2);
        }

        @Test
        @DisplayName("should convert resolved entity with resolution tracking")
        void shouldConvertResolvedEntity() {
            CodeAnalysis analysis = mock(CodeAnalysis.class);
            when(analysis.getBranchName()).thenReturn("main");
            when(analysis.getPrNumber()).thenReturn(10L);
            when(analysis.getPrVersion()).thenReturn(3);

            CodeAnalysisIssue issue = mock(CodeAnalysisIssue.class);
            when(issue.getId()).thenReturn(456L);
            when(issue.getAnalysis()).thenReturn(analysis);
            when(issue.getIssueCategory()).thenReturn(IssueCategory.CODE_QUALITY);
            when(issue.getSeverity()).thenReturn(IssueSeverity.LOW);
            when(issue.getReason()).thenReturn("Minor code issue");
            when(issue.getFilePath()).thenReturn("src/Utils.java");
            when(issue.getLineNumber()).thenReturn(10);
            when(issue.isResolved()).thenReturn(true);
            when(issue.getResolvedDescription()).thenReturn("Fixed by refactoring");
            when(issue.getResolvedCommitHash()).thenReturn("abc123def");
            when(issue.getResolvedAnalysisId()).thenReturn(5L);

            AiRequestPreviousIssueDTO dto = AiRequestPreviousIssueDTO.fromEntity(issue);

            assertThat(dto.status()).isEqualTo("resolved");
            assertThat(dto.prVersion()).isEqualTo(3);
            assertThat(dto.resolvedDescription()).isEqualTo("Fixed by refactoring");
            assertThat(dto.resolvedByCommit()).isEqualTo("abc123def");
            assertThat(dto.resolvedInAnalysisId()).isEqualTo(5L);
        }

        @Test
        @DisplayName("should handle null issueCategory with default")
        void shouldHandleNullIssueCategoryWithDefault() {
            CodeAnalysis analysis = mock(CodeAnalysis.class);
            when(analysis.getBranchName()).thenReturn("main");
            when(analysis.getPrNumber()).thenReturn(1L);
            when(analysis.getPrVersion()).thenReturn(1);

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
            when(analysis.getPrVersion()).thenReturn(1);

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
            assertThat(dto.prVersion()).isNull();
        }

        @Test
        @DisplayName("should handle analysis with null prNumber")
        void shouldHandleAnalysisWithNullPrNumber() {
            CodeAnalysis analysis = mock(CodeAnalysis.class);
            when(analysis.getBranchName()).thenReturn("develop");
            when(analysis.getPrNumber()).thenReturn(null);
            when(analysis.getPrVersion()).thenReturn(1);

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
