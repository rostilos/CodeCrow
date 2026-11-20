package org.rostilos.codecrow.webserver;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.codeanalysis.*;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.persistence.repository.workspace.WorkspaceRepository;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.test.util.ReflectionTestUtils;

import java.time.OffsetDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.hamcrest.Matchers.*;

class ProjectAnalyticsControllerIT extends BaseWebServerIT {
    @Autowired private WorkspaceRepository workspaceRepository;
    @Autowired private ProjectRepository projectRepository;
    @Autowired private CodeAnalysisRepository analysisRepository;
    @Autowired private CodeAnalysisService codeAnalysisService;

    private Project project;
    private CodeAnalysis completed;
    private CodeAnalysis partial;
    private String base;

    @BeforeEach
    void setupAnalyses() {
        createTestUser("reviewer", "reviewer@example.com", "password123");
        authenticatedRequest("reviewer").body("""
                {"slug":"review-history","name":"Review history"}
                """).post("/api/workspace/create").then().statusCode(201);
        project = createProject("mixed");
        base = "/api/review-history/projects/mixed/analysis";
        completed = analysis(project, AnalysisStatus.ACCEPTED, 42L, 1, "main", 4, IssueSeverity.HIGH);
        analysis(project, AnalysisStatus.PARTIAL, 99L, 1, "main", 3, IssueSeverity.HIGH);
        analysis(project, AnalysisStatus.ACCEPTED, 84L, 1, "feature", 2, IssueSeverity.MEDIUM);
        partial = analysis(project, AnalysisStatus.PARTIAL, 42L, 2, "main", 1,
                IssueSeverity.HIGH, IssueSeverity.HIGH, IssueSeverity.LOW);
        partial.getIssues().get(2).setResolved(true);
        partial.updateIssueCounts();
        partial = analysisRepository.saveAndFlush(partial);
    }

    @Test
    void partialHistoryIsPaginatedAndScopedWithoutMixingRegularReports() {
        authenticatedRequest("reviewer").get(base + "/history").then()
                .statusCode(200).body("totalElements", equalTo(2))
                .body("analyses.status", everyItem(equalTo("accepted")));
        authenticatedRequest("reviewer").queryParam("status", "PARTIAL").queryParam("pageSize", 1)
                .get(base + "/history").then().statusCode(200)
                .body("totalElements", equalTo(2)).body("totalPages", equalTo(2))
                .body("analyses", hasSize(1)).body("analyses[0].id", equalTo(partial.getId().toString()));
        authenticatedRequest("reviewer").queryParam("status", "PARTIAL").queryParam("pageSize", 1)
                .queryParam("page", 2).get(base + "/history").then().statusCode(200)
                .body("currentPage", equalTo(2)).body("analyses[0].pullRequestId", equalTo("99"));
        authenticatedRequest("reviewer").queryParam("status", "PARTIAL").queryParam("prNumber", 42)
                .get(base + "/history").then().statusCode(200).body("totalElements", equalTo(1));
        authenticatedRequest("reviewer").queryParam("status", "PARTIAL").queryParam("branch", "feature")
                .get(base + "/history").then().statusCode(200).body("analyses", empty());
    }

    @Test
    void metricsRecentReportsAndTrendsExcludePartialRunsAndTheirFindings() {
        authenticatedRequest("reviewer").get(base + "/detailed-stats").then().statusCode(200)
                .body("totalIssues", equalTo(2)).body("highIssues", equalTo(1))
                .body("mediumIssues", equalTo(1)).body("lowIssues", equalTo(0))
                .body("openIssuesCount", equalTo(2)).body("resolvedIssuesCount", equalTo(0))
                .body("issuesByType.security", equalTo(2))
                .body("recentAnalyses", hasSize(2))
                .body("recentAnalyses.status", everyItem(equalTo("completed")))
                .body("topFiles.file", not(hasItem("/src/partial.java")))
                .body("trend", equalTo("stable"));
        authenticatedRequest("reviewer").get(base + "/trends/resolved").then().statusCode(200)
                .body("$", hasSize(2)).body("totalIssues", everyItem(equalTo(1)));
        CodeAnalysisService.AnalysisStats stats = codeAnalysisService.getProjectAnalysisStats(project.getId());
        assertThat(stats.getTotalAnalyses()).isEqualTo(2);
        assertThat(stats.getAverageIssuesPerAnalysis()).isEqualTo(1);
    }

    @Test
    void partialReportKeepsTheExactSavedVersionSummaryAndAllFindings() {
        authenticatedRequest("reviewer").get(base + "/history/" + partial.getId()).then().statusCode(200)
                .body("analysis.status", equalTo("partial"))
                .body("analysis.prVersion", equalTo(2))
                .body("analysis.commitHash", equalTo(partial.getCommitHash()))
                .body("summary", equalTo("Review stopped before all files were checked."))
                .body("issues", hasSize(3))
                .body("issues.analysisId", everyItem(equalTo(partial.getId().intValue())));
    }

    @Test
    void selectedPrReportIncludesPartialVersionsAndKeepsSavedContentAccessible() {
        authenticatedRequest("reviewer").queryParam("pullRequestId", 42)
                .get("/api/review-history/project/mixed/analysis/issues").then().statusCode(200)
                .body("availableVersions", contains(2, 1)).body("partialVersions", contains(2))
                .body("maxVersion", equalTo(2)).body("currentVersion", equalTo(2))
                .body("analysisStatus", equalTo("PARTIAL")).body("issues", hasSize(3))
                .body("analysisSummary", equalTo(partial.getComment()))
                .body("commitHash", equalTo(partial.getCommitHash()));
        authenticatedRequest("reviewer").queryParam("pullRequestId", 42).queryParam("prVersion", 1)
                .get("/api/review-history/project/mixed/analysis/issues").then().statusCode(200)
                .body("currentVersion", equalTo(1)).body("analysisStatus", equalTo("ACCEPTED"))
                .body("issues", hasSize(1))
                .body("commitHash", equalTo(completed.getCommitHash()));
        authenticatedRequest("reviewer").queryParam("pullRequestId", 99)
                .get("/api/review-history/project/mixed/analysis/issues").then().statusCode(200)
                .body("availableVersions", contains(1)).body("partialVersions", contains(1))
                .body("analysisStatus", equalTo("PARTIAL")).body("issues", hasSize(1));
        assertThat(analysisRepository.findLatestAnalysisForPrNumbers(project.getId(), List.of(42L)))
                .extracting(CodeAnalysis::getId).containsExactly(completed.getId());
        assertThat(codeAnalysisService.getMaxAnalysisPrVersion(project.getId(), 42L)).isEqualTo(2);
    }

    @Test
    void historyAndReportDetailsEnforceWorkspaceAndProjectIsolation() {
        Project other = createProject("other");
        CodeAnalysis foreign = analysis(other, AnalysisStatus.PARTIAL, 42L, 1, "main", 1, IssueSeverity.HIGH);
        authenticatedRequest("reviewer").get(base + "/history/" + foreign.getId()).then().statusCode(404);
        createTestUser("stranger", "stranger@example.com", "password123");
        authenticatedRequest("stranger").get(base + "/history?status=PARTIAL").then().statusCode(403);
        authenticatedRequest("stranger").get(base + "/history/" + partial.getId()).then().statusCode(403);
        unauthenticatedRequest().get(base + "/history/" + partial.getId()).then().statusCode(401);
        authenticatedRequest("reviewer").get("/api/review-history/projects/other/analysis/history?status=PARTIAL")
                .then().statusCode(200).body("analyses.id", contains(foreign.getId().toString()));
    }

    private Project createProject(String namespace) {
        Project result = new Project();
        result.setWorkspace(workspaceRepository.findBySlug("review-history").orElseThrow());
        result.setNamespace(namespace);
        result.setName(namespace);
        return projectRepository.saveAndFlush(result);
    }

    private CodeAnalysis analysis(Project owner, AnalysisStatus status, Long prNumber, int version,
                                  String branch, int daysAgo, IssueSeverity... severities) {
        CodeAnalysis result = new CodeAnalysis();
        result.setProject(owner);
        result.setAnalysisType(AnalysisType.PR_REVIEW);
        result.setStatus(status);
        result.setPrNumber(prNumber);
        result.setPrVersion(version);
        result.setBranchName(branch);
        result.setSourceBranchName("pr-" + prNumber);
        result.setCommitHash(String.format("%040d", prNumber * 10 + version));
        result.setComment(status == AnalysisStatus.PARTIAL ? "Review stopped before all files were checked." : "Completed review.");
        ReflectionTestUtils.setField(result, "createdAt", OffsetDateTime.now().minusDays(daysAgo));
        for (IssueSeverity severity : severities) {
            CodeAnalysisIssue issue = new CodeAnalysisIssue();
            issue.setSeverity(severity);
            issue.setIssueCategory(IssueCategory.SECURITY);
            issue.setFilePath(status == AnalysisStatus.PARTIAL ? "/src/partial.java" : "/src/completed.java");
            issue.setLineNumber(12);
            issue.setTitle("Saved finding");
            issue.setReason("Saved finding details");
            result.addIssue(issue);
        }
        return analysisRepository.saveAndFlush(result);
    }
}
