package org.rostilos.codecrow.webserver.analysis.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueCategory;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.core.persistence.repository.pullrequest.PullRequestRepository;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.filecontent.service.FileSnapshotService;
import org.rostilos.codecrow.webserver.analysis.dto.response.FileViewResponse;

import java.lang.reflect.Field;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class FileViewServiceTest {

    @Mock private FileSnapshotService fileSnapshotService;
    @Mock private CodeAnalysisService codeAnalysisService;
    @Mock private PullRequestRepository pullRequestRepository;
    @Mock private BranchRepository branchRepository;
    @Mock private BranchIssueRepository branchIssueRepository;

    private FileViewService service;

    @BeforeEach
    void setUp() {
        service = new FileViewService(
                fileSnapshotService,
                codeAnalysisService,
                pullRequestRepository,
                branchRepository,
                branchIssueRepository);
    }

    @Test
    void prFileViewLabelsHistoricalTipsWithoutMixingThemWithCurrentFindings() throws Exception {
        PullRequest pullRequest = new PullRequest();
        pullRequest.setId(9L);
        pullRequest.setPrNumber(42L);
        pullRequest.setCommitHash("current-head");
        CodeAnalysisIssue current = issue(400L, 2, "Current defect");
        CodeAnalysisIssue historical = issue(100L, 4, "Historical defect");
        CodeAnalysisService.PrIssueHistoryProjection projection =
                new CodeAnalysisService.PrIssueHistoryProjection(
                        List.of(current), List.of(historical));

        when(pullRequestRepository.findByPrNumberAndProject_id(42L, 7L))
                .thenReturn(Optional.of(pullRequest));
        when(fileSnapshotService.getFileContentForPr(9L, "src/App.java"))
                .thenReturn(Optional.of("one\ntwo\nthree\nfour\n"));
        when(codeAnalysisService.projectPrIssueHistory(7L, 42L))
                .thenReturn(projection);

        FileViewResponse result = service.getPrFileView(7L, 42L, "src/App.java")
                .orElseThrow();

        assertThat(result.issues()).extracting(FileViewResponse.InlineIssue::issueId)
                .containsExactly(400L, 100L);
        assertThat(result.issues()).extracting(FileViewResponse.InlineIssue::historicalNotRevalidated)
                .containsExactly(false, true);
    }

    private static CodeAnalysisIssue issue(Long id, int line, String title) throws Exception {
        CodeAnalysisIssue issue = new CodeAnalysisIssue();
        Field idField = CodeAnalysisIssue.class.getDeclaredField("id");
        idField.setAccessible(true);
        idField.set(issue, id);
        issue.setFilePath("src/App.java");
        issue.setLineNumber(line);
        issue.setTitle(title);
        issue.setReason(title + " remains active");
        issue.setSeverity(IssueSeverity.HIGH);
        issue.setIssueCategory(IssueCategory.BUG_RISK);
        return issue;
    }
}
