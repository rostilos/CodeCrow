package org.rostilos.codecrow.core.model.codeanalysis;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.project.Project;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("CodeAnalysis Entity")
class CodeAnalysisTest {

    private CodeAnalysis analysis;

    @BeforeEach
    void setUp() {
        analysis = new CodeAnalysis();
    }

    @Nested
    @DisplayName("Default Values")
    class DefaultValues {

        @Test
        @DisplayName("should have null id by default")
        void shouldHaveNullIdByDefault() {
            assertThat(analysis.getId()).isNull();
        }

        @Test
        @DisplayName("should have ACCEPTED status by default")
        void shouldHaveAcceptedStatusByDefault() {
            assertThat(analysis.getStatus()).isEqualTo(AnalysisStatus.ACCEPTED);
        }

        @Test
        @DisplayName("should have zero issue counts by default")
        void shouldHaveZeroIssueCountsByDefault() {
            assertThat(analysis.getTotalIssues()).isZero();
            assertThat(analysis.getHighSeverityCount()).isZero();
            assertThat(analysis.getMediumSeverityCount()).isZero();
            assertThat(analysis.getLowSeverityCount()).isZero();
            assertThat(analysis.getInfoSeverityCount()).isZero();
            assertThat(analysis.getResolvedCount()).isZero();
        }

        @Test
        @DisplayName("should have createdAt set on creation")
        void shouldHaveCreatedAtSetOnCreation() {
            assertThat(analysis.getCreatedAt()).isNotNull();
        }

        @Test
        @DisplayName("should have updatedAt set on creation")
        void shouldHaveUpdatedAtSetOnCreation() {
            assertThat(analysis.getUpdatedAt()).isNotNull();
        }

        @Test
        @DisplayName("should have empty issues list by default")
        void shouldHaveEmptyIssuesListByDefault() {
            assertThat(analysis.getIssues()).isNotNull();
            assertThat(analysis.getIssues()).isEmpty();
        }
    }

    @Nested
    @DisplayName("Project Association")
    class ProjectAssociation {

        @Test
        @DisplayName("should set and get project")
        void shouldSetAndGetProject() {
            Project project = new Project();
            project.setName("Test Project");
            
            analysis.setProject(project);
            
            assertThat(analysis.getProject()).isEqualTo(project);
        }
    }

    @Nested
    @DisplayName("Analysis Type")
    class AnalysisTypeTests {

        @Test
        @DisplayName("should set and get analysis type PR_REVIEW")
        void shouldSetAndGetAnalysisTypePR() {
            analysis.setAnalysisType(AnalysisType.PR_REVIEW);
            
            assertThat(analysis.getAnalysisType()).isEqualTo(AnalysisType.PR_REVIEW);
        }

        @Test
        @DisplayName("should set and get analysis type BRANCH_ANALYSIS")
        void shouldSetAndGetAnalysisTypeBranch() {
            analysis.setAnalysisType(AnalysisType.BRANCH_ANALYSIS);
            
            assertThat(analysis.getAnalysisType()).isEqualTo(AnalysisType.BRANCH_ANALYSIS);
        }
    }

    @Nested
    @DisplayName("PR Properties")
    class PRProperties {

        @Test
        @DisplayName("should set and get PR number")
        void shouldSetAndGetPrNumber() {
            analysis.setPrNumber(42L);
            
            assertThat(analysis.getPrNumber()).isEqualTo(42L);
        }

        @Test
        @DisplayName("should set and get commit hash")
        void shouldSetAndGetCommitHash() {
            analysis.setCommitHash("abc123def456");
            
            assertThat(analysis.getCommitHash()).isEqualTo("abc123def456");
        }

        @Test
        @DisplayName("should set and get branch name")
        void shouldSetAndGetBranchName() {
            analysis.setBranchName("main");
            
            assertThat(analysis.getBranchName()).isEqualTo("main");
        }

        @Test
        @DisplayName("should set and get source branch name")
        void shouldSetAndGetSourceBranchName() {
            analysis.setSourceBranchName("feature/new-feature");
            
            assertThat(analysis.getSourceBranchName()).isEqualTo("feature/new-feature");
        }

        @Test
        @DisplayName("should set and get PR version")
        void shouldSetAndGetPrVersion() {
            analysis.setPrVersion(3);
            
            assertThat(analysis.getPrVersion()).isEqualTo(3);
        }
    }

    @Nested
    @DisplayName("Comment")
    class Comment {

        @Test
        @DisplayName("should set and get comment")
        void shouldSetAndGetComment() {
            analysis.setComment("This is a test comment");
            
            assertThat(analysis.getComment()).isEqualTo("This is a test comment");
        }
    }

    @Nested
    @DisplayName("Status and Result")
    class StatusAndResult {

        @Test
        @DisplayName("should set and get status")
        void shouldSetAndGetStatus() {
            analysis.setStatus(AnalysisStatus.PENDING);
            
            assertThat(analysis.getStatus()).isEqualTo(AnalysisStatus.PENDING);
        }

        @Test
        @DisplayName("should set and get analysis result PASSED")
        void shouldSetAndGetAnalysisResultPassed() {
            analysis.setAnalysisResult(AnalysisResult.PASSED);
            
            assertThat(analysis.getAnalysisResult()).isEqualTo(AnalysisResult.PASSED);
        }

        @Test
        @DisplayName("should set and get analysis result FAILED")
        void shouldSetAndGetAnalysisResultFailed() {
            analysis.setAnalysisResult(AnalysisResult.FAILED);
            
            assertThat(analysis.getAnalysisResult()).isEqualTo(AnalysisResult.FAILED);
        }
    }

    @Nested
    @DisplayName("Issue Management")
    class IssueManagement {

        @Test
        @DisplayName("should add issue and update counts")
        void shouldAddIssueAndUpdateCounts() {
            CodeAnalysisIssue issue = new CodeAnalysisIssue();
            issue.setSeverity(IssueSeverity.HIGH);
            issue.setFilePath("Test.java");
            issue.setResolved(false);
            
            analysis.addIssue(issue);
            
            assertThat(analysis.getIssues()).hasSize(1);
            assertThat(analysis.getTotalIssues()).isEqualTo(1);
            assertThat(analysis.getHighSeverityCount()).isEqualTo(1);
            assertThat(issue.getAnalysis()).isEqualTo(analysis);
        }

        @Test
        @DisplayName("should update counts for multiple severities")
        void shouldUpdateCountsForMultipleSeverities() {
            CodeAnalysisIssue highIssue = new CodeAnalysisIssue();
            highIssue.setSeverity(IssueSeverity.HIGH);
            highIssue.setFilePath("High.java");
            highIssue.setResolved(false);
            
            CodeAnalysisIssue mediumIssue = new CodeAnalysisIssue();
            mediumIssue.setSeverity(IssueSeverity.MEDIUM);
            mediumIssue.setFilePath("Medium.java");
            mediumIssue.setResolved(false);
            
            CodeAnalysisIssue lowIssue = new CodeAnalysisIssue();
            lowIssue.setSeverity(IssueSeverity.LOW);
            lowIssue.setFilePath("Low.java");
            lowIssue.setResolved(false);
            
            CodeAnalysisIssue infoIssue = new CodeAnalysisIssue();
            infoIssue.setSeverity(IssueSeverity.INFO);
            infoIssue.setFilePath("Info.java");
            infoIssue.setResolved(false);
            
            analysis.addIssue(highIssue);
            analysis.addIssue(mediumIssue);
            analysis.addIssue(lowIssue);
            analysis.addIssue(infoIssue);
            
            assertThat(analysis.getTotalIssues()).isEqualTo(4);
            assertThat(analysis.getHighSeverityCount()).isEqualTo(1);
            assertThat(analysis.getMediumSeverityCount()).isEqualTo(1);
            assertThat(analysis.getLowSeverityCount()).isEqualTo(1);
            assertThat(analysis.getInfoSeverityCount()).isEqualTo(1);
        }

        @Test
        @DisplayName("should not count resolved issues in total")
        void shouldNotCountResolvedIssuesInTotal() {
            CodeAnalysisIssue resolvedIssue = new CodeAnalysisIssue();
            resolvedIssue.setSeverity(IssueSeverity.HIGH);
            resolvedIssue.setFilePath("Resolved.java");
            resolvedIssue.setResolved(true);
            
            CodeAnalysisIssue unresolvedIssue = new CodeAnalysisIssue();
            unresolvedIssue.setSeverity(IssueSeverity.HIGH);
            unresolvedIssue.setFilePath("Unresolved.java");
            unresolvedIssue.setResolved(false);
            
            analysis.addIssue(resolvedIssue);
            analysis.addIssue(unresolvedIssue);
            
            assertThat(analysis.getTotalIssues()).isEqualTo(1);
            assertThat(analysis.getResolvedCount()).isEqualTo(1);
        }

        @Test
        @DisplayName("should set issues list and update counts")
        void shouldSetIssuesListAndUpdateCounts() {
            CodeAnalysisIssue issue1 = new CodeAnalysisIssue();
            issue1.setSeverity(IssueSeverity.HIGH);
            issue1.setFilePath("Test1.java");
            issue1.setResolved(false);
            
            CodeAnalysisIssue issue2 = new CodeAnalysisIssue();
            issue2.setSeverity(IssueSeverity.MEDIUM);
            issue2.setFilePath("Test2.java");
            issue2.setResolved(false);
            
            List<CodeAnalysisIssue> issues = new ArrayList<>();
            issues.add(issue1);
            issues.add(issue2);
            
            analysis.setIssues(issues);
            
            assertThat(analysis.getIssues()).hasSize(2);
            assertThat(analysis.getTotalIssues()).isEqualTo(2);
            assertThat(analysis.getHighSeverityCount()).isEqualTo(1);
            assertThat(analysis.getMediumSeverityCount()).isEqualTo(1);
        }
    }

    @Nested
    @DisplayName("onUpdate")
    class OnUpdate {

        @Test
        @DisplayName("should update updatedAt timestamp")
        void shouldUpdateUpdatedAtTimestamp() {
            OffsetDateTime originalUpdatedAt = analysis.getUpdatedAt();
            
            try {
                Thread.sleep(10);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            
            analysis.onUpdate();
            
            assertThat(analysis.getUpdatedAt()).isAfterOrEqualTo(originalUpdatedAt);
        }
    }
}
