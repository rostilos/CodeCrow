package org.rostilos.codecrow.analysisengine.processor.analysis;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyMap;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.OptionalLong;
import java.util.concurrent.atomic.AtomicReference;
import java.util.stream.Stream;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.mockito.ArgumentCaptor;
import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.analysisengine.aiclient.AiAnalysisClient;
import org.rostilos.codecrow.analysisengine.coverage.CoverageAnalysisState;
import org.rostilos.codecrow.analysisengine.coverage.CoverageCounts;
import org.rostilos.codecrow.analysisengine.coverage.CoverageLedgerService;
import org.rostilos.codecrow.analysisengine.coverage.CoverageLedgerSnapshot;
import org.rostilos.codecrow.analysisengine.coverage.CoverageReceipt;
import org.rostilos.codecrow.analysisengine.coverage.CoverageWorkPlan;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequestImpl;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.FileContentDto;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.PrEnrichmentDataDto;
import org.rostilos.codecrow.analysisengine.dto.request.processor.AnalysisProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryIntent;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryOutcome;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryService;
import org.rostilos.codecrow.analysisengine.delivery.ReviewDeliveryState;
import org.rostilos.codecrow.analysisengine.execution.ArtifactManifestEntry;
import org.rostilos.codecrow.analysisengine.execution.ExecutionManifestService;
import org.rostilos.codecrow.analysisengine.execution.ExecutionArtifactPayload;
import org.rostilos.codecrow.analysisengine.execution.ExecutionManifestPersistencePort;
import org.rostilos.codecrow.analysisengine.execution.ImmutableExecutionManifest;
import org.rostilos.codecrow.analysisengine.exception.AnalysisLockedException;
import org.rostilos.codecrow.analysisengine.policy.ExecutionMode;
import org.rostilos.codecrow.analysisengine.policy.ExecutionPolicyConfig;
import org.rostilos.codecrow.analysisengine.policy.ExecutionPolicyRuntime;
import org.rostilos.codecrow.analysisengine.policy.FrozenExecutionPlan;
import org.rostilos.codecrow.analysisengine.policy.LatestHeadRegistration;
import org.rostilos.codecrow.analysisengine.policy.PolicyExecution;
import org.rostilos.codecrow.analysisengine.policy.PolicySelectionReason;
import org.rostilos.codecrow.analysisengine.policy.PublicationFence;
import org.rostilos.codecrow.analysisengine.policy.PublicationReservation;
import org.rostilos.codecrow.analysisengine.policy.StableRolloutKey;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.analysisengine.service.AstScopeEnricher;
import org.rostilos.codecrow.analysisengine.service.PullRequestService;
import org.rostilos.codecrow.analysisengine.service.pr.PrIssueTrackingService;
import org.rostilos.codecrow.analysisengine.service.vcs.ExactHeadAdmission;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsReportingService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.commitgraph.service.AnalyzedCommitService;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisMode;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.pullrequest.PullRequest;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.service.CodeAnalysisService;
import org.rostilos.codecrow.filecontent.service.FileSnapshotService;
import org.rostilos.codecrow.events.analysis.AnalysisCompletedEvent;
import org.rostilos.codecrow.events.analysis.AnalysisStartedEvent;
import org.rostilos.codecrow.queue.RedisQueueService;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.springframework.context.ApplicationEvent;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.web.client.RestTemplate;

/** Behavior contracts for the immutable, coverage-bound candidate path. */
class PullRequestAnalysisProcessorExecutionManifestContractTest {
    private static final String BASE_SHA = "a".repeat(40);
    private static final String HEAD_SHA = "b".repeat(40);
    private static final String MERGE_BASE_SHA = "c".repeat(40);
    private static final String RAW_DIFF =
            "diff --git a/src/A.java b/src/A.java\n@@ -1 +1 @@\n-old\n+new\n";
    private static final String INDEX_VERSION = "rag-commit-" + BASE_SHA;
    private static final String CANDIDATE_INDEX_VERSION = "rag-disabled";
    private static final Instant CREATED_AT = Instant.parse("2026-07-15T12:00:00Z");

    private PullRequestService pullRequestService;
    private CodeAnalysisService codeAnalysisService;
    private AiAnalysisClient aiAnalysisClient;
    private VcsServiceFactory vcsServiceFactory;
    private AnalysisLockService analysisLockService;
    private AnalyzedCommitService analyzedCommitService;
    private VcsClientProvider vcsClientProvider;
    private FileSnapshotService fileSnapshotService;
    private PrIssueTrackingService prIssueTrackingService;
    private AstScopeEnricher astScopeEnricher;
    private RagOperationsService ragOperationsService;
    private ApplicationEventPublisher eventPublisher;
    private ExecutionPolicyRuntime executionPolicyRuntime;
    private VcsReportingService reportingService;
    private PublicationFence publicationFence;
    private CoverageLedgerService coverageLedgerService;
    private AtomicReference<ImmutableExecutionManifest> coverageManifest;
    private ReviewDeliveryService reviewDeliveryService;
    private AtomicReference<ReviewDeliveryIntent> deliveryIntent;
    private Project project;
    private PullRequest pullRequest;
    private CodeAnalysis analysis;
    private CodeAnalysis cachedAnalysis;
    private Workspace workspace;
    private PrProcessRequest request;
    private AiAnalysisRequest exactRequest;

    @BeforeEach
    void setUp() {
        pullRequestService = mock(PullRequestService.class);
        codeAnalysisService = mock(CodeAnalysisService.class);
        aiAnalysisClient = mock(AiAnalysisClient.class);
        vcsServiceFactory = mock(VcsServiceFactory.class);
        analysisLockService = mock(AnalysisLockService.class);
        analyzedCommitService = mock(AnalyzedCommitService.class);
        vcsClientProvider = mock(VcsClientProvider.class);
        fileSnapshotService = mock(FileSnapshotService.class);
        prIssueTrackingService = mock(PrIssueTrackingService.class);
        astScopeEnricher = mock(AstScopeEnricher.class);
        ragOperationsService = mock(RagOperationsService.class);
        eventPublisher = mock(ApplicationEventPublisher.class);
        executionPolicyRuntime = mock(ExecutionPolicyRuntime.class);
        reportingService = mock(VcsReportingService.class);
        publicationFence = mock(PublicationFence.class);
        coverageLedgerService = mock(CoverageLedgerService.class);
        coverageManifest = new AtomicReference<>();
        reviewDeliveryService = mock(ReviewDeliveryService.class);
        deliveryIntent = new AtomicReference<>();
        project = mock(Project.class);
        pullRequest = mock(PullRequest.class);
        analysis = mock(CodeAnalysis.class);
        cachedAnalysis = mock(CodeAnalysis.class);
        workspace = mock(Workspace.class);
        exactRequest = exactRequest();

        request = new PrProcessRequest();
        request.projectId = 7L;
        request.pullRequestId = 42L;
        request.commitHash = HEAD_SHA;
        request.sourceBranchName = "feature";
        request.targetBranchName = "main";
        request.analysisType = AnalysisType.PR_REVIEW;
        request.preAcquiredLockKey = "pre-acquired";

        VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
        VcsConnection connection = mock(VcsConnection.class);
        when(project.getId()).thenReturn(7L);
        when(project.getWorkspace()).thenReturn(workspace);
        when(workspace.getId()).thenReturn(9L);
        when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
        when(repoInfo.getVcsConnection()).thenReturn(connection);
        when(repoInfo.getRepoWorkspace()).thenReturn("workspace");
        when(repoInfo.getRepoSlug()).thenReturn("repository");
        when(connection.getProviderType()).thenReturn(EVcsProvider.GITHUB);
        when(vcsServiceFactory.getReportingService(EVcsProvider.GITHUB))
                .thenReturn(reportingService);
        when(pullRequestService.createOrUpdatePullRequest(
                        7L, 42L, HEAD_SHA, "feature", "main", project))
                .thenReturn(pullRequest);
        when(pullRequest.getId()).thenReturn(100L);
        when(codeAnalysisService.getAllPrAnalyses(7L, 42L)).thenReturn(List.of());
        when(codeAnalysisService.getCodeAnalysisCache(7L, HEAD_SHA, 42L))
                .thenReturn(Optional.empty());
        when(codeAnalysisService.getAnalysisByCommitHash(7L, HEAD_SHA))
                .thenReturn(Optional.empty());
        when(codeAnalysisService.getAnalysisByDiffFingerprint(eq(7L), anyString()))
                .thenReturn(Optional.empty());
        when(codeAnalysisService.createAnalysisFromAiResponse(
                        any(), any(), anyLong(), anyString(), anyString(), anyString(),
                        any(), any(), anyString(), anyMap(), isNull(), isNull()))
                .thenReturn(analysis);
        when(codeAnalysisService.saveAnalysis(analysis)).thenReturn(analysis);
        when(analysis.getIssues()).thenReturn(List.of());
        when(analysis.getId()).thenReturn(101L);
        when(ragOperationsService.ensureRagIndexUpToDate(any(), anyString(), any()))
                .thenReturn(true);
        when(executionPolicyRuntime.currentConfig()).thenReturn(runtimeConfig());
        when(executionPolicyRuntime.publicationFence()).thenReturn(publicationFence);
        when(publicationFence.claimLatestHeadGeneration(anyString(), any()))
                .thenReturn(1L);
        when(publicationFence.findLatestHeadGeneration(any()))
                .thenReturn(OptionalLong.empty());
        when(publicationFence.registerLatestHead(any(), any(), eq(1L)))
                .thenReturn(LatestHeadRegistration.ACCEPTED);
        when(publicationFence.isLatestHead(any(), any())).thenReturn(true);
        when(publicationFence.reserve(any(), any()))
                .thenReturn(PublicationReservation.DUPLICATE);
        when(coverageLedgerService.initializeOrVerify(any(), anyString(), any()))
                .thenAnswer(invocation -> {
                    ImmutableExecutionManifest manifest = invocation.getArgument(0);
                    coverageManifest.set(manifest);
                    return coverageWorkPlan(manifest);
                });
        when(coverageLedgerService.requireSnapshot(anyString()))
                .thenAnswer(ignored -> coverageSnapshot(
                        coverageManifest.get(), CoverageAnalysisState.PENDING));
        when(coverageLedgerService.reconcileProducer(any(), any(CoverageReceipt.class)))
                .thenAnswer(invocation -> coverageSnapshot(
                        invocation.getArgument(0), CoverageAnalysisState.COMPLETE));
        when(reviewDeliveryService.registerCurrentHead(any()))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(reviewDeliveryService.enqueue(any())).thenAnswer(invocation -> {
            ReviewDeliveryIntent intent = invocation.getArgument(0);
            deliveryIntent.set(intent);
            return Optional.of(intent);
        });
        when(reviewDeliveryService.attempt(anyString(), any(Instant.class)))
                .thenAnswer(invocation -> {
                    ReviewDeliveryIntent intent = deliveryIntent.get();
                    return new ReviewDeliveryOutcome(
                            ReviewDeliveryState.DELIVERED,
                            intent.intentId(),
                            intent.idempotencyKey(),
                            1,
                            null,
                            "offline-provider-receipt");
                });
    }

    @Test
    void candidateRequiresDurableDeliveryBeforeAcquisition() {
        PullRequestAnalysisProcessor withoutDelivery = new PullRequestAnalysisProcessor(
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
                executionPolicyRuntime);

        assertThatThrownBy(() -> withoutDelivery.process(request, ignored -> { }, project))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("requires durable delivery");
        verify(vcsServiceFactory, never()).getAiClientService(any());
    }

    @Test
    void candidateSkipsLegacyCachesPersistsImmediatelyAndUsesManifestQueueOverload()
            throws Exception {
        FrozenExecutionPlan plan = candidatePlan();
        when(executionPolicyRuntime.freeze(anyString(), any(StableRolloutKey.class), any(ExecutionPolicyConfig.class)))
                .thenReturn(plan);
        when(codeAnalysisService.getCodeAnalysisCache(7L, HEAD_SHA, 42L))
                .thenReturn(Optional.of(cachedAnalysis));
        when(codeAnalysisService.getAnalysisByCommitHash(7L, HEAD_SHA))
                .thenReturn(Optional.of(cachedAnalysis));
        when(codeAnalysisService.getAnalysisByDiffFingerprint(eq(7L), anyString()))
                .thenReturn(Optional.of(cachedAnalysis));

        List<String> sequence = new ArrayList<>();
        doAnswer(invocation -> {
            sequence.add("pull-request-persist");
            return pullRequest;
        }).when(pullRequestService).createOrUpdatePullRequest(
                7L, 42L, HEAD_SHA, "feature", "main", project);
        RecordingVcsAiClientService vcs = new RecordingVcsAiClientService(
                exactRequest, sequence);
        when(vcsServiceFactory.getAiClientService(EVcsProvider.GITHUB)).thenReturn(vcs);

        ExecutionManifestService manifestService = mock(ExecutionManifestService.class);
        AtomicReference<ImmutableExecutionManifest> persisted = new AtomicReference<>();
        doAnswer(invocation -> {
            sequence.add("manifest-persist");
            ImmutableExecutionManifest manifest = invocation.getArgument(0);
            persisted.set(manifest);
            return manifest;
        }).when(manifestService).persistBeforeWork(
                any(ImmutableExecutionManifest.class), anyList());
        when(manifestService.requireVerified(plan.primary().executionId()))
                .thenAnswer(ignored -> {
                    sequence.add("manifest-reload");
                    return persisted.get();
                });
        List<Map<String, Object>> emitted = new ArrayList<>();
        doAnswer(invocation -> {
            sequence.add("ai-v1");
            ImmutableExecutionManifest manifest = invocation.getArgument(4);
            @SuppressWarnings("unchecked")
            java.util.function.Consumer<Map<String, Object>> eventConsumer =
                    invocation.getArgument(1);
            eventConsumer.accept(Map.of(
                    "type", "telemetry",
                    "stage", "generation",
                    "outcome", "running",
                    "executionId", manifest.executionId(),
                    "artifactManifestDigest", manifest.artifactManifestDigest()));
            return candidateResponse(manifest, CANDIDATE_INDEX_VERSION);
        }).when(aiAnalysisClient).performAnalysis(
                eq(exactRequest), any(), eq(plan.primary()), eq(CANDIDATE_INDEX_VERSION),
                any(ImmutableExecutionManifest.class), any(CoverageWorkPlan.class));
        when(analysis.hasExecutionIdentity()).thenReturn(true);
        when(analysis.getExecutionId()).thenReturn(plan.primary().executionId());
        when(analysis.getArtifactManifestDigest()).thenAnswer(
                ignored -> persisted.get().artifactManifestDigest());
        when(analysis.getProject()).thenReturn(project);
        when(analysis.getPrNumber()).thenReturn(42L);
        when(analysis.getCommitHash()).thenReturn(HEAD_SHA);
        when(codeAnalysisService.createCandidateAnalysisFromAiResponse(
                        any(), anyMap(), anyLong(), anyString(), anyString(), anyString(),
                        any(), any(), anyString(), anyMap(), isNull(), isNull(),
                        anyString(), anyString()))
                .thenReturn(analysis);

        PullRequestAnalysisProcessor processor = processorWithManifestService(manifestService);
        Map<String, Object> result = processor.process(request, emitted::add, project);

        ArgumentCaptor<String> executionIdentity = ArgumentCaptor.forClass(String.class);
        verify(executionPolicyRuntime).freeze(
                executionIdentity.capture(),
                eq(StableRolloutKey.forProject(9L, 7L)),
                eq(runtimeConfig()));
        assertThat(executionIdentity.getValue()).isEqualTo(
                PullRequestAnalysisProcessor.candidateExecutionIdentity(
                        project,
                        request,
                        runtimeConfig(),
                        exactRequest,
                        CANDIDATE_INDEX_VERSION));

        assertThat(result)
                .containsEntry("comment", "review")
                .containsEntry("issues", List.of())
                .containsEntry("analysisState", "COMPLETE")
                .containsEntry("executionId", plan.primary().executionId())
                .containsEntry(
                        "artifactManifestDigest",
                        persisted.get().artifactManifestDigest());
        @SuppressWarnings("unchecked")
        Map<String, Object> telemetry = (Map<String, Object>) result.get("telemetry");
        assertThat(telemetry)
                .containsEntry("finalizationState", "terminal");
        assertThat(sequence).containsExactly(
                "exact-acquisition",
                "manifest-persist",
                "manifest-reload",
                "pull-request-persist",
                "ai-v1");
        assertThat(emitted)
                .filteredOn(event -> "policy_selection".equals(event.get("type"))
                        || "telemetry".equals(event.get("type")))
                .isNotEmpty()
                .allSatisfy(event -> assertCandidateBinding(event, persisted.get()));
        assertThat(emitted)
                .filteredOn(event -> event.containsKey("stage"))
                .extracting(event -> event.get("stage"))
                .contains("acquisition", "retrieval", "generation", "persistence", "delivery");

        ArgumentCaptor<ApplicationEvent> lifecycleEvents =
                ArgumentCaptor.forClass(ApplicationEvent.class);
        verify(eventPublisher, org.mockito.Mockito.atLeast(2))
                .publishEvent(lifecycleEvents.capture());
        assertThat(lifecycleEvents.getAllValues())
                .filteredOn(AnalysisStartedEvent.class::isInstance)
                .singleElement()
                .satisfies(raw -> assertStartedBinding(
                        (AnalysisStartedEvent) raw, persisted.get()));
        assertThat(lifecycleEvents.getAllValues())
                .filteredOn(AnalysisCompletedEvent.class::isInstance)
                .singleElement()
                .satisfies(raw -> assertCompletedBinding(
                        (AnalysisCompletedEvent) raw, persisted.get()));
        assertThat(vcs.exactCalls).isEqualTo(1);
        assertThat(vcs.legacyCalls).isZero();
        verify(codeAnalysisService, never()).getCodeAnalysisCache(anyLong(), anyString(), anyLong());
        verify(codeAnalysisService, never()).getAnalysisByCommitHash(anyLong(), anyString());
        verify(codeAnalysisService, never()).getAnalysisByDiffFingerprint(anyLong(), anyString());
        verify(ragOperationsService, never())
                .ensureRagIndexUpToDate(any(), anyString(), any());
        verify(ragOperationsService, never()).getIndexVersion(any(), anyString());
        verify(vcsClientProvider, never()).getClient(any());

        ImmutableExecutionManifest manifest = persisted.get();
        assertThat(manifest).isNotNull();
        assertThat(manifest.executionId()).isEqualTo(plan.primary().executionId());
        assertThat(manifest.projectId()).isEqualTo(7L);
        assertThat(manifest.repositoryId()).isEqualTo("github:workspace/repository");
        assertThat(manifest.pullRequestId()).isEqualTo(42L);
        assertThat(manifest.baseSha()).isEqualTo(BASE_SHA);
        assertThat(manifest.headSha()).isEqualTo(HEAD_SHA);
        assertThat(manifest.mergeBaseSha()).isEqualTo(MERGE_BASE_SHA);
        assertThat(manifest.policyVersion()).isEqualTo(plan.primary().policyVersion());
        assertThat(manifest.creationFence())
                .matches("creation:[0-9a-f]{64}")
                .doesNotContain(plan.configRevision());
        manifest.verifyRawDiff(RAW_DIFF.getBytes(StandardCharsets.UTF_8));

        @SuppressWarnings("unchecked")
        ArgumentCaptor<List<ExecutionArtifactPayload>> artifacts =
                ArgumentCaptor.forClass(List.class);
        verify(manifestService).persistBeforeWork(eq(manifest), artifacts.capture());
        assertThat(artifacts.getValue()).hasSize(2);
        ExecutionArtifactPayload rawDiff = artifacts.getValue().stream()
                .filter(payload -> payload.entry().kind()
                        == ArtifactManifestEntry.Kind.RAW_DIFF)
                .findFirst()
                .orElseThrow();
        assertThat(rawDiff.content())
                .isEqualTo(RAW_DIFF.getBytes(StandardCharsets.UTF_8));
        ExecutionArtifactPayload enrichment = artifacts.getValue().stream()
                .filter(payload -> payload.entry().kind()
                        == ArtifactManifestEntry.Kind.PR_ENRICHMENT)
                .findFirst()
                .orElseThrow();
        assertThat(new String(enrichment.content(), StandardCharsets.UTF_8))
                .contains("src/A.java", "\"skipped\":true");
        verify(aiAnalysisClient).performAnalysis(
                eq(exactRequest), any(), eq(plan.primary()),
                eq(CANDIDATE_INDEX_VERSION), eq(manifest), any(CoverageWorkPlan.class));
        verify(codeAnalysisService).createCandidateAnalysisFromAiResponse(
                eq(project),
                anyMap(),
                eq(42L),
                eq("main"),
                eq("feature"),
                eq(HEAD_SHA),
                isNull(),
                isNull(),
                anyString(),
                anyMap(),
                isNull(),
                isNull(),
                eq(manifest.executionId()),
                eq(manifest.artifactManifestDigest()));
        verify(codeAnalysisService, never()).createAnalysisFromAiResponse(
                any(), anyMap(), anyLong(), anyString(), anyString(), anyString(),
                any(), any(), anyString(), anyMap(), any(), any());
        verify(fileSnapshotService, never())
                .persistSnapshotsForPr(any(), any(), anyMap(), anyString());
        verify(fileSnapshotService, never()).getFileContentsMap(anyLong());
        verify(prIssueTrackingService, never())
                .trackPrIteration(any(), any(), anyMap(), anyMap());
    }

    @Test
    void creationFenceIsStableForOneFrozenPlanAndSeparatesDistinctCreations() {
        FrozenExecutionPlan frozen = candidatePlan();
        String first = PullRequestAnalysisProcessor.creationFence(frozen);

        assertThat(PullRequestAnalysisProcessor.creationFence(frozen))
                .isEqualTo(first);

        Instant later = CREATED_AT.plusSeconds(1);
        PolicyExecution distinctPrimary = new PolicyExecution(
                "candidate-pr-43",
                "candidate-review-v2",
                ExecutionMode.ACTIVE,
                PolicySelectionReason.ACTIVE_ROLLOUT_SELECTED,
                7,
                true,
                later);
        FrozenExecutionPlan distinct = new FrozenExecutionPlan(
                distinctPrimary.executionId(),
                frozen.configRevision(),
                frozen.stableRolloutKeyHash(),
                distinctPrimary,
                null,
                later);

        assertThat(PullRequestAnalysisProcessor.creationFence(distinct))
                .matches("creation:[0-9a-f]{64}")
                .isNotEqualTo(first);
    }

    @ParameterizedTest(name = "empty coverage persists authoritative diff: {index}")
    @ValueSource(strings = {
            "",
            "diff --git a/docs/guide.md b/docs/guide.md\n@@ -1 +1 @@\n-old\n+new\n"
    })
    void emptyCoveragePersistsReloadsAndBindsItsTerminalLifecycle(
            String authoritativeDiff)
            throws Exception {
        FrozenExecutionPlan plan = candidatePlan();
        when(executionPolicyRuntime.freeze(anyString(), any(StableRolloutKey.class), any(ExecutionPolicyConfig.class)))
                .thenReturn(plan);
        AiAnalysisRequest acquisitionOnly = acquisitionOnlyRequest(authoritativeDiff);
        RecordingVcsAiClientService vcs = new RecordingVcsAiClientService(
                acquisitionOnly, new ArrayList<>());
        when(vcsServiceFactory.getAiClientService(EVcsProvider.GITHUB)).thenReturn(vcs);

        List<String> order = new ArrayList<>();
        InMemoryManifestPersistence persistence = new InMemoryManifestPersistence(order);
        ExecutionManifestService manifestService = new ExecutionManifestService(persistence);
        doAnswer(invocation -> {
            order.add("pull-request-persist");
            return pullRequest;
        }).when(pullRequestService).createOrUpdatePullRequest(
                7L, 42L, HEAD_SHA, "feature", "main", project);
        List<Map<String, Object>> emitted = new ArrayList<>();
        doAnswer(ignored -> coverageSnapshot(
                coverageManifest.get(), CoverageAnalysisState.EMPTY))
                .when(coverageLedgerService).requireSnapshot(anyString());
        PullRequestAnalysisProcessor processor = processorWithManifestService(manifestService);

        Map<String, Object> result = processor.process(request, emitted::add, project);

        assertThat(result)
                .containsEntry("status", "empty")
                .containsEntry("analysisState", "EMPTY")
                .containsEntry("executionId", plan.primary().executionId())
                .containsKey("artifactManifestDigest");
        assertThat(order).containsExactly(
                "manifest-persist", "manifest-reload", "pull-request-persist");
        assertThat(persistence.createOrLoadCalls).isEqualTo(1);
        ImmutableExecutionManifest persisted = new ExecutionManifestService(persistence)
                .requireVerified(plan.primary().executionId());
        assertThat(result.get("artifactManifestDigest"))
                .isEqualTo(persisted.artifactManifestDigest());
        persisted.verifyRawDiff(authoritativeDiff.getBytes(StandardCharsets.UTF_8));
        assertThat(persisted.inputArtifacts())
                .extracting(ArtifactManifestEntry::kind)
                .containsExactlyInAnyOrder(
                        ArtifactManifestEntry.Kind.RAW_DIFF,
                        ArtifactManifestEntry.Kind.PR_ENRICHMENT);
        assertThat(emitted)
                .filteredOn(event -> "policy_selection".equals(event.get("type"))
                        || "telemetry".equals(event.get("type")))
                .isNotEmpty()
                .allSatisfy(event -> assertCandidateBinding(event, persisted));

        ArgumentCaptor<ApplicationEvent> lifecycleEvents =
                ArgumentCaptor.forClass(ApplicationEvent.class);
        verify(eventPublisher, org.mockito.Mockito.atLeast(2))
                .publishEvent(lifecycleEvents.capture());
        assertThat(lifecycleEvents.getAllValues())
                .filteredOn(AnalysisStartedEvent.class::isInstance)
                .singleElement()
                .satisfies(raw -> assertStartedBinding(
                        (AnalysisStartedEvent) raw, persisted));
        assertThat(lifecycleEvents.getAllValues())
                .filteredOn(AnalysisCompletedEvent.class::isInstance)
                .singleElement()
                .satisfies(raw -> assertCompletedBinding(
                        (AnalysisCompletedEvent) raw, persisted));
        verify(aiAnalysisClient, never()).performAnalysis(any(), any());
        verify(aiAnalysisClient, never()).performAnalysis(any(), any(), any());
        verify(aiAnalysisClient, never()).performAnalysis(any(), any(), any(), anyString());
        verify(aiAnalysisClient, never()).performAnalysis(
                any(), any(), any(), anyString(), any(ImmutableExecutionManifest.class),
                any(CoverageWorkPlan.class));
        verify(codeAnalysisService, never()).createCandidateAnalysisFromAiResponse(
                any(), anyMap(), anyLong(), anyString(), anyString(), anyString(),
                any(), any(), anyString(), anyMap(), any(), any(), anyString(), anyString());
    }

    @ParameterizedTest(name = "candidate rejects malformed exact acquisition: {0}")
    @MethodSource("invalidExactAcquisitionResults")
    void candidateRejectsInvalidExactAcquisitionResultsBeforeManifestPersistence(
            String caseName,
            List<AiAnalysisRequest> invalidAcquisition) throws Exception {
        FrozenExecutionPlan plan = candidatePlan();
        when(executionPolicyRuntime.freeze(anyString(), any(StableRolloutKey.class), any(ExecutionPolicyConfig.class)))
                .thenReturn(plan);
        VcsAiClientService vcs = mock(VcsAiClientService.class);
        when(vcs.buildExactAiAnalysisRequests(
                        eq(project), eq(request), any(), anyList(), any()))
                .thenReturn(invalidAcquisition);
        when(vcsServiceFactory.getAiClientService(EVcsProvider.GITHUB)).thenReturn(vcs);
        ExecutionManifestService manifestService = mock(ExecutionManifestService.class);
        PullRequestAnalysisProcessor processor = processorWithManifestService(manifestService);

        assertThatThrownBy(() -> processor.process(request, ignored -> { }, project))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("exact acquisition")
                .hasMessageContaining("one manifest-bound request");

        verify(manifestService, never()).persistBeforeWork(any(), anyList());
        verify(pullRequestService, never()).createOrUpdatePullRequest(
                7L, 42L, HEAD_SHA, "feature", "main", project);
        verify(aiAnalysisClient, never()).performAnalysis(
                any(), any(), any(), anyString(), any(ImmutableExecutionManifest.class),
                any(CoverageWorkPlan.class));
    }

    @ParameterizedTest(name = "coverage accepts nullable acquired inventory: {0}")
    @MethodSource("nullableCandidateInventories")
    void candidateNullInventoriesResolveToAuthoritativeEmptyCoverage(
            String caseName,
            AiAnalysisRequest acquiredRequest) throws Exception {
        FrozenExecutionPlan plan = candidatePlan();
        when(executionPolicyRuntime.freeze(anyString(), any(StableRolloutKey.class), any(ExecutionPolicyConfig.class)))
                .thenReturn(plan);
        RecordingVcsAiClientService vcs = new RecordingVcsAiClientService(
                acquiredRequest, new ArrayList<>());
        when(vcsServiceFactory.getAiClientService(EVcsProvider.GITHUB)).thenReturn(vcs);
        InMemoryManifestPersistence persistence = new InMemoryManifestPersistence();
        doAnswer(ignored -> coverageSnapshot(
                coverageManifest.get(), CoverageAnalysisState.EMPTY))
                .when(coverageLedgerService).requireSnapshot(anyString());
        PullRequestAnalysisProcessor processor = processorWithManifestService(
                new ExecutionManifestService(persistence));

        Map<String, Object> result = processor.process(request, ignored -> { }, project);

        assertThat(result)
                .containsEntry("status", "empty")
                .containsEntry("analysisState", "EMPTY")
                .containsEntry("executionId", plan.primary().executionId())
                .containsKey("artifactManifestDigest");
        assertThat(persistence.createOrLoadCalls).isEqualTo(1);
        verify(aiAnalysisClient, never()).performAnalysis(
                any(), any(), any(), anyString(), any(ImmutableExecutionManifest.class),
                any(CoverageWorkPlan.class));
    }

    @Test
    void candidateBoundAiEventConsumerFailureIsNotDowngradedToBestEffort()
            throws Exception {
        CandidateSetup candidate = prepareCandidate(exactRequest);
        when(aiAnalysisClient.performAnalysis(
                        eq(exactRequest),
                        any(),
                        eq(candidate.plan().primary()),
                        eq(CANDIDATE_INDEX_VERSION),
                        any(ImmutableExecutionManifest.class),
                        any(CoverageWorkPlan.class)))
                .thenAnswer(invocation -> {
                    ImmutableExecutionManifest manifest = invocation.getArgument(4);
                    @SuppressWarnings("unchecked")
                    java.util.function.Consumer<Map<String, Object>> aiEvents =
                            invocation.getArgument(1);
                    aiEvents.accept(Map.of(
                            "type", "telemetry",
                            "stage", "generation",
                            "outcome", "running",
                            "executionId", manifest.executionId(),
                            "artifactManifestDigest", manifest.artifactManifestDigest()));
                    return candidateResponse(manifest, CANDIDATE_INDEX_VERSION);
                });
        PullRequestAnalysisProcessor processor = processorWithManifestService(
                candidate.manifestService());

        assertThatThrownBy(() -> processor.process(
                request,
                event -> {
                    if ("generation".equals(event.get("stage"))) {
                        throw new IllegalStateException("candidate sink failure");
                    }
                },
                project))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("candidate sink failure");

        verify(codeAnalysisService, never()).createCandidateAnalysisFromAiResponse(
                any(), anyMap(), anyLong(), anyString(), anyString(), anyString(),
                any(), any(), anyString(), anyMap(), any(), any(), anyString(), anyString());
    }

    @Test
    void partialCandidatePersistsEveryFindingAndPublishesWithPartialTruth()
            throws Exception {
        CandidateSetup candidate = prepareCandidate(exactRequest);
        when(coverageLedgerService.reconcileProducer(
                        any(), any(CoverageReceipt.class)))
                .thenAnswer(invocation -> coverageSnapshot(
                        invocation.getArgument(0), CoverageAnalysisState.PARTIAL));
        when(aiAnalysisClient.performAnalysis(
                        eq(exactRequest),
                        any(),
                        eq(candidate.plan().primary()),
                        eq(CANDIDATE_INDEX_VERSION),
                        any(ImmutableExecutionManifest.class),
                        any(CoverageWorkPlan.class)))
                .thenAnswer(invocation -> candidateResponseWithFinding(
                        invocation.getArgument(4), CANDIDATE_INDEX_VERSION));
        configureBoundCandidateAnalysis(candidate);
        when(analysis.getIssues()).thenReturn(List.of(mock(CodeAnalysisIssue.class)));
        when(publicationFence.reserve(any(), any()))
                .thenReturn(PublicationReservation.RESERVED);
        List<Map<String, Object>> emitted = new ArrayList<>();
        PullRequestAnalysisProcessor processor = processorWithManifestService(
                candidate.manifestService());

        Map<String, Object> result = processor.process(request, emitted::add, project);

        assertThat(result)
                .containsEntry("analysisState", "PARTIAL")
                .containsEntry("executionId", candidate.plan().primary().executionId());
        @SuppressWarnings("unchecked")
        ArgumentCaptor<Map<String, Object>> persistedResponse =
                ArgumentCaptor.forClass(Map.class);
        verify(codeAnalysisService).createCandidateAnalysisFromAiResponse(
                eq(project), persistedResponse.capture(), eq(42L), eq("main"),
                eq("feature"), eq(HEAD_SHA), isNull(), isNull(), anyString(),
                anyMap(), isNull(), isNull(), anyString(), anyString());
        assertThat(persistedResponse.getValue())
                .containsEntry("analysisState", "PARTIAL")
                .extractingByKey("issues")
                .asList()
                .singleElement()
                .asString()
                .contains("primary worker finding");
        assertThat(String.valueOf(persistedResponse.getValue().get("comment")))
                .contains("Partial review coverage")
                .contains("Worker declared raw output publishable.");
        assertThat(emitted)
                .noneMatch(event -> "warning".equals(event.get("type")));
        verify(reviewDeliveryService).enqueue(any(ReviewDeliveryIntent.class));
        verify(reviewDeliveryService).attempt(anyString(), any(Instant.class));
        verify(reportingService, never()).postAnalysisResults(
                any(), any(), anyLong(), anyLong(), any());

        ArgumentCaptor<ApplicationEvent> lifecycleEvents =
                ArgumentCaptor.forClass(ApplicationEvent.class);
        verify(eventPublisher, org.mockito.Mockito.atLeast(2))
                .publishEvent(lifecycleEvents.capture());
        assertThat(lifecycleEvents.getAllValues())
                .filteredOn(AnalysisCompletedEvent.class::isInstance)
                .singleElement()
                .satisfies(raw -> {
                    AnalysisCompletedEvent completed = (AnalysisCompletedEvent) raw;
                    assertThat(completed.getStatus())
                            .isEqualTo(AnalysisCompletedEvent.CompletionStatus.PARTIAL_SUCCESS);
                    assertThat(completed.getIssuesFound()).isEqualTo(1);
                });
    }

    @Test
    void partialCandidateWithZeroFindingsNeverPublishesOrClaimsClean()
            throws Exception {
        CandidateSetup candidate = prepareCandidate(exactRequest);
        when(coverageLedgerService.reconcileProducer(
                        any(), any(CoverageReceipt.class)))
                .thenAnswer(invocation -> coverageSnapshot(
                        invocation.getArgument(0), CoverageAnalysisState.PARTIAL));
        when(aiAnalysisClient.performAnalysis(
                        eq(exactRequest),
                        any(),
                        eq(candidate.plan().primary()),
                        eq(CANDIDATE_INDEX_VERSION),
                        any(ImmutableExecutionManifest.class),
                        any(CoverageWorkPlan.class)))
                .thenAnswer(invocation -> candidateResponse(
                        invocation.getArgument(4), CANDIDATE_INDEX_VERSION));
        configureBoundCandidateAnalysis(candidate);
        when(publicationFence.reserve(any(), any()))
                .thenReturn(PublicationReservation.RESERVED);
        List<Map<String, Object>> emitted = new ArrayList<>();
        PullRequestAnalysisProcessor processor = processorWithManifestService(
                candidate.manifestService());

        Map<String, Object> result = processor.process(request, emitted::add, project);

        assertThat(result)
                .containsEntry("analysisState", "PARTIAL")
                .containsEntry("issues", List.of());
        @SuppressWarnings("unchecked")
        ArgumentCaptor<Map<String, Object>> persistedResponse =
                ArgumentCaptor.forClass(Map.class);
        verify(codeAnalysisService).createCandidateAnalysisFromAiResponse(
                eq(project), persistedResponse.capture(), eq(42L), eq("main"),
                eq("feature"), eq(HEAD_SHA), isNull(), isNull(), anyString(),
                anyMap(), isNull(), isNull(), anyString(), anyString());
        assertThat(persistedResponse.getValue())
                .containsEntry("analysisState", "PARTIAL")
                .containsEntry("issues", List.of());
        assertThat(emitted).anyMatch(event ->
                "delivery".equals(event.get("stage"))
                        && "skipped".equals(event.get("outcome"))
                        && "coverage_incomplete_no_clean_claim".equals(
                                event.get("reasonCode")));
        verify(publicationFence, never()).reserve(any(), any());
        verify(reportingService, never()).postAnalysisResults(
                any(), any(), anyLong(), anyLong(), any());
    }

    @Test
    void failedProducerCoverageFailsClosedBeforePersistenceOrDelivery()
            throws Exception {
        CandidateSetup candidate = prepareCandidate(exactRequest);
        when(coverageLedgerService.reconcileProducer(
                        any(), any(CoverageReceipt.class)))
                .thenAnswer(invocation -> coverageSnapshot(
                        invocation.getArgument(0), CoverageAnalysisState.FAILED));
        when(aiAnalysisClient.performAnalysis(
                        eq(exactRequest),
                        any(),
                        eq(candidate.plan().primary()),
                        eq(CANDIDATE_INDEX_VERSION),
                        any(ImmutableExecutionManifest.class),
                        any(CoverageWorkPlan.class)))
                .thenAnswer(invocation -> candidateResponse(
                        invocation.getArgument(4), CANDIDATE_INDEX_VERSION));
        PullRequestAnalysisProcessor processor = processorWithManifestService(
                candidate.manifestService());

        Map<String, Object> result = processor.process(request, ignored -> { }, project);

        assertThat(result)
                .containsEntry("status", "error")
                .extractingByKey("message")
                .asString()
                .contains("failed to examine any mandatory coverage anchor");
        verify(codeAnalysisService, never()).createCandidateAnalysisFromAiResponse(
                any(), anyMap(), anyLong(), anyString(), anyString(), anyString(),
                any(), any(), anyString(), anyMap(), any(), any(), anyString(), anyString());
        verify(publicationFence, never()).reserve(any(), any());
        verify(reportingService, never()).postAnalysisResults(
                any(), any(), anyLong(), anyLong(), any());
    }

    @Test
    void candidatePersistsManifestBeforeLockTimeoutAndEmitsNoUnboundLockMessages()
            throws Exception {
        CandidateSetup candidate = prepareCandidate(exactRequest);
        request.preAcquiredLockKey = null;
        when(analysisLockService.acquireLockWithWait(
                        any(), anyString(), any(), anyString(), anyLong(), any()))
                .thenAnswer(invocation -> {
                    @SuppressWarnings("unchecked")
                    java.util.function.Consumer<Map<String, Object>> lockEvents =
                            invocation.getArgument(5);
                    lockEvents.accept(Map.of("type", "lock_wait"));
                    lockEvents.accept(Map.of("type", "error"));
                    return Optional.empty();
                });
        List<Map<String, Object>> emitted = new ArrayList<>();
        PullRequestAnalysisProcessor processor = processorWithManifestService(
                candidate.manifestService());

        assertThatThrownBy(() -> processor.process(request, emitted::add, project))
                .isInstanceOf(AnalysisLockedException.class);
        assertThat(candidate.persisted().get())
                .isNotNull()
                .extracting(ImmutableExecutionManifest::executionId)
                .isEqualTo(candidate.plan().primary().executionId());
        assertThat(emitted).isEmpty();
        verify(eventPublisher, never()).publishEvent(any(ApplicationEvent.class));
        verify(vcsServiceFactory).getAiClientService(EVcsProvider.GITHUB);
        verify(aiAnalysisClient, never()).performAnalysis(
                any(), any(), any(), anyString(), any(ImmutableExecutionManifest.class),
                any(CoverageWorkPlan.class));
    }

    @ParameterizedTest
    @ValueSource(strings = {"executionId", "artifactManifestDigest"})
    void candidateRejectsConflictingForwardedLifecycleIdentity(String conflictingField)
            throws Exception {
        FrozenExecutionPlan plan = candidatePlan();
        when(executionPolicyRuntime.freeze(anyString(), any(StableRolloutKey.class), any(ExecutionPolicyConfig.class)))
                .thenReturn(plan);
        RecordingVcsAiClientService vcs = new RecordingVcsAiClientService(
                exactRequest, new ArrayList<>());
        when(vcsServiceFactory.getAiClientService(EVcsProvider.GITHUB)).thenReturn(vcs);

        ExecutionManifestService manifestService = mock(ExecutionManifestService.class);
        AtomicReference<ImmutableExecutionManifest> persisted = new AtomicReference<>();
        when(manifestService.persistBeforeWork(
                        any(ImmutableExecutionManifest.class), anyList()))
                .thenAnswer(invocation -> {
                    ImmutableExecutionManifest manifest = invocation.getArgument(0);
                    persisted.set(manifest);
                    return manifest;
                });
        when(manifestService.requireVerified(plan.primary().executionId()))
                .thenAnswer(ignored -> persisted.get());
        when(aiAnalysisClient.performAnalysis(
                        eq(exactRequest),
                        any(),
                        eq(plan.primary()),
                        eq(CANDIDATE_INDEX_VERSION),
                        any(ImmutableExecutionManifest.class),
                        any(CoverageWorkPlan.class)))
                .thenAnswer(invocation -> {
                    ImmutableExecutionManifest manifest = invocation.getArgument(4);
                    Map<String, Object> event = new LinkedHashMap<>();
                    event.put("type", "telemetry");
                    event.put("stage", "generation");
                    event.put("executionId", manifest.executionId());
                    event.put(
                            "artifactManifestDigest",
                            manifest.artifactManifestDigest());
                    event.put(
                            conflictingField,
                            "executionId".equals(conflictingField)
                                    ? "pr:foreign-execution"
                                    : "0".repeat(64));
                    @SuppressWarnings("unchecked")
                    java.util.function.Consumer<Map<String, Object>> eventConsumer =
                            invocation.getArgument(1);
                    eventConsumer.accept(event);
                    return candidateResponse(manifest, CANDIDATE_INDEX_VERSION);
                });

        PullRequestAnalysisProcessor processor = processorWithManifestService(manifestService);

        assertThatThrownBy(() -> processor.process(request, ignored -> { }, project))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining(conflictingField)
                .hasMessageContaining("conflicts");
        verify(codeAnalysisService, never()).createCandidateAnalysisFromAiResponse(
                any(), anyMap(), anyLong(), anyString(), anyString(), anyString(),
                any(), any(), anyString(), anyMap(), any(), any(), anyString(), anyString());
    }

    @ParameterizedTest
    @ValueSource(strings = {"executionId", "artifactManifestDigest"})
    void candidateProcessorRejectsMissingProducerLifecycleIdentity(String missingField)
            throws Exception {
        FrozenExecutionPlan plan = candidatePlan();
        when(executionPolicyRuntime.freeze(anyString(), any(StableRolloutKey.class), any(ExecutionPolicyConfig.class)))
                .thenReturn(plan);
        RecordingVcsAiClientService vcs = new RecordingVcsAiClientService(
                exactRequest, new ArrayList<>());
        when(vcsServiceFactory.getAiClientService(EVcsProvider.GITHUB)).thenReturn(vcs);

        ExecutionManifestService manifestService = mock(ExecutionManifestService.class);
        AtomicReference<ImmutableExecutionManifest> persisted = new AtomicReference<>();
        when(manifestService.persistBeforeWork(
                        any(ImmutableExecutionManifest.class), anyList()))
                .thenAnswer(invocation -> {
                    ImmutableExecutionManifest manifest = invocation.getArgument(0);
                    persisted.set(manifest);
                    return manifest;
                });
        when(manifestService.requireVerified(plan.primary().executionId()))
                .thenAnswer(ignored -> persisted.get());
        when(aiAnalysisClient.performAnalysis(
                        eq(exactRequest),
                        any(),
                        eq(plan.primary()),
                        eq(CANDIDATE_INDEX_VERSION),
                        any(ImmutableExecutionManifest.class),
                        any(CoverageWorkPlan.class)))
                .thenAnswer(invocation -> {
                    ImmutableExecutionManifest manifest = invocation.getArgument(4);
                    Map<String, Object> event = new LinkedHashMap<>();
                    event.put("type", "progress");
                    event.put("executionId", manifest.executionId());
                    event.put(
                            "artifactManifestDigest",
                            manifest.artifactManifestDigest());
                    event.remove(missingField);
                    @SuppressWarnings("unchecked")
                    java.util.function.Consumer<Map<String, Object>> eventConsumer =
                            invocation.getArgument(1);
                    eventConsumer.accept(event);
                    return candidateResponse(manifest, CANDIDATE_INDEX_VERSION);
                });

        PullRequestAnalysisProcessor processor = processorWithManifestService(manifestService);

        assertThatThrownBy(() -> processor.process(request, ignored -> { }, project))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining(missingField);
        verify(codeAnalysisService, never()).createCandidateAnalysisFromAiResponse(
                any(), anyMap(), anyLong(), anyString(), anyString(), anyString(),
                any(), any(), anyString(), anyMap(), any(), any(), anyString(), anyString());
    }

    @ParameterizedTest(name = "real queue client aborts processor on {0}")
    @ValueSource(strings = {"missing_execution", "conflicting_digest"})
    void candidateProcessorFailsClosedThroughRealClientOnInvalidIntermediateIdentity(
            String identityCase) throws Exception {
        FrozenExecutionPlan plan = candidatePlan();
        when(executionPolicyRuntime.freeze(anyString(), any(StableRolloutKey.class), any(ExecutionPolicyConfig.class)))
                .thenReturn(plan);
        RecordingVcsAiClientService vcs = new RecordingVcsAiClientService(
                exactRequest, new ArrayList<>());
        when(vcsServiceFactory.getAiClientService(EVcsProvider.GITHUB)).thenReturn(vcs);

        ExecutionManifestService manifestService = mock(ExecutionManifestService.class);
        AtomicReference<ImmutableExecutionManifest> persisted = new AtomicReference<>();
        when(manifestService.persistBeforeWork(
                        any(ImmutableExecutionManifest.class), anyList()))
                .thenAnswer(invocation -> {
                    ImmutableExecutionManifest manifest = invocation.getArgument(0);
                    persisted.set(manifest);
                    return manifest;
                });
        when(manifestService.requireVerified(plan.primary().executionId()))
                .thenAnswer(ignored -> persisted.get());

        RedisQueueService queueService = mock(RedisQueueService.class);
        ObjectMapper mapper = new ObjectMapper()
                .registerModule(new JavaTimeModule())
                .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
        when(queueService.rightPop(anyString(), anyLong()))
                .thenAnswer(ignored -> {
                    ImmutableExecutionManifest manifest = persisted.get();
                    Map<String, Object> event = new LinkedHashMap<>();
                    event.put("type", "progress");
                    event.put("percent", 10);
                    event.put("executionId", manifest.executionId());
                    event.put(
                            "artifactManifestDigest",
                            manifest.artifactManifestDigest());
                    if ("missing_execution".equals(identityCase)) {
                        event.remove("executionId");
                    } else {
                        event.put("artifactManifestDigest", "0".repeat(64));
                    }
                    return mapper.writeValueAsString(event);
                });
        AiAnalysisClient realClient = new AiAnalysisClient(
                mock(RestTemplate.class), queueService, mapper);
        PullRequestAnalysisProcessor processor = processorWithManifestService(
                manifestService,
                realClient);

        Map<String, Object> result = processor.process(request, ignored -> { }, project);

        assertThat(result)
                .containsEntry("status", "error")
                .containsEntry("executionId", plan.primary().executionId())
                .containsKey("artifactManifestDigest");
        assertThat(result.get("message").toString())
                .contains("missing_execution".equals(identityCase)
                        ? "executionId"
                        : "artifactManifestDigest");
        verify(codeAnalysisService, never()).createCandidateAnalysisFromAiResponse(
                any(), anyMap(), anyLong(), anyString(), anyString(), anyString(),
                any(), any(), anyString(), anyMap(), any(), any(), anyString(), anyString());
    }

    @Test
    void candidateRejectsAnUnboundPersistedOutputBeforeDelivery() throws Exception {
        FrozenExecutionPlan plan = candidatePlan();
        when(executionPolicyRuntime.freeze(anyString(), any(StableRolloutKey.class), any(ExecutionPolicyConfig.class)))
                .thenReturn(plan);
        RecordingVcsAiClientService vcs = new RecordingVcsAiClientService(
                exactRequest, new ArrayList<>());
        when(vcsServiceFactory.getAiClientService(EVcsProvider.GITHUB)).thenReturn(vcs);

        ExecutionManifestService manifestService = mock(ExecutionManifestService.class);
        AtomicReference<ImmutableExecutionManifest> persisted = new AtomicReference<>();
        when(manifestService.persistBeforeWork(
                        any(ImmutableExecutionManifest.class), anyList()))
                .thenAnswer(invocation -> {
                    ImmutableExecutionManifest manifest = invocation.getArgument(0);
                    persisted.set(manifest);
                    return manifest;
                });
        when(manifestService.requireVerified(plan.primary().executionId()))
                .thenAnswer(ignored -> persisted.get());
        when(aiAnalysisClient.performAnalysis(
                        eq(exactRequest),
                        any(),
                        eq(plan.primary()),
                        eq(CANDIDATE_INDEX_VERSION),
                        any(ImmutableExecutionManifest.class),
                        any(CoverageWorkPlan.class)))
                .thenAnswer(invocation -> candidateResponse(
                        invocation.getArgument(4), CANDIDATE_INDEX_VERSION));
        when(codeAnalysisService.createCandidateAnalysisFromAiResponse(
                        any(), anyMap(), anyLong(), anyString(), anyString(), anyString(),
                        any(), any(), anyString(), anyMap(), isNull(), isNull(),
                        anyString(), anyString()))
                .thenReturn(analysis);

        PullRequestAnalysisProcessor processor = processorWithManifestService(manifestService);

        assertThatThrownBy(() -> processor.process(request, ignored -> { }, project))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("persisted candidate output conflicts");
        verify(fileSnapshotService, never())
                .persistSnapshotsForPr(any(), any(), anyMap(), anyString());
        verify(reportingService, never()).postAnalysisResults(
                any(), any(), anyLong(), anyLong(), any());
    }

    @Test
    void candidateRechecksLatestHeadAfterWorkerBeforeAnyDownstreamPersistence()
            throws Exception {
        CandidateSetup candidate = prepareCandidate(exactRequest);
        AtomicReference<Boolean> workerReturned = new AtomicReference<>(false);
        List<Boolean> freshnessChecksAfterWorker = new ArrayList<>();
        when(publicationFence.isLatestHead(any(), any())).thenAnswer(ignored -> {
            boolean afterWorker = workerReturned.get();
            freshnessChecksAfterWorker.add(afterWorker);
            return !afterWorker;
        });
        when(aiAnalysisClient.performAnalysis(
                        eq(exactRequest),
                        any(),
                        eq(candidate.plan().primary()),
                        eq(CANDIDATE_INDEX_VERSION),
                        any(ImmutableExecutionManifest.class),
                        any(CoverageWorkPlan.class)))
                .thenAnswer(invocation -> {
                    Map<String, Object> response = candidateResponse(
                            invocation.getArgument(4), CANDIDATE_INDEX_VERSION);
                    workerReturned.set(true);
                    return response;
                });
        configureBoundCandidateAnalysis(candidate);
        List<Map<String, Object>> emitted = new ArrayList<>();
        PullRequestAnalysisProcessor processor = processorWithManifestService(
                candidate.manifestService());

        Map<String, Object> result = processor.process(request, emitted::add, project);

        assertThat(result)
                .containsEntry("status", "superseded")
                .containsEntry("reason", "latest_head_advanced");
        assertThat(freshnessChecksAfterWorker).contains(true);
        assertThat(emitted).noneMatch(event ->
                "persistence".equals(event.get("stage"))
                        || "delivery".equals(event.get("stage")));
        verify(codeAnalysisService, never()).createCandidateAnalysisFromAiResponse(
                any(), anyMap(), anyLong(), anyString(), anyString(), anyString(),
                any(), any(), anyString(), anyMap(), any(), any(), anyString(), anyString());
        verify(reportingService, never()).postAnalysisResults(
                any(), any(), anyLong(), anyLong(), any());
    }

    @Test
    void legacyPlanUsesOnlyTheCompatibilityAcquisitionAndNeedsNoManifestService()
            throws Exception {
        FrozenExecutionPlan plan = legacyPlan();
        when(executionPolicyRuntime.currentConfig()).thenReturn(
                new ExecutionPolicyConfig(
                        "config-legacy",
                        ExecutionMode.LEGACY,
                        "candidate-review-v2",
                        10_000,
                        "rollout-salt",
                        false,
                        false));
        when(executionPolicyRuntime.freeze(anyString(), any(StableRolloutKey.class), any(ExecutionPolicyConfig.class)))
                .thenReturn(plan);
        RecordingVcsAiClientService vcs = new RecordingVcsAiClientService(
                exactRequest, new ArrayList<>());
        when(vcsServiceFactory.getAiClientService(EVcsProvider.GITHUB)).thenReturn(vcs);
        when(ragOperationsService.getIndexVersion(project, "main"))
                .thenReturn(INDEX_VERSION);
        Map<String, Object> response = Map.of("comment", "legacy", "issues", List.of());
        when(aiAnalysisClient.performAnalysis(
                        eq(exactRequest), any(), eq(plan.primary()), eq(INDEX_VERSION)))
                .thenReturn(response);

        PullRequestAnalysisProcessor processor = compatibilityProcessor();
        Map<String, Object> result = processor.process(request, ignored -> { }, project);

        assertThat(result).isEqualTo(response);
        assertThat(vcs.legacyCalls).isEqualTo(1);
        assertThat(vcs.exactCalls).isZero();
        verify(aiAnalysisClient).performAnalysis(
                eq(exactRequest), any(), eq(plan.primary()), eq(INDEX_VERSION));
        verify(aiAnalysisClient, never()).performAnalysis(
                any(), any(), any(), anyString(), any(ImmutableExecutionManifest.class),
                any(CoverageWorkPlan.class));
        verify(codeAnalysisService, never()).createCandidateAnalysisFromAiResponse(
                any(), anyMap(), anyLong(), anyString(), anyString(), anyString(),
                any(), any(), anyString(), anyMap(), any(), any(),
                anyString(), anyString());
    }

    @Test
    void candidateWithoutManifestPersistenceFailsClosedBeforeRagOrAi() throws Exception {
        FrozenExecutionPlan plan = candidatePlan();
        when(executionPolicyRuntime.freeze(anyString(), any(StableRolloutKey.class), any(ExecutionPolicyConfig.class)))
                .thenReturn(plan);
        RecordingVcsAiClientService vcs = new RecordingVcsAiClientService(
                exactRequest, new ArrayList<>());
        when(vcsServiceFactory.getAiClientService(EVcsProvider.GITHUB)).thenReturn(vcs);

        PullRequestAnalysisProcessor processor = compatibilityProcessor();

        assertThatThrownBy(() -> processor.process(request, ignored -> { }, project))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("manifest");
        assertThat(vcs.legacyCalls).isZero();
        verify(codeAnalysisService, never()).getCodeAnalysisCache(anyLong(), anyString(), anyLong());
        verify(codeAnalysisService, never()).getAnalysisByCommitHash(anyLong(), anyString());
        verify(codeAnalysisService, never()).getAnalysisByDiffFingerprint(anyLong(), anyString());
        verify(ragOperationsService, never()).ensureRagIndexUpToDate(any(), anyString(), any());
        verify(aiAnalysisClient, never()).performAnalysis(any(), any());
        verify(aiAnalysisClient, never()).performAnalysis(any(), any(), any());
        verify(aiAnalysisClient, never()).performAnalysis(any(), any(), any(), anyString());
        verify(aiAnalysisClient, never()).performAnalysis(
                any(), any(), any(), anyString(), any(ImmutableExecutionManifest.class),
                any(CoverageWorkPlan.class));
    }

    @Test
    void candidateWithoutCoverageAccountingFailsClosedBeforeModelWork()
            throws Exception {
        CandidateSetup candidate = prepareCandidate(exactRequest);
        PullRequestAnalysisProcessor processor = processorWithManifestService(
                candidate.manifestService(), aiAnalysisClient, null);

        assertThatThrownBy(() -> processor.process(request, ignored -> { }, project))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("requires durable coverage accounting");
        assertThat(candidate.persisted().get()).isNotNull();
        verify(aiAnalysisClient, never()).performAnalysis(
                any(), any(), any(), anyString(), any(ImmutableExecutionManifest.class),
                any(CoverageWorkPlan.class));
        verify(codeAnalysisService, never()).createCandidateAnalysisFromAiResponse(
                any(), anyMap(), anyLong(), anyString(), anyString(), anyString(),
                any(), any(), anyString(), anyMap(), any(), any(), anyString(), anyString());
    }

    private PullRequestAnalysisProcessor compatibilityProcessor() {
        PullRequestAnalysisProcessor processor = new PullRequestAnalysisProcessor(
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
                executionPolicyRuntime);
        processor.setReviewDeliveryService(reviewDeliveryService);
        return processor;
    }

    private CandidateSetup prepareCandidate(AiAnalysisRequest acquiredRequest) {
        FrozenExecutionPlan plan = candidatePlan();
        when(executionPolicyRuntime.freeze(anyString(), any(StableRolloutKey.class), any(ExecutionPolicyConfig.class)))
                .thenReturn(plan);
        RecordingVcsAiClientService vcs = new RecordingVcsAiClientService(
                acquiredRequest, new ArrayList<>());
        when(vcsServiceFactory.getAiClientService(EVcsProvider.GITHUB)).thenReturn(vcs);
        ExecutionManifestService manifestService = mock(ExecutionManifestService.class);
        AtomicReference<ImmutableExecutionManifest> persisted = new AtomicReference<>();
        when(manifestService.persistBeforeWork(
                        any(ImmutableExecutionManifest.class), anyList()))
                .thenAnswer(invocation -> {
                    ImmutableExecutionManifest manifest = invocation.getArgument(0);
                    persisted.set(manifest);
                    return manifest;
                });
        when(manifestService.requireVerified(plan.primary().executionId()))
                .thenAnswer(ignored -> persisted.get());
        return new CandidateSetup(plan, manifestService, persisted);
    }

    private void configureBoundCandidateAnalysis(CandidateSetup candidate) {
        when(analysis.hasExecutionIdentity()).thenReturn(true);
        when(analysis.getExecutionId()).thenReturn(candidate.plan().primary().executionId());
        when(analysis.getArtifactManifestDigest()).thenAnswer(
                ignored -> candidate.persisted().get().artifactManifestDigest());
        when(analysis.getProject()).thenReturn(project);
        when(analysis.getPrNumber()).thenReturn(42L);
        when(analysis.getCommitHash()).thenReturn(HEAD_SHA);
        when(codeAnalysisService.createCandidateAnalysisFromAiResponse(
                        any(), anyMap(), anyLong(), anyString(), anyString(), anyString(),
                        any(), any(), anyString(), anyMap(), isNull(), isNull(),
                        anyString(), anyString()))
                .thenReturn(analysis);
    }

    private PullRequestAnalysisProcessor processorWithManifestService(
            ExecutionManifestService manifestService) throws Exception {
        return processorWithManifestService(manifestService, aiAnalysisClient);
    }

    private PullRequestAnalysisProcessor processorWithManifestService(
            ExecutionManifestService manifestService,
            AiAnalysisClient client) throws Exception {
        return processorWithManifestService(
                manifestService, client, coverageLedgerService);
    }

    private PullRequestAnalysisProcessor processorWithManifestService(
            ExecutionManifestService manifestService,
            AiAnalysisClient client,
            CoverageLedgerService coverageService) {
        PullRequestAnalysisProcessor processor = new PullRequestAnalysisProcessor(
                pullRequestService,
                codeAnalysisService,
                client,
                vcsServiceFactory,
                analysisLockService,
                analyzedCommitService,
                vcsClientProvider,
                fileSnapshotService,
                prIssueTrackingService,
                astScopeEnricher,
                ragOperationsService,
                eventPublisher,
                executionPolicyRuntime,
                manifestService,
                coverageService);
        processor.setReviewDeliveryService(reviewDeliveryService);
        return processor;
    }

    private static AiAnalysisRequest exactRequest() {
        PrEnrichmentDataDto enrichment = new PrEnrichmentDataDto(
                List.of(FileContentDto.skipped("src/A.java", "file_too_large")),
                List.of(),
                List.of(),
                new PrEnrichmentDataDto.EnrichmentStats(
                        1,
                        0,
                        1,
                        0,
                        0,
                        1,
                        Map.of("file_too_large", 1)));
        return AiAnalysisRequestImpl.builder()
                .withProjectId(7L)
                .withPullRequestId(42L)
                .withProjectVcsConnectionBindingInfo("workspace", "repository")
                .withVcsProvider("github")
                .withRawDiff(RAW_DIFF)
                .withImmutableSnapshot(BASE_SHA, HEAD_SHA, MERGE_BASE_SHA)
                .withPreviousCommitHash(BASE_SHA)
                .withCurrentCommitHash(HEAD_SHA)
                .withAnalysisMode(AnalysisMode.FULL)
                .withAnalysisType(AnalysisType.PR_REVIEW)
                .withChangedFiles(List.of("src/A.java"))
                .withDeletedFiles(List.of())
                .withDiffSnippets(List.of())
                .withEnrichmentData(enrichment)
                .build();
    }

    private static AiAnalysisRequest acquisitionOnlyRequest(String rawDiff) {
        return AiAnalysisRequestImpl.builder()
                .withProjectId(7L)
                .withPullRequestId(42L)
                .withProjectVcsConnectionBindingInfo("workspace", "repository")
                .withVcsProvider("github")
                .withRawDiff(rawDiff)
                .withImmutableSnapshot(BASE_SHA, HEAD_SHA, MERGE_BASE_SHA)
                .withPreviousCommitHash(BASE_SHA)
                .withCurrentCommitHash(HEAD_SHA)
                .withAnalysisMode(AnalysisMode.FULL)
                .withAnalysisType(AnalysisType.PR_REVIEW)
                .withChangedFiles(List.of())
                .withDeletedFiles(List.of())
                .withDiffSnippets(List.of())
                .withEnrichmentData(PrEnrichmentDataDto.empty())
                .build();
    }

    private static Stream<Arguments> invalidExactAcquisitionResults() {
        return Stream.of(
                Arguments.of("null list", (List<AiAnalysisRequest>) null),
                Arguments.of("wrong size", List.<AiAnalysisRequest>of()),
                Arguments.of(
                        "singleton null",
                        java.util.Collections.singletonList((AiAnalysisRequest) null)));
    }

    private static Stream<Arguments> nullableCandidateInventories() {
        return Stream.of(
                Arguments.of(
                        "null deleted files",
                        candidateInventoryRequest(List.of(), null)),
                Arguments.of(
                        "null changed files",
                        candidateInventoryRequest(null, List.of())));
    }

    private static AiAnalysisRequest candidateInventoryRequest(
            List<String> changedFiles,
            List<String> deletedFiles) {
        return AiAnalysisRequestImpl.builder()
                .withProjectId(7L)
                .withPullRequestId(42L)
                .withProjectVcsConnectionBindingInfo("workspace", "repository")
                .withVcsProvider("github")
                .withRawDiff(RAW_DIFF)
                .withImmutableSnapshot(BASE_SHA, HEAD_SHA, MERGE_BASE_SHA)
                .withPreviousCommitHash(BASE_SHA)
                .withCurrentCommitHash(HEAD_SHA)
                .withAnalysisMode(AnalysisMode.FULL)
                .withAnalysisType(AnalysisType.PR_REVIEW)
                .withChangedFiles(changedFiles)
                .withDeletedFiles(deletedFiles)
                .withDiffSnippets(List.of())
                .withEnrichmentData(PrEnrichmentDataDto.empty())
                .build();
    }

    private static void assertCandidateBinding(
            Map<String, Object> event,
            ImmutableExecutionManifest manifest) {
        assertThat(event)
                .containsEntry("executionId", manifest.executionId())
                .containsEntry(
                        "artifactManifestDigest",
                        manifest.artifactManifestDigest());
    }

    private static void assertStartedBinding(
            AnalysisStartedEvent event,
            ImmutableExecutionManifest manifest) {
        assertThat(event.getExecutionId()).isEqualTo(manifest.executionId());
        assertThat(event.getArtifactManifestDigest())
                .isEqualTo(manifest.artifactManifestDigest());
    }

    private static void assertCompletedBinding(
            AnalysisCompletedEvent event,
            ImmutableExecutionManifest manifest) {
        assertThat(event.getExecutionId()).isEqualTo(manifest.executionId());
        assertThat(event.getArtifactManifestDigest())
                .isEqualTo(manifest.artifactManifestDigest());
        assertThat(event.getMetrics())
                .containsEntry("executionId", manifest.executionId())
                .containsEntry(
                        "artifactManifestDigest",
                        manifest.artifactManifestDigest());
    }

    private static ExecutionPolicyConfig runtimeConfig() {
        return new ExecutionPolicyConfig(
                "config-1",
                ExecutionMode.ACTIVE,
                "candidate-review-v2",
                10_000,
                "rollout-salt",
                false,
                false);
    }

    private static FrozenExecutionPlan candidatePlan() {
        PolicyExecution primary = new PolicyExecution(
                "candidate-pr-42",
                "candidate-review-v2",
                ExecutionMode.ACTIVE,
                PolicySelectionReason.ACTIVE_ROLLOUT_SELECTED,
                7,
                true,
                CREATED_AT);
        return new FrozenExecutionPlan(
                primary.executionId(), "config-1", "e".repeat(64), primary, null, CREATED_AT);
    }

    private static FrozenExecutionPlan legacyPlan() {
        PolicyExecution primary = new PolicyExecution(
                "legacy-pr-42",
                "legacy-review-v1",
                ExecutionMode.LEGACY,
                PolicySelectionReason.LEGACY_CONFIGURED,
                0,
                true,
                CREATED_AT);
        return new FrozenExecutionPlan(
                primary.executionId(), "config-1", "f".repeat(64), primary, null, CREATED_AT);
    }

    private static Map<String, Object> candidateResponse(
            ImmutableExecutionManifest manifest,
            String indexVersion) {
        Map<String, Object> response = new LinkedHashMap<>();
        response.put("comment", "review");
        response.put("issues", List.of());
        response.put("coverageReceipt", coverageReceipt(manifest));
        response.put("telemetry", pendingCandidateTelemetry(manifest, indexVersion));
        return response;
    }

    private static CoverageWorkPlan coverageWorkPlan(
            ImmutableExecutionManifest manifest) {
        return new CoverageWorkPlan(
                1,
                manifest.executionId(),
                manifest.artifactManifestDigest(),
                manifest.diffDigest(),
                manifest.diffByteLength(),
                "d".repeat(64),
                List.of());
    }

    private static CoverageLedgerSnapshot coverageSnapshot(
            ImmutableExecutionManifest manifest,
            CoverageAnalysisState state) {
        return new CoverageLedgerSnapshot(
                1,
                manifest.executionId(),
                manifest.artifactManifestDigest(),
                manifest.diffDigest(),
                manifest.diffByteLength(),
                "d".repeat(64),
                List.of(),
                List.of(),
                state,
                CoverageCounts.fromDispositions(List.of()));
    }

    private static Map<String, Object> coverageReceipt(
            ImmutableExecutionManifest manifest) {
        return Map.of(
                "schemaVersion", 1,
                "executionId", manifest.executionId(),
                "artifactManifestDigest", manifest.artifactManifestDigest(),
                "diffDigest", manifest.diffDigest(),
                "diffByteLength", manifest.diffByteLength(),
                "ledgerDigest", "d".repeat(64),
                "dispositions", List.of());
    }

    private static Map<String, Object> candidateResponseWithFinding(
            ImmutableExecutionManifest manifest,
            String indexVersion) {
        Map<String, Object> response = new LinkedHashMap<>(
                candidateResponse(manifest, indexVersion));
        response.put("comment", "Worker declared raw output publishable.");
        response.put("issues", List.of(Map.of(
                "severity", "HIGH",
                "file", "src/A.java",
                "line", 1,
                "reason", "primary worker finding",
                "title", "primary worker finding",
                "category", "BUG_RISK",
                "scope", "LINE",
                "codeSnippet", "new",
                "isResolved", false)));
        return Map.copyOf(response);
    }

    private static Map<String, Object> pendingCandidateTelemetry(
            ImmutableExecutionManifest manifest,
            String indexVersion) {
        Map<String, Object> trace = new LinkedHashMap<>();
        trace.put("execution_id", manifest.executionId());
        trace.put("artifact_manifest_digest", manifest.artifactManifestDigest());
        trace.put("base_revision", manifest.baseSha());
        trace.put("head_revision", manifest.headSha());
        trace.put("versions", Map.of(
                "provider", "scripted",
                "model", "fixture-v1",
                "prompt_version", "prompt-v1",
                "rules_version", "rules-v1",
                "policy_version", manifest.policyVersion(),
                "index_version", indexVersion));
        trace.put("outcome", "complete");
        trace.put("duration_ms", 1L);
        trace.put("usage", telemetryUsage());
        trace.put("candidates", Map.of("input", 0, "produced", 0, "retained", 0));
        trace.put("coverage", Map.of(
                "inventory", 0,
                "represented", 0,
                "unrepresented", 0));
        trace.put("reason", null);
        trace.put("stages", List.of(
                telemetryStage("generation"),
                telemetryStage("pre_dedup"),
                telemetryStage("post_dedup"),
                telemetryStage("verification"),
                telemetryStage("reconciliation")));
        trace.put("model_calls", List.of());
        trace.put("tool_calls", List.of());
        trace.put("lineage", List.of());

        Map<String, Object> document = new LinkedHashMap<>();
        document.put("schemaVersion", 1);
        document.put("finalizationState", "pending_java");
        document.put("trace", trace);
        document.put("metric", null);
        document.put("sinkErrors", List.of());
        return document;
    }

    private static Map<String, Object> telemetryStage(String name) {
        Map<String, Object> stage = new LinkedHashMap<>();
        stage.put("name", name);
        stage.put("producer", "python");
        stage.put("outcome", "complete");
        stage.put("duration_ms", 1L);
        stage.put("usage", telemetryUsage());
        stage.put("candidates", Map.of("input", 0, "produced", 0, "retained", 0));
        stage.put("coverage", Map.of(
                "inventory", 0,
                "represented", 0,
                "unrepresented", 0));
        stage.put("reason", null);
        return stage;
    }

    private static Map<String, Object> telemetryUsage() {
        Map<String, Object> usage = new LinkedHashMap<>();
        for (String field : List.of(
                "requested_input_tokens",
                "requested_output_tokens",
                "provider_input_tokens",
                "provider_output_tokens",
                "provider_cache_read_tokens",
                "calls",
                "retries",
                "estimated_cost_microunits",
                "provider_usage_missing_calls",
                "cost_estimate_missing_calls")) {
            usage.put(field, 0);
        }
        return usage;
    }

    private record CandidateSetup(
            FrozenExecutionPlan plan,
            ExecutionManifestService manifestService,
            AtomicReference<ImmutableExecutionManifest> persisted) {
    }

    /**
     * The exact method is deliberately declared on this fake before it exists on
     * the production interface. Once the interface owns the method, ordinary
     * virtual dispatch reaches this implementation without a test-only adapter.
     */
    private static final class RecordingVcsAiClientService
            implements VcsAiClientService {
        private final AiAnalysisRequest request;
        private final List<String> sequence;
        private int legacyCalls;
        private int exactCalls;

        private RecordingVcsAiClientService(
                AiAnalysisRequest request,
                List<String> sequence) {
            this.request = request;
            this.sequence = sequence;
        }

        @Override
        public EVcsProvider getProvider() {
            return EVcsProvider.GITHUB;
        }

        @Override
        public List<AiAnalysisRequest> buildAiAnalysisRequests(
                Project project,
                AnalysisProcessRequest processRequest,
                Optional<CodeAnalysis> previousAnalysis) {
            legacyCalls++;
            sequence.add("legacy-acquisition");
            return List.of(request);
        }

        @Override
        public List<AiAnalysisRequest> buildAiAnalysisRequests(
                Project project,
                AnalysisProcessRequest processRequest,
                Optional<CodeAnalysis> previousAnalysis,
                List<CodeAnalysis> allPrAnalyses) {
            legacyCalls++;
            sequence.add("legacy-acquisition");
            return List.of(request);
        }

        @Override
        public List<AiAnalysisRequest> buildExactAiAnalysisRequests(
                Project project,
                AnalysisProcessRequest processRequest,
                Optional<CodeAnalysis> previousAnalysis,
                List<CodeAnalysis> allPrAnalyses,
                ExactHeadAdmission headAdmission) throws GeneralSecurityException {
            exactCalls++;
            sequence.add("exact-acquisition");
            headAdmission.admit(processRequest.getCommitHash());
            return List.of(request);
        }
    }

    private static final class InMemoryManifestPersistence
            implements ExecutionManifestPersistencePort {
        private PersistedExecution persisted;
        private int createOrLoadCalls;
        private final List<String> order;

        private InMemoryManifestPersistence() {
            this(null);
        }

        private InMemoryManifestPersistence(List<String> order) {
            this.order = order;
        }

        @Override
        public synchronized PersistedExecution createOrLoad(
                ImmutableExecutionManifest manifest,
                List<ExecutionArtifactPayload> inputArtifacts) {
            createOrLoadCalls++;
            if (order != null) {
                order.add("manifest-persist");
            }
            if (persisted == null) {
                persisted = new PersistedExecution(manifest, inputArtifacts);
            }
            return persisted;
        }

        @Override
        public synchronized Optional<PersistedExecution> findByExecutionId(
                String executionId) {
            if (order != null) {
                order.add("manifest-reload");
            }
            return persisted != null
                            && persisted.manifest().executionId().equals(executionId)
                    ? Optional.of(persisted)
                    : Optional.empty();
        }
    }
}
