package org.rostilos.codecrow.webserver.internal.controller;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.dto.analysis.issue.IssueDTO;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueCategory;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.webserver.analysis.service.AnalysisService;
import org.springframework.http.ResponseEntity;

import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class InternalIssueControllerTest {

    @Test
    void searchReturnsEveryMatchingIssueWithoutAHiddenMaximum() {
        AnalysisService analysisService = mock(AnalysisService.class);
        InternalIssueController controller = new InternalIssueController(analysisService);
        List<CodeAnalysisIssue> persistedIssues = issues(247);
        when(analysisService.findIssues(17L, "main", "8087", "HIGH", "SECURITY", 0))
                .thenReturn(persistedIssues);

        ResponseEntity<List<IssueDTO>> response = controller.searchIssues(
                17L, "HIGH", "SECURITY", "main", "8087");

        assertThat(response.getStatusCode().value()).isEqualTo(200);
        assertThat(response.getBody()).hasSize(247);
        assertThat(response.getBody().get(246).suggestedFixDiff()).isEqualTo("diff-246");
        verify(analysisService).findIssues(17L, "main", "8087", "HIGH", "SECURITY", 0);
    }

    private List<CodeAnalysisIssue> issues(int count) {
        CodeAnalysis analysis = new CodeAnalysis();
        analysis.setBranchName("main");
        analysis.setPrNumber(8087L);
        List<CodeAnalysisIssue> issues = new ArrayList<>();
        for (int index = 0; index < count; index++) {
            CodeAnalysisIssue issue = new CodeAnalysisIssue();
            issue.setAnalysis(analysis);
            issue.setSeverity(IssueSeverity.HIGH);
            issue.setIssueCategory(IssueCategory.SECURITY);
            issue.setTitle("issue-" + index);
            issue.setReason("reason-" + index);
            issue.setFilePath("src/File" + index + ".java");
            issue.setSuggestedFixDiff("diff-" + index);
            issues.add(issue);
        }
        return issues;
    }
}
