package org.rostilos.codecrow.analysisengine.processor.analysis;

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
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.filecontent.service.FileSnapshotService;
import org.rostilos.codecrow.analysisengine.service.AstScopeEnricher;
import org.rostilos.codecrow.analysisengine.service.pr.PrIssueTrackingService;
import org.rostilos.codecrow.analysisengine.policy.ExecutionMode;
import org.rostilos.codecrow.analysisengine.policy.ExecutionPolicyConfig;
import org.rostilos.codecrow.analysisengine.policy.ExecutionPolicyRuntime;
import org.rostilos.codecrow.analysisengine.policy.FrozenExecutionPlan;
import org.rostilos.codecrow.analysisengine.policy.PolicyExecution;
import org.rostilos.codecrow.analysisengine.policy.PolicySelectionReason;
import org.rostilos.codecrow.analysisengine.policy.StableRolloutKey;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.springframework.context.ApplicationEventPublisher;

import java.io.IOException;
import java.time.Instant;
import java.util.*;

import static org.assertj.core.api.Assertions.assertThat;
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
        private AiAnalysisClient aiAnalysisClient;

        @Mock
        private VcsServiceFactory vcsServiceFactory;

        @Mock
        private AnalysisLockService analysisLockService;

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
                processor = new PullRequestAnalysisProcessor(
                                pullRequestService,
                                codeAnalysisService,
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

        private PrProcessRequest createRequest() {
                PrProcessRequest request = new PrProcessRequest();
                request.projectId = 1L;
                request.pullRequestId = 42L;
                request.commitHash = "abc123";
                request.sourceBranchName = "feature-branch";
                request.targetBranchName = "main";
                return request;
        }

        @Nested
        @DisplayName("process()")
        class ProcessTests {

                @Test
                @DisplayName("should successfully process PR analysis")
                void shouldSuccessfullyProcessPRAnalysis() throws Exception {
                        PrProcessRequest request = createRequest();
                        PullRequestAnalysisProcessor.EventConsumer consumer = mock(
                                        PullRequestAnalysisProcessor.EventConsumer.class);
                        doAnswer(invocation -> {
                                Map<String, Object> event = invocation.getArgument(0);
                                if ("telemetry".equals(event.get("type"))) {
                                        throw new IllegalStateException("telemetry sink unavailable");
                                }
                                return null;
                        }).when(consumer).accept(anyMap());

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

                        Map<String, Object> aiResponse = Map.of(
                                        "comment", "Review comment",
                                        "issues", List.of());
                        when(aiAnalysisClient.performAnalysis(any(), any())).thenReturn(aiResponse);

                        when(codeAnalysisService.createAnalysisFromAiResponse(
                                        any(), any(), anyLong(), anyString(), anyString(), anyString(), any(), any(),
                                        any(), any(), any(), any()))
                                        .thenReturn(codeAnalysis);

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
                        verify(consumer).accept(argThat(event -> isStageTelemetry(event, "acquisition", "complete")));
                        verify(consumer).accept(argThat(event -> isStageTelemetry(event, "persistence", "complete")));
                        verify(consumer).accept(argThat(event -> isStageTelemetry(event, "delivery", "complete")));
                }

                @Test
                @DisplayName("should cancel safely when a frozen candidate is killed before work starts")
                void shouldCancelFrozenCandidateWhenKillSwitchChanges() throws Exception {
                        ExecutionPolicyRuntime policyRuntime = mock(ExecutionPolicyRuntime.class);
                        PullRequestAnalysisProcessor policyProcessor = new PullRequestAnalysisProcessor(
                                        pullRequestService,
                                        codeAnalysisService,
                                        aiAnalysisClient,
                                        vcsServiceFactory,
                                        analysisLockService,
                                        analyzedCommitService,
                                        vcsClientProvider,
                                        fileSnapshotService,
                                        prIssueTrackingService,
                                        astScopeEnricher,
                                        ragOperationsService,
                                        eventPublisher,
                                        policyRuntime);
                        PrProcessRequest request = createRequest();
                        request.preAcquiredLockKey = "policy-lock";
                        Workspace workspace = mock(Workspace.class);
                        when(project.getId()).thenReturn(1L);
                        when(project.getWorkspace()).thenReturn(workspace);
                        when(workspace.getId()).thenReturn(10L);
                        Instant createdAt = Instant.parse("2026-07-14T12:00:00Z");
                        PolicyExecution candidate = new PolicyExecution(
                                        "pr:" + "a".repeat(64),
                                        "candidate-review-v2",
                                        ExecutionMode.ACTIVE,
                                        PolicySelectionReason.ACTIVE_ROLLOUT_SELECTED,
                                        42,
                                        true,
                                        createdAt);
                        FrozenExecutionPlan plan = new FrozenExecutionPlan(
                                        candidate.executionId(),
                                        "flags-before",
                                        "b".repeat(64),
                                        candidate,
                                        null,
                                        createdAt);
                        when(policyRuntime.freeze(anyString(), eq(StableRolloutKey.forProject(10L, 1L))))
                                        .thenReturn(plan);
                        when(policyRuntime.currentConfig()).thenReturn(new ExecutionPolicyConfig(
                                        "flags-killed",
                                        ExecutionMode.ACTIVE,
                                        "candidate-review-v2",
                                        10_000,
                                        "rollout-salt-v1",
                                        false,
                                        true));
                        List<Map<String, Object>> events = new ArrayList<>();

                        Map<String, Object> result = policyProcessor.process(request, events::add, project);

                        assertThat(result)
                                        .containsEntry("status", "cancelled")
                                        .containsEntry("reason", "policy_kill_switch");
                        assertThat(events).anyMatch(event ->
                                        "policy_selection".equals(event.get("type"))
                                                        && "candidate-review-v2".equals(event.get("policyVersion")));
                        assertThat(events).anyMatch(event ->
                                        "telemetry".equals(event.get("type"))
                                                        && "cancelled".equals(event.get("outcome")));
                        verifyNoInteractions(aiAnalysisClient);
                        verifyNoInteractions(vcsServiceFactory);
                        verify(analysisLockService, never()).releaseLock(anyString());
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
                        when(codeAnalysisService.getCodeAnalysisCache(anyLong(), anyString(), anyLong()))
                                        .thenReturn(Optional.of(codeAnalysis));

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

                        // No exact cache match, but commit hash matches from another PR
                        when(codeAnalysisService.getCodeAnalysisCache(anyLong(), anyString(), anyLong()))
                                        .thenReturn(Optional.empty());
                        CodeAnalysis sourceAnalysis = mock(CodeAnalysis.class);
                        when(sourceAnalysis.getPrNumber()).thenReturn(99L);
                        when(sourceAnalysis.getDiffFingerprint()).thenReturn("fp123");
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
                                        eq("abc123"), eq("main"), eq("feature-branch"), eq("fp123"));
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
                        verify(consumer).accept(argThat(event ->
                                        isStageTelemetry(event, "delivery", "failed")
                                                        && "vcs_delivery_failed".equals(event.get("reasonCode"))));
                        verify(consumer, never()).accept(argThat(event ->
                                        isStageTelemetry(event, "delivery", "complete")));
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

                        when(codeAnalysisService.getCodeAnalysisCache(anyLong(), anyString(), anyLong()))
                                        .thenReturn(Optional.empty());
                        CodeAnalysis sourceAnalysis = mock(CodeAnalysis.class);
                        when(sourceAnalysis.getPrNumber()).thenReturn(99L);
                        when(sourceAnalysis.getDiffFingerprint()).thenReturn("fp");
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
                        when(pullRequest.getId()).thenReturn(100L);

                        PullRequestAnalysisProcessor.CacheHitType result = processor.postAnalysisCacheIfExist(
                                        project, pullRequest, "abc123", 42L, reportingService, "placeholder-id",
                                        "main", "feature-branch");

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
                                        "main", "feature-branch");

                        assertThat(result).isEqualTo(PullRequestAnalysisProcessor.CacheHitType.NONE);
                        verify(reportingService, never()).postAnalysisResults(any(), any(), anyLong(), any(), any());
                }

                @Test
                @DisplayName("should return EXACT even when posting fails")
                void shouldReturnTrueEvenWhenPostingFails() throws IOException {
                        when(project.getId()).thenReturn(1L);
                        when(codeAnalysisService.getCodeAnalysisCache(1L, "abc123", 42L))
                                        .thenReturn(Optional.of(codeAnalysis));
                        when(pullRequest.getId()).thenReturn(100L);
                        doThrow(new IOException("Post error")).when(reportingService).postAnalysisResults(any(), any(),
                                        anyLong(), any(), any());

                        PullRequestAnalysisProcessor.CacheHitType result = processor.postAnalysisCacheIfExist(
                                        project, pullRequest, "abc123", 42L, reportingService, "placeholder-id",
                                        "main", "feature-branch");

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

        private static boolean isStageTelemetry(
                        Map<String, Object> event,
                        String stage,
                        String outcome) {
                return "telemetry".equals(event.get("type"))
                                && Integer.valueOf(1).equals(event.get("schemaVersion"))
                                && stage.equals(event.get("stage"))
                                && outcome.equals(event.get("outcome"))
                                && event.get("durationMs") instanceof Long
                                && event.get("itemCount") instanceof Integer
                                && !event.containsKey("source")
                                && !event.containsKey("prompt")
                                && !event.containsKey("credentials")
                                && !event.containsKey("project")
                                && !event.containsKey("branch")
                                && !event.containsKey("commitHash");
        }
}
