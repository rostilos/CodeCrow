package org.rostilos.codecrow.core.dto.analysis;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.OffsetDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("ProjectAnalysisSummary")
class ProjectAnalysisSummaryTest {

    @Test
    @DisplayName("should set and get projectName")
    void shouldSetAndGetProjectName() {
        ProjectAnalysisSummary summary = new ProjectAnalysisSummary();
        summary.setProjectName("test-project");
        
        assertThat(summary.getProjectName()).isEqualTo("test-project");
    }

    @Test
    @DisplayName("should set and get lastAnalysisAt")
    void shouldSetAndGetLastAnalysisAt() {
        ProjectAnalysisSummary summary = new ProjectAnalysisSummary();
        OffsetDateTime now = OffsetDateTime.now();
        summary.setLastAnalysisAt(now);
        
        assertThat(summary.getLastAnalysisAt()).isEqualTo(now);
    }

    @Test
    @DisplayName("should set and get totalIssues")
    void shouldSetAndGetTotalIssues() {
        ProjectAnalysisSummary summary = new ProjectAnalysisSummary();
        summary.setTotalIssues(42);
        
        assertThat(summary.getTotalIssues()).isEqualTo(42);
    }

    @Test
    @DisplayName("should set and get criticalIssues")
    void shouldSetAndGetCriticalIssues() {
        ProjectAnalysisSummary summary = new ProjectAnalysisSummary();
        summary.setCriticalIssues(5);
        
        assertThat(summary.getCriticalIssues()).isEqualTo(5);
    }

    @Test
    @DisplayName("should set and get totalPullRequests")
    void shouldSetAndGetTotalPullRequests() {
        ProjectAnalysisSummary summary = new ProjectAnalysisSummary();
        summary.setTotalPullRequests(100);
        
        assertThat(summary.getTotalPullRequests()).isEqualTo(100);
    }

    @Test
    @DisplayName("should set and get openPullRequests")
    void shouldSetAndGetOpenPullRequests() {
        ProjectAnalysisSummary summary = new ProjectAnalysisSummary();
        summary.setOpenPullRequests(15);
        
        assertThat(summary.getOpenPullRequests()).isEqualTo(15);
    }

    @Test
    @DisplayName("should set and get branches")
    void shouldSetAndGetBranches() {
        ProjectAnalysisSummary summary = new ProjectAnalysisSummary();
        BranchSummary branch = new BranchSummary();
        branch.setName("main");
        branch.setIssueCount(10);
        branch.setCriticalIssueCount(2);
        summary.setBranches(List.of(branch));
        
        assertThat(summary.getBranches()).hasSize(1);
        assertThat(summary.getBranches().get(0).getName()).isEqualTo("main");
    }

    @Test
    @DisplayName("should initialize with empty branches list")
    void shouldInitializeWithEmptyBranchesList() {
        ProjectAnalysisSummary summary = new ProjectAnalysisSummary();
        
        assertThat(summary.getBranches()).isNotNull().isEmpty();
    }

    @Test
    @DisplayName("should initialize with trends")
    void shouldInitializeWithTrends() {
        ProjectAnalysisSummary summary = new ProjectAnalysisSummary();
        
        assertThat(summary.getTrends()).isNotNull();
    }

    @Test
    @DisplayName("should set and get trends")
    void shouldSetAndGetTrends() {
        ProjectAnalysisSummary summary = new ProjectAnalysisSummary();
        ProjectTrends trends = new ProjectTrends();
        trends.setNewIssuesLast7Days(10);
        summary.setTrends(trends);
        
        assertThat(summary.getTrends()).isNotNull();
        assertThat(summary.getTrends().getNewIssuesLast7Days()).isEqualTo(10);
    }
}
