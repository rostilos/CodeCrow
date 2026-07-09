package org.rostilos.codecrow.pipelineagent.generic.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.model.pullrequest.PullRequestState;
import org.rostilos.codecrow.core.model.qadoc.QaDocDocument;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.core.persistence.repository.pullrequest.PullRequestRepository;
import org.rostilos.codecrow.core.service.QaDocDocumentService;
import org.springframework.data.domain.PageRequest;

import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
@DisplayName("TaskHistoryContextService")
class TaskHistoryContextServiceTest {

    @Mock
    private CodeAnalysisRepository codeAnalysisRepository;

    @Mock
    private PullRequestRepository pullRequestRepository;

    @Mock
    private QaDocDocumentService qaDocDocumentService;

    private TaskHistoryContextService service;

    @BeforeEach
    void setUp() {
        service = new TaskHistoryContextService(
                codeAnalysisRepository,
                pullRequestRepository,
                qaDocDocumentService);
    }

    @Test
    @DisplayName("should build bounded context from prior analyses and QA docs")
    void shouldBuildBoundedContextFromPriorAnalysesAndQaDocs() {
        CodeAnalysis analysis = new CodeAnalysis();
        analysis.setPrNumber(41L);
        analysis.setTaskId("PROJ-123");
        analysis.setTaskSummary("Reopened checkout");
        analysis.setSourceBranchName("feature/PROJ-123-checkout");
        analysis.setBranchName("main");
        analysis.setCommitHash("abcdef1234567890");
        analysis.setComment("Review summary: discount checkout behavior was implemented in the previous PR.");

        CodeAnalysisIssue issue = new CodeAnalysisIssue();
        issue.setSeverity(IssueSeverity.MEDIUM);
        issue.setTitle("Verify totals after discount application");
        issue.setFilePath("src/Checkout.java");
        issue.setLineNumber(12);
        analysis.addIssue(issue);

        PullRequest pullRequest = new PullRequest();
        pullRequest.setPrNumber(41L);
        pullRequest.setState(PullRequestState.MERGED);

        QaDocDocument document = new QaDocDocument(null, 41L);
        document.setTaskId("PROJ-123");
        document.setMarkdownContent("# QA Testing Guide\n\nDiscount checkout scenarios are covered.");

        when(codeAnalysisRepository.findLatestPrAnalysesByProjectIdAndTaskId(
                1L, "PROJ-123", 99L, PageRequest.of(0, 5)))
                .thenReturn(List.of(analysis));
        when(qaDocDocumentService.findDocumentsForTask(1L, "PROJ-123", 99L, 3))
                .thenReturn(List.of(document));
        when(pullRequestRepository.findByProject_IdAndPrNumberIn(eq(1L), anyList()))
                .thenReturn(List.of(pullRequest));

        String context = service.buildTaskHistoryContext(
                1L,
                99L,
                Map.of("task_key", "PROJ-123", "task_summary", "Reopened checkout"));

        assertThat(context).contains("Prior Task Implementation Context");
        assertThat(context).contains("PR #41 (MERGED)");
        assertThat(context).contains("discount checkout behavior was implemented");
        assertThat(context).contains("Discount checkout scenarios are covered");
        assertThat(context.length()).isLessThanOrEqualTo(7_000);
        verify(codeAnalysisRepository).findLatestPrAnalysesByProjectIdAndTaskId(
                1L, "PROJ-123", 99L, PageRequest.of(0, 5));
        verify(qaDocDocumentService).findDocumentsForTask(1L, "PROJ-123", 99L, 3);
    }

    @Test
    @DisplayName("should build context from fallback task key when full task context is unavailable")
    void shouldBuildContextFromFallbackTaskKeyWhenFullTaskContextIsUnavailable() {
        CodeAnalysis analysis = new CodeAnalysis();
        analysis.setPrNumber(42L);
        analysis.setTaskId("PROJ-456");
        analysis.setSourceBranchName("feature/PROJ-456-cart");
        analysis.setBranchName("main");
        analysis.setCommitHash("1234567890abcdef");
        analysis.setComment("Review summary: cart validation was already covered.");

        PullRequest pullRequest = new PullRequest();
        pullRequest.setPrNumber(42L);
        pullRequest.setState(PullRequestState.MERGED);

        when(codeAnalysisRepository.findLatestPrAnalysesByProjectIdAndTaskId(
                1L, "PROJ-456", 99L, PageRequest.of(0, 5)))
                .thenReturn(List.of(analysis));
        when(qaDocDocumentService.findDocumentsForTask(1L, "PROJ-456", 99L, 3))
                .thenReturn(List.of());
        when(pullRequestRepository.findByProject_IdAndPrNumberIn(eq(1L), anyList()))
                .thenReturn(List.of(pullRequest));

        String context = service.buildTaskHistoryContext(
                1L,
                99L,
                Map.of(),
                "PROJ-456");

        assertThat(context).contains("Task: PROJ-456");
        assertThat(context).contains("PR #42 (MERGED)");
        assertThat(context).contains("cart validation was already covered");
    }

    @Test
    @DisplayName("should skip repository lookups when task key is absent")
    void shouldSkipRepositoryLookupsWhenTaskKeyIsAbsent() {
        String context = service.buildTaskHistoryContext(1L, 99L, Map.of());

        assertThat(context).isEmpty();
        verifyNoInteractions(codeAnalysisRepository, pullRequestRepository, qaDocDocumentService);
    }
}
