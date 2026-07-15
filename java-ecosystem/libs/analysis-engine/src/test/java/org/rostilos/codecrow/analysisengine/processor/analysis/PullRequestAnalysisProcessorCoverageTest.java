package org.rostilos.codecrow.analysisengine.processor.analysis;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.aiclient.AiAnalysisClient;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequestImpl;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.FileContentDto;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.PrEnrichmentDataDto;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.policy.ExecutionLifecycle;
import org.rostilos.codecrow.analysisengine.policy.ExecutionMode;
import org.rostilos.codecrow.analysisengine.policy.ExecutionPolicyConfig;
import org.rostilos.codecrow.analysisengine.policy.ExecutionPolicyRuntime;
import org.rostilos.codecrow.analysisengine.policy.FrozenExecutionPlan;
import org.rostilos.codecrow.analysisengine.policy.PolicyExecution;
import org.rostilos.codecrow.analysisengine.policy.PolicySelectionReason;
import org.rostilos.codecrow.analysisengine.policy.PublicationFence;
import org.rostilos.codecrow.analysisengine.policy.PublicationReservation;
import org.rostilos.codecrow.analysisengine.policy.StableRolloutKey;
import org.rostilos.codecrow.analysisengine.telemetry.PipelineTelemetryFinalizer.StageObservation;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.analysisengine.service.AstScopeEnricher;
import org.rostilos.codecrow.analysisengine.service.PullRequestService;
import org.rostilos.codecrow.analysisengine.service.pr.PrIssueTrackingService;
import org.rostilos.codecrow.analysisengine.service.rag.RagOperationsService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsReportingService;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsServiceFactory;
import org.rostilos.codecrow.commitgraph.service.AnalyzedCommitService;
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
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.springframework.context.ApplicationEventPublisher;

import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.security.GeneralSecurityException;
import java.time.Instant;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyMap;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.reset;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class PullRequestAnalysisProcessorCoverageTest {
    private static final Instant NOW = Instant.parse("2026-07-14T12:00:00Z");

    @Mock PullRequestService pullRequestService;
    @Mock CodeAnalysisService codeAnalysisService;
    @Mock AiAnalysisClient aiAnalysisClient;
    @Mock VcsServiceFactory vcsServiceFactory;
    @Mock AnalysisLockService analysisLockService;
    @Mock AnalyzedCommitService analyzedCommitService;
    @Mock VcsClientProvider vcsClientProvider;
    @Mock FileSnapshotService fileSnapshotService;
    @Mock PrIssueTrackingService prIssueTrackingService;
    @Mock AstScopeEnricher astScopeEnricher;
    @Mock RagOperationsService ragOperationsService;
    @Mock ApplicationEventPublisher eventPublisher;
    @Mock ExecutionPolicyRuntime executionPolicyRuntime;
    @Mock VcsReportingService reportingService;
    @Mock Project project;
    @Mock PullRequest pullRequest;
    @Mock CodeAnalysis analysis;

    private PullRequestAnalysisProcessor processor;

    @BeforeEach
    void setUp() {
        processor = processor(executionPolicyRuntime, ragOperationsService, eventPublisher);
    }

    @Test
    void freezesStablePolicyIdentityAndRejectsUnpersistedIdentities() throws Throwable {
        PrProcessRequest request = request();
        Workspace workspace = mock(Workspace.class);
        when(project.getId()).thenReturn(7L);
        when(project.getWorkspace()).thenReturn(workspace);
        when(workspace.getId()).thenReturn(9L);
        FrozenExecutionPlan expected = plan("pr:" + "a".repeat(64), false);
        when(executionPolicyRuntime.freeze(anyString(), any(StableRolloutKey.class)))
                .thenReturn(expected);

        assertThat(invoke(processor, "freezePolicyPlan",
                new Class<?>[]{Project.class, PrProcessRequest.class}, project, request))
                .isEqualTo(expected);
        verify(executionPolicyRuntime).freeze(
                anyString(), any(StableRolloutKey.class));

        PullRequestAnalysisProcessor legacy = processor(null, ragOperationsService, eventPublisher);
        assertThat(invoke(legacy, "freezePolicyPlan",
                new Class<?>[]{Project.class, PrProcessRequest.class}, project, request)).isNull();

        when(project.getId()).thenReturn(null);
        assertInvocationCause(IllegalArgumentException.class, () -> invoke(processor, "freezePolicyPlan",
                new Class<?>[]{Project.class, PrProcessRequest.class}, project, request));
        when(project.getId()).thenReturn(0L);
        assertInvocationCause(IllegalArgumentException.class, () -> invoke(processor, "freezePolicyPlan",
                new Class<?>[]{Project.class, PrProcessRequest.class}, project, request));
        when(project.getId()).thenReturn(7L);
        request.pullRequestId = null;
        assertInvocationCause(IllegalArgumentException.class, () -> invoke(processor, "freezePolicyPlan",
                new Class<?>[]{Project.class, PrProcessRequest.class}, project, request));
        request.pullRequestId = 42L;

        when(project.getWorkspace()).thenReturn(null);
        assertInvocationCause(IllegalArgumentException.class, () -> invoke(processor, "freezePolicyPlan",
                new Class<?>[]{Project.class, PrProcessRequest.class}, project, request));
        when(project.getWorkspace()).thenReturn(workspace);
        when(workspace.getId()).thenReturn(null);
        assertInvocationCause(IllegalArgumentException.class, () -> invoke(processor, "freezePolicyPlan",
                new Class<?>[]{Project.class, PrProcessRequest.class}, project, request));
        when(workspace.getId()).thenReturn(0L);
        assertInvocationCause(IllegalArgumentException.class, () -> invoke(processor, "freezePolicyPlan",
                new Class<?>[]{Project.class, PrProcessRequest.class}, project, request));
    }

    @Test
    void lifecycleHelpersHandleAbsentStoppedAndAlreadyCancelledExecutions() throws Throwable {
        ExecutionLifecycle noSwitch = lifecycle("no-switch");
        when(executionPolicyRuntime.currentConfig()).thenReturn(config(false, false));

        assertThat(invoke(processor, "cancelRequested",
                new Class<?>[]{ExecutionLifecycle.class}, new Object[]{null})).isEqualTo(false);
        assertThat(invoke(processor(null, ragOperationsService, eventPublisher), "cancelRequested",
                new Class<?>[]{ExecutionLifecycle.class}, noSwitch)).isEqualTo(false);
        assertThat(invoke(processor, "cancelRequested",
                new Class<?>[]{ExecutionLifecycle.class}, noSwitch)).isEqualTo(false);

        ExecutionLifecycle stopped = lifecycle("stopped");
        when(executionPolicyRuntime.currentConfig()).thenReturn(config(true, false), config(false, false));
        assertThat(invoke(processor, "cancelRequested",
                new Class<?>[]{ExecutionLifecycle.class}, stopped)).isEqualTo(true);
        assertThat(invoke(processor, "cancelRequested",
                new Class<?>[]{ExecutionLifecycle.class}, stopped)).isEqualTo(true);

        ExecutionLifecycle completed = lifecycle("completed");
        invoke(processor, "completePolicyLifecycle",
                new Class<?>[]{ExecutionLifecycle.class}, completed);
        assertThat(completed.state().name()).isEqualTo("COMPLETED");
        invoke(processor, "completePolicyLifecycle",
                new Class<?>[]{ExecutionLifecycle.class}, new Object[]{null});

        ExecutionLifecycle failed = lifecycle("failed");
        invoke(processor, "failPolicyLifecycle",
                new Class<?>[]{ExecutionLifecycle.class}, failed);
        assertThat(failed.state().name()).isEqualTo("FAILED");
        invoke(processor, "failPolicyLifecycle",
                new Class<?>[]{ExecutionLifecycle.class}, new Object[]{null});
    }

    @Test
    void policySelectionTelemetryHandlesPrimaryShadowAndBrokenConsumers() throws Throwable {
        PullRequestAnalysisProcessor.EventConsumer consumer = mock(
                PullRequestAnalysisProcessor.EventConsumer.class);
        invoke(processor, "emitPolicySelection",
                new Class<?>[]{PullRequestAnalysisProcessor.EventConsumer.class, FrozenExecutionPlan.class},
                consumer, null);
        invoke(processor, "emitPolicySelection",
                new Class<?>[]{PullRequestAnalysisProcessor.EventConsumer.class, FrozenExecutionPlan.class},
                consumer, plan("primary-only", false));
        invoke(processor, "emitPolicySelection",
                new Class<?>[]{PullRequestAnalysisProcessor.EventConsumer.class, FrozenExecutionPlan.class},
                consumer, plan("with-shadow", true));
        verify(consumer, times(2)).accept(anyMap());

        doThrow(new IllegalStateException("closed"))
                .when(consumer).accept(anyMap());
        invoke(processor, "emitPolicySelection",
                new Class<?>[]{PullRequestAnalysisProcessor.EventConsumer.class, FrozenExecutionPlan.class},
                consumer, plan("broken-consumer", false));
    }

    @Test
    void taskContextAndEnrichmentHelpersCoverFallbackAndFilteringRules() throws Throwable {
        AiAnalysisRequest request = mock(AiAnalysisRequest.class);
        when(request.getTaskContext()).thenReturn(null, Map.of(),
                new HashMap<>(Map.of("first", " ", "second", "  value  ")),
                Map.of("first", " "));
        Class<?>[] taskTypes = {AiAnalysisRequest.class, String[].class};

        assertThat(invoke(processor, "taskContextValue", taskTypes, request, new String[]{"first"})).isNull();
        assertThat(invoke(processor, "taskContextValue", taskTypes, request, new String[]{"first"})).isNull();
        assertThat(invoke(processor, "taskContextValue", taskTypes, request,
                new String[]{"missing", "first", "second"})).isEqualTo("value");
        assertThat(invoke(processor, "taskContextValue", taskTypes, request, new String[]{"first"})).isNull();

        assertThat(invoke(processor, "extractFileContents",
                new Class<?>[]{AiAnalysisRequest.class}, request)).isEqualTo(Map.of());
        AiAnalysisRequestImpl noEnrichment = AiAnalysisRequestImpl.builder().build();
        assertThat(invoke(processor, "extractFileContents",
                new Class<?>[]{AiAnalysisRequest.class}, noEnrichment)).isEqualTo(Map.of());
        AiAnalysisRequestImpl nullContents = AiAnalysisRequestImpl.builder()
                .withEnrichmentData(new PrEnrichmentDataDto(null, null, null, null))
                .build();
        assertThat(invoke(processor, "extractFileContents",
                new Class<?>[]{AiAnalysisRequest.class}, nullContents)).isEqualTo(Map.of());

        PrEnrichmentDataDto enrichment = new PrEnrichmentDataDto(
                List.of(
                        FileContentDto.skipped("skip.java", "binary"),
                        new FileContentDto("null.java", null, 0, false, null),
                        FileContentDto.of("kept.java", "first"),
                        FileContentDto.of("kept.java", "second")),
                List.of(), List.of(), PrEnrichmentDataDto.EnrichmentStats.empty());
        AiAnalysisRequestImpl enriched = AiAnalysisRequestImpl.builder()
                .withEnrichmentData(enrichment)
                .build();
        assertThat(invoke(processor, "extractFileContents",
                new Class<?>[]{AiAnalysisRequest.class}, enriched))
                .isEqualTo(Map.of("kept.java", "first"));
    }

    @Test
    void vcsFallbackRejectsEmptyInputsAndHandlesMissingConnectionsSuccessAndFailure() throws Throwable {
        Class<?>[] types = {Project.class, List.class, String.class};
        assertThat(invoke(processor, "fetchFileContentsFromVcs", types, project, null, "head"))
                .isEqualTo(Map.of());
        assertThat(invoke(processor, "fetchFileContentsFromVcs", types, project, List.of(), "head"))
                .isEqualTo(Map.of());

        when(project.getEffectiveVcsRepoInfo()).thenReturn(null);
        assertThat(invoke(processor, "fetchFileContentsFromVcs", types,
                project, List.of("A.java"), "head")).isEqualTo(Map.of());

        VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
        when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
        when(repoInfo.getVcsConnection()).thenReturn(null);
        assertThat(invoke(processor, "fetchFileContentsFromVcs", types,
                project, List.of("A.java"), "head")).isEqualTo(Map.of());

        VcsConnection connection = mock(VcsConnection.class);
        VcsClient vcsClient = mock(VcsClient.class);
        when(repoInfo.getVcsConnection()).thenReturn(connection);
        when(repoInfo.getRepoWorkspace()).thenReturn("workspace");
        when(repoInfo.getRepoSlug()).thenReturn("repository");
        when(vcsClientProvider.getClient(connection)).thenReturn(vcsClient);
        when(vcsClient.getFileContents(anyString(), anyString(), any(), any(), anyInt()))
                .thenReturn(Map.of("A.java", "class A {}"));
        assertThat(invoke(processor, "fetchFileContentsFromVcs", types,
                project, List.of("A.java"), "head-revision"))
                .isEqualTo(Map.of("A.java", "class A {}"));
        assertThat(invoke(processor, "fetchFileContentsFromVcs", types,
                project, List.of("A.java"), null))
                .isEqualTo(Map.of("A.java", "class A {}"));

        when(vcsClientProvider.getClient(connection)).thenThrow(new IllegalStateException("provider down"));
        assertThat(invoke(processor, "fetchFileContentsFromVcs", types,
                project, List.of("A.java"), "head")).isEqualTo(Map.of());
    }

    @Test
    void cacheHitSnapshotsCopyFirstThenFallBackToDistinctIssuePaths() throws Throwable {
        Class<?>[] types = {
                PullRequest.class, CodeAnalysis.class, CodeAnalysis.class, Project.class,
                String.class, List.class};
        CodeAnalysis source = mock(CodeAnalysis.class);
        CodeAnalysis cloned = mock(CodeAnalysis.class);
        PullRequest sourcePr = mock(PullRequest.class);
        when(project.getId()).thenReturn(7L);
        when(source.getPrNumber()).thenReturn(88L);
        when(pullRequestService.findPullRequest(7L, 88L)).thenReturn(Optional.of(sourcePr));
        when(sourcePr.getId()).thenReturn(800L);
        when(fileSnapshotService.getFileContentsMapForPr(800L))
                .thenReturn(Map.of("Copied.java", "copied"));
        invoke(processor, "persistPrSnapshotsForCacheHit", types,
                pullRequest, cloned, source, project, "head", List.of("ignored.java"));
        verify(fileSnapshotService).persistSnapshotsForPr(
                pullRequest, cloned, Map.of("Copied.java", "copied"), "head");

        reset(fileSnapshotService);
        when(fileSnapshotService.getFileContentsMapForPr(800L)).thenReturn(Map.of());
        VcsRepoInfo emptySourceRepo = mock(VcsRepoInfo.class);
        VcsConnection emptySourceConnection = mock(VcsConnection.class);
        VcsClient emptySourceClient = mock(VcsClient.class);
        when(project.getEffectiveVcsRepoInfo()).thenReturn(emptySourceRepo);
        when(emptySourceRepo.getVcsConnection()).thenReturn(emptySourceConnection);
        when(emptySourceRepo.getRepoWorkspace()).thenReturn("workspace");
        when(emptySourceRepo.getRepoSlug()).thenReturn("repository");
        when(vcsClientProvider.getClient(emptySourceConnection)).thenReturn(emptySourceClient);
        when(emptySourceClient.getFileContents(anyString(), anyString(), any(), any(), anyInt()))
                .thenReturn(Map.of("Fallback.java", "class Fallback {}"));
        invoke(processor, "persistPrSnapshotsForCacheHit", types,
                pullRequest, cloned, source, project, "head", List.of("Fallback.java"));
        verify(fileSnapshotService).persistSnapshotsForPr(
                pullRequest, cloned, Map.of("Fallback.java", "class Fallback {}"), "head");

        reset(fileSnapshotService, pullRequestService, vcsClientProvider);
        when(source.getPrNumber()).thenReturn(null);
        CodeAnalysisIssue missing = mock(CodeAnalysisIssue.class);
        CodeAnalysisIssue blank = mock(CodeAnalysisIssue.class);
        CodeAnalysisIssue present = mock(CodeAnalysisIssue.class);
        CodeAnalysisIssue duplicate = mock(CodeAnalysisIssue.class);
        when(missing.getFilePath()).thenReturn(null);
        when(blank.getFilePath()).thenReturn(" ");
        when(present.getFilePath()).thenReturn("Issue.java");
        when(duplicate.getFilePath()).thenReturn("Issue.java");
        when(cloned.getIssues()).thenReturn(List.of(missing, blank, present, duplicate));
        VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
        VcsConnection connection = mock(VcsConnection.class);
        VcsClient vcsClient = mock(VcsClient.class);
        when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
        when(repoInfo.getVcsConnection()).thenReturn(connection);
        when(repoInfo.getRepoWorkspace()).thenReturn("workspace");
        when(repoInfo.getRepoSlug()).thenReturn("repository");
        when(vcsClientProvider.getClient(connection)).thenReturn(vcsClient);
        when(vcsClient.getFileContents(anyString(), anyString(), any(), any(), anyInt()))
                .thenReturn(Map.of("Issue.java", "class Issue {}"));
        invoke(processor, "persistPrSnapshotsForCacheHit", types,
                pullRequest, cloned, source, project, "head", null);
        verify(fileSnapshotService).persistSnapshotsForPr(
                pullRequest, cloned, Map.of("Issue.java", "class Issue {}"), "head");

        when(cloned.getIssues()).thenThrow(new IllegalStateException("broken issues"));
        invoke(processor, "persistPrSnapshotsForCacheHit", types,
                pullRequest, cloned, source, project, "head", List.of());
    }

    @Test
    void fingerprintAndCommitCacheOverloadsCoverNoHitCloneFailureAndDeliveryFailure() throws Exception {
        PrProcessRequest request = request();
        AiAnalysisRequest aiRequest = mock(AiAnalysisRequest.class);
        when(project.getId()).thenReturn(7L);
        when(codeAnalysisService.getAnalysisByDiffFingerprint(7L, "fingerprint"))
                .thenReturn(Optional.empty());
        assertThat(processor.postDiffFingerprintCacheIfExist(
                request, "fingerprint", project, pullRequest, aiRequest, reportingService)).isFalse();
        assertThat(processor.postDiffFingerprintCacheIfExist(
                request, "fingerprint", project, pullRequest, aiRequest, reportingService, event -> { })).isFalse();

        CodeAnalysis source = mock(CodeAnalysis.class);
        when(codeAnalysisService.getAnalysisByDiffFingerprint(7L, "fingerprint"))
                .thenReturn(Optional.of(source));
        when(source.getPrNumber()).thenReturn(88L);
        when(codeAnalysisService.cloneAnalysisForPr(any(), any(), anyLong(), any(), any(), any(), any()))
                .thenThrow(new IllegalStateException("clone failed"));
        assertThatThrownBy(() -> processor.postDiffFingerprintCacheIfExist(
                request, "fingerprint", project, pullRequest, aiRequest, reportingService, event -> { }))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("clone failed");

        reset(codeAnalysisService);
        CodeAnalysis cloned = mock(CodeAnalysis.class);
        when(codeAnalysisService.getAnalysisByDiffFingerprint(7L, "fingerprint"))
                .thenReturn(Optional.of(source));
        when(codeAnalysisService.cloneAnalysisForPr(any(), any(), anyLong(), any(), any(), any(), any()))
                .thenReturn(cloned);
        when(aiRequest.getChangedFiles()).thenReturn(List.of());
        when(pullRequest.getId()).thenReturn(100L);
        doThrow(new java.io.IOException("delivery failed")).when(reportingService)
                .postAnalysisResults(any(), any(), anyLong(), any(), any());
        assertThat(processor.postDiffFingerprintCacheIfExist(
                request, "fingerprint", project, pullRequest, aiRequest, reportingService, event -> { })).isTrue();

        reset(reportingService);
        assertThat(processor.postDiffFingerprintCacheIfExist(
                request, "fingerprint", project, pullRequest, aiRequest, reportingService)).isTrue();

        reset(codeAnalysisService);
        when(codeAnalysisService.getCodeAnalysisCache(7L, "head", 42L)).thenReturn(Optional.empty());
        when(codeAnalysisService.getAnalysisByCommitHash(7L, "head")).thenReturn(Optional.empty());
        assertThat(processor.postAnalysisCacheIfExist(
                project, pullRequest, "head", 42L, reportingService, null,
                "main", "feature", event -> { }))
                .isEqualTo(PullRequestAnalysisProcessor.CacheHitType.NONE);

        when(codeAnalysisService.getAnalysisByCommitHash(7L, "head")).thenReturn(Optional.of(source));
        when(codeAnalysisService.cloneAnalysisForPr(any(), any(), anyLong(), any(), any(), any(), any()))
                .thenThrow(new IllegalStateException("commit clone failed"));
        assertThatThrownBy(() -> processor.postAnalysisCacheIfExist(
                project, pullRequest, "head", 42L, reportingService, null,
                "main", "feature", event -> { }))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("commit clone failed");
    }

    @Test
    void publicationFenceBlocksShadowAndDuplicatesAndAllowsReservedDelivery() throws Throwable {
        PublicationFence fence = mock(PublicationFence.class);
        when(executionPolicyRuntime.publicationFence()).thenReturn(fence);
        VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
        VcsConnection connection = mock(VcsConnection.class);
        when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
        when(repoInfo.getVcsConnection()).thenReturn(connection);
        when(connection.getProviderType()).thenReturn(EVcsProvider.GITHUB);
        when(project.getId()).thenReturn(7L);
        FrozenExecutionPlan plan = plan("publication", false);
        PullRequestAnalysisProcessor.EventConsumer consumer = mock(
                PullRequestAnalysisProcessor.EventConsumer.class);
        Class<?>[] types = publicationTypes();
        Object[] args = publicationArgs(plan, consumer);

        when(fence.reserve(any(), any())).thenReturn(PublicationReservation.SHADOW_DENIED);
        assertThat(invoke(processor, "publishAnalysisResults", types, args)).isEqualTo(false);
        when(fence.reserve(any(), any())).thenReturn(PublicationReservation.DUPLICATE);
        assertThat(invoke(processor, "publishAnalysisResults", types, args)).isEqualTo(false);
        verify(reportingService, never()).postAnalysisResults(any(), any(), anyLong(), any(), any());

        when(fence.reserve(any(), any())).thenReturn(PublicationReservation.RESERVED);
        assertThat(invoke(processor, "publishAnalysisResults", types, args)).isEqualTo(true);
        verify(reportingService).postAnalysisResults(any(), any(), anyLong(), any(), any());

        PullRequestAnalysisProcessor noRuntime = processor(null, ragOperationsService, eventPublisher);
        assertThat(invoke(noRuntime, "publishAnalysisResults", types, args)).isEqualTo(true);
        Object[] noPlan = publicationArgs(null, consumer);
        assertThat(invoke(processor, "publishAnalysisResults", types, noPlan)).isEqualTo(true);
    }

    @Test
    void commitRagTelemetryAndEventHelpersAreFailSafe() throws Throwable {
        Class<?>[] markTypes = {Project.class, String.class, String.class, CodeAnalysis.class};
        invoke(processor, "markPrCommitsAnalyzed", markTypes, project, "feature", null, analysis);
        when(analysis.getId()).thenReturn(12L);
        invoke(processor, "markPrCommitsAnalyzed", markTypes, project, "feature", "abcdef", analysis);
        invoke(processor, "markPrCommitsAnalyzed", markTypes, project, "feature", "abcdef", null);
        doThrow(new IllegalStateException("ledger failed")).when(analyzedCommitService)
                .recordPrCommitsAnalyzed(any(), any(), any());
        invoke(processor, "markPrCommitsAnalyzed", markTypes, project, "feature", "abcdef", analysis);

        Class<?>[] ragTypes = {
                Project.class, String.class, PullRequestAnalysisProcessor.EventConsumer.class};
        PullRequestAnalysisProcessor noRag = processor(executionPolicyRuntime, null, eventPublisher);
        assertThat(invoke(noRag, "ensureRagIndexForTargetBranch", ragTypes,
                project, "main", (PullRequestAnalysisProcessor.EventConsumer) event -> { }))
                .isEqualTo("rag_service_unavailable");
        when(ragOperationsService.ensureRagIndexUpToDate(any(), anyString(), any()))
                .thenReturn(true, false)
                .thenThrow(new IllegalStateException("rag failed"));
        assertThat(invoke(processor, "ensureRagIndexForTargetBranch", ragTypes,
                project, "main", (PullRequestAnalysisProcessor.EventConsumer) event -> { })).isNull();
        assertThat(invoke(processor, "ensureRagIndexForTargetBranch", ragTypes,
                project, "main", (PullRequestAnalysisProcessor.EventConsumer) event -> { }))
                .isEqualTo("rag_index_not_ready");
        assertThat(invoke(processor, "ensureRagIndexForTargetBranch", ragTypes,
                project, "main", (PullRequestAnalysisProcessor.EventConsumer) event -> { }))
                .isEqualTo("rag_index_refresh_failed");

        Class<?>[] telemetryTypes = {
                PullRequestAnalysisProcessor.EventConsumer.class, String.class, String.class,
                String.class, Instant.class, int.class, String.class};
        PullRequestAnalysisProcessor.EventConsumer telemetry = mock(
                PullRequestAnalysisProcessor.EventConsumer.class);
        invoke(processor, "emitStageTelemetry", telemetryTypes,
                telemetry, "stage", "producer", "complete", NOW.plusSeconds(1), -1, null);
        invoke(processor, "emitStageTelemetry", telemetryTypes,
                telemetry, "stage", "producer", "skipped", NOW, 2, "reason");
        doThrow(new IllegalStateException("sink failed")).when(telemetry).accept(anyMap());
        invoke(processor, "emitStageTelemetry", telemetryTypes,
                telemetry, "stage", "producer", "failed", NOW, 0, null);

        Class<?>[] issueTypes = {CodeAnalysis.class};
        assertThat(invoke(processor, "telemetryIssueCount", issueTypes, new Object[]{null})).isEqualTo(0);
        when(analysis.getIssues()).thenReturn(null, List.of(mock(CodeAnalysisIssue.class)))
                .thenThrow(new IllegalStateException("broken issues"));
        assertThat(invoke(processor, "telemetryIssueCount", issueTypes, analysis)).isEqualTo(0);
        assertThat(invoke(processor, "telemetryIssueCount", issueTypes, analysis)).isEqualTo(1);
        assertThat(invoke(processor, "telemetryIssueCount", issueTypes, analysis)).isEqualTo(0);

        PrProcessRequest request = request();
        request.prTitle = "Title";
        request.prDescription = "Description";
        Workspace workspace = mock(Workspace.class);
        when(project.getWorkspace()).thenReturn(workspace);
        when(workspace.getName()).thenReturn("Workspace");
        when(project.getName()).thenReturn("Project");
        when(project.getNamespace()).thenReturn("namespace");
        invoke(processor, "publishAnalysisStartedEvent",
                new Class<?>[]{Project.class, PrProcessRequest.class, String.class},
                project, request, "correlation");
        invoke(processor, "publishAnalysisCompletedEvent", completedEventTypes(),
                project, request, "correlation", NOW,
                org.rostilos.codecrow.events.analysis.AnalysisCompletedEvent.CompletionStatus.SUCCESS,
                2, 3, null);

        doThrow(new IllegalStateException("publisher failed")).when(eventPublisher)
                .publishEvent(any(Object.class));
        invoke(processor, "publishAnalysisStartedEvent",
                new Class<?>[]{Project.class, PrProcessRequest.class, String.class},
                project, request, "correlation");
        invoke(processor, "publishAnalysisCompletedEvent", completedEventTypes(),
                project, request, "correlation", NOW,
                org.rostilos.codecrow.events.analysis.AnalysisCompletedEvent.CompletionStatus.FAILED,
                0, 0, "failed");

        PullRequestAnalysisProcessor noPublisher = processor(
                executionPolicyRuntime, ragOperationsService, null);
        invoke(noPublisher, "publishAnalysisStartedEvent",
                new Class<?>[]{Project.class, PrProcessRequest.class, String.class},
                project, request, "correlation");
        invoke(noPublisher, "publishAnalysisCompletedEvent", completedEventTypes(),
                project, request, "correlation", NOW,
                org.rostilos.codecrow.events.analysis.AnalysisCompletedEvent.CompletionStatus.SUCCESS,
                0, 0, null);
    }

    @Test
    void indexDispatchAndTerminalHelpersCoverEveryFailClosedBoundary() throws Throwable {
        RagOperationsService defaultRag = mock(
                RagOperationsService.class, org.mockito.Answers.CALLS_REAL_METHODS);
        assertThat(defaultRag.getIndexVersion(project, "main")).isNull();

        Class<?>[] indexTypes = {Project.class, String.class};
        PullRequestAnalysisProcessor noRag = processor(executionPolicyRuntime, null, eventPublisher);
        assertThat(invoke(noRag, "resolveIndexVersion", indexTypes, project, "main"))
                .isEqualTo("rag-service-unavailable");
        when(ragOperationsService.getIndexVersion(project, "main"))
                .thenReturn(null, " ", "stale-index-v1", "rag-commit-" + "c".repeat(40))
                .thenThrow(new IllegalStateException("index store unavailable"));
        assertThat(invoke(processor, "resolveIndexVersion", indexTypes, project, "main"))
                .isEqualTo("rag-version-unavailable");
        assertThat(invoke(processor, "resolveIndexVersion", indexTypes, project, "main"))
                .isEqualTo("rag-version-unavailable");
        assertThat(invoke(processor, "resolveIndexVersion", indexTypes, project, "main"))
                .isEqualTo("rag-version-unavailable");
        assertThat(invoke(processor, "resolveIndexVersion", indexTypes, project, "main"))
                .isEqualTo("rag-commit-" + "c".repeat(40));
        assertThat(invoke(processor, "resolveIndexVersion", indexTypes, project, "main"))
                .isEqualTo("rag-version-unavailable");

        AiAnalysisRequest aiRequest = mock(AiAnalysisRequest.class);
        FrozenExecutionPlan plan = plan("index-dispatch", false);
        Map<String, Object> exact = Map.of("path", "exact");
        Map<String, Object> unavailable = Map.of("path", "unavailable");
        when(aiAnalysisClient.performAnalysis(
                eq(aiRequest), any(), eq(plan.primary()), eq("rag-commit-" + "d".repeat(40))))
                .thenReturn(exact);
        when(aiAnalysisClient.performAnalysis(eq(aiRequest), any(), eq(plan.primary())))
                .thenReturn(unavailable);
        Class<?>[] dispatchTypes = {
                AiAnalysisRequest.class, Consumer.class, FrozenExecutionPlan.class, String.class};
        assertThat(invoke(processor, "performAiAnalysis", dispatchTypes,
                aiRequest, (Consumer<Map<String, Object>>) event -> { }, plan,
                "rag-commit-" + "d".repeat(40))).isEqualTo(exact);
        assertThat(invoke(processor, "performAiAnalysis", dispatchTypes,
                aiRequest, (Consumer<Map<String, Object>>) event -> { }, plan,
                "rag-service-unavailable")).isEqualTo(unavailable);
        assertThat(invoke(processor, "performAiAnalysis", dispatchTypes,
                aiRequest, (Consumer<Map<String, Object>>) event -> { }, plan,
                "rag-version-unavailable")).isEqualTo(unavailable);
        assertThat(invoke(processor, "performAiAnalysis", dispatchTypes,
                aiRequest, (Consumer<Map<String, Object>>) event -> { }, plan,
                "stale-index-v1")).isEqualTo(unavailable);
        assertThat(invoke(processor, "performAiAnalysis", dispatchTypes,
                aiRequest, (Consumer<Map<String, Object>>) event -> { }, plan,
                null)).isEqualTo(unavailable);

        Class<?>[] finalizerTypes = {
                Map.class, PullRequestAnalysisProcessor.EventConsumer.class, Instant.class, List.class};
        List<Map<String, Object>> events = new java.util.ArrayList<>();
        PullRequestAnalysisProcessor.EventConsumer consumer = events::add;
        assertThat(invoke(processor, "finalizePipelineTelemetry", finalizerTypes,
                null, consumer, NOW, List.of())).isNull();

        Map<String, Object> noSnapshot = new HashMap<>(Map.of("comment", "review"));
        assertThat(invoke(processor, "finalizePipelineTelemetry", finalizerTypes,
                noSnapshot, consumer, NOW, List.of())).isEqualTo(noSnapshot);
        assertThat(events).anyMatch(event -> "python_snapshot_unavailable".equals(event.get("reason")));

        Map<String, Object> invalidSnapshot = new HashMap<>();
        invalidSnapshot.put("telemetry", Map.of("finalizationState", "invalid"));
        assertThat(invoke(processor, "finalizePipelineTelemetry", finalizerTypes,
                invalidSnapshot, consumer, NOW, List.of())).isEqualTo(invalidSnapshot);
        assertThat(events).anyMatch(event -> "terminal_contract_rejected".equals(event.get("reason")));

        Class<?>[] attachTypes = {Map.class, Map.class};
        Map<String, Object> cancelled = Map.of("status", "cancelled");
        assertThat(invoke(processor, "attachFinalizedTelemetry", attachTypes,
                cancelled, null)).isEqualTo(cancelled);
        assertThat(invoke(processor, "attachFinalizedTelemetry", attachTypes,
                cancelled, Map.of("comment", "review"))).isEqualTo(cancelled);
        Map<String, Object> telemetry = Map.of("finalizationState", "terminal");
        assertThat(invoke(processor, "attachFinalizedTelemetry", attachTypes,
                cancelled, Map.of("telemetry", telemetry)))
                .isEqualTo(Map.of("status", "cancelled", "telemetry", telemetry));
    }

    @Test
    void processHandlesBlankLocksCompleteRetrievalPreviousAnalysisAndNullRequests() throws Exception {
        PrProcessRequest request = request();
        request.preAcquiredLockKey = " ";
        CodeAnalysis previous = mock(CodeAnalysis.class);
        VcsAiClientService aiClientService = stubProcessThroughAcquisition(
                request, List.of(previous));
        when(analysisLockService.acquireLockWithWait(
                any(), anyString(), any(), anyString(), anyLong(), any()))
                .thenReturn(Optional.of("acquired"));
        when(ragOperationsService.ensureRagIndexUpToDate(any(), anyString(), any()))
                .thenReturn(true);
        when(aiClientService.buildAiAnalysisRequests(any(), any(), any(), any()))
                .thenReturn(null);

        Map<String, Object> result = processor(null, ragOperationsService, eventPublisher)
                .process(request, event -> { }, project);

        assertThat(result).containsEntry("status", "ignored");
        verify(analysisLockService).releaseLock("acquired");
    }

    @Test
    void processReportsFailedRetrievalAndIgnoresEmptyRequests() throws Exception {
        PrProcessRequest request = request();
        request.preAcquiredLockKey = "pre-acquired";
        VcsAiClientService aiClientService = stubProcessThroughAcquisition(request, List.of());
        when(ragOperationsService.ensureRagIndexUpToDate(any(), anyString(), any()))
                .thenThrow(new IllegalStateException("index unavailable"));
        when(aiClientService.buildAiAnalysisRequests(any(), any(), any(), any()))
                .thenReturn(List.of());
        List<Map<String, Object>> events = new java.util.ArrayList<>();

        Map<String, Object> result = processor(null, ragOperationsService, eventPublisher)
                .process(request, events::add, project);

        assertThat(result).containsEntry("status", "ignored");
        assertThat(events).anyMatch(event -> "retrieval".equals(event.get("stage"))
                && "failed".equals(event.get("outcome")));
    }

    @Test
    void processEmitsAcquisitionFailureBeforePropagatingBuilderErrors() throws Exception {
        PrProcessRequest request = request();
        request.preAcquiredLockKey = "pre-acquired";
        VcsAiClientService aiClientService = stubProcessThroughAcquisition(request, List.of());
        when(ragOperationsService.ensureRagIndexUpToDate(any(), anyString(), any()))
                .thenReturn(false);
        when(aiClientService.buildAiAnalysisRequests(any(), any(), any(), any()))
                .thenThrow(new GeneralSecurityException("credentials unavailable"));
        List<Map<String, Object>> events = new java.util.ArrayList<>();

        assertThatThrownBy(() -> processor(null, ragOperationsService, eventPublisher)
                .process(request, events::add, project))
                .isInstanceOf(GeneralSecurityException.class)
                .hasMessageContaining("credentials unavailable");
        assertThat(events).anyMatch(event -> "acquisition".equals(event.get("stage"))
                && "failed".equals(event.get("outcome")));
    }

    @Test
    void processForwardsAiEventsAndKeepsNonCriticalEnrichmentFailuresIsolated() throws Exception {
        PrProcessRequest request = request();
        request.preAcquiredLockKey = "pre-acquired";
        CodeAnalysis previous = mock(CodeAnalysis.class);
        AiAnalysisRequestImpl aiRequest = mock(AiAnalysisRequestImpl.class);
        VcsAiClientService aiClientService = stubProcessThroughAcquisition(request, List.of(previous));
        when(ragOperationsService.ensureRagIndexUpToDate(any(), anyString(), any()))
                .thenReturn(true);
        when(aiClientService.buildAiAnalysisRequests(any(), any(), any(), any()))
                .thenReturn(List.of(aiRequest));
        when(aiRequest.getRawDiff()).thenReturn("+line");
        when(aiRequest.getChangedFiles()).thenReturn(List.of("Changed.java"));
        when(aiRequest.getEnrichmentData()).thenReturn(new PrEnrichmentDataDto(
                List.of(FileContentDto.of("Changed.java", "class Changed {}")),
                List.of(), List.of(), PrEnrichmentDataDto.EnrichmentStats.empty()));
        when(codeAnalysisService.getAnalysisByDiffFingerprint(eq(7L), anyString()))
                .thenReturn(Optional.empty());
        Map<String, Object> aiResponse = Map.of("comment", "review", "issues", List.of());
        doAnswer(invocation -> {
            @SuppressWarnings("unchecked")
            Consumer<Map<String, Object>> aiEvents = invocation.getArgument(1);
            aiEvents.accept(Map.of("type", "ai_progress_ok"));
            aiEvents.accept(Map.of("type", "ai_progress"));
            return aiResponse;
        }).when(aiAnalysisClient).performAnalysis(eq(aiRequest), any());
        CodeAnalysisIssue issue = mock(CodeAnalysisIssue.class);
        when(analysis.getIssues()).thenReturn(List.of(issue));
        when(codeAnalysisService.createAnalysisFromAiResponse(
                any(), any(), anyLong(), anyString(), anyString(), anyString(), any(), any(),
                anyString(), anyMap(), isNull(), isNull()))
                .thenReturn(analysis);
        when(previous.getId()).thenReturn(91L);
        when(fileSnapshotService.getFileContentsMap(91L)).thenReturn(Map.of("Old.java", "old"));
        when(pullRequest.getId()).thenReturn(100L);
        PullRequestAnalysisProcessor.EventConsumer consumer = event -> {
            if ("ai_progress".equals(event.get("type"))) {
                throw new IllegalStateException("client disconnected");
            }
        };

        Map<String, Object> result = processor(null, ragOperationsService, eventPublisher)
                .process(request, consumer, project);

        assertThat(result).isEqualTo(aiResponse);
        verify(astScopeEnricher).enrichWithAstScopes(List.of(issue),
                Map.of("Changed.java", "class Changed {}"));
        verify(prIssueTrackingService).trackPrIteration(
                analysis, previous, Map.of("Changed.java", "class Changed {}"), Map.of("Old.java", "old"));
    }

    @Test
    void processEmitsTheOnlyTerminalAfterJavaPersistenceAndDelivery() throws Exception {
        PrProcessRequest request = request();
        request.preAcquiredLockKey = "pre-acquired";
        AiAnalysisRequest aiRequest = mock(AiAnalysisRequest.class);
        VcsAiClientService aiClientService = stubProcessThroughAcquisition(request, List.of());
        Workspace workspace = mock(Workspace.class);
        when(project.getWorkspace()).thenReturn(workspace);
        when(workspace.getId()).thenReturn(9L);
        FrozenExecutionPlan policyPlan = plan("p004-terminal", false);
        when(executionPolicyRuntime.freeze(anyString(), any(StableRolloutKey.class)))
                .thenReturn(policyPlan);
        when(executionPolicyRuntime.currentConfig()).thenReturn(config(false, false));
        PublicationFence fence = mock(PublicationFence.class);
        when(executionPolicyRuntime.publicationFence()).thenReturn(fence);
        when(fence.reserve(any(), any())).thenReturn(PublicationReservation.SHADOW_DENIED);
        String indexVersion = "rag-commit-" + "c".repeat(40);
        when(ragOperationsService.ensureRagIndexUpToDate(any(), anyString(), any()))
                .thenReturn(true);
        when(ragOperationsService.getIndexVersion(project, "main")).thenReturn(indexVersion);
        when(aiClientService.buildAiAnalysisRequests(any(), any(), any(), any()))
                .thenReturn(List.of(aiRequest));
        when(aiRequest.getRawDiff()).thenReturn("+line");
        when(aiRequest.getChangedFiles()).thenReturn(List.of());
        when(codeAnalysisService.getAnalysisByDiffFingerprint(eq(7L), anyString()))
                .thenReturn(Optional.empty());
        Map<String, Object> provisional = pendingTelemetryDocument();
        Map<String, Object> aiResponse = new HashMap<>();
        aiResponse.put("comment", "review");
        aiResponse.put("issues", List.of());
        aiResponse.put("telemetry", provisional);
        doAnswer(invocation -> {
            @SuppressWarnings("unchecked")
            Consumer<Map<String, Object>> aiEvents = invocation.getArgument(1);
            aiEvents.accept(Map.of(
                    "type", "telemetry",
                    "state", "provisional",
                    "outcome", "complete"));
            return aiResponse;
        }).when(aiAnalysisClient).performAnalysis(
                eq(aiRequest), any(), eq(policyPlan.primary()), eq(indexVersion));
        when(codeAnalysisService.createAnalysisFromAiResponse(
                any(), any(), anyLong(), anyString(), anyString(), anyString(), any(), any(),
                anyString(), anyMap(), isNull(), isNull()))
                .thenReturn(analysis);
        when(analysis.getIssues()).thenReturn(List.of());
        when(pullRequest.getId()).thenReturn(100L);
        List<Map<String, Object>> events = new java.util.ArrayList<>();

        Map<String, Object> result = processor.process(request, events::add, project);

        @SuppressWarnings("unchecked")
        Map<String, Object> terminal = (Map<String, Object>) result.get("telemetry");
        assertThat(terminal)
                .containsEntry("finalizationState", "terminal")
                .containsKey("metric");
        assertThat(provisional)
                .containsEntry("finalizationState", "pending_java")
                .containsEntry("metric", null);

        int provisionalEvent = eventIndex(events, "state", "provisional");
        int persistenceEvent = stageEventIndex(events, "persistence", "complete");
        int deliveryEvent = stageEventIndex(events, "delivery", "skipped");
        int terminalEvent = eventIndex(events, "state", "emitted");
        assertThat(provisionalEvent).isGreaterThanOrEqualTo(0);
        assertThat(persistenceEvent).isGreaterThan(provisionalEvent);
        assertThat(deliveryEvent).isGreaterThan(persistenceEvent);
        assertThat(terminalEvent).isGreaterThan(deliveryEvent);
        List<Map<String, Object>> terminalEvents = events.stream()
                .filter(event -> "emitted".equals(event.get("state")))
                .toList();
        assertThat(terminalEvents).hasSize(1);
        assertThat(terminalEvents.get(0)).containsEntry("outcome", "complete");
    }

    @Test
    void processReturnsFinalizedPartialWhenDeliveryAndWarningSinkBothFail() throws Exception {
        PrProcessRequest request = request();
        request.preAcquiredLockKey = "pre-acquired";
        AiAnalysisRequest aiRequest = mock(AiAnalysisRequest.class);
        VcsAiClientService aiClientService = stubProcessThroughAcquisition(request, List.of());
        Workspace workspace = mock(Workspace.class);
        when(project.getWorkspace()).thenReturn(workspace);
        when(workspace.getId()).thenReturn(9L);
        FrozenExecutionPlan policyPlan = plan("p004-delivery-failure", false);
        when(executionPolicyRuntime.freeze(anyString(), any(StableRolloutKey.class)))
                .thenReturn(policyPlan);
        when(executionPolicyRuntime.currentConfig()).thenReturn(config(false, false));
        PublicationFence fence = mock(PublicationFence.class);
        when(executionPolicyRuntime.publicationFence()).thenReturn(fence);
        when(fence.reserve(any(), any())).thenReturn(PublicationReservation.RESERVED);
        String indexVersion = "rag-commit-" + "c".repeat(40);
        when(ragOperationsService.ensureRagIndexUpToDate(any(), anyString(), any()))
                .thenReturn(true);
        when(ragOperationsService.getIndexVersion(project, "main")).thenReturn(indexVersion);
        when(aiClientService.buildAiAnalysisRequests(any(), any(), any(), any()))
                .thenReturn(List.of(aiRequest));
        when(aiRequest.getRawDiff()).thenReturn("+line");
        when(aiRequest.getChangedFiles()).thenReturn(List.of());
        when(codeAnalysisService.getAnalysisByDiffFingerprint(eq(7L), anyString()))
                .thenReturn(Optional.empty());
        Map<String, Object> aiResponse = new HashMap<>();
        aiResponse.put("comment", "review");
        aiResponse.put("issues", List.of());
        aiResponse.put("telemetry", pendingTelemetryDocument());
        when(aiAnalysisClient.performAnalysis(
                eq(aiRequest), any(), eq(policyPlan.primary()), eq(indexVersion)))
                .thenReturn(aiResponse);
        when(codeAnalysisService.createAnalysisFromAiResponse(
                any(), any(), anyLong(), anyString(), anyString(), anyString(), any(), any(),
                anyString(), anyMap(), isNull(), isNull()))
                .thenReturn(analysis);
        when(analysis.getIssues()).thenReturn(List.of());
        when(pullRequest.getId()).thenReturn(100L);
        doThrow(new java.io.IOException("VCS API error")).when(reportingService)
                .postAnalysisResults(any(), any(), anyLong(), any(), any());
        List<Map<String, Object>> events = new java.util.ArrayList<>();
        PullRequestAnalysisProcessor.EventConsumer disconnectedWarningSink = event -> {
            if ("warning".equals(event.get("type"))) {
                throw new IllegalStateException("event stream closed");
            }
            events.add(event);
        };

        Map<String, Object> result = processor.process(request, disconnectedWarningSink, project);

        @SuppressWarnings("unchecked")
        Map<String, Object> terminal = (Map<String, Object>) result.get("telemetry");
        @SuppressWarnings("unchecked")
        Map<String, Object> trace = (Map<String, Object>) terminal.get("trace");
        assertThat(terminal).containsEntry("finalizationState", "terminal");
        assertThat(trace)
                .containsEntry("outcome", "partial")
                .containsEntry("reason", "vcs_delivery_failed");
        assertThat(events).anyMatch(event -> "delivery".equals(event.get("stage"))
                && "failed".equals(event.get("outcome")));
        assertThat(events).anyMatch(event -> "emitted".equals(event.get("state"))
                && "partial".equals(event.get("outcome")));
        verify(reportingService).postAnalysisResults(any(), any(), anyLong(), any(), any());
    }

    @Test
    void finalizedTelemetrySurvivesAnUnavailableTerminalEventConsumer() throws Throwable {
        Map<String, Object> aiResponse = new HashMap<>();
        aiResponse.put("comment", "review");
        aiResponse.put("issues", List.of());
        aiResponse.put("telemetry", pendingTelemetryDocument());
        List<StageObservation> javaStages = List.of(
                new StageObservation("acquisition", "java_vcs_diff", "complete", 1, 1, null),
                new StageObservation("retrieval", "java_rag_index", "complete", 1, 0, null),
                new StageObservation("persistence", "java_analysis_store", "complete", 1, 0, null),
                new StageObservation("delivery", "java_vcs_reporting", "complete", 1, 0, null));
        PullRequestAnalysisProcessor.EventConsumer unavailable = event -> {
            throw new IllegalStateException("event stream closed");
        };

        @SuppressWarnings("unchecked")
        Map<String, Object> result = (Map<String, Object>) invoke(
                processor,
                "finalizePipelineTelemetry",
                new Class<?>[]{Map.class, PullRequestAnalysisProcessor.EventConsumer.class,
                        Instant.class, List.class},
                aiResponse,
                unavailable,
                Instant.now(),
                javaStages);

        @SuppressWarnings("unchecked")
        Map<String, Object> terminal = (Map<String, Object>) result.get("telemetry");
        assertThat(terminal).containsEntry("finalizationState", "terminal");
    }

    @Test
    void processPreservesResultWhenSnapshotsAndIssueTrackingFailAndIssuesAreNull() throws Exception {
        PrProcessRequest request = request();
        request.preAcquiredLockKey = "pre-acquired";
        CodeAnalysis previous = mock(CodeAnalysis.class);
        AiAnalysisRequest aiRequest = mock(AiAnalysisRequest.class);
        VcsAiClientService aiClientService = stubProcessThroughAcquisition(request, List.of(previous));
        when(ragOperationsService.ensureRagIndexUpToDate(any(), anyString(), any())).thenReturn(true);
        when(aiClientService.buildAiAnalysisRequests(any(), any(), any(), any()))
                .thenReturn(List.of(aiRequest));
        when(aiRequest.getRawDiff()).thenReturn("+line");
        when(aiRequest.getChangedFiles()).thenReturn(List.of("Changed.java"));
        when(codeAnalysisService.getAnalysisByDiffFingerprint(eq(7L), anyString()))
                .thenReturn(Optional.empty());
        Map<String, Object> aiResponse = Map.of("comment", "review");
        when(aiAnalysisClient.performAnalysis(eq(aiRequest), any())).thenReturn(aiResponse);
        when(codeAnalysisService.createAnalysisFromAiResponse(
                any(), any(), anyLong(), anyString(), anyString(), anyString(), any(), any(),
                anyString(), anyMap(), isNull(), isNull()))
                .thenReturn(analysis);
        when(analysis.getIssues()).thenReturn(null);
        doThrow(new IllegalStateException("snapshot unavailable")).when(fileSnapshotService)
                .persistSnapshotsForPr(any(), any(), anyMap(), anyString());
        when(previous.getId()).thenReturn(92L);
        when(fileSnapshotService.getFileContentsMap(92L))
                .thenThrow(new IllegalStateException("history unavailable"));
        when(pullRequest.getId()).thenReturn(100L);

        Map<String, Object> result = processor(null, ragOperationsService, eventPublisher)
                .process(request, event -> { }, project);

        assertThat(result).isEqualTo(aiResponse);
    }

    @Test
    void processEmitsPersistenceFailureAndAcceptsNullChangedFiles() throws Exception {
        PrProcessRequest request = request();
        request.preAcquiredLockKey = "pre-acquired";
        AiAnalysisRequest aiRequest = mock(AiAnalysisRequest.class);
        VcsAiClientService aiClientService = stubProcessThroughAcquisition(request, List.of());
        when(ragOperationsService.ensureRagIndexUpToDate(any(), anyString(), any())).thenReturn(true);
        when(aiClientService.buildAiAnalysisRequests(any(), any(), any(), any()))
                .thenReturn(List.of(aiRequest));
        when(aiRequest.getRawDiff()).thenReturn("+line");
        when(aiRequest.getChangedFiles()).thenReturn(null);
        when(codeAnalysisService.getAnalysisByDiffFingerprint(eq(7L), anyString()))
                .thenReturn(Optional.empty());
        when(aiAnalysisClient.performAnalysis(eq(aiRequest), any())).thenReturn(Map.of());
        when(codeAnalysisService.createAnalysisFromAiResponse(
                any(), any(), anyLong(), anyString(), anyString(), anyString(), any(), any(),
                anyString(), anyMap(), isNull(), isNull()))
                .thenThrow(new IllegalStateException("database unavailable"));
        List<Map<String, Object>> events = new java.util.ArrayList<>();

        assertThatThrownBy(() -> processor(null, ragOperationsService, eventPublisher)
                .process(request, events::add, project))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("database unavailable");
        assertThat(events).anyMatch(event -> "persistence".equals(event.get("stage"))
                && "failed".equals(event.get("outcome")));
    }

    @Test
    void policyKillSwitchCancelsAtEachSafeCheckpoint() throws Exception {
        assertPolicyCancellationAtCheckpoint(2);
        resetProcessMocks();
        assertPolicyCancellationAtCheckpoint(3);
        resetProcessMocks();
        assertPolicyCancellationAtCheckpoint(4);
    }

    private PullRequestAnalysisProcessor processor(
            ExecutionPolicyRuntime runtime,
            RagOperationsService rag,
            ApplicationEventPublisher publisher) {
        return new PullRequestAnalysisProcessor(
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
                rag,
                publisher,
                runtime);
    }

    private VcsAiClientService stubProcessThroughAcquisition(
            PrProcessRequest request,
            List<CodeAnalysis> previousAnalyses) throws GeneralSecurityException {
        VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
        VcsConnection connection = mock(VcsConnection.class);
        VcsAiClientService aiClientService = mock(VcsAiClientService.class);
        when(project.getId()).thenReturn(7L);
        when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
        when(repoInfo.getVcsConnection()).thenReturn(connection);
        when(connection.getProviderType()).thenReturn(EVcsProvider.GITHUB);
        when(vcsServiceFactory.getReportingService(EVcsProvider.GITHUB)).thenReturn(reportingService);
        when(vcsServiceFactory.getAiClientService(EVcsProvider.GITHUB)).thenReturn(aiClientService);
        when(pullRequestService.createOrUpdatePullRequest(
                7L, 42L, request.getCommitHash(), "feature", "main", project))
                .thenReturn(pullRequest);
        when(codeAnalysisService.getCodeAnalysisCache(7L, request.getCommitHash(), 42L))
                .thenReturn(Optional.empty());
        when(codeAnalysisService.getAnalysisByCommitHash(7L, request.getCommitHash()))
                .thenReturn(Optional.empty());
        when(codeAnalysisService.getAllPrAnalyses(7L, 42L)).thenReturn(previousAnalyses);
        return aiClientService;
    }

    private void assertPolicyCancellationAtCheckpoint(int checkpoint) throws Exception {
        PrProcessRequest request = request();
        request.preAcquiredLockKey = "policy-lock";
        Workspace workspace = mock(Workspace.class);
        when(project.getWorkspace()).thenReturn(workspace);
        when(workspace.getId()).thenReturn(9L);
        FrozenExecutionPlan candidatePlan = candidatePlan("checkpoint-" + checkpoint);
        when(executionPolicyRuntime.freeze(anyString(), any(StableRolloutKey.class)))
                .thenReturn(candidatePlan);
        ExecutionPolicyConfig keepRunning = config(false, false);
        ExecutionPolicyConfig cancel = config(false, true);
        if (checkpoint == 2) {
            when(executionPolicyRuntime.currentConfig()).thenReturn(keepRunning, cancel);
        } else if (checkpoint == 3) {
            when(executionPolicyRuntime.currentConfig()).thenReturn(keepRunning, keepRunning, cancel);
        } else {
            when(executionPolicyRuntime.currentConfig()).thenReturn(
                    keepRunning, keepRunning, keepRunning, cancel);
        }

        AiAnalysisRequest aiRequest = mock(AiAnalysisRequest.class);
        VcsAiClientService aiClientService = stubProcessThroughAcquisition(request, List.of());
        when(ragOperationsService.ensureRagIndexUpToDate(any(), anyString(), any())).thenReturn(true);
        when(aiClientService.buildAiAnalysisRequests(any(), any(), any(), any()))
                .thenReturn(List.of(aiRequest));
        when(aiRequest.getRawDiff()).thenReturn("+line");
        when(aiRequest.getChangedFiles()).thenReturn(List.of("Changed.java"));
        when(codeAnalysisService.getAnalysisByDiffFingerprint(eq(7L), anyString()))
                .thenReturn(Optional.empty());
        if (checkpoint >= 3) {
            when(aiAnalysisClient.performAnalysis(
                    eq(aiRequest), any(), eq(candidatePlan.primary())))
                    .thenReturn(Map.of("comment", "review"));
        }
        if (checkpoint == 4) {
            when(codeAnalysisService.createAnalysisFromAiResponse(
                    any(), any(), anyLong(), anyString(), anyString(), anyString(), any(), any(),
                    anyString(), anyMap(), isNull(), isNull()))
                    .thenReturn(analysis);
            when(analysis.getIssues()).thenReturn(List.of());
        }

        Map<String, Object> result = processor.process(request, event -> { }, project);

        assertThat(result)
                .containsEntry("status", "cancelled")
                .containsEntry("reason", "policy_kill_switch");
    }

    private void resetProcessMocks() {
        reset(project, pullRequest, pullRequestService, codeAnalysisService, aiAnalysisClient,
                vcsServiceFactory, analysisLockService, fileSnapshotService, prIssueTrackingService,
                astScopeEnricher, ragOperationsService, eventPublisher, executionPolicyRuntime,
                reportingService, analysis);
        processor = processor(executionPolicyRuntime, ragOperationsService, eventPublisher);
    }

    private static PrProcessRequest request() {
        PrProcessRequest request = new PrProcessRequest();
        request.projectId = 7L;
        request.pullRequestId = 42L;
        request.commitHash = "b".repeat(40);
        request.sourceBranchName = "feature";
        request.targetBranchName = "main";
        return request;
    }

    private static ExecutionPolicyConfig config(boolean stop, boolean candidateKill) {
        return new ExecutionPolicyConfig(
                "revision", ExecutionMode.ACTIVE, "candidate-review-v2", 10_000,
                "salt", stop, candidateKill);
    }

    private static ExecutionLifecycle lifecycle(String executionId) {
        return new ExecutionLifecycle(new PolicyExecution(
                executionId,
                "candidate-review-v2",
                ExecutionMode.ACTIVE,
                PolicySelectionReason.ACTIVE_ROLLOUT_SELECTED,
                0,
                true,
                NOW));
    }

    private static FrozenExecutionPlan plan(String executionId, boolean withShadow) {
        PolicyExecution primary = new PolicyExecution(
                executionId,
                "legacy-review-v1",
                ExecutionMode.LEGACY,
                PolicySelectionReason.LEGACY_CONFIGURED,
                0,
                true,
                NOW);
        PolicyExecution shadow = withShadow
                ? new PolicyExecution(
                        executionId + ":shadow",
                        "candidate-review-v2",
                        ExecutionMode.SHADOW,
                        PolicySelectionReason.SHADOW_CANDIDATE,
                        0,
                        false,
                        NOW)
                : null;
        return new FrozenExecutionPlan(
                executionId, "revision", "a".repeat(64), primary, shadow, NOW);
    }

    private static FrozenExecutionPlan candidatePlan(String executionId) {
        PolicyExecution primary = new PolicyExecution(
                executionId,
                "candidate-review-v2",
                ExecutionMode.ACTIVE,
                PolicySelectionReason.ACTIVE_ROLLOUT_SELECTED,
                0,
                true,
                NOW);
        return new FrozenExecutionPlan(
                executionId, "revision", "a".repeat(64), primary, null, NOW);
    }

    private static int eventIndex(
            List<Map<String, Object>> events,
            String field,
            String value) {
        for (int index = 0; index < events.size(); index++) {
            if (value.equals(events.get(index).get(field))) {
                return index;
            }
        }
        return -1;
    }

    private static int stageEventIndex(
            List<Map<String, Object>> events,
            String stage,
            String outcome) {
        for (int index = 0; index < events.size(); index++) {
            Map<String, Object> event = events.get(index);
            if (stage.equals(event.get("stage")) && outcome.equals(event.get("outcome"))) {
                return index;
            }
        }
        return -1;
    }

    private static Map<String, Object> pendingTelemetryDocument() {
        Map<String, Object> trace = new HashMap<>();
        trace.put("execution_id", "execution-p004");
        trace.put("base_revision", "a".repeat(40));
        trace.put("head_revision", "b".repeat(40));
        trace.put("versions", Map.of(
                "provider", "scripted",
                "model", "fixture-v1",
                "prompt_version", "prompt-sha256-" + "1".repeat(64),
                "rules_version", "rules-sha256-" + "2".repeat(64),
                "policy_version", "legacy-review-v1",
                "index_version", "rag-commit-" + "c".repeat(40)));
        trace.put("outcome", "complete");
        trace.put("duration_ms", 10);
        trace.put("usage", telemetryUsage());
        trace.put("candidates", Map.of("input", 0, "produced", 0, "retained", 0));
        trace.put("coverage", Map.of("inventory", 1, "represented", 1, "unrepresented", 0));
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

        Map<String, Object> document = new HashMap<>();
        document.put("schemaVersion", 1);
        document.put("finalizationState", "pending_java");
        document.put("trace", trace);
        document.put("metric", null);
        document.put("sinkErrors", List.of());
        return document;
    }

    private static Map<String, Object> telemetryStage(String name) {
        Map<String, Object> stage = new HashMap<>();
        stage.put("name", name);
        stage.put("producer", "python");
        stage.put("outcome", "complete");
        stage.put("duration_ms", 1);
        stage.put("usage", telemetryUsage());
        stage.put("candidates", Map.of("input", 0, "produced", 0, "retained", 0));
        stage.put("coverage", Map.of("inventory", 1, "represented", 1, "unrepresented", 0));
        stage.put("reason", null);
        return stage;
    }

    private static Map<String, Object> telemetryUsage() {
        Map<String, Object> usage = new HashMap<>();
        usage.put("requested_input_tokens", 10);
        usage.put("requested_output_tokens", 5);
        usage.put("provider_input_tokens", 9);
        usage.put("provider_output_tokens", 4);
        usage.put("provider_cache_read_tokens", 0);
        usage.put("calls", 1);
        usage.put("retries", 0);
        usage.put("estimated_cost_microunits", 13);
        usage.put("provider_usage_missing_calls", 0);
        usage.put("cost_estimate_missing_calls", 0);
        return usage;
    }

    private Class<?>[] publicationTypes() {
        return new Class<?>[]{
                FrozenExecutionPlan.class,
                PullRequestAnalysisProcessor.EventConsumer.class,
                Instant.class,
                int.class,
                VcsReportingService.class,
                CodeAnalysis.class,
                Project.class,
                Long.class,
                Long.class,
                String.class,
                String.class};
    }

    private Object[] publicationArgs(
            FrozenExecutionPlan policyPlan,
            PullRequestAnalysisProcessor.EventConsumer consumer) {
        return new Object[]{
                policyPlan,
                consumer,
                NOW,
                2,
                reportingService,
                analysis,
                project,
                42L,
                100L,
                "placeholder",
                "b".repeat(40)};
    }

    private static Class<?>[] completedEventTypes() {
        return new Class<?>[]{
                Project.class,
                PrProcessRequest.class,
                String.class,
                Instant.class,
                org.rostilos.codecrow.events.analysis.AnalysisCompletedEvent.CompletionStatus.class,
                int.class,
                int.class,
                String.class};
    }

    private static Object invoke(
            Object target,
            String methodName,
            Class<?>[] types,
            Object... args) throws Throwable {
        Method method = PullRequestAnalysisProcessor.class.getDeclaredMethod(methodName, types);
        method.setAccessible(true);
        try {
            return method.invoke(target, args);
        } catch (InvocationTargetException error) {
            throw error;
        }
    }

    private static void assertInvocationCause(
            Class<? extends Throwable> type,
            ThrowingInvocation invocation) {
        assertThatThrownBy(invocation::run)
                .isInstanceOf(InvocationTargetException.class)
                .hasCauseInstanceOf(type);
    }

    @FunctionalInterface
    private interface ThrowingInvocation {
        void run() throws Throwable;
    }
}
