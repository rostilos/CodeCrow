package org.rostilos.codecrow.analysisengine.processor.analysis;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.aiclient.AiAnalysisClient;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.exception.AnalysisLockedException;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.analysisengine.service.PullRequestService;
import org.rostilos.codecrow.commitgraph.service.AnalyzedCommitService;
import org.rostilos.codecrow.analysisengine.service.rag.RagOperationsService;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsReportingService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.core.service.TaskImplementationEvidenceService;
import org.rostilos.codecrow.filecontent.service.FileSnapshotService;
import org.rostilos.codecrow.vcsclient.model.VcsCommit;
import org.rostilos.codecrow.analysisengine.service.AstScopeEnricher;
import org.rostilos.codecrow.analysisengine.service.pr.PrIssueTrackingService;
import org.rostilos.codecrow.analysisengine.util.PromptDryRunMode;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.test.util.ReflectionTestUtils;

import java.io.IOException;
import java.util.*;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@DisplayName("PullRequestAnalysisProcessor")
class PullRequestAnalysisProcessorTest {

        @Mock
        private PullRequestService pullRequestService;

        @Mock
        private CodeAnalysisService codeAnalysisService;

        @Mock
        private TaskImplementationEvidenceService taskImplementationEvidenceService;

        @Mock
        private AiAnalysisClient aiAnalysisClient;

        @Mock
        private VcsServiceFactory vcsServiceFactory;

        @Mock
        private AnalysisLockService analysisLockService;

        @Mock
        private AnalysisLockService.LockLease lockLease;

        @Mock
        private AnalyzedCommitService analyzedCommitService;

        @Mock
        private VcsClientProvider vcsClientProvider;

        @Mock
        private FileSnapshotService fileSnapshotService;

        @Mock
        private PrIssueTrackingService prIssueTrackingService;

        @Mock
        private AstScopeEnricher astScopeEnricher;

        @Mock
        private RagOperationsService ragOperationsService;

        @Mock
        private ApplicationEventPublisher eventPublisher;

        @Mock
        private VcsReportingService reportingService;

        @Mock
        private VcsAiClientService aiClientService;

        @Mock
        private Project project;

        @Mock
        private VcsConnection vcsConnection;

        @Mock
        private PullRequest pullRequest;

        @Mock
        private CodeAnalysis codeAnalysis;

        @Mock
        private AiAnalysisRequest aiAnalysisRequest;

        private PullRequestAnalysisProcessor processor;

        @BeforeEach
        void setUp() {
                System.setProperty(PromptDryRunMode.ENABLED_KEY, "false");
                System.clearProperty(PromptDryRunMode.PROJECT_IDS_KEY);
                lenient().when(taskImplementationEvidenceService.persistFromAnalysisResponse(
                                any(), any()))
                                .thenReturn(TaskImplementationEvidenceService.PersistenceResult.empty());
                lenient().when(taskImplementationEvidenceService.copyForAnalysis(any(), any()))
                                .thenReturn(TaskImplementationEvidenceService.PersistenceResult.empty());
                lenient().when(analysisLockService.getLeaseMinutes(AnalysisLockType.PR_ANALYSIS))
                                .thenReturn(30);
                lenient().when(analysisLockService.maintainLockLease(anyString(), eq(30)))
                                .thenReturn(lockLease);
                lenient().when(lockLease.isOwnershipLost()).thenReturn(false);
                lenient().when(lockLease.confirmOwnership()).thenReturn(true);
                processor = new PullRequestAnalysisProcessor(
                                pullRequestService,
                                codeAnalysisService,
                                taskImplementationEvidenceService,
                                aiAnalysisClient,
                                vcsServiceFactory,
                                analysisLockService,
                                analyzedCommitService,
                                vcsClientProvider,
                                fileSnapshotService,
                                prIssueTrackingService,
                                astScopeEnricher,
                                ragOperationsService,
                                eventPublisher);
        }

        @AfterEach
        void clearPromptDryRunProperties() {
                System.clearProperty(PromptDryRunMode.ENABLED_KEY);
                System.clearProperty(PromptDryRunMode.PROJECT_IDS_KEY);
        }

        private PrProcessRequest createRequest() {
                PrProcessRequest request = new PrProcessRequest();
                request.projectId = 1L;
                request.pullRequestId = 42L;
                request.commitHash = "abc123";
                request.sourceBranchName = "feature-branch";
                request.targetBranchName = "main";
                return request;
        }

        @Test
        @DisplayName("SCM evidence starts at the reviewed PR revision")
        void scmEvidenceExcludesCommitsPushedAfterReviewedHead() {
                List<VcsCommit> selected = PullRequestAnalysisProcessor.selectPrEvidenceCommits(
                                List.of(
                                                commit("newer-2"),
                                                commit("newer-1"),
                                                commit("reviewed-head"),
                                                commit("pr-parent"),
                                                commit("target-base"),
                                                commit("older")),
                                "reviewed-head",
                                "target-base");

                assertThat(selected).extracting(VcsCommit::hash)
                                .containsExactly("reviewed-head", "pr-parent");
        }

        @Test
        @DisplayName("SCM evidence stays empty when reviewed head is outside provider history")
        void scmEvidenceDoesNotClaimUnknownHistoryWindow() {
                List<VcsCommit> selected = PullRequestAnalysisProcessor.selectPrEvidenceCommits(
                                List.of(commit("newer"), commit("target-base")),
                                "reviewed-head",
                                "target-base");

                assertThat(selected).isEmpty();
        }

        @Test
        @DisplayName("SCM evidence keeps only reviewed head when PR base is outside provider history")
        void scmEvidenceDoesNotClaimCommitsPastUnknownBase() {
                List<VcsCommit> selected = PullRequestAnalysisProcessor.selectPrEvidenceCommits(
                                List.of(
                                                commit("reviewed-head"),
                                                commit("unknown-parent"),
                                                commit("older")),
                                "reviewed-head",
                                "target-base");

                assertThat(selected).extracting(VcsCommit::hash)
                                .containsExactly("reviewed-head");
        }

        private static VcsCommit commit(String hash) {
                return new VcsCommit(hash, hash, "author", "author@example.test", null, List.of());
        }

        @Nested
        @DisplayName("process()")
        class ProcessTests {

                @Test
                @DisplayName("should persist only the provider-canonicalized full PR head")
                void shouldPersistProviderCanonicalizedFullPrHead() throws Exception {
                        PrProcessRequest request = createRequest();
                        request.commitHash = "eb59a730e565";
                        String fullHead = "eb59a730e56532cc96d0e9fbb6b7616d6ca9897e";
                        PullRequestAnalysisProcessor.EventConsumer consumer = mock(
                                        PullRequestAnalysisProcessor.EventConsumer.class);

                        VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
                        when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
                        when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
                        when(project.getId()).thenReturn(1L);
                        when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
                        when(analysisLockService.acquireLockWithWait(
                                        any(), anyString(), any(), anyString(), anyLong(), any()))
                                        .thenReturn(Optional.of("lock-key-123"));
                        when(vcsServiceFactory.getReportingService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(reportingService);
                        when(vcsServiceFactory.getAiClientService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(aiClientService);
                        when(codeAnalysisService.getAllPrAnalyses(anyLong(), anyLong()))
                                        .thenReturn(List.of());
                        when(pullRequestService.createOrUpdatePullRequest(
                                        anyLong(), anyLong(), anyString(), anyString(), anyString(), any()))
                                        .thenReturn(pullRequest);
                        when(aiClientService.buildAiAnalysisRequests(any(), any(), any(), anyList()))
                                        .thenAnswer(invocation -> {
                                                PrProcessRequest acquired =
                                                        invocation.getArgument(1, PrProcessRequest.class);
                                                acquired.commitHash = fullHead;
                                                return List.of();
                                        });

                        Map<String, Object> result = processor.process(request, consumer, project);

                        assertThat(result).containsEntry("status", "ignored");
                        assertThat(request.commitHash).isEqualTo(fullHead);
                        verify(pullRequestService).createOrUpdatePullRequest(
                                        eq(1L),
                                        eq(42L),
                                        eq(fullHead),
                                        eq("feature-branch"),
                                        eq("main"),
                                        eq(project));
                        verify(analysisLockService).releaseLock("lock-key-123");
                }

                @Test
                @DisplayName("cleanup failures cannot override a successful PR outcome")
                void cleanupFailuresCannotOverrideSuccessfulPrOutcome() throws Exception {
                        PrProcessRequest request = createRequest();
                        PullRequestAnalysisProcessor.EventConsumer consumer = mock(
                                        PullRequestAnalysisProcessor.EventConsumer.class);
                        VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
                        when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
                        when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
                        when(project.getId()).thenReturn(1L);
                        when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
                        when(analysisLockService.acquireLockWithWait(
                                        any(), anyString(), any(), anyString(), anyLong(), any()))
                                        .thenReturn(Optional.of("lock-key-123"));
                        when(vcsServiceFactory.getReportingService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(reportingService);
                        when(vcsServiceFactory.getAiClientService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(aiClientService);
                        when(codeAnalysisService.getAllPrAnalyses(anyLong(), anyLong()))
                                        .thenReturn(List.of());
                        when(pullRequestService.createOrUpdatePullRequest(
                                        anyLong(), anyLong(), anyString(), anyString(), anyString(), any()))
                                        .thenReturn(pullRequest);
                        when(aiClientService.buildAiAnalysisRequests(any(), any(), any(), anyList()))
                                        .thenReturn(List.of());
                        doThrow(new IllegalStateException("lease executor unavailable"))
                                        .when(lockLease).close();
                        doThrow(new IllegalStateException("lock database unavailable"))
                                        .when(analysisLockService).releaseLock("lock-key-123");

                        Map<String, Object> result = processor.process(request, consumer, project);

                        assertThat(result).containsEntry("status", "ignored");
                        verify(lockLease).close();
                        verify(analysisLockService).releaseLock("lock-key-123");
                }

                @Test
                @DisplayName("should successfully process PR analysis")
                void shouldSuccessfullyProcessPRAnalysis() throws Exception {
                        PrProcessRequest request = createRequest();
                        PullRequestAnalysisProcessor.EventConsumer consumer = mock(
                                        PullRequestAnalysisProcessor.EventConsumer.class);

                        // Setup mocks
                        VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
                        when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
                        when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
                        when(project.getId()).thenReturn(1L);
                        when(project.getName()).thenReturn("Test Project");
                        when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);

                        when(analysisLockService.acquireLockWithWait(
                                        any(), anyString(), any(), anyString(), anyLong(), any()))
                                        .thenReturn(Optional.of("lock-key-123"));

                        when(pullRequestService.createOrUpdatePullRequest(
                                        anyLong(), anyLong(), anyString(), anyString(), anyString(), any()))
                                        .thenReturn(pullRequest);

                        when(vcsServiceFactory.getReportingService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(reportingService);
                        when(vcsServiceFactory.getAiClientService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(aiClientService);

                        when(codeAnalysisService.getCodeAnalysisCache(anyLong(), anyString(), anyLong()))
                                        .thenReturn(Optional.empty());
                        when(codeAnalysisService.getAnalysisByCommitHash(anyLong(), anyString()))
                                        .thenReturn(Optional.empty());
                        when(codeAnalysisService.getAllPrAnalyses(anyLong(), anyLong()))
                                        .thenReturn(List.of());

                        when(aiClientService.buildAiAnalysisRequests(any(), any(), any(), anyList()))
                                        .thenReturn(List.of(aiAnalysisRequest));
                        when(aiAnalysisRequest.getRawDiff()).thenReturn("");
                        when(aiAnalysisRequest.getChangedFiles()).thenReturn(List.of("file.java"));
                        when(aiAnalysisRequest.getTaskContext()).thenReturn(Map.of(
                                        "task_key", "PROJ-123",
                                        "task_summary", "Build export"));

                        Map<String, Object> taskEvidence = Map.of(
                                        "taskKey", "PROJ-123",
                                        "source", "DETERMINISTIC_PR_LEDGER",
                                        "fullEvidenceComplete", true,
                                        "items", List.of(Map.of(
                                                        "evidenceRef", "PRF001",
                                                        "filePath", "src/Export.java",
                                                        "hunkId", "hunk-1",
                                                        "lineStart", 10,
                                                        "lineEnd", 12,
                                                        "excerpt", "exportService.run();")));
                        Map<String, Object> aiResponse = Map.of(
                                        "comment", "Review comment",
                                        "issues", List.of(),
                                        "taskEvidence", taskEvidence);
                        when(aiAnalysisClient.performAnalysis(any(), any())).thenReturn(aiResponse);

                        when(codeAnalysisService.createAnalysisFromAiResponse(
                                        any(), any(), anyLong(), anyString(), anyString(), anyString(), any(), any(),
                                        any(), any(), any(), any()))
                                        .thenReturn(codeAnalysis);
                        when(taskImplementationEvidenceService.persistFromAnalysisResponse(
                                        codeAnalysis, taskEvidence))
                                        .thenReturn(new TaskImplementationEvidenceService.PersistenceResult(
                                                        1, 0, 0));

                        Map<String, Object> result = processor.process(request, consumer, project);

                        assertThat(result).containsKey("comment");
                        verify(analysisLockService).acquireLockWithWait(any(), anyString(), any(), anyString(),
                                        anyLong(), any());
                        verify(analysisLockService).releaseLock("lock-key-123");
                        verify(reportingService).postAnalysisResults(any(), any(), anyLong(), any(), any());
                        verify(codeAnalysisService).createAnalysisFromAiResponse(
                                        eq(project),
                                        eq(aiResponse),
                                        eq(42L),
                                        eq("main"),
                                        eq("feature-branch"),
                                        eq("abc123"),
                                        isNull(),
                                        isNull(),
                                        isNull(),
                                        anyMap(),
                                        eq("PROJ-123"),
                                        eq("Build export"));
                        verify(taskImplementationEvidenceService)
                                        .persistFromAnalysisResponse(codeAnalysis, taskEvidence);
                }

                @Test
                @DisplayName("should fail open when auxiliary task evidence persistence is unavailable")
                void shouldFailOpenWhenTaskEvidencePersistenceFails() {
                        Map<String, Object> taskEvidence = Map.of(
                                        "taskKey", "PROJ-123",
                                        "source", "DETERMINISTIC_PR_LEDGER",
                                        "items", List.of());
                        when(taskImplementationEvidenceService.persistFromAnalysisResponse(
                                        codeAnalysis, taskEvidence))
                                        .thenThrow(new RuntimeException("database unavailable"));

                        assertThatCode(() -> ReflectionTestUtils.invokeMethod(
                                        processor,
                                        "persistTaskImplementationEvidence",
                                        codeAnalysis,
                                        taskEvidence))
                                        .doesNotThrowAnyException();
                }

                @Test
                @DisplayName("should run full preparation but not persist or publish a prompt dry run")
                void shouldNotPersistOrPublishPromptDryRun() throws Exception {
                        System.setProperty(PromptDryRunMode.ENABLED_KEY, "true");
                        System.setProperty(PromptDryRunMode.PROJECT_IDS_KEY, "1");
                        PrProcessRequest request = createRequest();
                        PullRequestAnalysisProcessor.EventConsumer consumer = mock(
                                        PullRequestAnalysisProcessor.EventConsumer.class);
                        VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
                        when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
                        when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
                        when(project.getId()).thenReturn(1L);
                        when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
                        when(analysisLockService.acquireLockWithWait(
                                        any(), anyString(), any(), anyString(), anyLong(), any()))
                                        .thenReturn(Optional.of("lock-key-123"));
                        when(pullRequestService.createOrUpdatePullRequest(
                                        anyLong(), anyLong(), anyString(), anyString(), anyString(), any()))
                                        .thenReturn(pullRequest);
                        when(vcsServiceFactory.getReportingService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(reportingService);
                        when(vcsServiceFactory.getAiClientService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(aiClientService);
                        when(codeAnalysisService.getAllPrAnalyses(anyLong(), anyLong()))
                                        .thenReturn(List.of());
                        when(aiClientService.buildAiAnalysisRequests(any(), any(), any(), anyList()))
                                        .thenReturn(List.of(aiAnalysisRequest));
                        when(aiAnalysisRequest.getRawDiff()).thenReturn("diff");
                        when(aiAnalysisRequest.getChangedFiles()).thenReturn(List.of("file.java"));
                        Map<String, Object> dryRunResponse = Map.of(
                                        "dryRun", true,
                                        "status", "prompt_capture_completed",
                                        "promptArtifact", Map.of(
                                                        "filename", "capture.json",
                                                        "containerPath",
                                                        "/app/logs/prompt-dry-runs/capture.json"));
                        when(aiAnalysisClient.performAnalysis(any(), any()))
                                        .thenAnswer(invocation -> {
                                                @SuppressWarnings("unchecked")
                                                java.util.function.Consumer<Map<String, Object>> eventHandler =
                                                                invocation.getArgument(1);
                                                eventHandler.accept(Map.of(
                                                                "type", "status",
                                                                "state", "processing",
                                                                "message",
                                                                "Review pipeline is still processing"));
                                                return dryRunResponse;
                                        });

                        Map<String, Object> result = processor.process(request, consumer, project);

                        assertThat(result).isEqualTo(dryRunResponse);
                        verify(codeAnalysisService, never()).getCodeAnalysisCache(
                                        anyLong(), anyString(), anyLong());
                        verify(codeAnalysisService, never()).createAnalysisFromAiResponse(
                                        any(), any(), anyLong(), anyString(), anyString(), anyString(),
                                        any(), any(), any(), any(), any(), any());
                        verify(analysisLockService).maintainLockLease("lock-key-123", 30);
                        verify(lockLease, times(2)).confirmOwnership();
                        verify(lockLease).close();
                        verify(reportingService, never()).postAnalysisResults(
                                        any(), any(), anyLong(), any(), any());
                }

                @Test
                @DisplayName("should reject a quiet completed review when the independent lease heartbeat lost ownership")
                void shouldRejectCompletedReviewAfterLockLeaseIsLost() throws Exception {
                        PrProcessRequest request = createRequest();
                        PullRequestAnalysisProcessor.EventConsumer consumer = mock(
                                        PullRequestAnalysisProcessor.EventConsumer.class);
                        VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
                        when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
                        when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
                        when(project.getId()).thenReturn(1L);
                        when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
                        when(analysisLockService.acquireLockWithWait(
                                        any(), anyString(), any(), anyString(), anyLong(), any()))
                                        .thenReturn(Optional.of("lock-key-123"));
                        when(pullRequestService.createOrUpdatePullRequest(
                                        anyLong(), anyLong(), anyString(), anyString(), anyString(), any()))
                                        .thenReturn(pullRequest);
                        when(vcsServiceFactory.getReportingService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(reportingService);
                        when(vcsServiceFactory.getAiClientService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(aiClientService);
                        when(codeAnalysisService.getAllPrAnalyses(anyLong(), anyLong()))
                                        .thenReturn(List.of());
                        when(aiClientService.buildAiAnalysisRequests(any(), any(), any(), anyList()))
                                        .thenReturn(List.of(aiAnalysisRequest));
                        when(aiAnalysisRequest.getRawDiff()).thenReturn("diff");
                        when(aiAnalysisRequest.getChangedFiles()).thenReturn(List.of("file.java"));
                        when(lockLease.confirmOwnership()).thenReturn(true, false);
                        when(aiAnalysisClient.performAnalysis(any(), any())).thenReturn(Map.of(
                                        "comment", "must not be published",
                                        "issues", List.of()));

                        Map<String, Object> result = processor.process(request, consumer, project);

                        assertThat(result)
                                        .containsEntry("status", "error")
                                        .containsEntry(
                                                        "message",
                                                        "PR analysis lost its lock lease while the review worker was active");
                        verify(codeAnalysisService, never()).createAnalysisFromAiResponse(
                                        any(), any(), anyLong(), anyString(), anyString(), anyString(),
                                        any(), any(), any(), any(), any(), any());
                        verify(reportingService, never()).postAnalysisResults(
                                        any(), any(), anyLong(), any(), any());
                        verify(analysisLockService).maintainLockLease("lock-key-123", 30);
                        verify(lockLease, times(2)).confirmOwnership();
                        verify(lockLease).close();
                        verify(analysisLockService).releaseLock("lock-key-123");
                }

                @Test
                @DisplayName("should re-confirm ownership after direct VCS fallback before persistence")
                void shouldRejectLeaseLostDuringDirectVcsFallbackBeforePersistence() throws Exception {
                        PrProcessRequest request = createRequest();
                        PullRequestAnalysisProcessor.EventConsumer consumer = mock(
                                        PullRequestAnalysisProcessor.EventConsumer.class);
                        Map<String, Object> aiResponse = Map.of(
                                        "comment", "must not be persisted",
                                        "issues", List.of());
                        stubReviewThroughAi(aiResponse);
                        when(lockLease.confirmOwnership()).thenReturn(true, true, false);

                        Map<String, Object> result = processor.process(request, consumer, project);

                        assertThat(result).containsEntry("status", "error");
                        verify(lockLease, times(3)).confirmOwnership();
                        verify(codeAnalysisService, never()).createAnalysisFromAiResponse(
                                        any(), any(), anyLong(), anyString(), anyString(), anyString(),
                                        any(), any(), any(), any(), any(), any());
                        verify(reportingService, never()).postAnalysisResults(
                                        any(), any(), anyLong(), any(), any());
                }

                @Test
                @DisplayName("should atomically re-confirm ownership before VCS publication")
                void shouldRejectLeaseLostBeforeVcsPublication() throws Exception {
                        PrProcessRequest request = createRequest();
                        PullRequestAnalysisProcessor.EventConsumer consumer = mock(
                                        PullRequestAnalysisProcessor.EventConsumer.class);
                        Map<String, Object> aiResponse = Map.of(
                                        "comment", "must not be published",
                                        "issues", List.of());
                        stubReviewThroughAi(aiResponse);
                        when(lockLease.confirmOwnership()).thenReturn(true, true, true, false);
                        when(codeAnalysisService.createAnalysisFromAiResponse(
                                        any(), any(), anyLong(), anyString(), anyString(), anyString(),
                                        any(), any(), any(), any(), any(), any()))
                                        .thenReturn(codeAnalysis);

                        Map<String, Object> result = processor.process(request, consumer, project);

                        assertThat(result).containsEntry("status", "error");
                        verify(lockLease, times(4)).confirmOwnership();
                        verify(codeAnalysisService).createAnalysisFromAiResponse(
                                        any(), eq(aiResponse), anyLong(), anyString(), anyString(), anyString(),
                                        any(), any(), any(), any(), any(), any());
                        verify(reportingService, never()).postAnalysisResults(
                                        any(), any(), anyLong(), any(), any());
                }

                private void stubReviewThroughAi(Map<String, Object> aiResponse) throws Exception {
                        VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
                        when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
                        when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
                        when(project.getId()).thenReturn(1L);
                        when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
                        when(analysisLockService.acquireLockWithWait(
                                        any(), anyString(), any(), anyString(), anyLong(), any()))
                                        .thenReturn(Optional.of("lock-key-123"));
                        when(pullRequestService.createOrUpdatePullRequest(
                                        anyLong(), anyLong(), anyString(), anyString(), anyString(), any()))
                                        .thenReturn(pullRequest);
                        when(vcsServiceFactory.getReportingService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(reportingService);
                        when(vcsServiceFactory.getAiClientService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(aiClientService);
                        when(codeAnalysisService.getAllPrAnalyses(anyLong(), anyLong()))
                                        .thenReturn(List.of());
                        when(aiClientService.buildAiAnalysisRequests(any(), any(), any(), anyList()))
                                        .thenReturn(List.of(aiAnalysisRequest));
                        when(aiAnalysisRequest.getRawDiff()).thenReturn("diff");
                        when(aiAnalysisRequest.getChangedFiles()).thenReturn(List.of("file.java"));
                        when(aiAnalysisClient.performAnalysis(any(), any())).thenReturn(aiResponse);
                }

                private String stubCacheLookupPrerequisites() throws Exception {
                        VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
                        when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
                        when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
                        when(project.getId()).thenReturn(1L);
                        when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
                        when(analysisLockService.acquireLockWithWait(
                                        any(), anyString(), any(), anyString(), anyLong(), any()))
                                        .thenReturn(Optional.of("lock-key-cache"));
                        when(vcsServiceFactory.getReportingService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(reportingService);
                        when(vcsServiceFactory.getAiClientService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(aiClientService);
                        when(codeAnalysisService.getAllPrAnalyses(anyLong(), anyLong()))
                                        .thenReturn(List.of());
                        when(aiClientService.buildAiAnalysisRequests(any(), any(), any(), anyList()))
                                        .thenReturn(List.of(aiAnalysisRequest));
                        when(pullRequestService.createOrUpdatePullRequest(
                                        anyLong(), anyLong(), anyString(), anyString(), anyString(), any()))
                                        .thenReturn(pullRequest);
                        when(aiAnalysisRequest.getRawDiff()).thenReturn(
                                        "diff --git a/file.java b/file.java\n@@ -1 +1 @@\n-old\n+new\n");
                        when(aiAnalysisRequest.getChangedFiles()).thenReturn(List.of("file.java"));
                        return processor.computeReviewIdentity(aiAnalysisRequest);
                }

                @Test
                @DisplayName("should fence the first durable PR write after request construction")
                void shouldRejectLeaseLostBeforePullRequestPersistence() throws Exception {
                        VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
                        when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
                        when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
                        when(project.getId()).thenReturn(1L);
                        when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
                        when(analysisLockService.acquireLockWithWait(
                                        any(), anyString(), any(), anyString(), anyLong(), any()))
                                        .thenReturn(Optional.of("lock-key-before-pr"));
                        when(vcsServiceFactory.getReportingService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(reportingService);
                        when(vcsServiceFactory.getAiClientService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(aiClientService);
                        when(codeAnalysisService.getAllPrAnalyses(anyLong(), anyLong()))
                                        .thenReturn(List.of());
                        when(aiClientService.buildAiAnalysisRequests(any(), any(), any(), anyList()))
                                        .thenReturn(List.of(aiAnalysisRequest));
                        when(lockLease.confirmOwnership()).thenReturn(false);

                        Map<String, Object> result = processor.process(
                                        createRequest(), mock(PullRequestAnalysisProcessor.EventConsumer.class), project);

                        assertThat(result).containsEntry("status", "error");
                        verify(pullRequestService, never()).createOrUpdatePullRequest(
                                        anyLong(), anyLong(), anyString(), anyString(), anyString(), any());
                }

                @Test
                @DisplayName("should fence exact cache publication")
                void shouldRejectLeaseLostBeforeExactCachePublication() throws Exception {
                        String identity = stubCacheLookupPrerequisites();
                        when(codeAnalysisService.getCodeAnalysisCache(1L, "abc123", 42L))
                                        .thenReturn(Optional.of(codeAnalysis));
                        when(codeAnalysis.getDiffFingerprint()).thenReturn(identity);
                        when(lockLease.confirmOwnership()).thenReturn(true, false);

                        Map<String, Object> result = processor.process(
                                        createRequest(), mock(PullRequestAnalysisProcessor.EventConsumer.class), project);

                        assertThat(result).containsEntry("status", "error");
                        verify(reportingService, never()).postAnalysisResults(
                                        any(), any(), anyLong(), any(), any());
                }

                @Test
                @DisplayName("should fence commit cache cloning")
                void shouldRejectLeaseLostBeforeCommitCacheClone() throws Exception {
                        String identity = stubCacheLookupPrerequisites();
                        when(codeAnalysisService.getCodeAnalysisCache(1L, "abc123", 42L))
                                        .thenReturn(Optional.empty());
                        CodeAnalysis source = mock(CodeAnalysis.class);
                        when(source.getDiffFingerprint()).thenReturn(identity);
                        when(source.getPrNumber()).thenReturn(99L);
                        when(codeAnalysisService.getAnalysisByCommitHash(1L, "abc123"))
                                        .thenReturn(Optional.of(source));
                        when(lockLease.confirmOwnership()).thenReturn(true, false);

                        Map<String, Object> result = processor.process(
                                        createRequest(), mock(PullRequestAnalysisProcessor.EventConsumer.class), project);

                        assertThat(result).containsEntry("status", "error");
                        verify(codeAnalysisService, never()).cloneAnalysisForPr(
                                        any(), any(), anyLong(), anyString(), anyString(), anyString(), anyString());
                }

                @Test
                @DisplayName("should fence fingerprint cache cloning")
                void shouldRejectLeaseLostBeforeFingerprintCacheClone() throws Exception {
                        stubCacheLookupPrerequisites();
                        when(codeAnalysisService.getCodeAnalysisCache(1L, "abc123", 42L))
                                        .thenReturn(Optional.empty());
                        when(codeAnalysisService.getAnalysisByCommitHash(1L, "abc123"))
                                        .thenReturn(Optional.empty());
                        when(codeAnalysisService.getAnalysisByDiffFingerprint(eq(1L), anyString()))
                                        .thenReturn(Optional.of(mock(CodeAnalysis.class)));
                        when(lockLease.confirmOwnership()).thenReturn(true, false);

                        Map<String, Object> result = processor.process(
                                        createRequest(), mock(PullRequestAnalysisProcessor.EventConsumer.class), project);

                        assertThat(result).containsEntry("status", "error");
                        verify(codeAnalysisService, never()).cloneAnalysisForPr(
                                        any(), any(), anyLong(), anyString(), anyString(), anyString(), anyString());
                }

                @Test
                @DisplayName("should re-confirm after VCS publication before commit receipts")
                void shouldRejectLeaseLostAfterVcsPublicationBeforeCommitReceipts() throws Exception {
                        Map<String, Object> aiResponse = Map.of("comment", "review", "issues", List.of());
                        stubReviewThroughAi(aiResponse);
                        when(codeAnalysisService.createAnalysisFromAiResponse(
                                        any(), any(), anyLong(), anyString(), anyString(), anyString(),
                                        any(), any(), any(), any(), any(), any()))
                                        .thenReturn(codeAnalysis);
                        when(lockLease.confirmOwnership()).thenReturn(true, true, true, true, false);

                        Map<String, Object> result = processor.process(
                                        createRequest(), mock(PullRequestAnalysisProcessor.EventConsumer.class), project);

                        assertThat(result).containsEntry("status", "error");
                        verify(reportingService).postAnalysisResults(
                                        eq(codeAnalysis), any(), anyLong(), any(), any());
                        verifyNoInteractions(analyzedCommitService);
                }

                @Test
                @DisplayName("observer failure cannot turn a successful review into failure")
                void observerFailureDoesNotChangeReviewOutcome() throws Exception {
                        Map<String, Object> aiResponse = Map.of("comment", "review", "issues", List.of());
                        stubReviewThroughAi(aiResponse);
                        when(codeAnalysisService.createAnalysisFromAiResponse(
                                        any(), any(), anyLong(), anyString(), anyString(), anyString(),
                                        any(), any(), any(), any(), any(), any()))
                                        .thenReturn(codeAnalysis);
                        PullRequestAnalysisProcessor.EventConsumer observer = mock(
                                        PullRequestAnalysisProcessor.EventConsumer.class);
                        doThrow(new IllegalStateException("observer disconnected"))
                                        .when(observer).accept(anyMap());
                        when(analysisLockService.acquireLockWithWait(
                                        any(), anyString(), any(), anyString(), anyLong(), any()))
                                        .thenAnswer(invocation -> {
                                                @SuppressWarnings("unchecked")
                                                java.util.function.Consumer<Map<String, Object>> progress =
                                                                invocation.getArgument(5);
                                                progress.accept(Map.of(
                                                                "type", "lock_wait",
                                                                "message", "waiting"));
                                                return Optional.of("lock-key-123");
                                        });
                        when(ragOperationsService.ensureRagIndexUpToDate(
                                        any(), anyString(), any()))
                                        .thenAnswer(invocation -> {
                                                @SuppressWarnings("unchecked")
                                                java.util.function.Consumer<Map<String, Object>> progress =
                                                                invocation.getArgument(2);
                                                progress.accept(Map.of(
                                                                "type", "status",
                                                                "state", "rag_update"));
                                                return true;
                                        });
                        when(aiAnalysisClient.performAnalysis(any(), any())).thenAnswer(invocation -> {
                                @SuppressWarnings("unchecked")
                                java.util.function.Consumer<Map<String, Object>> progress =
                                                invocation.getArgument(1);
                                progress.accept(Map.of(
                                                "type", "status",
                                                "state", "processing"));
                                return aiResponse;
                        });

                        Map<String, Object> result = processor.process(createRequest(), observer, project);

                        assertThat(result).isEqualTo(aiResponse);
                        verify(reportingService).postAnalysisResults(
                                        eq(codeAnalysis), any(), anyLong(), any(), any());
                        verify(observer).accept(argThat(event -> "lock_wait".equals(event.get("type"))));
                        verify(observer).accept(argThat(event -> "rag_update".equals(event.get("state"))));
                        verify(observer).accept(argThat(event -> "processing".equals(event.get("state"))));
                }

                @Test
                @DisplayName("should throw AnalysisLockedException when lock cannot be acquired")
                void shouldThrowAnalysisLockedExceptionWhenLockCannotBeAcquired() {
                        PrProcessRequest request = createRequest();
                        PullRequestAnalysisProcessor.EventConsumer consumer = mock(
                                        PullRequestAnalysisProcessor.EventConsumer.class);

                        when(project.getId()).thenReturn(1L);
                        when(project.getName()).thenReturn("Test Project");
                        when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), anyLong(),
                                        any()))
                                        .thenReturn(Optional.empty());
                        when(analysisLockService.getLockWaitTimeoutMinutes()).thenReturn(10);

                        assertThatThrownBy(() -> processor.process(request, consumer, project))
                                        .isInstanceOf(AnalysisLockedException.class);
                }

                @Test
                @DisplayName("should return cached result when analysis cache exists")
                void shouldReturnCachedResultWhenAnalysisCacheExists() throws Exception {
                        PrProcessRequest request = createRequest();
                        PullRequestAnalysisProcessor.EventConsumer consumer = mock(
                                        PullRequestAnalysisProcessor.EventConsumer.class);

                        VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
                        when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
                        when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
                        when(project.getId()).thenReturn(1L);
                        when(project.getName()).thenReturn("Test Project");
                        when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);

                        when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), anyLong(),
                                        any()))
                                        .thenReturn(Optional.of("lock-key-123"));

                        when(pullRequestService.createOrUpdatePullRequest(anyLong(), anyLong(), anyString(),
                                        anyString(), anyString(), any()))
                                        .thenReturn(pullRequest);
                        when(pullRequest.getId()).thenReturn(100L);

                        when(vcsServiceFactory.getReportingService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(reportingService);
                        when(vcsServiceFactory.getAiClientService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(aiClientService);
                        when(codeAnalysisService.getAllPrAnalyses(anyLong(), anyLong())).thenReturn(List.of());
                        when(aiClientService.buildAiAnalysisRequests(any(), any(), any(), anyList()))
                                        .thenReturn(List.of(aiAnalysisRequest));
                        when(aiAnalysisRequest.getRawDiff()).thenReturn(
                                        "diff --git a/file.java b/file.java\n@@ -1 +1 @@\n-old\n+new\n");
                        when(aiAnalysisRequest.getChangedFiles()).thenReturn(List.of("file.java"));
                        String reviewIdentity = processor.computeReviewIdentity(aiAnalysisRequest);
                        when(codeAnalysisService.getCodeAnalysisCache(anyLong(), anyString(), anyLong()))
                                        .thenReturn(Optional.of(codeAnalysis));
                        when(codeAnalysis.getDiffFingerprint()).thenReturn(reviewIdentity);

                        Map<String, Object> result = processor.process(request, consumer, project);

                        assertThat(result).containsEntry("status", "cached");
                        assertThat(result).containsEntry("cached", true);
                        verify(reportingService).postAnalysisResults(eq(codeAnalysis), any(), anyLong(), anyLong(),
                                        any());
                        verify(aiAnalysisClient, never()).performAnalysis(any(), any());
                }

                @Test
                @DisplayName("should use pre-acquired lock and skip lock acquisition")
                void shouldUsePreAcquiredLockAndSkipLockAcquisition() throws Exception {
                        PrProcessRequest request = createRequest();
                        request.preAcquiredLockKey = "pre-lock-key-999";
                        PullRequestAnalysisProcessor.EventConsumer consumer = mock(
                                        PullRequestAnalysisProcessor.EventConsumer.class);

                        VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
                        when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
                        when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
                        when(project.getId()).thenReturn(1L);
                        when(project.getName()).thenReturn("Test Project");
                        when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);

                        when(pullRequestService.createOrUpdatePullRequest(anyLong(), anyLong(), anyString(),
                                        anyString(), anyString(), any()))
                                        .thenReturn(pullRequest);
                        when(vcsServiceFactory.getReportingService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(reportingService);
                        when(vcsServiceFactory.getAiClientService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(aiClientService);
                        when(codeAnalysisService.getCodeAnalysisCache(anyLong(), anyString(), anyLong()))
                                        .thenReturn(Optional.empty());
                        when(codeAnalysisService.getAnalysisByCommitHash(anyLong(), anyString()))
                                        .thenReturn(Optional.empty());
                        when(codeAnalysisService.getAllPrAnalyses(anyLong(), anyLong())).thenReturn(List.of());
                        when(aiClientService.buildAiAnalysisRequests(any(), any(), any(), anyList()))
                                        .thenReturn(List.of(aiAnalysisRequest));
                        when(aiAnalysisRequest.getRawDiff()).thenReturn("");
                        when(aiAnalysisRequest.getChangedFiles()).thenReturn(List.of("file.java"));
                        Map<String, Object> aiResponse = Map.of("comment", "Review", "issues", List.of());
                        when(aiAnalysisClient.performAnalysis(any(), any())).thenReturn(aiResponse);
                        when(codeAnalysisService.createAnalysisFromAiResponse(any(), any(), anyLong(), anyString(),
                                        anyString(), anyString(), any(), any(), any(), any(), any(), any()))
                                        .thenReturn(codeAnalysis);

                        processor.process(request, consumer, project);

                        // Should NOT call acquireLockWithWait since we have pre-acquired lock
                        verify(analysisLockService, never()).acquireLockWithWait(any(), anyString(), any(), anyString(),
                                        anyLong(), any());
                        // Should NOT release lock (pre-acquired locks are released by caller)
                        verify(analysisLockService, never()).releaseLock(anyString());
                }

                @Test
                @DisplayName("should return cached_by_commit when commit hash cache hits")
                void shouldReturnCachedByCommitWhenCommitHashCacheHits() throws Exception {
                        PrProcessRequest request = createRequest();
                        PullRequestAnalysisProcessor.EventConsumer consumer = mock(
                                        PullRequestAnalysisProcessor.EventConsumer.class);

                        VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
                        when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
                        when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
                        when(project.getId()).thenReturn(1L);
                        when(project.getName()).thenReturn("Test Project");
                        when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);

                        when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), anyLong(),
                                        any()))
                                        .thenReturn(Optional.of("lock-key"));
                        when(pullRequestService.createOrUpdatePullRequest(anyLong(), anyLong(), anyString(),
                                        anyString(), anyString(), any()))
                                        .thenReturn(pullRequest);
                        when(pullRequest.getId()).thenReturn(100L);
                        when(vcsServiceFactory.getReportingService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(reportingService);
                        when(vcsServiceFactory.getAiClientService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(aiClientService);
                        when(codeAnalysisService.getAllPrAnalyses(anyLong(), anyLong())).thenReturn(List.of());
                        when(aiClientService.buildAiAnalysisRequests(any(), any(), any(), anyList()))
                                        .thenReturn(List.of(aiAnalysisRequest));
                        when(aiAnalysisRequest.getRawDiff()).thenReturn(
                                        "diff --git a/file.java b/file.java\n@@ -1 +1 @@\n-old\n+new\n");
                        when(aiAnalysisRequest.getChangedFiles()).thenReturn(List.of("file.java"));
                        String reviewIdentity = processor.computeReviewIdentity(aiAnalysisRequest);

                        // No exact cache match, but commit hash matches from another PR
                        when(codeAnalysisService.getCodeAnalysisCache(anyLong(), anyString(), anyLong()))
                                        .thenReturn(Optional.empty());
                        CodeAnalysis sourceAnalysis = mock(CodeAnalysis.class);
                        when(sourceAnalysis.getPrNumber()).thenReturn(99L);
                        when(sourceAnalysis.getDiffFingerprint()).thenReturn(reviewIdentity);
                        when(codeAnalysisService.getAnalysisByCommitHash(1L, "abc123"))
                                        .thenReturn(Optional.of(sourceAnalysis));

                        CodeAnalysis clonedAnalysis = mock(CodeAnalysis.class);
                        when(codeAnalysisService.cloneAnalysisForPr(any(), any(), anyLong(), anyString(), anyString(),
                                        anyString(), anyString()))
                                        .thenReturn(clonedAnalysis);

                        Map<String, Object> result = processor.process(request, consumer, project);

                        assertThat(result).containsEntry("status", "cached_by_commit");
                        assertThat(result).containsEntry("cached", true);
                        verify(codeAnalysisService).cloneAnalysisForPr(eq(sourceAnalysis), eq(project), eq(42L),
                                        eq("abc123"), eq("main"), eq("feature-branch"), eq(reviewIdentity));
                        verify(taskImplementationEvidenceService)
                                        .copyForAnalysis(sourceAnalysis, clonedAnalysis);
                        verify(reportingService).postAnalysisResults(eq(clonedAnalysis), any(), anyLong(), any(),
                                        any());
                        verify(analysisLockService).releaseLock("lock-key");
                }

                @Test
                @DisplayName("should return cached_by_fingerprint when diff fingerprint matches")
                void shouldReturnCachedByFingerprintWhenDiffFingerprintMatches() throws Exception {
                        PrProcessRequest request = createRequest();
                        PullRequestAnalysisProcessor.EventConsumer consumer = mock(
                                        PullRequestAnalysisProcessor.EventConsumer.class);

                        VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
                        when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
                        when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
                        when(project.getId()).thenReturn(1L);
                        when(project.getName()).thenReturn("Test Project");
                        when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);

                        when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), anyLong(),
                                        any()))
                                        .thenReturn(Optional.of("lock-key"));
                        when(pullRequestService.createOrUpdatePullRequest(anyLong(), anyLong(), anyString(),
                                        anyString(), anyString(), any()))
                                        .thenReturn(pullRequest);
                        when(pullRequest.getId()).thenReturn(100L);
                        when(vcsServiceFactory.getReportingService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(reportingService);
                        when(vcsServiceFactory.getAiClientService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(aiClientService);

                        when(codeAnalysisService.getCodeAnalysisCache(anyLong(), anyString(), anyLong()))
                                        .thenReturn(Optional.empty());
                        when(codeAnalysisService.getAnalysisByCommitHash(anyLong(), anyString()))
                                        .thenReturn(Optional.empty());
                        when(codeAnalysisService.getAllPrAnalyses(anyLong(), anyLong())).thenReturn(List.of());

                        when(aiClientService.buildAiAnalysisRequests(any(), any(), any(), anyList()))
                                        .thenReturn(List.of(aiAnalysisRequest));
                        // A diff that produces a non-null fingerprint
                        when(aiAnalysisRequest.getRawDiff()).thenReturn("+added line\n-removed line\n");
                        when(aiAnalysisRequest.getChangedFiles()).thenReturn(List.of("file.java"));

                        CodeAnalysis fingerprintSource = mock(CodeAnalysis.class);
                        when(fingerprintSource.getPrNumber()).thenReturn(77L);
                        when(codeAnalysisService.getAnalysisByDiffFingerprint(eq(1L), anyString()))
                                        .thenReturn(Optional.of(fingerprintSource));

                        CodeAnalysis clonedAnalysis = mock(CodeAnalysis.class);
                        when(codeAnalysisService.cloneAnalysisForPr(any(), any(), anyLong(), anyString(), anyString(),
                                        anyString(), anyString()))
                                        .thenReturn(clonedAnalysis);

                        Map<String, Object> result = processor.process(request, consumer, project);

                        assertThat(result).containsEntry("status", "cached_by_fingerprint");
                        assertThat(result).containsEntry("cached", true);
                        verify(taskImplementationEvidenceService)
                                        .copyForAnalysis(fingerprintSource, clonedAnalysis);
                        verify(analysisLockService).releaseLock("lock-key");
                }

                @Test
                @DisplayName("should handle IOException during analysis gracefully")
                void shouldHandleIOExceptionDuringAnalysis() throws Exception {
                        PrProcessRequest request = createRequest();
                        PullRequestAnalysisProcessor.EventConsumer consumer = mock(
                                        PullRequestAnalysisProcessor.EventConsumer.class);

                        VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
                        when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
                        when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
                        when(project.getId()).thenReturn(1L);
                        when(project.getName()).thenReturn("Test Project");
                        when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);

                        when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), anyLong(),
                                        any()))
                                        .thenReturn(Optional.of("lock-key"));
                        when(pullRequestService.createOrUpdatePullRequest(anyLong(), anyLong(), anyString(),
                                        anyString(), anyString(), any()))
                                        .thenReturn(pullRequest);
                        when(vcsServiceFactory.getReportingService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(reportingService);
                        when(vcsServiceFactory.getAiClientService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(aiClientService);

                        when(codeAnalysisService.getCodeAnalysisCache(anyLong(), anyString(), anyLong()))
                                        .thenReturn(Optional.empty());
                        when(codeAnalysisService.getAnalysisByCommitHash(anyLong(), anyString()))
                                        .thenReturn(Optional.empty());
                        when(codeAnalysisService.getAllPrAnalyses(anyLong(), anyLong())).thenReturn(List.of());
                        when(aiClientService.buildAiAnalysisRequests(any(), any(), any(), anyList()))
                                        .thenReturn(List.of(aiAnalysisRequest));

                        when(aiAnalysisClient.performAnalysis(any(), any()))
                                        .thenThrow(new IOException("AI service down"));

                        Map<String, Object> result = processor.process(request, consumer, project);

                        assertThat(result).containsEntry("status", "error");
                        assertThat(result.get("message").toString()).contains("AI service down");
                        verify(consumer).accept(argThat(event -> "error".equals(event.get("type"))
                                        && event.get("message").toString().contains("I/O error")));
                        verify(analysisLockService).releaseLock("lock-key");
                }

                @Test
                @DisplayName("should handle IOException when posting results to VCS")
                void shouldHandleIOExceptionWhenPostingResults() throws Exception {
                        PrProcessRequest request = createRequest();
                        PullRequestAnalysisProcessor.EventConsumer consumer = mock(
                                        PullRequestAnalysisProcessor.EventConsumer.class);

                        VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
                        when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
                        when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
                        when(project.getId()).thenReturn(1L);
                        when(project.getName()).thenReturn("Test Project");
                        when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);

                        when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), anyLong(),
                                        any()))
                                        .thenReturn(Optional.of("lock-key"));
                        when(pullRequestService.createOrUpdatePullRequest(anyLong(), anyLong(), anyString(),
                                        anyString(), anyString(), any()))
                                        .thenReturn(pullRequest);
                        when(pullRequest.getId()).thenReturn(100L);
                        when(vcsServiceFactory.getReportingService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(reportingService);
                        when(vcsServiceFactory.getAiClientService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(aiClientService);
                        when(codeAnalysisService.getCodeAnalysisCache(anyLong(), anyString(), anyLong()))
                                        .thenReturn(Optional.empty());
                        when(codeAnalysisService.getAnalysisByCommitHash(anyLong(), anyString()))
                                        .thenReturn(Optional.empty());
                        when(codeAnalysisService.getAllPrAnalyses(anyLong(), anyLong())).thenReturn(List.of());
                        when(aiClientService.buildAiAnalysisRequests(any(), any(), any(), anyList()))
                                        .thenReturn(List.of(aiAnalysisRequest));
                        when(aiAnalysisRequest.getRawDiff()).thenReturn("");
                        when(aiAnalysisRequest.getChangedFiles()).thenReturn(List.of("file.java"));

                        Map<String, Object> aiResponse = Map.of("comment", "Review", "issues", List.of());
                        when(aiAnalysisClient.performAnalysis(any(AiAnalysisRequest.class), any()))
                                        .thenReturn(aiResponse);
                        when(codeAnalysisService.createAnalysisFromAiResponse(any(), any(), anyLong(), anyString(),
                                        anyString(), anyString(), any(), any(), any(), any(), any(), any()))
                                        .thenReturn(codeAnalysis);
                        doThrow(new IOException("VCS API error")).when(reportingService)
                                        .postAnalysisResults(any(), any(), anyLong(), any(), any());

                        Map<String, Object> result = processor.process(request, consumer, project);

                        // Should still return AI response despite posting failure
                        assertThat(result).containsKey("comment");
                        verify(consumer).accept(argThat(event -> "warning".equals(event.get("type"))));
                }

                @Test
                @DisplayName("should handle IOException when posting commit-hash cached results")
                void shouldHandleIOExceptionWhenPostingCommitHashCachedResults() throws Exception {
                        PrProcessRequest request = createRequest();
                        PullRequestAnalysisProcessor.EventConsumer consumer = mock(
                                        PullRequestAnalysisProcessor.EventConsumer.class);

                        VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
                        when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
                        when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
                        when(project.getId()).thenReturn(1L);
                        when(project.getName()).thenReturn("Test Project");
                        when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);

                        when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), anyLong(),
                                        any()))
                                        .thenReturn(Optional.of("lock-key"));
                        when(pullRequestService.createOrUpdatePullRequest(anyLong(), anyLong(), anyString(),
                                        anyString(), anyString(), any()))
                                        .thenReturn(pullRequest);
                        when(pullRequest.getId()).thenReturn(100L);
                        when(vcsServiceFactory.getReportingService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(reportingService);
                        when(vcsServiceFactory.getAiClientService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(aiClientService);
                        when(codeAnalysisService.getAllPrAnalyses(anyLong(), anyLong())).thenReturn(List.of());
                        when(aiClientService.buildAiAnalysisRequests(any(), any(), any(), anyList()))
                                        .thenReturn(List.of(aiAnalysisRequest));
                        when(aiAnalysisRequest.getRawDiff()).thenReturn(
                                        "diff --git a/file.java b/file.java\n@@ -1 +1 @@\n-old\n+new\n");
                        when(aiAnalysisRequest.getChangedFiles()).thenReturn(List.of("file.java"));
                        String reviewIdentity = processor.computeReviewIdentity(aiAnalysisRequest);

                        when(codeAnalysisService.getCodeAnalysisCache(anyLong(), anyString(), anyLong()))
                                        .thenReturn(Optional.empty());
                        CodeAnalysis sourceAnalysis = mock(CodeAnalysis.class);
                        when(sourceAnalysis.getPrNumber()).thenReturn(99L);
                        when(sourceAnalysis.getDiffFingerprint()).thenReturn(reviewIdentity);
                        when(codeAnalysisService.getAnalysisByCommitHash(1L, "abc123"))
                                        .thenReturn(Optional.of(sourceAnalysis));
                        CodeAnalysis clonedAnalysis = mock(CodeAnalysis.class);
                        when(codeAnalysisService.cloneAnalysisForPr(any(), any(), anyLong(), anyString(), anyString(),
                                        anyString(), any()))
                                        .thenReturn(clonedAnalysis);
                        doThrow(new IOException("Post fail")).when(reportingService).postAnalysisResults(any(), any(),
                                        anyLong(), any(), any());

                        Map<String, Object> result = processor.process(request, consumer, project);

                        // Should still return cached result despite posting failure
                        assertThat(result).containsEntry("status", "cached_by_commit");
                        assertThat(result).containsEntry("cached", true);
                }
        }

        @Nested
        @DisplayName("postAnalysisCacheIfExist()")
        class PostAnalysisCacheIfExistTests {

                @Test
                @DisplayName("should return EXACT and post when cache exists")
                void shouldReturnTrueAndPostWhenCacheExists() throws IOException {
                        when(project.getId()).thenReturn(1L);
                        when(codeAnalysisService.getCodeAnalysisCache(1L, "abc123", 42L))
                                        .thenReturn(Optional.of(codeAnalysis));
                        when(codeAnalysis.getDiffFingerprint()).thenReturn("identity");
                        when(pullRequest.getId()).thenReturn(100L);

                        PullRequestAnalysisProcessor.CacheHitType result = processor.postAnalysisCacheIfExist(
                                        project, pullRequest, "abc123", 42L, reportingService, "placeholder-id",
                                        "main", "feature-branch", "identity", lockLease);

                        assertThat(result).isEqualTo(PullRequestAnalysisProcessor.CacheHitType.EXACT);
                        verify(reportingService).postAnalysisResults(eq(codeAnalysis), eq(project), eq(42L), eq(100L),
                                        eq("placeholder-id"));
                }

                @Test
                @DisplayName("should return NONE when no cache exists")
                void shouldReturnFalseWhenNoCacheExists() throws IOException {
                        when(project.getId()).thenReturn(1L);
                        when(codeAnalysisService.getCodeAnalysisCache(1L, "abc123", 42L))
                                        .thenReturn(Optional.empty());
                        when(codeAnalysisService.getAnalysisByCommitHash(1L, "abc123"))
                                        .thenReturn(Optional.empty());

                        PullRequestAnalysisProcessor.CacheHitType result = processor.postAnalysisCacheIfExist(
                                        project, pullRequest, "abc123", 42L, reportingService, "placeholder-id",
                                        "main", "feature-branch", "identity", lockLease);

                        assertThat(result).isEqualTo(PullRequestAnalysisProcessor.CacheHitType.NONE);
                        verify(reportingService, never()).postAnalysisResults(any(), any(), anyLong(), any(), any());
                }

                @Test
                @DisplayName("should reject stale exact and commit caches with a different review identity")
                void shouldRejectCacheEntriesWithDifferentReviewIdentity() throws IOException {
                        when(project.getId()).thenReturn(1L);
                        CodeAnalysis staleExact = mock(CodeAnalysis.class);
                        CodeAnalysis staleCommit = mock(CodeAnalysis.class);
                        when(staleExact.getDiffFingerprint()).thenReturn("old-identity");
                        when(staleCommit.getDiffFingerprint()).thenReturn("old-identity");
                        when(codeAnalysisService.getCodeAnalysisCache(1L, "abc123", 42L))
                                        .thenReturn(Optional.of(staleExact));
                        when(codeAnalysisService.getAnalysisByCommitHash(1L, "abc123"))
                                        .thenReturn(Optional.of(staleCommit));

                        PullRequestAnalysisProcessor.CacheHitType result = processor.postAnalysisCacheIfExist(
                                        project, pullRequest, "abc123", 42L, reportingService, "placeholder-id",
                                        "main", "feature-branch", "current-identity", lockLease);

                        assertThat(result).isEqualTo(PullRequestAnalysisProcessor.CacheHitType.NONE);
                        verify(reportingService, never()).postAnalysisResults(any(), any(), anyLong(), any(), any());
                        verify(codeAnalysisService, never()).cloneAnalysisForPr(
                                        any(), any(), anyLong(), anyString(), anyString(), anyString(), anyString());
                }

                @Test
                @DisplayName("should return EXACT even when posting fails")
                void shouldReturnTrueEvenWhenPostingFails() throws IOException {
                        when(project.getId()).thenReturn(1L);
                        when(codeAnalysisService.getCodeAnalysisCache(1L, "abc123", 42L))
                                        .thenReturn(Optional.of(codeAnalysis));
                        when(codeAnalysis.getDiffFingerprint()).thenReturn("identity");
                        when(pullRequest.getId()).thenReturn(100L);
                        doThrow(new IOException("Post error")).when(reportingService).postAnalysisResults(any(), any(),
                                        anyLong(), any(), any());

                        PullRequestAnalysisProcessor.CacheHitType result = processor.postAnalysisCacheIfExist(
                                        project, pullRequest, "abc123", 42L, reportingService, "placeholder-id",
                                        "main", "feature-branch", "identity", lockLease);

                        // Should still return EXACT (cache existed)
                        assertThat(result).isEqualTo(PullRequestAnalysisProcessor.CacheHitType.EXACT);
                }
        }

        @Nested
        @DisplayName("Constructor")
        class ConstructorTests {

                @Test
                @DisplayName("should work without optional dependencies")
                void shouldWorkWithoutOptionalDependencies() {
                        PullRequestAnalysisProcessor processorWithoutOptional = new PullRequestAnalysisProcessor(
                                        pullRequestService,
                                        codeAnalysisService,
                                        taskImplementationEvidenceService,
                                        aiAnalysisClient,
                                        vcsServiceFactory,
                                        analysisLockService,
                                        analyzedCommitService,
                                        vcsClientProvider,
                                        fileSnapshotService,
                                        prIssueTrackingService,
                                        null, // astScopeEnricher
                                        null, // ragOperationsService
                                        null // eventPublisher
                        );

                        assertThat(processorWithoutOptional).isNotNull();
                }
        }

        @Nested
        @DisplayName("VCS Provider")
        class VcsProviderTests {

                @Test
                @DisplayName("should throw when no VCS connection configured")
                void shouldThrowWhenNoVcsConnectionConfigured() {
                        PrProcessRequest request = createRequest();
                        PullRequestAnalysisProcessor.EventConsumer consumer = mock(
                                        PullRequestAnalysisProcessor.EventConsumer.class);

                        when(project.getId()).thenReturn(1L);
                        when(project.getName()).thenReturn("Test Project");
                        when(project.getEffectiveVcsRepoInfo()).thenReturn(null);

                        when(analysisLockService.acquireLockWithWait(any(), anyString(), any(), anyString(), anyLong(),
                                        any()))
                                        .thenReturn(Optional.of("lock-key-123"));

                        assertThatThrownBy(() -> processor.process(request, consumer, project))
                                        .isInstanceOf(IllegalStateException.class)
                                        .hasMessageContaining("No VCS connection configured");
                }
        }
}
