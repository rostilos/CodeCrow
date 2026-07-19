package org.rostilos.codecrow.core.model.codeanalysis;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("CodeAnalysisIssue Entity")
class CodeAnalysisIssueTest {

    private CodeAnalysisIssue issue;

    @BeforeEach
    void setUp() {
        issue = new CodeAnalysisIssue();
    }

    @Nested
    @DisplayName("Default Values")
    class DefaultValues {

        @Test
        @DisplayName("should have null id by default")
        void shouldHaveNullIdByDefault() {
            assertThat(issue.getId()).isNull();
        }

        @Test
        @DisplayName("should have resolved as false by default")
        void shouldHaveResolvedFalseByDefault() {
            assertThat(issue.isResolved()).isFalse();
        }
    }

    @Nested
    @DisplayName("Analysis Association")
    class AnalysisAssociation {

        @Test
        @DisplayName("should set and get analysis")
        void shouldSetAndGetAnalysis() {
            CodeAnalysis analysis = new CodeAnalysis();
            
            issue.setAnalysis(analysis);
            
            assertThat(issue.getAnalysis()).isEqualTo(analysis);
        }
    }

    @Nested
    @DisplayName("Severity")
    class Severity {

        @Test
        @DisplayName("should set and get HIGH severity")
        void shouldSetAndGetHighSeverity() {
            issue.setSeverity(IssueSeverity.HIGH);
            
            assertThat(issue.getSeverity()).isEqualTo(IssueSeverity.HIGH);
        }

        @Test
        @DisplayName("should set and get MEDIUM severity")
        void shouldSetAndGetMediumSeverity() {
            issue.setSeverity(IssueSeverity.MEDIUM);
            
            assertThat(issue.getSeverity()).isEqualTo(IssueSeverity.MEDIUM);
        }

        @Test
        @DisplayName("should set and get LOW severity")
        void shouldSetAndGetLowSeverity() {
            issue.setSeverity(IssueSeverity.LOW);
            
            assertThat(issue.getSeverity()).isEqualTo(IssueSeverity.LOW);
        }

        @Test
        @DisplayName("should set and get INFO severity")
        void shouldSetAndGetInfoSeverity() {
            issue.setSeverity(IssueSeverity.INFO);
            
            assertThat(issue.getSeverity()).isEqualTo(IssueSeverity.INFO);
        }

        @Test
        @DisplayName("should set and get RESOLVED severity")
        void shouldSetAndGetResolvedSeverity() {
            issue.setSeverity(IssueSeverity.RESOLVED);
            
            assertThat(issue.getSeverity()).isEqualTo(IssueSeverity.RESOLVED);
        }
    }

    @Nested
    @DisplayName("File Location")
    class FileLocation {

        @Test
        @DisplayName("should set and get file path")
        void shouldSetAndGetFilePath() {
            issue.setFilePath("src/main/java/Test.java");
            
            assertThat(issue.getFilePath()).isEqualTo("src/main/java/Test.java");
        }

        @Test
        @DisplayName("should set and get line number")
        void shouldSetAndGetLineNumber() {
            issue.setLineNumber(42);
            
            assertThat(issue.getLineNumber()).isEqualTo(42);
        }
    }

    @Nested
    @DisplayName("Issue Details")
    class IssueDetails {

        @Test
        @DisplayName("should set and get reason")
        void shouldSetAndGetReason() {
            issue.setReason("Potential null pointer dereference");
            
            assertThat(issue.getReason()).isEqualTo("Potential null pointer dereference");
        }

        @Test
        @DisplayName("should set and get suggested fix description")
        void shouldSetAndGetSuggestedFixDescription() {
            issue.setSuggestedFixDescription("Add null check before accessing the variable");
            
            assertThat(issue.getSuggestedFixDescription()).isEqualTo("Add null check before accessing the variable");
        }

        @Test
        @DisplayName("should set and get suggested fix diff")
        void shouldSetAndGetSuggestedFixDiff() {
            String diff = "- if (obj.method())\n+ if (obj != null && obj.method())";
            issue.setSuggestedFixDiff(diff);
            
            assertThat(issue.getSuggestedFixDiff()).isEqualTo(diff);
        }

        @Test
        @DisplayName("should set and get issue category")
        void shouldSetAndGetIssueCategory() {
            issue.setIssueCategory(IssueCategory.BUG_RISK);
            
            assertThat(issue.getIssueCategory()).isEqualTo(IssueCategory.BUG_RISK);
        }
    }

    @Nested
    @DisplayName("Resolution Status")
    class ResolutionStatus {

        @Test
        @DisplayName("should set and get resolved status")
        void shouldSetAndGetResolvedStatus() {
            issue.setResolved(true);
            
            assertThat(issue.isResolved()).isTrue();
        }

        @Test
        @DisplayName("should set and get resolved description")
        void shouldSetAndGetResolvedDescription() {
            issue.setResolvedDescription("Fixed in latest commit");
            
            assertThat(issue.getResolvedDescription()).isEqualTo("Fixed in latest commit");
        }

        @Test
        @DisplayName("should set and get resolved by PR")
        void shouldSetAndGetResolvedByPr() {
            issue.setResolvedByPr(123L);
            
            assertThat(issue.getResolvedByPr()).isEqualTo(123L);
        }

        @Test
        @DisplayName("should set and get resolved commit hash")
        void shouldSetAndGetResolvedCommitHash() {
            issue.setResolvedCommitHash("abc123def456");
            
            assertThat(issue.getResolvedCommitHash()).isEqualTo("abc123def456");
        }

        @Test
        @DisplayName("should set and get resolved analysis id")
        void shouldSetAndGetResolvedAnalysisId() {
            issue.setResolvedAnalysisId(456L);
            
            assertThat(issue.getResolvedAnalysisId()).isEqualTo(456L);
        }

        @Test
        @DisplayName("should set and get resolved at")
        void shouldSetAndGetResolvedAt() {
            OffsetDateTime resolvedAt = OffsetDateTime.now();
            issue.setResolvedAt(resolvedAt);
            
            assertThat(issue.getResolvedAt()).isEqualTo(resolvedAt);
        }

        @Test
        @DisplayName("should set and get resolved by")
        void shouldSetAndGetResolvedBy() {
            issue.setResolvedBy("developer@example.com");
            
            assertThat(issue.getResolvedBy()).isEqualTo("developer@example.com");
        }
    }

    @Nested
    @DisplayName("Issue Categories")
    class IssueCategories {

        @Test
        @DisplayName("should support SECURITY category")
        void shouldSupportSecurityCategory() {
            issue.setIssueCategory(IssueCategory.SECURITY);
            assertThat(issue.getIssueCategory()).isEqualTo(IssueCategory.SECURITY);
        }

        @Test
        @DisplayName("should support PERFORMANCE category")
        void shouldSupportPerformanceCategory() {
            issue.setIssueCategory(IssueCategory.PERFORMANCE);
            assertThat(issue.getIssueCategory()).isEqualTo(IssueCategory.PERFORMANCE);
        }

        @Test
        @DisplayName("should support CODE_QUALITY category")
        void shouldSupportCodeQualityCategory() {
            issue.setIssueCategory(IssueCategory.CODE_QUALITY);
            assertThat(issue.getIssueCategory()).isEqualTo(IssueCategory.CODE_QUALITY);
        }

        @Test
        @DisplayName("should support BUG_RISK category")
        void shouldSupportBugRiskCategory() {
            issue.setIssueCategory(IssueCategory.BUG_RISK);
            assertThat(issue.getIssueCategory()).isEqualTo(IssueCategory.BUG_RISK);
        }

        @Test
        @DisplayName("should support STYLE category")
        void shouldSupportStyleCategory() {
            issue.setIssueCategory(IssueCategory.STYLE);
            assertThat(issue.getIssueCategory()).isEqualTo(IssueCategory.STYLE);
        }

        @Test
        @DisplayName("should support ARCHITECTURE category")
        void shouldSupportArchitectureCategory() {
            issue.setIssueCategory(IssueCategory.ARCHITECTURE);
            assertThat(issue.getIssueCategory()).isEqualTo(IssueCategory.ARCHITECTURE);
        }
    }
}
