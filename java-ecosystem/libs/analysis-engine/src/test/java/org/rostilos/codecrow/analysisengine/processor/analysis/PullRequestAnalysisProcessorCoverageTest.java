package org.rostilos.codecrow.analysisengine.processor.analysis;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.aiclient.AiAnalysisClient;
import org.rostilos.codecrow.analysisengine.coverage.CoverageWorkPlan;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequestImpl;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.FileContentDto;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.PrEnrichmentDataDto;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.execution.ExecutionManifestService;
import org.rostilos.codecrow.analysisengine.execution.ImmutableExecutionManifest;
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
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisMode;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.ProjectAiConnectionBinding;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
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

import java.io.IOException;
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
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyMap;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.reset;
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
        lenient().when(executionPolicyRuntime.currentConfig())
                .thenReturn(config(false, false));
    }

    @Test
    void semanticExecutionIdentityChangesWithPolicyModelAndBoundedInputConfig() {
        PrProcessRequest request = request();
        AIConnection aiConnection = new AIConnection();
        aiConnection.setProviderKey(AIProviderKey.OPENAI);
        aiConnection.setAiModel("review-model-v1");
        aiConnection.setBaseUrl("https://models.example/v1");
        aiConnection.setCustomParameters("{\"temperature\":0}");
        ProjectAiConnectionBinding aiBinding = new ProjectAiConnectionBinding();
        aiBinding.setAiConnection(aiConnection);
        aiBinding.setPolicyJson("{\"reasoning\":\"bounded\"}");
        ProjectConfig projectConfig = new ProjectConfig();
        projectConfig.setMaxAnalysisTokenLimit(12_000);

        when(project.getId()).thenReturn(7L);
        when(project.getAiBinding()).thenReturn(aiBinding);
        when(project.getEffectiveConfig()).thenReturn(projectConfig);

        ExecutionPolicyConfig baselinePolicy = new ExecutionPolicyConfig(
                "policy-revision-a",
                ExecutionMode.ACTIVE,
                "candidate-review-v2",
                10_000,
                "salt",
                false,
                false);
        PrEnrichmentDataDto baselineEnrichment = identityEnrichment(
                "Changed.java", "class Changed { int value = 1; }");
        AiAnalysisRequest baselineAcquisition = identityRequest(
                "workspace",
                "repository",
                "github",
                "a".repeat(40),
                request.getCommitHash(),
                "c".repeat(40),
                "+line\n",
                baselineEnrichment);
        String baseline = PullRequestAnalysisProcessor.candidateExecutionIdentity(
                project,
                request,
                baselinePolicy,
                baselineAcquisition,
                "rag-disabled");

        assertThat(baseline).matches("pr:[0-9a-f]{64}");
        assertThat(PullRequestAnalysisProcessor.candidateExecutionIdentity(
                project, request, baselinePolicy, baselineAcquisition, "rag-disabled"))
                .isEqualTo(baseline);

        aiConnection.setAiModel("review-model-v2");
        assertThat(PullRequestAnalysisProcessor.candidateExecutionIdentity(
                project, request, baselinePolicy, baselineAcquisition, "rag-disabled"))
                .isNotEqualTo(baseline);
        aiConnection.setAiModel("review-model-v1");

        aiConnection.setProviderKey(AIProviderKey.ANTHROPIC);
        assertThat(PullRequestAnalysisProcessor.candidateExecutionIdentity(
                project, request, baselinePolicy, baselineAcquisition, "rag-disabled"))
                .isNotEqualTo(baseline);
        aiConnection.setProviderKey(AIProviderKey.OPENAI);

        aiConnection.setBaseUrl("https://models.example/v2\nwith-boundary");
        assertThat(PullRequestAnalysisProcessor.candidateExecutionIdentity(
                project, request, baselinePolicy, baselineAcquisition, "rag-disabled"))
                .isNotEqualTo(baseline);
        aiConnection.setBaseUrl("https://models.example/v1");

        aiConnection.setCustomParameters("{\n\"temperature\":1\n}");
        assertThat(PullRequestAnalysisProcessor.candidateExecutionIdentity(
                project, request, baselinePolicy, baselineAcquisition, "rag-disabled"))
                .isNotEqualTo(baseline);
        aiConnection.setCustomParameters("{\"temperature\":0}");

        projectConfig.setMaxAnalysisTokenLimit(24_000);
        assertThat(PullRequestAnalysisProcessor.candidateExecutionIdentity(
                project, request, baselinePolicy, baselineAcquisition, "rag-disabled"))
                .isNotEqualTo(baseline);
        projectConfig.setMaxAnalysisTokenLimit(12_000);

        ExecutionPolicyConfig changedPolicy = new ExecutionPolicyConfig(
                "policy-revision-b",
                ExecutionMode.ACTIVE,
                "candidate-review-v3",
                10_000,
                "salt",
                false,
                false);
        assertThat(PullRequestAnalysisProcessor.candidateExecutionIdentity(
                project, request, changedPolicy, baselineAcquisition, "rag-disabled"))
                .isNotEqualTo(baseline);

        assertThat(PullRequestAnalysisProcessor.candidateExecutionIdentity(
                project,
                request,
                baselinePolicy,
                identityRequest(
                        "workspace", "repository", "github",
                        "d".repeat(40), request.getCommitHash(), "c".repeat(40),
                        "+line\n", baselineEnrichment),
                "rag-disabled")).isNotEqualTo(baseline);
        assertThat(PullRequestAnalysisProcessor.candidateExecutionIdentity(
                project,
                request,
                baselinePolicy,
                identityRequest(
                        "workspace", "repository", "github",
                        "a".repeat(40), request.getCommitHash(), "e".repeat(40),
                        "+line\n", baselineEnrichment),
                "rag-disabled")).isNotEqualTo(baseline);
        assertThat(PullRequestAnalysisProcessor.candidateExecutionIdentity(
                project,
                request,
                baselinePolicy,
                identityRequest(
                        "workspace", "repository", "github",
                        "a".repeat(40), request.getCommitHash(), "c".repeat(40),
                        "+different-line\n", baselineEnrichment),
                "rag-disabled")).isNotEqualTo(baseline);
        assertThat(PullRequestAnalysisProcessor.candidateExecutionIdentity(
                project,
                request,
                baselinePolicy,
                identityRequest(
                        "workspace", "other-repository", "github",
                        "a".repeat(40), request.getCommitHash(), "c".repeat(40),
                        "+line\n", baselineEnrichment),
                "rag-disabled")).isNotEqualTo(baseline);
        assertThat(PullRequestAnalysisProcessor.candidateExecutionIdentity(
                project,
                request,
                baselinePolicy,
                identityRequest(
                        "workspace", "repository", "github",
                        "a".repeat(40), request.getCommitHash(), "c".repeat(40),
                        "+line\n",
                        identityEnrichment(
                                "Changed.java", "class Changed { int value = 2; }")),
                "rag-disabled")).isNotEqualTo(baseline);
        assertThat(PullRequestAnalysisProcessor.candidateExecutionIdentity(
                project,
                request,
                baselinePolicy,
                identityRequest(
                        "workspace", "repository", "github",
                        "a".repeat(40), request.getCommitHash(), "c".repeat(40),
                        "+line\n", identityGap("Changed.java", "binary_file")),
                "rag-disabled")).isNotEqualTo(baseline);
        assertThat(PullRequestAnalysisProcessor.candidateExecutionIdentity(
                project,
                request,
                baselinePolicy,
                baselineAcquisition,
                "rag-commit-" + "f".repeat(40))).isNotEqualTo(baseline);
    }

    @Test
    void freezesStablePolicyIdentityAndRejectsUnpersistedIdentities() throws Throwable {
        PrProcessRequest request = request();
        Workspace workspace = mock(Workspace.class);
        when(project.getId()).thenReturn(7L);
        when(project.getWorkspace()).thenReturn(workspace);
        when(workspace.getId()).thenReturn(9L);
        FrozenExecutionPlan expected = plan("pr:" + "a".repeat(64));
        when(executionPolicyRuntime.freeze(anyString(), any(StableRolloutKey.class), any(ExecutionPolicyConfig.class)))
                .thenReturn(expected);

        assertThat(invoke(processor, "freezePolicyPlan",
                new Class<?>[]{Project.class, PrProcessRequest.class}, project, request))
                .isEqualTo(expected);
        verify(executionPolicyRuntime).freeze(
                anyString(),
                any(StableRolloutKey.class),
                any(ExecutionPolicyConfig.class));

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
    void legacyExecutionCanCancelBeforeAnyVcsAcquisition() throws Exception {
        PrProcessRequest request = request();
        request.preAcquiredLockKey = "pre-acquired";
        Workspace workspace = mock(Workspace.class);
        when(project.getId()).thenReturn(7L);
        when(project.getWorkspace()).thenReturn(workspace);
        when(workspace.getId()).thenReturn(9L);
        when(executionPolicyRuntime.freeze(anyString(), any(StableRolloutKey.class), any(ExecutionPolicyConfig.class)))
                .thenReturn(plan("legacy-cancel-before-acquisition"));
        when(executionPolicyRuntime.currentConfig()).thenReturn(
                new ExecutionPolicyConfig(
                        "revision",
                        ExecutionMode.LEGACY,
                        "candidate-review-v2",
                        10_000,
                        "salt",
                        true,
                        false));

        Map<String, Object> result = processor.process(request, event -> { }, project);

        assertThat(result)
                .containsEntry("status", "cancelled")
                .containsEntry("reason", "policy_kill_switch");
        verify(vcsServiceFactory, never()).getAiClientService(any());
    }

    @Test
    void policySelectionTelemetryHandlesPrimaryAndBrokenConsumers() throws Throwable {
        PullRequestAnalysisProcessor.EventConsumer consumer = mock(
                PullRequestAnalysisProcessor.EventConsumer.class);
        Class<?>[] selectionTypes = {
                PullRequestAnalysisProcessor.EventConsumer.class,
                FrozenExecutionPlan.class,
                executionEventBindingType()};
        invoke(processor, "emitPolicySelection",
                selectionTypes, consumer, null, null);
        invoke(processor, "emitPolicySelection",
                selectionTypes, consumer, plan("primary-only"), null);
        verify(consumer).accept(anyMap());

        doThrow(new IllegalStateException("closed"))
                .when(consumer).accept(anyMap());
        invoke(processor, "emitPolicySelection",
                selectionTypes, consumer, plan("broken-consumer"), null);

        Object binding = eventBinding(candidateManifest("bound-policy-selection"));
        assertInvocationCause(IllegalStateException.class, () -> invoke(
                processor,
                "emitPolicySelection",
                selectionTypes,
                consumer,
                candidatePlan("bound-policy-selection"),
                binding));
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
    void publicationFenceBlocksStaleAndDuplicateDeliveryAndAllowsReservation() throws Throwable {
        PublicationFence fence = mock(PublicationFence.class);
        when(executionPolicyRuntime.publicationFence()).thenReturn(fence);
        VcsRepoInfo repoInfo = mock(VcsRepoInfo.class);
        VcsConnection connection = mock(VcsConnection.class);
        when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
        when(repoInfo.getVcsConnection()).thenReturn(connection);
        when(connection.getProviderType()).thenReturn(EVcsProvider.GITHUB);
        when(project.getId()).thenReturn(7L);
        FrozenExecutionPlan plan = plan("publication");
        PullRequestAnalysisProcessor.EventConsumer consumer = mock(
                PullRequestAnalysisProcessor.EventConsumer.class);
        Class<?>[] types = publicationTypes();
        Object[] args = publicationArgs(plan, consumer);

        when(fence.reserve(any(), any())).thenReturn(PublicationReservation.DUPLICATE);
        assertThat(invoke(processor, "publishAnalysisResults", types, args)).isEqualTo(false);
        when(fence.reserve(any(), any())).thenReturn(PublicationReservation.STALE_HEAD);
        assertThat(invoke(processor, "publishAnalysisResults", types, args)).isEqualTo(false);
        verify(reportingService, never()).postAnalysisResults(any(), any(), anyLong(), any(), any());
        verify(consumer).accept(org.mockito.ArgumentMatchers.argThat(event ->
                "stale_publication_blocked".equals(event.get("reasonCode"))));

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
        Object stageBinding = eventBinding(candidateManifest("bound-stage-telemetry"));
        Class<?>[] boundTelemetryTypes = {
                PullRequestAnalysisProcessor.EventConsumer.class, String.class, String.class,
                String.class, Instant.class, int.class, String.class,
                executionEventBindingType()};
        invoke(
                processor,
                "emitStageTelemetry",
                boundTelemetryTypes,
                telemetry,
                "stage",
                "producer",
                "failed",
                NOW,
                0,
                null,
                stageBinding);

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
        FrozenExecutionPlan plan = plan("index-dispatch");
        Map<String, Object> exact = Map.of("path", "exact");
        Map<String, Object> unavailable = Map.of("path", "unavailable");
        when(aiAnalysisClient.performAnalysis(
                eq(aiRequest), any(), eq(plan.primary()), eq("rag-commit-" + "d".repeat(40))))
                .thenReturn(exact);
        when(aiAnalysisClient.performAnalysis(eq(aiRequest), any(), eq(plan.primary())))
                .thenReturn(unavailable);
        Class<?>[] dispatchTypes = {
                AiAnalysisRequest.class,
                Consumer.class,
                FrozenExecutionPlan.class,
                String.class,
                ImmutableExecutionManifest.class,
                CoverageWorkPlan.class};
        assertThat(invoke(processor, "performAiAnalysis", dispatchTypes,
                aiRequest, (Consumer<Map<String, Object>>) event -> { }, plan,
                "rag-commit-" + "d".repeat(40), null, null)).isEqualTo(exact);
        assertThat(invoke(processor, "performAiAnalysis", dispatchTypes,
                aiRequest, (Consumer<Map<String, Object>>) event -> { }, plan,
                "rag-service-unavailable", null, null)).isEqualTo(unavailable);
        assertThat(invoke(processor, "performAiAnalysis", dispatchTypes,
                aiRequest, (Consumer<Map<String, Object>>) event -> { }, plan,
                "rag-version-unavailable", null, null)).isEqualTo(unavailable);
        assertThat(invoke(processor, "performAiAnalysis", dispatchTypes,
                aiRequest, (Consumer<Map<String, Object>>) event -> { }, plan,
                "stale-index-v1", null, null)).isEqualTo(unavailable);
        assertThat(invoke(processor, "performAiAnalysis", dispatchTypes,
                aiRequest, (Consumer<Map<String, Object>>) event -> { }, plan,
                null, null, null)).isEqualTo(unavailable);

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
    void manifestPersistenceAndCandidateOutputGuardsRejectEveryConflictingCoordinate()
            throws Throwable {
        ExecutionManifestService manifestService = mock(ExecutionManifestService.class);
        PullRequestAnalysisProcessor manifestProcessor = processor(
                executionPolicyRuntime, ragOperationsService, eventPublisher, manifestService);
        PrProcessRequest processRequest = request();
        FrozenExecutionPlan candidatePlan = candidatePlan("candidate-manifest-guards");
        AiAnalysisRequest validRequest = candidateAiRequest(null);
        Class<?>[] persistTypes = {
                AiAnalysisRequest.class, PrProcessRequest.class, FrozenExecutionPlan.class};

        assertInvocationCause(IllegalArgumentException.class, () -> invoke(
                manifestProcessor,
                "persistCandidateManifest",
                persistTypes,
                validRequest,
                processRequest,
                null));
        assertInvocationCause(IllegalArgumentException.class, () -> invoke(
                manifestProcessor,
                "persistCandidateManifest",
                persistTypes,
                validRequest,
                processRequest,
                plan("legacy-manifest-guards")));

        AiAnalysisRequest missingProject = mock(AiAnalysisRequest.class);
        when(missingProject.getPullRequestId()).thenReturn(42L);
        assertInvocationCause(IllegalArgumentException.class, () -> invoke(
                manifestProcessor,
                "persistCandidateManifest",
                persistTypes,
                missingProject,
                processRequest,
                candidatePlan));

        AiAnalysisRequest missingPullRequest = mock(AiAnalysisRequest.class);
        when(missingPullRequest.getProjectId()).thenReturn(7L);
        assertInvocationCause(IllegalArgumentException.class, () -> invoke(
                manifestProcessor,
                "persistCandidateManifest",
                persistTypes,
                missingPullRequest,
                processRequest,
                candidatePlan));

        when(manifestService.persistBeforeWork(
                any(ImmutableExecutionManifest.class), anyList()))
                .thenAnswer(invocation -> invocation.getArgument(0));
        AiAnalysisRequest emptyReconciliation = candidateAiRequest(Map.of());
        assertThat(invoke(
                manifestProcessor,
                "persistCandidateManifest",
                persistTypes,
                emptyReconciliation,
                processRequest,
                candidatePlan)).isInstanceOf(ImmutableExecutionManifest.class);

        AiAnalysisRequest conflictingReconciliation = candidateAiRequest(
                Map.of("Changed.java", "mutable compatibility input"));
        assertInvocationCause(IllegalArgumentException.class, () -> invoke(
                manifestProcessor,
                "persistCandidateManifest",
                persistTypes,
                conflictingReconciliation,
                processRequest,
                candidatePlan));

        Class<?>[] reloadTypes = {ImmutableExecutionManifest.class};
        assertInvocationCause(IllegalStateException.class, () -> invoke(
                manifestProcessor,
                "requireReloadedCandidateManifest",
                reloadTypes,
                new Object[]{null}));
        ImmutableExecutionManifest persisted = candidateManifest("persisted-manifest");
        when(manifestService.requireVerified(persisted.executionId()))
                .thenReturn(candidateManifest("conflicting-reload"));
        assertInvocationCause(IllegalStateException.class, () -> invoke(
                manifestProcessor,
                "requireReloadedCandidateManifest",
                reloadTypes,
                persisted));

        Class<?>[] requiredPartTypes = {String.class, String.class};
        assertInvocationCause(IllegalArgumentException.class, () -> invoke(
                null,
                "requiredManifestPart",
                requiredPartTypes,
                null,
                "provider"));
        assertInvocationCause(IllegalArgumentException.class, () -> invoke(
                null,
                "requiredManifestPart",
                requiredPartTypes,
                " ",
                "provider"));
        assertThat(invoke(null, "requiredManifestPart", requiredPartTypes,
                "github", "provider")).isEqualTo("github");

        Class<?>[] equalTypes = {Object.class, Object.class, String.class};
        assertInvocationCause(IllegalArgumentException.class, () -> invoke(
                null,
                "requireManifestEqual",
                equalTypes,
                "observed",
                "expected",
                "headSha"));
        invoke(null, "requireManifestEqual", equalTypes,
                "same", "same", "headSha");

        ImmutableExecutionManifest manifest = candidateManifest("candidate-output-guards");
        Class<?>[] outputTypes = {CodeAnalysis.class, ImmutableExecutionManifest.class};
        assertOutputBindingRejected(outputTypes, null, manifest);
        assertOutputBindingRejected(outputTypes, candidateAnalysis(
                manifest, null, 42L, manifest.headSha(),
                manifest.executionId(), manifest.artifactManifestDigest()), manifest);
        assertOutputBindingRejected(outputTypes, candidateAnalysis(
                manifest, 7L, 42L, manifest.headSha(), null, null), manifest);
        assertOutputBindingRejected(outputTypes, candidateAnalysis(
                manifest, 7L, 42L, manifest.headSha(),
                "conflicting-execution", manifest.artifactManifestDigest()), manifest);
        assertOutputBindingRejected(outputTypes, candidateAnalysis(
                manifest, 7L, 42L, manifest.headSha(),
                manifest.executionId(), "f".repeat(64)), manifest);
        assertOutputBindingRejected(outputTypes, candidateAnalysis(
                manifest, 8L, 42L, manifest.headSha(),
                manifest.executionId(), manifest.artifactManifestDigest()), manifest);
        assertOutputBindingRejected(outputTypes, candidateAnalysis(
                manifest, 7L, 43L, manifest.headSha(),
                manifest.executionId(), manifest.artifactManifestDigest()), manifest);
        assertOutputBindingRejected(outputTypes, candidateAnalysis(
                manifest, 7L, 42L, "d".repeat(40),
                manifest.executionId(), manifest.artifactManifestDigest()), manifest);
        invoke(null, "requireCandidateOutputBinding", outputTypes,
                candidateAnalysis(
                        manifest, 7L, 42L, manifest.headSha(),
                        manifest.executionId(), manifest.artifactManifestDigest()),
                manifest);
    }

    @Test
    void candidateDispatchFailsClosedWhileTerminalTelemetryRemainsBestEffort()
            throws Throwable {
        ImmutableExecutionManifest manifest = candidateManifest("candidate-terminal-guards");
        FrozenExecutionPlan candidatePlan = candidatePlan(manifest.executionId());
        AiAnalysisRequest aiRequest = mock(AiAnalysisRequest.class);
        Consumer<Map<String, Object>> aiEvents = event -> { };
        Class<?>[] dispatchTypes = {
                AiAnalysisRequest.class,
                Consumer.class,
                FrozenExecutionPlan.class,
                String.class,
                ImmutableExecutionManifest.class,
                CoverageWorkPlan.class};

        assertInvocationCause(IllegalStateException.class, () -> invoke(
                processor,
                "performAiAnalysis",
                dispatchTypes,
                aiRequest,
                aiEvents,
                plan("legacy-manifest-dispatch"),
                "rag-disabled",
                manifest,
                null));
        assertInvocationCause(IllegalStateException.class, () -> invoke(
                processor,
                "performAiAnalysis",
                dispatchTypes,
                aiRequest,
                aiEvents,
                candidatePlan,
                "rag-commit-" + "d".repeat(40),
                manifest,
                null));

        String exactIndex = "rag-commit-" + "e".repeat(40);
        Map<String, Object> noPolicyResult = Map.of("path", "exact-without-policy");
        when(aiAnalysisClient.performAnalysis(
                eq(aiRequest), any(), isNull(), eq(exactIndex)))
                .thenReturn(noPolicyResult);
        assertThat(invoke(
                processor,
                "performAiAnalysis",
                dispatchTypes,
                aiRequest,
                aiEvents,
                null,
                exactIndex,
                null,
                null)).isEqualTo(noPolicyResult);

        Class<?>[] finalizerTypes = {
                Map.class,
                PullRequestAnalysisProcessor.EventConsumer.class,
                Instant.class,
                List.class,
                ImmutableExecutionManifest.class,
                String.class};
        PullRequestAnalysisProcessor.EventConsumer available = event -> { };
        assertThat(invoke(
                processor,
                "finalizePipelineTelemetry",
                finalizerTypes,
                null,
                available,
                NOW,
                List.of(),
                manifest,
                "rag-disabled")).isNull();

        Map<String, Object> missingTelemetry = Map.of(
                "comment", "review remains valid without telemetry");
        @SuppressWarnings("unchecked")
        Map<String, Object> missingTelemetryResult = (Map<String, Object>) invoke(
                processor,
                "finalizePipelineTelemetry",
                finalizerTypes,
                missingTelemetry,
                available,
                NOW,
                List.of(),
                manifest,
                "rag-disabled");
        assertThat(missingTelemetryResult)
                .containsEntry("comment", "review remains valid without telemetry")
                .containsEntry("executionId", manifest.executionId())
                .containsEntry(
                        "artifactManifestDigest", manifest.artifactManifestDigest());

        Map<String, Object> invalidTelemetry = Map.of(
                "comment", "review remains valid with rejected telemetry",
                "telemetry", Map.of("finalizationState", "invalid"));
        @SuppressWarnings("unchecked")
        Map<String, Object> invalidTelemetryResult = (Map<String, Object>) invoke(
                processor,
                "finalizePipelineTelemetry",
                finalizerTypes,
                invalidTelemetry,
                available,
                NOW,
                List.of(),
                manifest,
                "rag-disabled");
        assertThat(invalidTelemetryResult)
                .containsEntry("comment", "review remains valid with rejected telemetry")
                .containsEntry("executionId", manifest.executionId())
                .containsEntry(
                        "artifactManifestDigest", manifest.artifactManifestDigest());

        Map<String, Object> response = new HashMap<>();
        response.put("comment", "review");
        response.put("issues", List.of());
        response.put("telemetry", pendingTelemetryDocument(manifest, "rag-disabled"));
        List<StageObservation> completeJavaStages = List.of(
                new StageObservation("acquisition", "java_vcs_diff", "complete", 1, 1, null),
                new StageObservation("retrieval", "java_rag_index", "complete", 1, 0, null),
                new StageObservation("persistence", "java_analysis_store", "complete", 1, 0, null),
                new StageObservation("delivery", "java_vcs_reporting", "complete", 1, 0, null));
        PullRequestAnalysisProcessor.EventConsumer unavailable = event -> {
            throw new IllegalStateException("candidate event stream unavailable");
        };
        @SuppressWarnings("unchecked")
        Map<String, Object> finalized = (Map<String, Object>) invoke(
                processor,
                "finalizePipelineTelemetry",
                finalizerTypes,
                response,
                unavailable,
                NOW,
                completeJavaStages,
                manifest,
                "rag-disabled");
        assertThat(finalized)
                .containsEntry("comment", "review")
                .containsEntry("executionId", manifest.executionId())
                .containsEntry(
                        "artifactManifestDigest", manifest.artifactManifestDigest());
        assertThat((Map<String, Object>) finalized.get("telemetry"))
                .containsEntry("finalizationState", "terminal");
    }

    @Test
    void boundLifecycleEventsPropagateRuntimeFailuresButContainCheckedPublisherFailures()
            throws Throwable {
        ImmutableExecutionManifest manifest = candidateManifest("candidate-publisher-guards");
        Object binding = eventBinding(manifest);
        PrProcessRequest request = request();
        request.prTitle = "Title";
        request.prDescription = "Description";
        Workspace workspace = mock(Workspace.class);
        when(project.getId()).thenReturn(7L);
        when(project.getName()).thenReturn("Project");
        when(project.getWorkspace()).thenReturn(workspace);
        when(workspace.getName()).thenReturn("Workspace");
        when(project.getNamespace()).thenReturn("namespace");

        Class<?>[] startedTypes = {
                Project.class,
                PrProcessRequest.class,
                String.class,
                executionEventBindingType()};
        doThrow(new IllegalStateException("runtime publisher failure"))
                .when(eventPublisher).publishEvent(any(
                        org.rostilos.codecrow.events.analysis.AnalysisStartedEvent.class));
        assertInvocationCause(IllegalStateException.class, () -> invoke(
                processor,
                "publishAnalysisStartedEvent",
                startedTypes,
                project,
                request,
                "correlation",
                binding));

        reset(eventPublisher);
        doAnswer(invocation -> {
            throw new Exception("checked publisher failure");
        }).when(eventPublisher).publishEvent(any(
                org.rostilos.codecrow.events.analysis.AnalysisStartedEvent.class));
        invoke(processor, "publishAnalysisStartedEvent", startedTypes,
                project, request, "correlation", binding);

        Class<?>[] completedTypes = {
                Project.class,
                PrProcessRequest.class,
                String.class,
                Instant.class,
                org.rostilos.codecrow.events.analysis.AnalysisCompletedEvent.CompletionStatus.class,
                int.class,
                int.class,
                String.class,
                executionEventBindingType()};
        reset(eventPublisher);
        doThrow(new IllegalStateException("runtime publisher failure"))
                .when(eventPublisher).publishEvent(any(
                        org.rostilos.codecrow.events.analysis.AnalysisCompletedEvent.class));
        assertInvocationCause(IllegalStateException.class, () -> invoke(
                processor,
                "publishAnalysisCompletedEvent",
                completedTypes,
                project,
                request,
                "correlation",
                NOW,
                org.rostilos.codecrow.events.analysis.AnalysisCompletedEvent.CompletionStatus.SUCCESS,
                2,
                3,
                null,
                binding));

        reset(eventPublisher);
        doAnswer(invocation -> {
            throw new Exception("checked publisher failure");
        }).when(eventPublisher).publishEvent(any(
                org.rostilos.codecrow.events.analysis.AnalysisCompletedEvent.class));
        invoke(processor, "publishAnalysisCompletedEvent", completedTypes,
                project,
                request,
                "correlation",
                NOW,
                org.rostilos.codecrow.events.analysis.AnalysisCompletedEvent.CompletionStatus.SUCCESS,
                2,
                3,
                null,
                binding);
    }

    @Test
    void executionEventBindingValidatesConstructionOwnershipAndManifestCompatibility()
            throws Throwable {
        ImmutableExecutionManifest manifest = candidateManifest("candidate-event-binding");
        Class<?> bindingType = executionEventBindingType();
        assertInvocationCause(IllegalArgumentException.class, () -> newEventBinding(
                null, manifest.artifactManifestDigest()));
        assertInvocationCause(IllegalArgumentException.class, () -> newEventBinding(
                " ", manifest.artifactManifestDigest()));
        assertInvocationCause(IllegalArgumentException.class, () -> newEventBinding(
                manifest.executionId(), null));
        assertInvocationCause(IllegalArgumentException.class, () -> newEventBinding(
                manifest.executionId(), "not-a-digest"));

        Class<?>[] requireTypes = {FrozenExecutionPlan.class, ImmutableExecutionManifest.class};
        assertInvocationCause(IllegalStateException.class, () -> invokeDeclared(
                bindingType,
                null,
                "require",
                requireTypes,
                null,
                manifest));
        assertInvocationCause(IllegalStateException.class, () -> invokeDeclared(
                bindingType,
                null,
                "require",
                requireTypes,
                candidatePlan(manifest.executionId()),
                null));
        assertInvocationCause(IllegalStateException.class, () -> invokeDeclared(
                bindingType,
                null,
                "require",
                requireTypes,
                candidatePlan("conflicting-event-execution"),
                manifest));

        Object binding = invokeDeclared(
                bindingType,
                null,
                "require",
                requireTypes,
                candidatePlan(manifest.executionId()),
                manifest);
        Class<?>[] mapTypes = {Map.class};
        @SuppressWarnings("unchecked")
        Map<String, Object> bound = (Map<String, Object>) invokeDeclared(
                bindingType,
                binding,
                "bindOwned",
                mapTypes,
                Map.of(
                        "executionId", manifest.executionId(),
                        "artifactManifestDigest", manifest.artifactManifestDigest()));
        assertThat(bound)
                .containsEntry("executionId", manifest.executionId())
                .containsEntry("artifactManifestDigest", manifest.artifactManifestDigest());
        assertInvocationCause(IllegalStateException.class, () -> invokeDeclared(
                bindingType,
                binding,
                "bindOwned",
                mapTypes,
                Map.of("executionId", "conflicting-event-execution")));
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
        doThrow(new IllegalStateException("AST parser unavailable"))
                .when(astScopeEnricher).enrichWithAstScopes(
                        List.of(issue), Map.of("Changed.java", "class Changed {}"));
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
        FrozenExecutionPlan policyPlan = plan("p004-terminal");
        when(executionPolicyRuntime.freeze(anyString(), any(StableRolloutKey.class), any(ExecutionPolicyConfig.class)))
                .thenReturn(policyPlan);
        when(executionPolicyRuntime.currentConfig()).thenReturn(legacyConfig(false));
        PublicationFence fence = mock(PublicationFence.class);
        when(executionPolicyRuntime.publicationFence()).thenReturn(fence);
        when(fence.reserve(any(), any())).thenReturn(PublicationReservation.DUPLICATE);
        String indexVersion = "rag-commit-" + "c".repeat(40);
        when(ragOperationsService.ensureRagIndexUpToDate(any(), anyString(), any()))
                .thenReturn(true);
        when(ragOperationsService.getIndexVersion(project, "main")).thenReturn(indexVersion);
        when(aiClientService.buildAiAnalysisRequests(any(), any(), any(), any()))
                .thenReturn(List.of(aiRequest));
        when(aiRequest.getRawDiff()).thenReturn("+line");
        when(aiRequest.getChangedFiles()).thenReturn(List.of());
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
        FrozenExecutionPlan policyPlan = plan("p004-delivery-failure");
        when(executionPolicyRuntime.freeze(anyString(), any(StableRolloutKey.class), any(ExecutionPolicyConfig.class)))
                .thenReturn(policyPlan);
        when(executionPolicyRuntime.currentConfig()).thenReturn(legacyConfig(false));
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

    private PullRequestAnalysisProcessor processor(
            ExecutionPolicyRuntime runtime,
            RagOperationsService rag,
            ApplicationEventPublisher publisher) {
        return processor(runtime, rag, publisher, null);
    }

    private PullRequestAnalysisProcessor processor(
            ExecutionPolicyRuntime runtime,
            RagOperationsService rag,
            ApplicationEventPublisher publisher,
            ExecutionManifestService manifestService) {
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
                runtime,
                manifestService);
    }

    private VcsAiClientService stubProcessThroughAcquisition(
            PrProcessRequest request,
            List<CodeAnalysis> previousAnalyses) throws GeneralSecurityException {
        VcsAiClientService aiClientService = stubCandidateProcessThroughAcquisition(request);
        when(codeAnalysisService.getAllPrAnalyses(7L, 42L)).thenReturn(previousAnalyses);
        return aiClientService;
    }

    private VcsAiClientService stubCandidateProcessThroughAcquisition(
            PrProcessRequest request) throws GeneralSecurityException {
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
        return aiClientService;
    }

    private static AiAnalysisRequest candidateAiRequest(
            Map<String, String> reconciliationFileContents) {
        AiAnalysisRequestImpl.Builder<?> builder = AiAnalysisRequestImpl.builder()
                .withProjectId(7L)
                .withPullRequestId(42L)
                .withProjectVcsConnectionBindingInfo("workspace", "repository")
                .withVcsProvider("github")
                .withRawDiff("+line")
                .withImmutableSnapshot(
                        "a".repeat(40), "b".repeat(40), "c".repeat(40))
                .withPreviousCommitHash("a".repeat(40))
                .withCurrentCommitHash("b".repeat(40))
                .withAnalysisMode(AnalysisMode.FULL)
                .withChangedFiles(List.of("Changed.java"))
                .withDeletedFiles(List.of())
                .withDiffSnippets(List.of());
        if (reconciliationFileContents != null) {
            builder.withReconciliationFileContents(reconciliationFileContents);
        }
        return builder.build();
    }

    private static AiAnalysisRequest identityRequest(
            String workspace,
            String repository,
            String provider,
            String baseSha,
            String headSha,
            String mergeBaseSha,
            String rawDiff,
            PrEnrichmentDataDto enrichment) {
        return AiAnalysisRequestImpl.builder()
                .withProjectId(7L)
                .withPullRequestId(42L)
                .withProjectVcsConnectionBindingInfo(workspace, repository)
                .withVcsProvider(provider)
                .withRawDiff(rawDiff)
                .withImmutableSnapshot(baseSha, headSha, mergeBaseSha)
                .withPreviousCommitHash(baseSha)
                .withCurrentCommitHash(headSha)
                .withAnalysisMode(AnalysisMode.FULL)
                .withChangedFiles(List.of("Changed.java"))
                .withDeletedFiles(List.of())
                .withDiffSnippets(List.of())
                .withEnrichmentData(enrichment)
                .build();
    }

    private static PrEnrichmentDataDto identityEnrichment(
            String path,
            String content) {
        long bytes = content.getBytes(java.nio.charset.StandardCharsets.UTF_8).length;
        return new PrEnrichmentDataDto(
                List.of(FileContentDto.of(path, content)),
                List.of(),
                List.of(),
                new PrEnrichmentDataDto.EnrichmentStats(
                        1, 1, 0, 0, bytes, 0, Map.of()));
    }

    private static PrEnrichmentDataDto identityGap(
            String path,
            String reason) {
        return new PrEnrichmentDataDto(
                List.of(FileContentDto.skipped(path, reason)),
                List.of(),
                List.of(),
                new PrEnrichmentDataDto.EnrichmentStats(
                        1, 0, 1, 0, 0, 0, Map.of(reason, 1)));
    }

    private static ImmutableExecutionManifest candidateManifest(String executionId) {
        return ImmutableExecutionManifest.create(
                1,
                executionId,
                7L,
                "github:workspace/repository",
                42L,
                "a".repeat(40),
                "b".repeat(40),
                "c".repeat(40),
                "diff:" + executionId,
                "0".repeat(64),
                0L,
                "raw-diff",
                "java-vcs-acquisition",
                "analysis-engine-v1",
                "review-artifact-v1",
                "candidate-review-v2",
                "config:coverage",
                NOW);
    }

    private static CodeAnalysis candidateAnalysis(
            ImmutableExecutionManifest manifest,
            Long projectId,
            Long pullRequestId,
            String commitHash,
            String executionId,
            String manifestDigest) {
        CodeAnalysis candidate = new CodeAnalysis();
        if (projectId != null) {
            Project candidateProject = mock(Project.class);
            when(candidateProject.getId()).thenReturn(projectId);
            candidate.setProject(candidateProject);
        }
        candidate.setPrNumber(pullRequestId);
        candidate.setCommitHash(commitHash);
        if (executionId != null && manifestDigest != null) {
            candidate.bindExecutionIdentity(executionId, manifestDigest);
        }
        return candidate;
    }

    private static void assertOutputBindingRejected(
            Class<?>[] outputTypes,
            CodeAnalysis candidate,
            ImmutableExecutionManifest manifest) {
        assertInvocationCause(IllegalStateException.class, () -> invoke(
                null,
                "requireCandidateOutputBinding",
                outputTypes,
                candidate,
                manifest));
    }

    private static Object eventBinding(ImmutableExecutionManifest manifest) throws Throwable {
        return invokeDeclared(
                executionEventBindingType(),
                null,
                "fromManifest",
                new Class<?>[]{ImmutableExecutionManifest.class},
                manifest);
    }

    private static Object newEventBinding(
            String executionId,
            String artifactManifestDigest) throws Throwable {
        var constructor = executionEventBindingType().getDeclaredConstructor(
                String.class, String.class);
        constructor.setAccessible(true);
        try {
            return constructor.newInstance(executionId, artifactManifestDigest);
        } catch (InvocationTargetException error) {
            throw error;
        }
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

    private static ExecutionPolicyConfig legacyConfig(boolean stop) {
        return new ExecutionPolicyConfig(
                "revision", ExecutionMode.LEGACY, "candidate-review-v2", 10_000,
                "salt", stop, false);
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

    private static FrozenExecutionPlan plan(String executionId) {
        PolicyExecution primary = new PolicyExecution(
                executionId,
                "legacy-review-v1",
                ExecutionMode.LEGACY,
                PolicySelectionReason.LEGACY_CONFIGURED,
                0,
                true,
                NOW);
        return new FrozenExecutionPlan(
                executionId, "revision", "a".repeat(64), primary, null, NOW);
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

    @SuppressWarnings("unchecked")
    private static Map<String, Object> pendingTelemetryDocument(
            ImmutableExecutionManifest manifest,
            String indexVersion) {
        Map<String, Object> document = pendingTelemetryDocument();
        Map<String, Object> trace = (Map<String, Object>) document.get("trace");
        trace.put("execution_id", manifest.executionId());
        trace.put("artifact_manifest_digest", manifest.artifactManifestDigest());
        trace.put("base_revision", manifest.baseSha());
        trace.put("head_revision", manifest.headSha());
        Map<String, Object> versions = new HashMap<>(
                (Map<String, Object>) trace.get("versions"));
        versions.put("policy_version", manifest.policyVersion());
        versions.put("index_version", indexVersion);
        trace.put("versions", versions);
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
                String.class,
                executionEventBindingType()};
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
                "b".repeat(40),
                null};
    }

    private static Class<?> executionEventBindingType() {
        for (Class<?> nestedType : PullRequestAnalysisProcessor.class.getDeclaredClasses()) {
            if ("ExecutionEventBinding".equals(nestedType.getSimpleName())) {
                return nestedType;
            }
        }
        throw new IllegalStateException("ExecutionEventBinding type is missing");
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
        return invokeDeclared(
                PullRequestAnalysisProcessor.class, target, methodName, types, args);
    }

    private static Object invokeDeclared(
            Class<?> owner,
            Object target,
            String methodName,
            Class<?>[] types,
            Object... args) throws Throwable {
        Method method = owner.getDeclaredMethod(methodName, types);
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
