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
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequestImpl;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.coverage.CoverageAnalysisState;
import org.rostilos.codecrow.analysisengine.coverage.CoverageAnchorState;
import org.rostilos.codecrow.analysisengine.coverage.CoverageCounts;
import org.rostilos.codecrow.analysisengine.coverage.CoverageDisposition;
import org.rostilos.codecrow.analysisengine.coverage.CoverageLedgerPersistencePort;
import org.rostilos.codecrow.analysisengine.coverage.CoverageLedgerSeed;
import org.rostilos.codecrow.analysisengine.coverage.CoverageLedgerService;
import org.rostilos.codecrow.analysisengine.coverage.CoverageLedgerSnapshot;
import org.rostilos.codecrow.analysisengine.execution.ExecutionManifestService;
import org.rostilos.codecrow.analysisengine.execution.ImmutableExecutionManifest;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryService;
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
import org.rostilos.codecrow.analysisengine.policy.LatestHeadRegistration;
import org.rostilos.codecrow.analysisengine.policy.PolicyExecution;
import org.rostilos.codecrow.analysisengine.policy.PolicySelectionReason;
import org.rostilos.codecrow.analysisengine.policy.PublicationFence;
import org.rostilos.codecrow.analysisengine.policy.StableRolloutKey;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisMode;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.springframework.context.ApplicationEventPublisher;

import java.io.IOException;
import java.time.Instant;
import java.util.*;
import java.util.concurrent.atomic.AtomicReference;

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
                @DisplayName("should cancel safely at the first checkpoint after immutable candidate acquisition")
                void shouldCancelFrozenCandidateWhenKillSwitchChanges() throws Exception {
                        ExecutionPolicyRuntime policyRuntime = mock(ExecutionPolicyRuntime.class);
                        ExecutionManifestService manifestService = mock(ExecutionManifestService.class);
                        CoverageLedgerPersistencePort coveragePersistence =
                                        mock(CoverageLedgerPersistencePort.class);
                        AtomicReference<CoverageLedgerSnapshot> durableCoverage =
                                        new AtomicReference<>();
                        when(coveragePersistence.createOrLoad(any(CoverageLedgerSeed.class)))
                                        .thenAnswer(invocation -> {
                                                CoverageLedgerSeed seed = invocation.getArgument(0);
                                                List<CoverageDisposition> dispositions = seed.anchors().stream()
                                                                .map(anchor -> new CoverageDisposition(
                                                                                anchor.anchorId(),
                                                                                anchor.initialState(),
                                                                                anchor.reasonCode()))
                                                                .toList();
                                                CoverageLedgerSnapshot initial = new CoverageLedgerSnapshot(
                                                                seed.schemaVersion(),
                                                                seed.executionId(),
                                                                seed.artifactManifestDigest(),
                                                                seed.diffDigest(),
                                                                seed.diffByteLength(),
                                                                seed.ledgerDigest(),
                                                                seed.anchors(),
                                                                dispositions,
                                                                CoverageAnalysisState.PENDING,
                                                                CoverageCounts.fromDispositions(dispositions));
                                                durableCoverage.compareAndSet(null, initial);
                                                return durableCoverage.get();
                                        });
                        when(coveragePersistence.findByExecutionId(anyString()))
                                        .thenAnswer(ignored -> Optional.ofNullable(durableCoverage.get()));
                        when(coveragePersistence.compareAndSet(
                                        any(CoverageLedgerSnapshot.class),
                                        any(CoverageLedgerSnapshot.class)))
                                        .thenAnswer(invocation -> {
                                                CoverageLedgerSnapshot expected = invocation.getArgument(0);
                                                CoverageLedgerSnapshot replacement = invocation.getArgument(1);
                                                if (!durableCoverage.compareAndSet(expected, replacement)) {
                                                        throw new IllegalStateException(
                                                                        "coverage ledger changed concurrently");
                                                }
                                                return replacement;
                                        });
                        CoverageLedgerService coverageLedgerService =
                                        new CoverageLedgerService(coveragePersistence);
                        ReviewDeliveryService reviewDeliveryService =
                                        mock(ReviewDeliveryService.class);
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
                                        policyRuntime,
                                        manifestService,
                                        coverageLedgerService);
                        policyProcessor.setReviewDeliveryService(reviewDeliveryService);
                        when(reviewDeliveryService.registerCurrentHead(any()))
                                        .thenAnswer(invocation -> invocation.getArgument(0));
                        PrProcessRequest request = createRequest();
                        request.commitHash = "b".repeat(40);
                        request.preAcquiredLockKey = "policy-lock";
                        Workspace workspace = mock(Workspace.class);
                        VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
                        when(project.getId()).thenReturn(1L);
                        when(project.getWorkspace()).thenReturn(workspace);
                        when(workspace.getId()).thenReturn(10L);
                        when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
                        when(repoInfo.getVcsConnection()).thenReturn(vcsConnection);
                        when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
                        when(vcsServiceFactory.getReportingService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(reportingService);
                        when(vcsServiceFactory.getAiClientService(EVcsProvider.BITBUCKET_CLOUD))
                                        .thenReturn(aiClientService);
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
                        when(policyRuntime.freeze(
                                        anyString(),
                                        eq(StableRolloutKey.forProject(10L, 1L)),
                                        any(ExecutionPolicyConfig.class)))
                                        .thenReturn(plan);
                        ExecutionPolicyConfig beforeKill = new ExecutionPolicyConfig(
                                        "flags-before",
                                        ExecutionMode.ACTIVE,
                                        "candidate-review-v2",
                                        10_000,
                                        "rollout-salt-v1",
                                        false,
                                        false);
                        ExecutionPolicyConfig afterKill = new ExecutionPolicyConfig(
                                        "flags-killed",
                                        ExecutionMode.ACTIVE,
                                        "candidate-review-v2",
                                        10_000,
                                        "rollout-salt-v1",
                                        false,
                                        true);
                        when(policyRuntime.currentConfig()).thenReturn(beforeKill, afterKill);
                        PublicationFence latestHeadFence = mock(PublicationFence.class);
                        when(policyRuntime.publicationFence()).thenReturn(latestHeadFence);
                        when(latestHeadFence.claimLatestHeadGeneration(anyString(), any()))
                                        .thenReturn(1L);
                        when(latestHeadFence.findLatestHeadGeneration(any()))
                                        .thenReturn(OptionalLong.empty());
                        when(latestHeadFence.registerLatestHead(any(), any(), eq(1L)))
                                        .thenReturn(LatestHeadRegistration.ACCEPTED);
                        when(latestHeadFence.isLatestHead(any(), any())).thenReturn(true);
                        String rawDiff = """
                                        diff --git a/Changed.java b/Changed.java
                                        index 1111111..2222222 100644
                                        --- a/Changed.java
                                        +++ b/Changed.java
                                        @@ -1 +1 @@
                                        -before
                                        +line
                                        """;
                        AiAnalysisRequest exactRequest = AiAnalysisRequestImpl.builder()
                                        .withProjectId(1L)
                                        .withPullRequestId(42L)
                                        .withProjectVcsConnectionBindingInfo("workspace", "repository")
                                        .withVcsProvider("bitbucket_cloud")
                                        .withRawDiff(rawDiff)
                                        .withImmutableSnapshot(
                                                        "a".repeat(40),
                                                        request.getCommitHash(),
                                                        "c".repeat(40))
                                        .withPreviousCommitHash("a".repeat(40))
                                        .withCurrentCommitHash(request.getCommitHash())
                                        .withAnalysisMode(AnalysisMode.FULL)
                                        .withChangedFiles(List.of("Changed.java"))
                                        .withDeletedFiles(List.of())
                                        .withDiffSnippets(List.of())
                                        .build();
                        when(aiClientService.buildExactAiAnalysisRequests(
                                        any(), any(), any(), any(), any()))
                                        .thenAnswer(invocation -> {
                                                org.rostilos.codecrow.analysisengine.service.vcs.ExactHeadAdmission admission =
                                                                invocation.getArgument(4);
                                                admission.admit(request.getCommitHash());
                                                return List.of(exactRequest);
                                        });
                        ImmutableExecutionManifest[] persistedManifest = {null};
                        when(manifestService.persistBeforeWork(
                                        any(ImmutableExecutionManifest.class), anyList()))
                                        .thenAnswer(invocation -> {
                                                persistedManifest[0] = invocation.getArgument(0);
                                                return persistedManifest[0];
                                        });
                        when(manifestService.requireVerified(candidate.executionId()))
                                        .thenAnswer(ignored -> persistedManifest[0]);
                        when(pullRequestService.createOrUpdatePullRequest(
                                        1L,
                                        42L,
                                        request.getCommitHash(),
                                        "feature-branch",
                                        "main",
                                        project))
                                        .thenReturn(pullRequest);
                        List<Map<String, Object>> events = new ArrayList<>();

                        Map<String, Object> result = policyProcessor.process(request, events::add, project);

                        assertThat(result)
                                        .containsEntry("status", "cancelled")
                                        .containsEntry("reason", "policy_kill_switch")
                                        .containsEntry("executionId", candidate.executionId())
                                        .containsEntry(
                                                        "artifactManifestDigest",
                                                        persistedManifest[0].artifactManifestDigest());
                        assertThat(events).allSatisfy(event -> assertThat(event)
                                        .containsEntry("executionId", candidate.executionId())
                                        .containsEntry(
                                                        "artifactManifestDigest",
                                                        persistedManifest[0].artifactManifestDigest()));
                        assertThat(events).anyMatch(event ->
                                        "policy_selection".equals(event.get("type"))
                                                        && "candidate-review-v2".equals(event.get("policyVersion")));
                        assertThat(events).anyMatch(event ->
                                        "telemetry".equals(event.get("type"))
                                                        && "cancelled".equals(event.get("outcome")));
                        assertThat(durableCoverage.get().analysisState())
                                        .isEqualTo(CoverageAnalysisState.FAILED);
                        assertThat(durableCoverage.get().dispositions())
                                        .singleElement()
                                        .satisfies(disposition -> {
                                                assertThat(disposition.state())
                                                                .isEqualTo(CoverageAnchorState.FAILED);
                                                assertThat(disposition.reasonCode())
                                                                .isEqualTo("analysis_cancelled");
                                        });
                        verifyNoInteractions(aiAnalysisClient);
                        verify(aiClientService).buildExactAiAnalysisRequests(
                                        eq(project), eq(request), eq(Optional.empty()), eq(List.of()), any());
                        verify(aiClientService, never()).buildAiAnalysisRequests(
                                        any(), any(), any(), any());
                        verify(aiClientService).discardUndispatchedAiAnalysisRequest(
                                        exactRequest);
                        verify(manifestService).persistBeforeWork(
                                        any(ImmutableExecutionManifest.class), anyList());
                        verify(manifestService).requireVerified(candidate.executionId());
                        verify(reportingService, never()).postAnalysisResults(
                                        any(), any(), anyLong(), any(), any());
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
                @DisplayName("should recompute when an exact final-result cache row exists")
                void shouldRecomputeWhenExactFinalResultExists() throws Exception {
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
                        lenient().when(codeAnalysisService.getCodeAnalysisCache(anyLong(), anyString(), anyLong()))
                                        .thenReturn(Optional.of(codeAnalysis));
                        when(codeAnalysisService.getAllPrAnalyses(anyLong(), anyLong()))
                                        .thenReturn(List.of());
                        when(aiClientService.buildAiAnalysisRequests(any(), any(), any(), anyList()))
                                        .thenReturn(List.of(aiAnalysisRequest));
                        when(aiAnalysisRequest.getRawDiff()).thenReturn("+fresh\n-stale\n");
                        when(aiAnalysisRequest.getChangedFiles()).thenReturn(List.of("file.java"));

                        Map<String, Object> freshResponse = Map.of(
                                        "comment", "Fresh review",
                                        "issues", List.of());
                        when(aiAnalysisClient.performAnalysis(eq(aiAnalysisRequest), any()))
                                        .thenReturn(freshResponse);
                        CodeAnalysis freshAnalysis = mock(CodeAnalysis.class);
                        when(freshAnalysis.getIssues()).thenReturn(List.of());
                        when(codeAnalysisService.createAnalysisFromAiResponse(
                                        any(), eq(freshResponse), anyLong(), anyString(), anyString(),
                                        anyString(), any(), any(), any(), any(), any(), any()))
                                        .thenReturn(freshAnalysis);

                        Map<String, Object> result = processor.process(request, consumer, project);

                        assertThat(result).containsEntry("comment", "Fresh review");
                        assertThat(result).doesNotContainKey("cached");
                        verify(aiAnalysisClient).performAnalysis(eq(aiAnalysisRequest), any());
                        verify(codeAnalysisService, never())
                                        .getCodeAnalysisCache(anyLong(), anyString(), anyLong());
                        verify(reportingService).postAnalysisResults(eq(freshAnalysis), any(), anyLong(), anyLong(),
                                        any());
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
