package org.rostilos.codecrow.webserver.internal.controller;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.dto.analysis.issue.IssueDTO;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueCategory;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.webserver.project.service.ProjectService;
import org.springframework.http.ResponseEntity;

import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class InternalAnalysisControllerTest {

    private static final long PROJECT_ID = 17L;
    private static final int ISSUE_COUNT = 37;

    @Mock private CodeAnalysisService codeAnalysisService;
    @Mock private ProjectService projectService;
    @Mock private Project project;

    private InternalAnalysisController controller;

    @BeforeEach
    void setUp() {
        controller = new InternalAnalysisController(codeAnalysisService, projectService);
    }

    @Test
    void analysisByIdReturnsEveryIssueWithCompleteIssueFields() {
        CodeAnalysis analysis = analysisWithIssues();
        when(project.getId()).thenReturn(PROJECT_ID);
        when(codeAnalysisService.findById(41L)).thenReturn(Optional.of(analysis));

        ResponseEntity<Map<String, Object>> response =
                controller.getAnalysisById(41L, PROJECT_ID);

        assertCompleteIssueResponse(response);
    }

    @Test
    void analysisByPullRequestReturnsEveryIssue() {
        CodeAnalysis analysis = analysisWithIssues();
        when(codeAnalysisService.findByProjectIdAndPrNumber(PROJECT_ID, 8087L))
                .thenReturn(Optional.of(analysis));

        ResponseEntity<Map<String, Object>> response =
                controller.getAnalysisByPr(8087L, PROJECT_ID, null);

        assertCompleteIssueResponse(response);
    }

    private CodeAnalysis analysisWithIssues() {
        CodeAnalysis analysis = new CodeAnalysis();
        analysis.setProject(project);
        analysis.setBranchName("main");
        analysis.setPrNumber(8087L);
        analysis.setCommitHash("abc123");

        for (int index = 0; index < ISSUE_COUNT; index++) {
            CodeAnalysisIssue issue = new CodeAnalysisIssue();
            issue.setSeverity(index % 2 == 0 ? IssueSeverity.HIGH : IssueSeverity.MEDIUM);
            issue.setIssueCategory(index % 2 == 0 ? IssueCategory.SECURITY : IssueCategory.BUG_RISK);
            issue.setTitle("issue-" + index);
            issue.setReason("complete-reason-" + index);
            issue.setFilePath("src/File" + index + ".java");
            issue.setLineNumber(index + 1);
            issue.setSuggestedFixDescription("complete-fix-" + index);
            issue.setSuggestedFixDiff("complete-diff-" + index);
            analysis.addIssue(issue);
        }
        return analysis;
    }

    @SuppressWarnings("unchecked")
    private void assertCompleteIssueResponse(ResponseEntity<Map<String, Object>> response) {
        assertThat(response.getStatusCode().value()).isEqualTo(200);
        assertThat(response.getBody()).isNotNull();

        Map<String, Object> body = response.getBody();
        assertThat(body)
                .containsEntry("issuesComplete", true)
                .containsEntry("issueCount", ISSUE_COUNT)
                .doesNotContainKey("topIssues");

        List<IssueDTO> issues = (List<IssueDTO>) body.get("issues");
        assertThat(issues).hasSize(ISSUE_COUNT);
        assertThat(issues.get(ISSUE_COUNT - 1).title()).isEqualTo("issue-36");
        assertThat(issues.get(ISSUE_COUNT - 1).description()).isEqualTo("complete-reason-36");
        assertThat(issues.get(ISSUE_COUNT - 1).suggestedFixDescription()).isEqualTo("complete-fix-36");
        assertThat(issues.get(ISSUE_COUNT - 1).suggestedFixDiff()).isEqualTo("complete-diff-36");
    }
}
