package org.rostilos.codecrow.analysisengine.characterization.p002;

import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.aiclient.AiAnalysisClient;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.processor.analysis.PullRequestAnalysisProcessor;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.analysisengine.service.AstScopeEnricher;
import org.rostilos.codecrow.analysisengine.service.PullRequestService;
import org.rostilos.codecrow.analysisengine.service.pr.PrIssueTrackingService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsReportingService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.commitgraph.service.AnalyzedCommitService;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisResult;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisStatus;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueCategory;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisIssueRepository;
import org.rostilos.codecrow.core.persistence.repository.codeanalysis.CodeAnalysisRepository;
import org.rostilos.codecrow.core.persistence.repository.qualitygate.QualityGateRepository;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.core.service.IssueDeduplicationService;
import org.rostilos.codecrow.core.service.qualitygate.QualityGateEvaluator;
import org.rostilos.codecrow.events.analysis.AnalysisCompletedEvent;
import org.rostilos.codecrow.filecontent.service.FileSnapshotService;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.springframework.context.ApplicationEventPublisher;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@Tag("legacy-defect")
class PullRequestLifecycleLegacyCharacterizationTest {

    @Test
    void fingerprintHitClonesStaleFieldsAndBypassesTheLlmProducer() throws Exception {
        CodeAnalysisRepository analysisRepository = mock(CodeAnalysisRepository.class);
        CodeAnalysisService codeAnalysisService = new CodeAnalysisService(
                analysisRepository,
                mock(CodeAnalysisIssueRepository.class),
                mock(QualityGateRepository.class),
                mock(QualityGateEvaluator.class),
                new IssueDeduplicationService());

        Project project = mock(Project.class);
        VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
        VcsConnection connection = mock(VcsConnection.class);
        when(project.getId()).thenReturn(1L);
        when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
        when(repoInfo.getVcsConnection()).thenReturn(connection);
        when(connection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);

        CodeAnalysis staleSource = staleSourceAnalysis(project);
        when(analysisRepository.findByProjectIdAndCommitHashAndPrNumber(1L, "new-head", 42L))
                .thenReturn(Optional.empty());
        when(analysisRepository.findTopByProjectIdAndCommitHash(1L, "new-head"))
                .thenReturn(Optional.empty());
        when(analysisRepository.findAllByProjectIdAndPrNumberOrderByPrVersionDesc(1L, 42L))
                .thenReturn(List.of());
        when(analysisRepository.findTopByProjectIdAndDiffFingerprint(eq(1L), anyString()))
                .thenReturn(Optional.of(staleSource));
        when(analysisRepository.findMaxPrVersion(1L, 42L)).thenReturn(Optional.of(0));
        when(analysisRepository.save(any(CodeAnalysis.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        PullRequestService pullRequestService = mock(PullRequestService.class);
        PullRequest currentPullRequest = mock(PullRequest.class);
        PullRequest sourcePullRequest = mock(PullRequest.class);
        when(currentPullRequest.getId()).thenReturn(100L);
        when(sourcePullRequest.getId()).thenReturn(700L);
        when(pullRequestService.createOrUpdatePullRequest(
                eq(1L), eq(42L), eq("new-head"), eq("feature"), eq("main"), eq(project)))
                .thenReturn(currentPullRequest);
        when(pullRequestService.findPullRequest(1L, 77L)).thenReturn(Optional.of(sourcePullRequest));

        FileSnapshotService fileSnapshotService = mock(FileSnapshotService.class);
        when(fileSnapshotService.getFileContentsMapForPr(700L))
                .thenReturn(Map.of("src/Legacy.java", "legacy source"));

        VcsServiceFactory vcsServiceFactory = mock(VcsServiceFactory.class);
        VcsReportingService reportingService = mock(VcsReportingService.class);
        VcsAiClientService aiClientService = mock(VcsAiClientService.class);
        when(vcsServiceFactory.getReportingService(EVcsProvider.BITBUCKET_CLOUD))
                .thenReturn(reportingService);
        when(vcsServiceFactory.getAiClientService(EVcsProvider.BITBUCKET_CLOUD))
                .thenReturn(aiClientService);

        AiAnalysisRequest requestToLlm = mock(AiAnalysisRequest.class);
        when(requestToLlm.getRawDiff()).thenReturn("+new line\n-old line\n");
        when(requestToLlm.getChangedFiles()).thenReturn(List.of("src/New.java"));
        when(aiClientService.buildAiAnalysisRequests(eq(project), any(), any(), any()))
                .thenReturn(List.of(requestToLlm));

        AnalysisLockService lockService = mock(AnalysisLockService.class);
        when(lockService.acquireLockWithWait(
                eq(project), eq("feature"), any(), eq("new-head"), eq(42L), any()))
                .thenReturn(Optional.of("legacy-lock"));

        AiAnalysisClient llmProducer = mock(AiAnalysisClient.class);
        PullRequestAnalysisProcessor processor = new PullRequestAnalysisProcessor(
                pullRequestService,
                codeAnalysisService,
                llmProducer,
                vcsServiceFactory,
                lockService,
                mock(AnalyzedCommitService.class),
                mock(VcsClientProvider.class),
                fileSnapshotService,
                mock(PrIssueTrackingService.class),
                mock(AstScopeEnricher.class),
                null,
                null);

        PrProcessRequest processRequest = new PrProcessRequest();
        processRequest.projectId = 1L;
        processRequest.pullRequestId = 42L;
        processRequest.commitHash = "new-head";
        processRequest.sourceBranchName = "feature";
        processRequest.targetBranchName = "main";

        Map<String, Object> result = processor.process(processRequest, ignored -> { }, project);

        org.mockito.ArgumentCaptor<CodeAnalysis> posted =
                org.mockito.ArgumentCaptor.forClass(CodeAnalysis.class);
        verify(reportingService).postAnalysisResults(
                posted.capture(), eq(project), eq(42L), eq(100L), any());
        CodeAnalysis cloned = posted.getValue();

        assertThat(result).containsEntry("status", "cached_by_fingerprint");
        assertThat(cloned.getAnalysisType()).isEqualTo(AnalysisType.BRANCH_ANALYSIS);
        assertThat(cloned.getTaskId()).isEqualTo("OLD-1");
        assertThat(cloned.getComment()).isEqualTo("stale branch review");
        assertThat(cloned.getStatus()).isEqualTo(AnalysisStatus.REJECTED);
        assertThat(cloned.getAnalysisResult()).isEqualTo(AnalysisResult.FAILED);
        assertThat(cloned.getIssues()).singleElement().satisfies(issue -> {
            assertThat(issue.getFilePath()).isEqualTo("src/Legacy.java");
            assertThat(issue.getReason()).isEqualTo("old target-branch reasoning");
            assertThat(issue.isResolved()).isTrue();
            assertThat(issue.getResolvedDescription()).isEqualTo("old resolution state");
        });
        verify(llmProducer, never()).performAnalysis(any(), any());
        verify(lockService).releaseLock("legacy-lock");
    }

    @Test
    void firstRequestOnlyAndDeliveryFailureStillCompletesSuccessfully() throws Exception {
        Project project = mock(Project.class);
        VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
        VcsConnection connection = mock(VcsConnection.class);
        Workspace workspace = mock(Workspace.class);
        when(project.getId()).thenReturn(1L);
        when(project.getName()).thenReturn("project");
        when(project.getNamespace()).thenReturn("namespace");
        when(project.getWorkspace()).thenReturn(workspace);
        when(workspace.getName()).thenReturn("workspace");
        when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
        when(repoInfo.getVcsConnection()).thenReturn(connection);
        when(connection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);

        PullRequestService pullRequestService = mock(PullRequestService.class);
        PullRequest pullRequest = mock(PullRequest.class);
        when(pullRequest.getId()).thenReturn(100L);
        when(pullRequestService.createOrUpdatePullRequest(
                eq(1L), eq(42L), eq("new-head"), eq("feature"), eq("main"), eq(project)))
                .thenReturn(pullRequest);

        CodeAnalysisService codeAnalysisService = mock(CodeAnalysisService.class);
        when(codeAnalysisService.getCodeAnalysisCache(1L, "new-head", 42L))
                .thenReturn(Optional.empty());
        when(codeAnalysisService.getAnalysisByCommitHash(1L, "new-head"))
                .thenReturn(Optional.empty());
        when(codeAnalysisService.getAllPrAnalyses(1L, 42L)).thenReturn(List.of());
        when(codeAnalysisService.getAnalysisByDiffFingerprint(eq(1L), anyString()))
                .thenReturn(Optional.empty());

        AiAnalysisRequest first = mock(AiAnalysisRequest.class);
        AiAnalysisRequest second = mock(AiAnalysisRequest.class);
        when(first.getRawDiff()).thenReturn("+first request\n");
        when(first.getChangedFiles()).thenReturn(List.of());

        VcsServiceFactory vcsServiceFactory = mock(VcsServiceFactory.class);
        VcsReportingService reportingService = mock(VcsReportingService.class);
        VcsAiClientService aiClientService = mock(VcsAiClientService.class);
        when(vcsServiceFactory.getReportingService(EVcsProvider.BITBUCKET_CLOUD))
                .thenReturn(reportingService);
        when(vcsServiceFactory.getAiClientService(EVcsProvider.BITBUCKET_CLOUD))
                .thenReturn(aiClientService);
        when(aiClientService.buildAiAnalysisRequests(eq(project), any(), any(), any()))
                .thenReturn(List.of(first, second));

        AnalysisLockService lockService = mock(AnalysisLockService.class);
        when(lockService.acquireLockWithWait(
                eq(project), eq("feature"), any(), eq("new-head"), eq(42L), any()))
                .thenReturn(Optional.of("legacy-lock"));

        AiAnalysisClient llmProducer = mock(AiAnalysisClient.class);
        Map<String, Object> firstResponse = Map.of("comment", "first", "issues", List.of());
        when(llmProducer.performAnalysis(eq(first), any())).thenReturn(firstResponse);
        CodeAnalysis createdAnalysis = new CodeAnalysis();
        when(codeAnalysisService.createAnalysisFromAiResponse(
                eq(project), eq(firstResponse), eq(42L), eq("main"), eq("feature"),
                eq("new-head"), any(), any(), anyString(), any(), any(), any()))
                .thenReturn(createdAnalysis);
        doThrow(new IOException("legacy delivery failure"))
                .when(reportingService)
                .postAnalysisResults(eq(createdAnalysis), eq(project), eq(42L), eq(100L), any());

        AnalyzedCommitService analyzedCommitService = mock(AnalyzedCommitService.class);
        List<Object> publishedEvents = new ArrayList<>();
        ApplicationEventPublisher eventPublisher = publishedEvents::add;

        PullRequestAnalysisProcessor processor = new PullRequestAnalysisProcessor(
                pullRequestService,
                codeAnalysisService,
                llmProducer,
                vcsServiceFactory,
                lockService,
                analyzedCommitService,
                mock(VcsClientProvider.class),
                mock(FileSnapshotService.class),
                mock(PrIssueTrackingService.class),
                mock(AstScopeEnricher.class),
                null,
                eventPublisher);

        PrProcessRequest processRequest = new PrProcessRequest();
        processRequest.projectId = 1L;
        processRequest.pullRequestId = 42L;
        processRequest.commitHash = "new-head";
        processRequest.sourceBranchName = "feature";
        processRequest.targetBranchName = "main";

        PullRequestAnalysisProcessor.EventConsumer consumer =
                mock(PullRequestAnalysisProcessor.EventConsumer.class);
        Map<String, Object> result = processor.process(processRequest, consumer, project);

        assertThat(result).isSameAs(firstResponse);
        verify(llmProducer).performAnalysis(eq(first), any());
        verify(llmProducer, never()).performAnalysis(eq(second), any());
        verify(consumer).accept(org.mockito.ArgumentMatchers.argThat(
                event -> "warning".equals(event.get("type"))));
        verify(analyzedCommitService).recordPrCommitsAnalyzed(
                project, List.of("new-head"), createdAnalysis);

        assertThat(publishedEvents)
                .filteredOn(AnalysisCompletedEvent.class::isInstance)
                .singleElement()
                .satisfies(event -> assertThat(((AnalysisCompletedEvent) event).getStatus())
                        .isEqualTo(AnalysisCompletedEvent.CompletionStatus.SUCCESS));
        verify(lockService).releaseLock("legacy-lock");
    }

    private static CodeAnalysis staleSourceAnalysis(Project project) {
        CodeAnalysis source = new CodeAnalysis();
        source.setProject(project);
        source.setAnalysisType(AnalysisType.BRANCH_ANALYSIS);
        source.setPrNumber(77L);
        source.setCommitHash("old-head");
        source.setDiffFingerprint("old-fingerprint");
        source.setBranchName("release/old");
        source.setSourceBranchName("feature/old");
        source.setTaskId("OLD-1");
        source.setTaskSummary("old task context");
        source.setComment("stale branch review");
        source.setStatus(AnalysisStatus.REJECTED);
        source.setAnalysisResult(AnalysisResult.FAILED);

        CodeAnalysisIssue issue = new CodeAnalysisIssue();
        issue.setSeverity(IssueSeverity.HIGH);
        issue.setFilePath("src/Legacy.java");
        issue.setLineNumber(9);
        issue.setReason("old target-branch reasoning");
        issue.setTitle("legacy issue");
        issue.setIssueCategory(IssueCategory.BUG_RISK);
        issue.setResolved(true);
        issue.setResolvedDescription("old resolution state");
        source.addIssue(issue);
        return source;
    }
}
