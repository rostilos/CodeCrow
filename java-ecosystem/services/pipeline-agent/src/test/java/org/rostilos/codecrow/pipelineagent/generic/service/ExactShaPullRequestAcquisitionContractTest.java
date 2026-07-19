package org.rostilos.codecrow.pipelineagent.generic.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.stream.Stream;

import okhttp3.OkHttpClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.aiclient.AiAnalysisClient;
import org.rostilos.codecrow.analysisengine.coverage.CoverageAnchor;
import org.rostilos.codecrow.analysisengine.coverage.CoverageAnchorKind;
import org.rostilos.codecrow.analysisengine.coverage.CoverageAnchorState;
import org.rostilos.codecrow.analysisengine.coverage.CoverageWorkPlan;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequestImpl;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AgenticRepositoryArchiveV1;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.FileContentDto;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.PrEnrichmentDataDto;
import org.rostilos.codecrow.analysisengine.dto.request.processor.AnalysisProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.execution.ArtifactManifestEntry;
import org.rostilos.codecrow.analysisengine.execution.ExecutionArtifactPayload;
import org.rostilos.codecrow.analysisengine.execution.ExecutionInputArtifactBundle;
import org.rostilos.codecrow.analysisengine.execution.ExecutionManifestPersistencePort;
import org.rostilos.codecrow.analysisengine.execution.ExecutionManifestService;
import org.rostilos.codecrow.analysisengine.execution.ImmutableExecutionManifest;
import org.rostilos.codecrow.analysisengine.execution.RagExecutionConfigV1;
import org.rostilos.codecrow.analysisengine.policy.ExecutionMode;
import org.rostilos.codecrow.analysisengine.policy.PolicyExecution;
import org.rostilos.codecrow.analysisengine.policy.PolicyHashing;
import org.rostilos.codecrow.analysisengine.policy.PolicySelectionReason;
import org.rostilos.codecrow.analysisengine.service.pr.PrFileEnrichmentService;
import org.rostilos.codecrow.analysisengine.service.pr.PullRequestDiffPreparationService;
import org.rostilos.codecrow.analysisengine.service.pr.PullRequestDiffPreparationService.PreparedDiff;
import org.rostilos.codecrow.analysisengine.service.vcs.VcsAiClientService;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisMode;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysisIssue;
import org.rostilos.codecrow.core.model.codeanalysis.IssueCategory;
import org.rostilos.codecrow.core.model.codeanalysis.IssueSeverity;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.ProjectAiConnectionBinding;
import org.rostilos.codecrow.core.model.project.config.AnalysisScopeConfig;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.project.config.ReviewApproach;
import org.rostilos.codecrow.core.model.project.config.ProjectRulesConfig;
import org.rostilos.codecrow.core.model.project.config.RuleType;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.pipelineagent.generic.service.AbstractVcsAiClientService.PullRequestData;
import org.rostilos.codecrow.pipelineagent.generic.service.AbstractVcsAiClientService.PullRequestMetadata;
import org.rostilos.codecrow.pipelineagent.generic.service.AbstractVcsAiClientService.RepositoryInfo;
import org.rostilos.codecrow.pipelineagent.agentic.AgenticRepositoryArchiveService;
import org.rostilos.codecrow.queue.RedisQueueService;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;
import org.rostilos.codecrow.vcsclient.diff.ExactDiffInventory;
import org.springframework.web.client.RestTemplate;

/**
 * Provider-neutral RED contract for the immutable pull-request snapshot boundary.
 * Provider-specific adapters may discover coordinates differently, but analysis
 * must not read a mutable PR diff or branch after these coordinates are selected.
 */
@ExtendWith(MockitoExtension.class)
class ExactShaPullRequestAcquisitionContractTest {
    private static final String FULL_DIFF =
            "diff --git a/src/A.java b/src/A.java\n@@ -1 +1 @@\n-old\n+new\n";
    private static final List<String> CHANGED_FILES = List.of("src/A.java");

    @Mock private TokenEncryptionService encryption;
    @Mock private VcsClientProvider vcsClients;
    @Mock private PullRequestDiffPreparationService diffPreparation;
    @Mock private PrFileEnrichmentService enrichment;
    @Mock private TaskContextEnrichmentService taskContextEnrichment;
    @Mock private TaskHistoryContextService taskHistoryContext;
    @Mock private VcsClient vcsClient;
    @Mock private AgenticRepositoryArchiveService agenticRepositoryArchiveService;
    @Mock private Project project;
    @Mock private VcsRepoInfo repoInfo;
    @Mock private VcsConnection connection;
    @Mock private ProjectAiConnectionBinding aiBinding;
    @Mock private AIConnection aiConnection;
    @Mock private Workspace workspace;

    private PrProcessRequest request;

    @BeforeEach
    void setUp() throws Exception {
        request = new PrProcessRequest();
        request.projectId = 1L;
        request.pullRequestId = 42L;
        request.sourceBranchName = "feature/mutable-name";
        request.targetBranchName = "main";
        request.analysisType = AnalysisType.PR_REVIEW;

        lenient().when(project.getId()).thenReturn(1L);
        lenient().when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
        lenient().when(repoInfo.getVcsConnection()).thenReturn(connection);
        lenient().when(repoInfo.getRepoWorkspace()).thenReturn("workspace");
        lenient().when(repoInfo.getRepoSlug()).thenReturn("repository");
        lenient().when(connection.getConnectionType()).thenReturn(EVcsConnectionType.APP);
        lenient().when(connection.getAccessToken()).thenReturn("encrypted");
        lenient().when(project.getAiBinding()).thenReturn(aiBinding);
        lenient().when(aiBinding.getAiConnection()).thenReturn(aiConnection);
        lenient().when(aiConnection.getProviderKey()).thenReturn(AIProviderKey.OPENAI);
        lenient().when(aiConnection.getAiModel()).thenReturn("fixture-v1");
        lenient().when(aiConnection.getApiKeyEncrypted()).thenReturn("encrypted");
        lenient().when(encryption.decrypt("encrypted")).thenReturn("decrypted");
        ProjectConfig mutableConfig = new ProjectConfig();
        mutableConfig.setUseMcpTools(true);
        mutableConfig.setReviewApproach(ReviewApproach.AGENTIC);
        mutableConfig.setProjectRules(new ProjectRulesConfig(List.of(
                new ProjectRulesConfig.CustomRule(
                        "rule-id",
                        "Mutable rule",
                        "Must not cross the candidate manifest boundary",
                        RuleType.ENFORCE,
                        List.of(),
                        true,
                        0))));
        lenient().when(project.getEffectiveConfig()).thenReturn(mutableConfig);
        lenient().when(project.getWorkspace()).thenReturn(workspace);
        lenient().when(workspace.getName()).thenReturn("tenant");
        lenient().when(project.getNamespace()).thenReturn("namespace");
        lenient().when(taskContextEnrichment.resolveTaskContext(
                        project,
                        "feature/mutable-name",
                        "title",
                        "description"))
                .thenReturn(Map.of("taskKey", "CC-42", "summary", "Bound context"));
        lenient().when(taskContextEnrichment.resolveTaskKey(
                        project,
                        "feature/mutable-name",
                        "title",
                        "description"))
                .thenReturn(Optional.of("CC-42"));
        lenient().when(taskHistoryContext.buildTaskHistoryContext(
                        eq(1L),
                        eq(42L),
                        eq(Map.of("taskKey", "CC-42", "summary", "Bound context")),
                        eq("CC-42")))
                .thenReturn("Prior CC-42 review context");
        lenient().when(vcsClients.getHttpClient(connection)).thenReturn(mock(OkHttpClient.class));
        lenient().when(vcsClients.getClient(connection)).thenReturn(vcsClient);
        lenient().when(enrichment.isEnrichmentEnabled()).thenReturn(true);
        lenient().when(agenticRepositoryArchiveService.stage(
                        eq(vcsClient),
                        anyString(),
                        eq("workspace"),
                        eq("repository"),
                        anyString()))
                .thenAnswer(invocation -> new AgenticRepositoryArchiveV1(
                        1,
                        "d".repeat(64),
                        invocation.getArgument(4, String.class),
                        "e".repeat(64),
                        1024L));
    }

    @Test
    void classicExactReviewDoesNotDownloadARepositoryArchive() throws Exception {
        String headSha = "b".repeat(40);
        request.commitHash = headSha;
        project.getEffectiveConfig().setReviewApproach(ReviewApproach.CLASSIC);
        RecordingService service = service(
                EVcsProvider.GITHUB,
                "a".repeat(40),
                headSha,
                "c".repeat(40));
        prepareSuccessfulExactAcquisition(headSha);

        AiAnalysisRequest built = buildExactAiAnalysisRequests(
                service, Optional.empty()).get(0);

        assertThat(built.getReviewApproach()).isEqualTo(ReviewApproach.CLASSIC);
        assertThat(built.getAgenticRepository()).isNull();
        service.discardUndispatchedAiAnalysisRequest(built);
        verify(agenticRepositoryArchiveService, never()).stage(
                any(), anyString(), anyString(), anyString(), anyString());
        verify(agenticRepositoryArchiveService, never()).cleanup(anyString());
    }

    @Test
    void agenticExactReviewDownloadsTheExactHeadAndCarriesItsDescriptor()
            throws Exception {
        String headSha = "b".repeat(40);
        request.commitHash = headSha;
        RecordingService service = service(
                EVcsProvider.GITHUB,
                "a".repeat(40),
                headSha,
                "c".repeat(40));
        prepareSuccessfulExactAcquisition(headSha);

        AiAnalysisRequest built = buildExactAiAnalysisRequests(
                service, Optional.empty()).get(0);

        assertThat(built.getReviewApproach()).isEqualTo(ReviewApproach.AGENTIC);
        assertThat(built.getAgenticRepository()).isNotNull();
        assertThat(built.getAgenticRepository().snapshotSha()).isEqualTo(headSha);
        verify(agenticRepositoryArchiveService).stage(
                eq(vcsClient),
                anyString(),
                eq("workspace"),
                eq("repository"),
                eq(headSha));

        service.discardUndispatchedAiAnalysisRequest(built);

        verify(agenticRepositoryArchiveService)
                .cleanup(built.getAgenticRepository().workspaceKey());
    }

    @Test
    void agenticExactReviewBindsPreviousFindingsOnlyInsideEnrichmentArtifact()
            throws Exception {
        String headSha = "b".repeat(40);
        request.commitHash = headSha;
        RecordingService service = service(
                EVcsProvider.GITHUB,
                "a".repeat(40),
                headSha,
                "c".repeat(40));
        prepareSuccessfulExactAcquisition(headSha);

        CodeAnalysis previous = mock(CodeAnalysis.class);
        CodeAnalysisIssue issue = mock(CodeAnalysisIssue.class);
        when(previous.getIssues()).thenReturn(List.of(issue));
        when(previous.getPrVersion()).thenReturn(3);
        when(issue.getId()).thenReturn(17L);
        when(issue.getAnalysis()).thenReturn(previous);
        when(issue.getIssueCategory()).thenReturn(IssueCategory.SECURITY);
        when(issue.getSeverity()).thenReturn(IssueSeverity.HIGH);
        when(issue.getTitle()).thenReturn("Unsafe input");
        when(issue.getReason()).thenReturn("Untrusted data reaches a sink");
        when(issue.getFilePath()).thenReturn("src/A.java");
        when(issue.getLineNumber()).thenReturn(9);
        when(issue.getCodeSnippet()).thenReturn("sink(value);");

        AiAnalysisRequestImpl bound = (AiAnalysisRequestImpl)
                buildExactAiAnalysisRequests(service, Optional.of(previous)).get(0);
        AiAnalysisRequestImpl withoutPrevious = (AiAnalysisRequestImpl)
                buildExactAiAnalysisRequests(service, Optional.empty()).get(0);

        assertThat(bound.getPreviousCodeAnalysisIssues()).isNull();
        assertThat(bound.getEnrichmentData().reviewContext().previousFindings())
                .singleElement()
                .satisfies(finding -> {
                    assertThat(finding.id()).isEqualTo("17");
                    assertThat(finding.title()).isEqualTo("Unsafe input");
                    assertThat(finding.file()).isEqualTo("src/A.java");
                    assertThat(finding.line()).isEqualTo(9);
                    assertThat(finding.prVersion()).isEqualTo(3);
                });

        ObjectMapper mapper = new ObjectMapper();
        JsonNode payload = mapper.valueToTree(bound);
        assertThat(payload.path("previousCodeAnalysisIssues").isNull()).isTrue();
        assertThat(payload.path("enrichmentData").path("reviewContext")
                .path("previousFindings").get(0).path("id").asText())
                .isEqualTo("17");

        String boundDigest = ExecutionInputArtifactBundle.canonicalInputDigest(
                bound.getRawDiff().getBytes(StandardCharsets.UTF_8),
                bound.getEnrichmentData());
        String withoutPreviousDigest = ExecutionInputArtifactBundle.canonicalInputDigest(
                withoutPrevious.getRawDiff().getBytes(StandardCharsets.UTF_8),
                withoutPrevious.getEnrichmentData());
        assertThat(boundDigest).isNotEqualTo(withoutPreviousDigest);
    }

    @Test
    void agenticExactReviewBindsCanonicalUnresolvedFindingsAcrossAllPrHistory()
            throws Exception {
        String headSha = "b".repeat(40);
        request.commitHash = headSha;
        RecordingService service = service(
                EVcsProvider.GITHUB,
                "a".repeat(40),
                headSha,
                "c".repeat(40));
        prepareSuccessfulExactAcquisition(headSha);

        CodeAnalysis oldest = historicalAnalysis(1);
        CodeAnalysis middle = historicalAnalysis(2);
        CodeAnalysis latest = historicalAnalysis(3);
        CodeAnalysisIssue lineageRoot = historicalIssue(
                oldest, 10L, null, false, "src/A.java", "Lineage issue");
        CodeAnalysisIssue laterLineage = historicalIssue(
                middle, 20L, 10L, false, "src/A.java", "Lineage issue");
        CodeAnalysisIssue latestLineage = historicalIssue(
                latest, 30L, 20L, false, "src/A.java", "Lineage issue");
        CodeAnalysisIssue resolvedRoot = historicalIssue(
                oldest, 11L, null, false, "src/Resolved.java", "Resolved issue");
        CodeAnalysisIssue latestResolution = historicalIssue(
                latest, 31L, 11L, true, "src/Resolved.java", "Resolved issue");
        CodeAnalysisIssue omittedButOpen = historicalIssue(
                oldest, 12L, null, false, "src/Old.java", "Older open issue");
        when(oldest.getIssues()).thenReturn(List.of(
                lineageRoot, resolvedRoot, omittedButOpen));
        when(middle.getIssues()).thenReturn(List.of(laterLineage));
        when(latest.getIssues()).thenReturn(List.of(latestLineage, latestResolution));

        AiAnalysisRequestImpl bound = (AiAnalysisRequestImpl)
                buildExactAiAnalysisRequests(
                        service,
                        Optional.of(latest),
                        List.of(latest, middle, oldest)).get(0);

        assertThat(bound.getEnrichmentData().reviewContext().previousFindings())
                .extracting(finding -> finding.id())
                .containsExactly("30", "12");
        assertThat(bound.getEnrichmentData().reviewContext().previousFindings())
                .allMatch(finding -> "open".equals(finding.status()));
    }

    @Test
    void classicExactReviewDoesNotDereferenceSuppliedPreviousAnalysis()
            throws Exception {
        String headSha = "b".repeat(40);
        request.commitHash = headSha;
        project.getEffectiveConfig().setReviewApproach(ReviewApproach.CLASSIC);
        RecordingService service = service(
                EVcsProvider.GITHUB,
                "a".repeat(40),
                headSha,
                "c".repeat(40));
        prepareSuccessfulExactAcquisition(headSha);
        CodeAnalysis previous = mock(CodeAnalysis.class);

        AiAnalysisRequestImpl built = (AiAnalysisRequestImpl)
                buildExactAiAnalysisRequests(service, Optional.of(previous)).get(0);

        assertThat(built.getPreviousCodeAnalysisIssues()).isNull();
        assertThat(built.getEnrichmentData().reviewContext().previousFindings())
                .isEmpty();
        verifyNoInteractions(previous);
    }

    @Test
    void missingAgenticDescriptorFailsWithoutLegacyFallback() throws Exception {
        String headSha = "b".repeat(40);
        request.commitHash = headSha;
        RecordingService service = service(
                EVcsProvider.GITHUB,
                "a".repeat(40),
                headSha,
                "c".repeat(40));
        prepareSuccessfulExactAcquisition(headSha);
        when(agenticRepositoryArchiveService.stage(
                eq(vcsClient), anyString(), eq("workspace"),
                eq("repository"), eq(headSha))).thenReturn(null);

        assertThatThrownBy(() -> buildExactAiAnalysisRequests(
                service, Optional.empty()))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("no descriptor");
        assertThat(service.mutablePullRequestDiffReads).isZero();
    }

    @Test
    void mismatchedAgenticSnapshotIsCleanedAndFailsWithoutFallback()
            throws Exception {
        String headSha = "b".repeat(40);
        String workspaceKey = "f".repeat(64);
        request.commitHash = headSha;
        RecordingService service = service(
                EVcsProvider.GITHUB,
                "a".repeat(40),
                headSha,
                "c".repeat(40));
        prepareSuccessfulExactAcquisition(headSha);
        when(agenticRepositoryArchiveService.stage(
                eq(vcsClient), anyString(), eq("workspace"),
                eq("repository"), eq(headSha)))
                .thenReturn(new AgenticRepositoryArchiveV1(
                        1,
                        workspaceKey,
                        "9".repeat(40),
                        "e".repeat(64),
                        1024L));

        assertThatThrownBy(() -> buildExactAiAnalysisRequests(
                service, Optional.empty()))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("conflicts with exact head");
        verify(agenticRepositoryArchiveService).cleanup(workspaceKey);
        assertThat(service.mutablePullRequestDiffReads).isZero();
    }

    @ParameterizedTest(name = "{0} acquires only exact {1}-hex coordinates")
    @MethodSource("providerCoordinates")
    void exactProviderCoordinatesDriveTheRangeDiffAndEveryFileRead(
            EVcsProvider provider,
            int shaLength,
            String baseSha,
            String headSha,
            String mergeBaseSha) throws Exception {
        request.commitHash = headSha;
        RecordingService service = service(provider, baseSha, headSha, mergeBaseSha);

        lenient().when(diffPreparation.prepare(
                        eq(project), eq(42L), eq(FULL_DIFF),
                        eq(null), eq(headSha), any()))
                .thenReturn(new PreparedDiff(
                        FULL_DIFF, null, AnalysisMode.FULL, CHANGED_FILES, List.of(),
                        null, headSha));
        PrEnrichmentDataDto enriched = enrichedFiles();
        lenient().when(enrichment.fetchFileContentsOnly(
                        vcsClient, "workspace", "repository", headSha, CHANGED_FILES))
                .thenReturn(enriched);

        List<AiAnalysisRequest> requests = buildExactAiAnalysisRequests(
                service, Optional.empty());

        assertThat(requests).singleElement().satisfies(built -> {
            assertThat(built.getCurrentCommitHash()).isEqualTo(headSha);
            assertThat(built.getPreviousCommitHash()).isEqualTo(baseSha);
            assertThat(built.getRawDiff()).isEqualTo(FULL_DIFF);
            assertThat(snapshotCoordinate(built, "getBaseSha")).isEqualTo(baseSha);
            assertThat(snapshotCoordinate(built, "getHeadSha")).isEqualTo(headSha);
            assertThat(snapshotCoordinate(built, "getMergeBaseSha")).isEqualTo(mergeBaseSha);
            assertThat(built.getPrTitle()).isEqualTo("title");
            assertThat(built.getPrDescription()).isEqualTo("description");
            assertThat(built.getTaskContext()).containsEntry("taskKey", "CC-42");
            assertThat(built.getTaskHistoryContext())
                    .isEqualTo("Prior CC-42 review context");
            assertThat(built.getUseMcpTools()).isFalse();
            assertThat(((AiAnalysisRequestImpl) built).getProjectRules())
                    .contains("Mutable rule");
            assertThat(((AiAnalysisRequestImpl) built).getEnrichmentData().stats().processingTimeMs())
                    .isZero();
            assertThat(built.getSourceBranchName()).isEqualTo("feature/mutable-name");
            assertThat(built.getTargetBranchName()).isEqualTo("main");
            PrEnrichmentDataDto.ReviewContext boundContext =
                    ((AiAnalysisRequestImpl) built).getEnrichmentData().reviewContext();
            assertThat(boundContext.prTitle()).isEqualTo(built.getPrTitle());
            assertThat(boundContext.taskContext()).isEqualTo(built.getTaskContext());
            assertThat(boundContext.projectRules())
                    .isEqualTo(((AiAnalysisRequestImpl) built).getProjectRules());
        });
        assertThat(baseSha).hasSize(shaLength).matches("[0-9a-f]+?");
        assertThat(headSha).hasSize(shaLength).matches("[0-9a-f]+?");
        assertThat(mergeBaseSha).hasSize(shaLength).matches("[0-9a-f]+?");
        assertThat(service.metadataReads).isEqualTo(1);
        assertThat(service.mutablePullRequestDiffReads).isZero();
        assertThat(service.exactRangeReads).isEqualTo(1);
        assertThat(service.rangeBase).isEqualTo(baseSha);
        assertThat(service.rangeHead).isEqualTo(headSha);
        verify(enrichment).fetchFileContentsOnly(
                vcsClient, "workspace", "repository", headSha, CHANGED_FILES);
        verify(enrichment, never()).enrichPrFiles(
                any(), any(), any(), any(), any());
        verify(enrichment, never()).isEnrichmentEnabled();
    }

    @Test
    void exactBuilderManifestPersistenceAndStrictQueueSerializationComposeEndToEnd()
            throws Exception {
        String baseSha = "a".repeat(40);
        String headSha = "b".repeat(40);
        String mergeBaseSha = "c".repeat(40);
        String executionId = "pr:exact-42-v1";
        String diffArtifactId = "diff:exact-42-v1";
        String artifactProducer = "java-vcs-acquisition";
        String artifactProducerVersion = "analysis-engine-v1";
        String policyVersion = "candidate-review-v1";
        Instant createdAt = Instant.parse("2026-07-15T12:00:00Z");
        String deterministicDiff = FULL_DIFF + """
                diff --git a/src/B.java b/src/B.java
                @@ -1 +1 @@
                -old
                +new
                """;
        request.commitHash = headSha;
        RecordingService service = service(
                EVcsProvider.GITHUB, baseSha, headSha, mergeBaseSha, deterministicDiff);
        List<String> deterministicFiles = List.of("src/A.java", "src/B.java");

        when(diffPreparation.prepare(
                        eq(project), eq(42L), eq(deterministicDiff),
                        isNull(), eq(headSha), any()))
                .thenReturn(new PreparedDiff(
                        deterministicDiff, null, AnalysisMode.FULL, deterministicFiles, List.of(),
                        null, headSha));
        PrEnrichmentDataDto firstAcquisition = deterministicEnrichedFiles(false, 7);
        PrEnrichmentDataDto replayAcquisition = deterministicEnrichedFiles(true, 9_999);
        when(enrichment.fetchFileContentsOnly(
                        vcsClient, "workspace", "repository", headSha, deterministicFiles))
                .thenReturn(firstAcquisition, replayAcquisition);
        CodeAnalysis previous = mock(CodeAnalysis.class);
        CodeAnalysisIssue previousIssue = mock(CodeAnalysisIssue.class);
        when(previous.getIssues()).thenReturn(List.of(previousIssue));
        when(previous.getPrVersion()).thenReturn(6);
        when(previousIssue.getId()).thenReturn(29L);
        when(previousIssue.getAnalysis()).thenReturn(previous);
        when(previousIssue.getIssueCategory()).thenReturn(IssueCategory.SECURITY);
        when(previousIssue.getSeverity()).thenReturn(IssueSeverity.HIGH);
        when(previousIssue.getTitle()).thenReturn("Bound previous finding");
        when(previousIssue.getFilePath()).thenReturn("src/A.java");
        when(previousIssue.getLineNumber()).thenReturn(1);

        AiAnalysisRequestImpl exactRequest = (AiAnalysisRequestImpl)
                buildExactAiAnalysisRequests(service, Optional.of(previous)).get(0);
        AiAnalysisRequestImpl replayRequest = (AiAnalysisRequestImpl)
                buildExactAiAnalysisRequests(service, Optional.of(previous)).get(0);
        ObjectMapper objectMapper = new ObjectMapper()
                .registerModule(new JavaTimeModule())
                .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
        assertThat(firstAcquisition.stats().processingTimeMs()).isEqualTo(7);
        assertThat(replayAcquisition.stats().processingTimeMs()).isEqualTo(9_999);
        assertThat(exactRequest.getEnrichmentData())
                .isEqualTo(replayRequest.getEnrichmentData());
        assertThat(exactRequest.getEnrichmentData().stats().processingTimeMs()).isZero();
        assertThat(objectMapper.writeValueAsBytes(exactRequest))
                .isEqualTo(objectMapper.writeValueAsBytes(replayRequest));
        assertThat(ExecutionInputArtifactBundle.canonicalEnrichmentBytes(
                        exactRequest.getEnrichmentData()))
                .isEqualTo(ExecutionInputArtifactBundle.canonicalEnrichmentBytes(
                        replayRequest.getEnrichmentData()));
        RagExecutionConfigV1 ragContext = RagExecutionConfigV1.defaults("rag-disabled");
        ExecutionInputArtifactBundle inputBundle = ExecutionInputArtifactBundle.create(
                executionId,
                headSha,
                diffArtifactId,
                deterministicDiff.getBytes(StandardCharsets.UTF_8),
                exactRequest.getEnrichmentData(),
                ragContext,
                ImmutableExecutionManifest.CURRENT_ARTIFACT_SCHEMA_VERSION,
                artifactProducer,
                artifactProducerVersion);
        ExecutionInputArtifactBundle replayInputBundle = ExecutionInputArtifactBundle.create(
                executionId,
                headSha,
                diffArtifactId,
                deterministicDiff.getBytes(StandardCharsets.UTF_8),
                replayRequest.getEnrichmentData(),
                ragContext,
                ImmutableExecutionManifest.CURRENT_ARTIFACT_SCHEMA_VERSION,
                artifactProducer,
                artifactProducerVersion);
        assertThat(replayInputBundle).isEqualTo(inputBundle);
        ArtifactManifestEntry rawDiffEntry = inputBundle.entries().stream()
                .filter(entry -> entry.kind() == ArtifactManifestEntry.Kind.RAW_DIFF)
                .findFirst()
                .orElseThrow();
        ImmutableExecutionManifest manifest = ImmutableExecutionManifest.create(
                ImmutableExecutionManifest.CURRENT_SCHEMA_VERSION,
                executionId,
                project.getId(),
                "github:workspace/repository",
                request.pullRequestId,
                baseSha,
                headSha,
                mergeBaseSha,
                diffArtifactId,
                rawDiffEntry.contentDigest(),
                rawDiffEntry.byteLength(),
                ImmutableExecutionManifest.RAW_DIFF_ARTIFACT_KIND,
                artifactProducer,
                artifactProducerVersion,
                ImmutableExecutionManifest.CURRENT_ARTIFACT_SCHEMA_VERSION,
                policyVersion,
                "creation:00000042",
                createdAt,
                inputBundle.entries());
        ImmutableExecutionManifest replayManifest = ImmutableExecutionManifest.create(
                ImmutableExecutionManifest.CURRENT_SCHEMA_VERSION,
                executionId,
                project.getId(),
                "github:workspace/repository",
                request.pullRequestId,
                baseSha,
                headSha,
                mergeBaseSha,
                diffArtifactId,
                rawDiffEntry.contentDigest(),
                rawDiffEntry.byteLength(),
                ImmutableExecutionManifest.RAW_DIFF_ARTIFACT_KIND,
                artifactProducer,
                artifactProducerVersion,
                ImmutableExecutionManifest.CURRENT_ARTIFACT_SCHEMA_VERSION,
                policyVersion,
                "creation:00000042",
                createdAt,
                replayInputBundle.entries());
        assertThat(replayManifest).isEqualTo(manifest);
        assertThat(replayManifest.artifactManifestDigest())
                .isEqualTo(manifest.artifactManifestDigest());

        InMemoryManifestPersistence persistence = new InMemoryManifestPersistence();
        ExecutionManifestService manifestService = new ExecutionManifestService(persistence);
        ImmutableExecutionManifest persistedManifest = manifestService.persistBeforeWork(
                manifest, inputBundle.artifacts());
        ExecutionManifestService restartedManifestService =
                new ExecutionManifestService(persistence);
        assertThat(restartedManifestService.persistBeforeWork(
                manifest, replayInputBundle.artifacts())).isEqualTo(manifest);
        assertThat(persistence.createOrLoadCalls).isEqualTo(2);
        assertThat(restartedManifestService.requireVerified(executionId)).isEqualTo(manifest);

        PolicyExecution policy = new PolicyExecution(
                executionId,
                policyVersion,
                ExecutionMode.ACTIVE,
                PolicySelectionReason.ACTIVE_ROLLOUT_SELECTED,
                42,
                true,
                createdAt);
        CoverageWorkPlan coverageWorkPlan = candidateCoverageWorkPlan(
                persistedManifest, deterministicFiles);
        Map<String, Object> coverageReceipt = incompleteCoverageReceipt(coverageWorkPlan);
        RedisQueueService queue = mock(RedisQueueService.class);
        when(queue.rightPop(anyString(), anyLong()))
                .thenReturn(objectMapper.writeValueAsString(Map.of(
                        "type", "final",
                        "executionId", executionId,
                        "artifactManifestDigest", manifest.artifactManifestDigest(),
                        "result", Map.of(
                                "comment", "ok",
                                "issues", List.of(),
                                "reviewApproach", exactRequest.getReviewApproach().name(),
                                "coverageReceipt", coverageReceipt))));

        AiAnalysisClient client = new AiAnalysisClient(
                mock(RestTemplate.class), queue, objectMapper);
        assertThat(client.performAnalysis(
                exactRequest,
                ignored -> { },
                policy,
                "rag-disabled",
                persistedManifest,
                coverageWorkPlan)).containsEntry("comment", "ok");

        ArgumentCaptor<String> queuedPayload = ArgumentCaptor.forClass(String.class);
        verify(queue).leftPush(eq("codecrow:analysis:jobs"), queuedPayload.capture());
        JsonNode queued = objectMapper.readTree(queuedPayload.getValue());
        JsonNode queuedRequest = queued.path("request");
        JsonNode queuedManifest = queuedRequest.path("executionManifest");
        JsonNode queuedLedger = queuedRequest.path("coverageLedger");
        JsonNode expectedQueuedManifest = objectMapper.readTree(
                objectMapper.writeValueAsBytes(manifest));

        assertThat(queued.path("schemaVersion").asInt()).isEqualTo(2);
        assertThat(queuedManifest).isEqualTo(expectedQueuedManifest);
        assertThat(queuedManifest.path("executionId").asText()).isEqualTo(executionId);
        assertThat(queuedManifest.path("projectId").asLong()).isEqualTo(project.getId());
        assertThat(queuedManifest.path("repositoryId").asText())
                .isEqualTo("github:workspace/repository");
        assertThat(queuedManifest.path("pullRequestId").asLong())
                .isEqualTo(request.pullRequestId);
        assertThat(queuedManifest.path("baseSha").asText()).isEqualTo(baseSha);
        assertThat(queuedManifest.path("headSha").asText()).isEqualTo(headSha);
        assertThat(queuedManifest.path("mergeBaseSha").asText()).isEqualTo(mergeBaseSha);
        assertThat(queuedManifest.path("artifactManifestDigest").asText())
                .isEqualTo(manifest.artifactManifestDigest());
        assertThat(queuedManifest.path("inputArtifacts").size())
                .isEqualTo(inputBundle.entries().size());
        assertThat(queuedLedger.path("executionId").asText()).isEqualTo(executionId);
        assertThat(queuedLedger.path("artifactManifestDigest").asText())
                .isEqualTo(manifest.artifactManifestDigest());
        assertThat(queuedLedger.path("ledgerDigest").asText())
                .isEqualTo(coverageWorkPlan.ledgerDigest());
        assertThat(queuedLedger.path("anchorCount").asInt())
                .isEqualTo(deterministicFiles.size());

        assertThat(queuedRequest.path("previousCommitHash").asText()).isEqualTo(baseSha);
        assertThat(queuedRequest.path("currentCommitHash").asText()).isEqualTo(headSha);
        assertThat(queuedRequest.path("projectId").asLong()).isEqualTo(project.getId());
        assertThat(queuedRequest.path("pullRequestId").asLong())
                .isEqualTo(request.pullRequestId);
        assertThat(queuedRequest.path("projectVcsWorkspace").asText())
                .isEqualTo("workspace");
        assertThat(queuedRequest.path("projectVcsRepoSlug").asText())
                .isEqualTo("repository");
        assertThat(queuedRequest.path("vcsProvider").asText()).isEqualTo("github");
        assertThat(queuedRequest.path("rawDiff").asText()).isEqualTo(deterministicDiff);
        assertThat(queuedRequest.path("changedFiles"))
                .isEqualTo(objectMapper.valueToTree(deterministicFiles));
        JsonNode expectedQueuedEnrichment = objectMapper.readTree(
                objectMapper.writeValueAsBytes(exactRequest.getEnrichmentData()));
        assertThat(queuedRequest.path("enrichmentData"))
                .isEqualTo(expectedQueuedEnrichment);
        assertThat(queuedRequest.path("analysisMode").asText()).isEqualTo("FULL");
        assertThat(queuedRequest.path("analysisType").asText()).isEqualTo("PR_REVIEW");
        assertThat(queuedRequest.path("indexVersion").asText()).isEqualTo("rag-disabled");
        assertThat(queuedRequest.path("policyVersion").asText()).isEqualTo(policyVersion);
        assertThat(queuedRequest.path("executionMode").asText()).isEqualTo("active");
        assertThat(queuedRequest.path("publicationAllowed").asBoolean()).isTrue();
        assertThat(queuedRequest.has("executionId")).isFalse();
        assertThat(queuedRequest.has("legacyCompatibility")).isFalse();

        assertThat(queuedRequest.path("prTitle").asText()).isEqualTo("title");
        assertThat(queuedRequest.path("prDescription").asText()).isEqualTo("description");
        assertThat(queuedRequest.path("taskContext").path("taskKey").asText())
                .isEqualTo("CC-42");
        assertThat(queuedRequest.path("taskHistoryContext").asText())
                .isEqualTo("Prior CC-42 review context");
        assertThat(queuedRequest.path("sourceBranchName").asText())
                .isEqualTo("feature/mutable-name");
        assertThat(queuedRequest.path("targetBranchName").asText()).isEqualTo("main");
        assertThat(queuedRequest.path("deltaDiff").isNull()).isTrue();
        assertThat(queuedRequest.path("previousCodeAnalysisIssues").isNull()).isTrue();
        assertThat(queuedRequest.path("enrichmentData").path("reviewContext")
                .path("previousFindings").get(0).path("id").asText())
                .isEqualTo("29");
        assertThat(queuedRequest.path("reconciliationFileContents").isNull()).isTrue();
        assertThat(queuedRequest.path("projectRules").asText()).contains("Mutable rule");
        assertThat(queuedRequest.path("enrichmentData").path("reviewContext")
                .path("prTitle").asText()).isEqualTo("title");
        assertThat(queuedRequest.path("useMcpTools").asBoolean()).isFalse();
        assertThat(exactRequest.getReviewApproach()).isEqualTo(ReviewApproach.AGENTIC);
        assertThat(queuedRequest.path("reviewApproach").asText()).isEqualTo("AGENTIC");
        assertThat(queuedRequest.path("agenticRepository").path("schemaVersion").asInt())
                .isEqualTo(1);
        assertThat(queuedRequest.path("agenticRepository").path("snapshotSha").asText())
                .isEqualTo(headSha);
        assertThat(queuedRequest.path("agenticRepository").path("workspaceKey").asText())
                .matches("[0-9a-f]{64}");
        assertThat(queuedRequest.path("agenticRepository").path("contentDigest").asText())
                .isEqualTo("e".repeat(64));
        assertThat(queuedRequest.path("agenticRepository").path("byteLength").asLong())
                .isEqualTo(1024L);
        assertThat(queuedRequest.path("deletedFiles").isEmpty()).isTrue();
        assertThat(queuedRequest.path("diffSnippets").isEmpty()).isTrue();
    }

    @Test
    void outOfScopeExactDiffStillReturnsAnAcquisitionOnlyImmutableRequest()
            throws Exception {
        String baseSha = "a".repeat(40);
        String headSha = "b".repeat(40);
        String mergeBaseSha = "c".repeat(40);
        request.commitHash = headSha;
        RecordingService service = service(
                EVcsProvider.GITHUB, baseSha, headSha, mergeBaseSha);
        project.getEffectiveConfig().setAnalysisScope(
                new AnalysisScopeConfig(List.of("docs/**"), List.of()));
        when(diffPreparation.prepare(
                        eq(project), eq(42L), eq(FULL_DIFF),
                        isNull(), eq(headSha), any()))
                .thenReturn(PreparedDiff.empty(null, headSha));

        AiAnalysisRequest built = buildExactAiAnalysisRequests(
                service, Optional.empty()).get(0);

        assertThat(built.getRawDiff()).isEqualTo(FULL_DIFF);
        assertThat(built.getChangedFiles()).isEmpty();
        assertThat(built.getDeletedFiles()).isEmpty();
        assertThat(built.getBaseSha()).isEqualTo(baseSha);
        assertThat(built.getHeadSha()).isEqualTo(headSha);
        assertThat(built.getMergeBaseSha()).isEqualTo(mergeBaseSha);
        PrEnrichmentDataDto enrichmentData =
                ((AiAnalysisRequestImpl) built).getEnrichmentData();
        assertThat(enrichmentData.fileContents()).isEmpty();
        assertThat(enrichmentData.fileMetadata()).isEmpty();
        assertThat(enrichmentData.relationships()).isEmpty();
        assertThat(enrichmentData.stats())
                .isEqualTo(PrEnrichmentDataDto.EnrichmentStats.empty());
        assertThat(enrichmentData.reviewContext()).isNotNull();
        assertThat(enrichmentData.reviewContext().prTitle()).isEqualTo("title");
        assertThat(enrichmentData.reviewContext().prDescription())
                .isEqualTo("description");
        assertThat(enrichmentData.reviewContext().sourceBranchName())
                .isEqualTo("feature/mutable-name");
        assertThat(enrichmentData.reviewContext().targetBranchName())
                .isEqualTo("main");
        verify(enrichment, never()).enrichPrFiles(any(), any(), any(), any(), any());
        verify(enrichment, never()).fetchFileContentsOnly(any(), any(), any(), any(), any());
    }

    @Test
    void zeroByteExactDiffRemainsAnAuthoritativeAcquisitionRequest()
            throws Exception {
        String baseSha = "a".repeat(40);
        String headSha = "b".repeat(40);
        request.commitHash = headSha;
        RecordingService service = service(
                EVcsProvider.GITHUB, baseSha, headSha, "c".repeat(40), "");
        when(diffPreparation.prepare(
                        eq(project), eq(42L), eq(""),
                        isNull(), eq(headSha), any()))
                .thenReturn(PreparedDiff.empty(null, headSha));

        AiAnalysisRequest built = buildExactAiAnalysisRequests(
                service, Optional.empty()).get(0);

        assertThat(built.getRawDiff()).isEmpty();
        assertThat(built.getChangedFiles()).isEmpty();
        assertThat(built.getDeletedFiles()).isEmpty();
        assertThat(built.getBaseSha()).isEqualTo(baseSha);
        assertThat(built.getHeadSha()).isEqualTo(headSha);
    }

    @ParameterizedTest(name = "{0} rejects malformed non-empty exact diff")
    @MethodSource("providers")
    void malformedNonEmptyExactDiffFailsBeforeLossyPreparation(EVcsProvider provider) {
        String headSha = "b".repeat(40);
        request.commitHash = headSha;
        RecordingService service = service(
                provider,
                "a".repeat(40),
                headSha,
                "c".repeat(40),
                "provider returned text but no unified diff inventory");

        assertThatThrownBy(() -> buildExactAiAnalysisRequests(
                service, Optional.empty()))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("MALFORMED");

        verifyNoInteractions(diffPreparation);
        verify(enrichment, never()).fetchFileContentsOnly(
                any(), any(), any(), any(), any());
    }

    @Test
    void typedExactInventoryPreservesRenameWithSpacesAndDeletionBeforeP103Anchors()
            throws Exception {
        String baseSha = "a".repeat(40);
        String headSha = "b".repeat(40);
        String mergeBaseSha = "c".repeat(40);
        String diff = """
                diff --git "a/src/Old Name.java" "b/src/New Name.java"
                similarity index 88%
                rename from src/Old Name.java
                rename to src/New Name.java
                --- "a/src/Old Name.java"
                +++ "b/src/New Name.java"
                @@ -1 +1 @@
                -old
                +new
                diff --git a/src/Deleted.java b/src/Deleted.java
                deleted file mode 100644
                --- a/src/Deleted.java
                +++ /dev/null
                @@ -1 +0,0 @@
                -removed
                """;
        request.commitHash = headSha;
        RecordingService service = service(
                EVcsProvider.GITHUB, baseSha, headSha, mergeBaseSha, diff);
        when(diffPreparation.prepare(
                        eq(project), eq(42L), eq(diff),
                        isNull(), eq(headSha), any()))
                .thenReturn(PreparedDiff.empty(null, headSha));
        String content = "final class NewName {}";
        PrEnrichmentDataDto acquired = new PrEnrichmentDataDto(
                List.of(FileContentDto.of("src/New Name.java", content)),
                List.of(),
                List.of(),
                completeStats(content));
        when(enrichment.fetchFileContentsOnly(
                        vcsClient,
                        "workspace",
                        "repository",
                        headSha,
                        List.of("src/New Name.java")))
                .thenReturn(acquired);

        AiAnalysisRequest built = buildExactAiAnalysisRequests(
                service, Optional.empty()).get(0);

        assertThat(built.getRawDiff()).isEqualTo(diff);
        assertThat(built.getChangedFiles()).containsExactly("src/New Name.java");
        assertThat(built.getDeletedFiles()).containsExactly("src/Deleted.java");
        verify(enrichment).fetchFileContentsOnly(
                vcsClient,
                "workspace",
                "repository",
                headSha,
                List.of("src/New Name.java"));
    }

    @ParameterizedTest(name = "{0} rejects a provider head that moved")
    @MethodSource("providers")
    void movedProviderHeadFailsBeforeDiffEnrichmentOrRequestCreation(EVcsProvider provider) {
        String acceptedHead = "b".repeat(40);
        request.commitHash = acceptedHead;
        RecordingService service = service(
                provider, "a".repeat(40), "c".repeat(40), "d".repeat(40));

        assertThatThrownBy(() -> buildExactAiAnalysisRequests(service, Optional.empty()))
                .isInstanceOf(RuntimeException.class)
                .hasMessageContaining("head");

        assertThat(service.metadataReads).isEqualTo(1);
        assertThat(service.mutablePullRequestDiffReads).isZero();
        assertThat(service.exactRangeReads).isZero();
        verifyNoInteractions(diffPreparation);
        verify(enrichment, never()).enrichPrFiles(any(), any(), any(), any(), any());
        verify(enrichment, never()).fetchFileContentsOnly(any(), any(), any(), any(), any());
    }

    @ParameterizedTest(name = "{0} rejects invalid {1}")
    @MethodSource("invalidCoordinates")
    void missingOrNonExactCoordinateFailsBeforeDiffEnrichmentOrRequestCreation(
            EVcsProvider provider,
            String invalidField,
            String baseSha,
            String headSha,
            String mergeBaseSha) {
        request.commitHash = "b".repeat(40);
        RecordingService service = service(provider, baseSha, headSha, mergeBaseSha);

        assertThatThrownBy(() -> buildExactAiAnalysisRequests(service, Optional.empty()))
                .isInstanceOf(RuntimeException.class)
                .hasMessageContaining(invalidField);

        assertThat(service.metadataReads).isEqualTo(1);
        assertThat(service.mutablePullRequestDiffReads).isZero();
        assertThat(service.exactRangeReads).isZero();
        verifyNoInteractions(diffPreparation);
        verify(enrichment, never()).enrichPrFiles(any(), any(), any(), any(), any());
        verify(enrichment, never()).fetchFileContentsOnly(any(), any(), any(), any(), any());
    }

    @Test
    void legacyBuilderRemainsAnExplicitMutableCompatibilityPath() throws Exception {
        String baseSha = "a".repeat(40);
        String headSha = "b".repeat(40);
        request.commitHash = headSha;
        RecordingService service = service(
                EVcsProvider.GITHUB, baseSha, headSha, "c".repeat(40));

        when(diffPreparation.prepare(
                        eq(project), eq(42L), eq(FULL_DIFF),
                        eq(null), eq(headSha), any()))
                .thenReturn(new PreparedDiff(
                        FULL_DIFF, null, AnalysisMode.FULL, CHANGED_FILES, List.of(),
                        null, headSha));
        when(enrichment.enrichPrFiles(
                        vcsClient, "workspace", "repository", headSha, CHANGED_FILES))
                .thenReturn(enrichedFiles());

        List<AiAnalysisRequest> requests = service.buildAiAnalysisRequests(
                project, request, Optional.empty(), List.of());

        assertThat(requests).singleElement().satisfies(built -> {
            assertThat(built.getPrTitle()).isEqualTo("legacy title");
            assertThat(built.getPrDescription()).isEqualTo("legacy description");
            assertThat(built.getUseMcpTools()).isTrue();
            assertThat(built.getSourceBranchName()).isEqualTo("feature/mutable-name");
            assertThat(built.getTargetBranchName()).isEqualTo("main");
            assertThat(((AiAnalysisRequestImpl) built).getProjectRules())
                    .contains("Mutable rule");
        });
        assertThat(service.mutablePullRequestDiffReads).isEqualTo(1);
        assertThat(service.metadataReads).isZero();
        assertThat(service.exactRangeReads).isZero();
    }

    @Test
    void exactSnapshotRejectsIncrementalReuseAndAlwaysBuildsFullComparison()
            throws Exception {
        String baseSha = "a".repeat(40);
        String headSha = "b".repeat(40);
        String mergeBaseSha = "c".repeat(40);
        request.commitHash = headSha;
        RecordingService service = service(
                EVcsProvider.GITHUB, baseSha, headSha, mergeBaseSha);
        CodeAnalysis previousAnalysis = mock(CodeAnalysis.class);
        when(previousAnalysis.getIssues()).thenReturn(List.of());
        when(diffPreparation.prepare(
                        eq(project), eq(42L), eq(FULL_DIFF),
                        isNull(), eq(headSha), any()))
                .thenReturn(new PreparedDiff(
                        FULL_DIFF,
                        null,
                        AnalysisMode.FULL,
                        CHANGED_FILES,
                        List.of(),
                        null,
                        headSha));
        when(enrichment.fetchFileContentsOnly(
                        vcsClient, "workspace", "repository", headSha, CHANGED_FILES))
                .thenReturn(enrichedFiles());

        AiAnalysisRequest built = buildExactAiAnalysisRequests(
                service, Optional.of(previousAnalysis)).get(0);

        assertThat(snapshotCoordinate(built, "getBaseSha")).isEqualTo(baseSha);
        assertThat(snapshotCoordinate(built, "getHeadSha")).isEqualTo(headSha);
        assertThat(snapshotCoordinate(built, "getMergeBaseSha")).isEqualTo(mergeBaseSha);
        assertThat(built.getPreviousCommitHash()).isEqualTo(baseSha);
        assertThat(built.getCurrentCommitHash()).isEqualTo(headSha);
        assertThat(built.getAnalysisMode()).isEqualTo(AnalysisMode.FULL);
        assertThat(built.getDeltaDiff()).isNull();
        verify(enrichment).fetchFileContentsOnly(
                vcsClient, "workspace", "repository", headSha, CHANGED_FILES);
        verify(enrichment, never()).enrichPrFiles(
                any(), any(), any(), any(), any());
    }

    @Test
    void exactSnapshotRejectsIncompleteOrSubstitutedFileAccounting() throws Exception {
        String baseSha = "a".repeat(40);
        String headSha = "b".repeat(40);
        request.commitHash = headSha;
        RecordingService service = service(
                EVcsProvider.GITHUB, baseSha, headSha, "c".repeat(40));
        when(diffPreparation.prepare(
                        eq(project), eq(42L), eq(FULL_DIFF),
                        isNull(), eq(headSha), any()))
                .thenReturn(new PreparedDiff(
                        FULL_DIFF, null, AnalysisMode.FULL, CHANGED_FILES, List.of(),
                        null, headSha));
        when(enrichment.fetchFileContentsOnly(
                        vcsClient, "workspace", "repository", headSha, CHANGED_FILES))
                .thenReturn(new PrEnrichmentDataDto(
                        List.of(FileContentDto.of("src/Substituted.java", "class Wrong {}")),
                        List.of(),
                        List.of(),
                        completeStats("class Wrong {}")));

        assertThatThrownBy(() -> buildExactAiAnalysisRequests(
                service, Optional.empty()))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("paths");
    }

    @Test
    void exactAcquisitionIsAnExplicitVcsContractNotALegacyDefault() throws Exception {
        Method exactBuilder = VcsAiClientService.class.getMethod(
                "buildExactAiAnalysisRequests",
                Project.class,
                AnalysisProcessRequest.class,
                Optional.class,
                List.class);

        assertThat(exactBuilder.getReturnType()).isEqualTo(List.class);
    }

    @Test
    void exactSnapshotCoordinatesAreFirstClassAiAnalysisRequestContract() throws Exception {
        assertThat(AiAnalysisRequest.class.getMethod("getBaseSha").getReturnType())
                .isEqualTo(String.class);
        assertThat(AiAnalysisRequest.class.getMethod("getHeadSha").getReturnType())
                .isEqualTo(String.class);
        assertThat(AiAnalysisRequest.class.getMethod("getMergeBaseSha").getReturnType())
                .isEqualTo(String.class);
    }

    @SuppressWarnings("unchecked")
    private List<AiAnalysisRequest> buildExactAiAnalysisRequests(
            RecordingService service,
            Optional<CodeAnalysis> previousAnalysis) throws Exception {
        return buildExactAiAnalysisRequests(
                service, previousAnalysis, List.of());
    }

    @SuppressWarnings("unchecked")
    private List<AiAnalysisRequest> buildExactAiAnalysisRequests(
            RecordingService service,
            Optional<CodeAnalysis> previousAnalysis,
            List<CodeAnalysis> allPrAnalyses) throws Exception {
        Method method = service.getClass().getMethod(
                "buildExactAiAnalysisRequests",
                Project.class,
                AnalysisProcessRequest.class,
                Optional.class,
                List.class);
        try {
            return (List<AiAnalysisRequest>) method.invoke(
                    service, project, request, previousAnalysis, allPrAnalyses);
        } catch (InvocationTargetException error) {
            Throwable cause = error.getCause();
            if (cause instanceof RuntimeException runtime) {
                throw runtime;
            }
            if (cause instanceof Error fatal) {
                throw fatal;
            }
            throw error;
        }
    }

    private static CodeAnalysis historicalAnalysis(int version) {
        CodeAnalysis analysis = mock(CodeAnalysis.class);
        when(analysis.getPrVersion()).thenReturn(version);
        return analysis;
    }

    private static CodeAnalysisIssue historicalIssue(
            CodeAnalysis analysis,
            Long id,
            Long trackedFrom,
            boolean resolved,
            String file,
            String title) {
        CodeAnalysisIssue issue = mock(CodeAnalysisIssue.class);
        lenient().when(issue.getId()).thenReturn(id);
        lenient().when(issue.getAnalysis()).thenReturn(analysis);
        lenient().when(issue.getTrackedFromIssueId()).thenReturn(trackedFrom);
        lenient().when(issue.isResolved()).thenReturn(resolved);
        lenient().when(issue.getIssueCategory()).thenReturn(IssueCategory.BUG_RISK);
        lenient().when(issue.getSeverity()).thenReturn(IssueSeverity.HIGH);
        lenient().when(issue.getTitle()).thenReturn(title);
        lenient().when(issue.getReason()).thenReturn(title + " remains relevant");
        lenient().when(issue.getFilePath()).thenReturn(file);
        lenient().when(issue.getLineNumber()).thenReturn(1);
        lenient().when(issue.getCodeSnippet()).thenReturn("risky();");
        lenient().when(issue.getContentFingerprint()).thenReturn(
                "content-" + file + "-" + title);
        return issue;
    }

    private static String snapshotCoordinate(
            AiAnalysisRequest request,
            String accessor) {
        try {
            return (String) request.getClass().getMethod(accessor).invoke(request);
        } catch (ReflectiveOperationException error) {
            throw new AssertionError("missing immutable snapshot accessor " + accessor, error);
        }
    }

    private static PrEnrichmentDataDto enrichedFiles() {
        return enrichedFiles(1);
    }

    private static PrEnrichmentDataDto enrichedFiles(long processingTimeMs) {
        String content = "final class A {}";
        return new PrEnrichmentDataDto(
                List.of(FileContentDto.of("src/A.java", content)),
                List.of(),
                List.of(),
                completeStats(content, processingTimeMs));
    }

    private static PrEnrichmentDataDto deterministicEnrichedFiles(
            boolean reversed,
            long processingTimeMs) {
        FileContentDto first = FileContentDto.skipped(
                "src/A.java", "binary_or_non_text");
        FileContentDto second = FileContentDto.skipped(
                "src/B.java", "fetch_failed");
        List<FileContentDto> files = reversed
                ? List.of(second, first)
                : List.of(first, second);
        Map<String, Integer> skipReasons = new LinkedHashMap<>();
        if (reversed) {
            skipReasons.put("fetch_failed", 1);
            skipReasons.put("binary_or_non_text", 1);
        } else {
            skipReasons.put("binary_or_non_text", 1);
            skipReasons.put("fetch_failed", 1);
        }
        return new PrEnrichmentDataDto(
                files,
                List.of(),
                List.of(),
                new PrEnrichmentDataDto.EnrichmentStats(
                        2,
                        0,
                        2,
                        0,
                        0,
                        processingTimeMs,
                        skipReasons));
    }

    private static PrEnrichmentDataDto.EnrichmentStats completeStats(String content) {
        return completeStats(content, 1);
    }

    private static PrEnrichmentDataDto.EnrichmentStats completeStats(
            String content,
            long processingTimeMs) {
        return new PrEnrichmentDataDto.EnrichmentStats(
                1,
                1,
                0,
                0,
                content.getBytes(java.nio.charset.StandardCharsets.UTF_8).length,
                processingTimeMs,
                java.util.Map.of());
    }

    private RecordingService service(
            EVcsProvider provider,
            String baseSha,
            String headSha,
            String mergeBaseSha) {
        return service(provider, baseSha, headSha, mergeBaseSha, FULL_DIFF);
    }

    private RecordingService service(
            EVcsProvider provider,
            String baseSha,
            String headSha,
            String mergeBaseSha,
            String rangeDiff) {
        lenient().when(connection.getProviderType()).thenReturn(provider);
        RecordingService service = new RecordingService(
                provider, baseSha, headSha, mergeBaseSha, rangeDiff);
        service.setAgenticRepositoryArchiveService(
                agenticRepositoryArchiveService);
        return service;
    }

    private void prepareSuccessfulExactAcquisition(String headSha) {
        lenient().when(diffPreparation.prepare(
                        eq(project),
                        eq(42L),
                        eq(FULL_DIFF),
                        isNull(),
                        eq(headSha),
                        any()))
                .thenReturn(new PreparedDiff(
                        FULL_DIFF,
                        null,
                        AnalysisMode.FULL,
                        CHANGED_FILES,
                        List.of(),
                        null,
                        headSha));
        lenient().when(enrichment.fetchFileContentsOnly(
                        vcsClient,
                        "workspace",
                        "repository",
                        headSha,
                        CHANGED_FILES))
                .thenReturn(enrichedFiles());
    }

    private static CoverageWorkPlan candidateCoverageWorkPlan(
            ImmutableExecutionManifest manifest,
            List<String> changedFiles) {
        List<CoverageAnchor> anchors = changedFiles.stream()
                .map(path -> new CoverageAnchor(
                        PolicyHashing.sha256("anchor:" + path),
                        manifest.executionId(),
                        PolicyHashing.sha256("hunk:" + path),
                        PolicyHashing.sha256("change:" + path),
                        CoverageAnchorKind.TEXT_HUNK,
                        path,
                        path,
                        1,
                        1,
                        1,
                        1,
                        ExactDiffInventory.ChangeStatus.MODIFY,
                        manifest.diffArtifactId(),
                        manifest.diffDigest(),
                        true,
                        CoverageAnchorState.PENDING,
                        null))
                .toList();
        String ledgerIdentity = String.join(
                ":", anchors.stream().map(CoverageAnchor::anchorId).sorted().toList());
        return new CoverageWorkPlan(
                1,
                manifest.executionId(),
                manifest.artifactManifestDigest(),
                manifest.diffDigest(),
                manifest.diffByteLength(),
                PolicyHashing.sha256("ledger:" + ledgerIdentity),
                anchors);
    }

    private static Map<String, Object> incompleteCoverageReceipt(
            CoverageWorkPlan workPlan) {
        List<Map<String, String>> dispositions = workPlan.anchors().stream()
                .map(anchor -> Map.of(
                        "anchorId", anchor.anchorId(),
                        "state", CoverageAnchorState.INCOMPLETE.name(),
                        "reasonCode", "source_unavailable"))
                .toList();
        Map<String, Object> receipt = new LinkedHashMap<>();
        receipt.put("schemaVersion", workPlan.schemaVersion());
        receipt.put("executionId", workPlan.executionId());
        receipt.put("artifactManifestDigest", workPlan.artifactManifestDigest());
        receipt.put("diffDigest", workPlan.diffDigest());
        receipt.put("diffByteLength", workPlan.diffByteLength());
        receipt.put("ledgerDigest", workPlan.ledgerDigest());
        receipt.put("analysisState", "PARTIAL");
        receipt.put("total", workPlan.anchors().size());
        receipt.put("pending", 0);
        receipt.put("ownerPending", 0);
        receipt.put("examined", 0);
        receipt.put("incomplete", workPlan.anchors().size());
        receipt.put("unsupported", 0);
        receipt.put("failed", 0);
        receipt.put("policyExcluded", 0);
        receipt.put("deletedRecorded", 0);
        receipt.put("dispositions", dispositions);
        return receipt;
    }

    private static Stream<Arguments> providerCoordinates() {
        return Stream.of(
                Arguments.of(
                        EVcsProvider.GITHUB, 40,
                        "a".repeat(40), "b".repeat(40), "c".repeat(40)),
                Arguments.of(
                        EVcsProvider.GITLAB, 64,
                        "1".repeat(64), "2".repeat(64), "3".repeat(64)),
                Arguments.of(
                        EVcsProvider.BITBUCKET_CLOUD, 40,
                        "d".repeat(40), "e".repeat(40), "f".repeat(40)));
    }

    private static Stream<EVcsProvider> providers() {
        return Stream.of(
                EVcsProvider.GITHUB,
                EVcsProvider.GITLAB,
                EVcsProvider.BITBUCKET_CLOUD);
    }

    private static Stream<Arguments> invalidCoordinates() {
        String base = "a".repeat(40);
        String head = "b".repeat(40);
        String mergeBase = "c".repeat(40);
        return Stream.of(
                Arguments.of(EVcsProvider.GITHUB, "head", base, null, mergeBase),
                Arguments.of(EVcsProvider.GITHUB, "base", null, head, mergeBase),
                Arguments.of(EVcsProvider.GITLAB, "base", "A".repeat(40), head, mergeBase),
                Arguments.of(EVcsProvider.GITLAB, "merge", base, head, null),
                Arguments.of(EVcsProvider.BITBUCKET_CLOUD, "merge", base, head, "main"),
                Arguments.of(EVcsProvider.GITHUB, "head", base, "b".repeat(39), mergeBase));
    }

    private static final class InMemoryManifestPersistence
            implements ExecutionManifestPersistencePort {
        private final Map<String, PersistedExecution> executions = new HashMap<>();
        private int createOrLoadCalls;

        @Override
        public synchronized PersistedExecution createOrLoad(
                ImmutableExecutionManifest manifest,
                List<ExecutionArtifactPayload> inputArtifacts) {
            createOrLoadCalls++;
            return executions.computeIfAbsent(
                    manifest.executionId(),
                    ignored -> new PersistedExecution(manifest, inputArtifacts));
        }

        @Override
        public synchronized Optional<PersistedExecution> findByExecutionId(String executionId) {
            return Optional.ofNullable(executions.get(executionId));
        }
    }

    private final class RecordingService extends AbstractVcsAiClientService {
        private final EVcsProvider provider;
        private final String baseSha;
        private final String headSha;
        private final String mergeBaseSha;
        private final String rangeDiff;
        private int metadataReads;
        private int mutablePullRequestDiffReads;
        private int exactRangeReads;
        private String rangeBase;
        private String rangeHead;

        private RecordingService(
                EVcsProvider provider,
                String baseSha,
                String headSha,
                String mergeBaseSha,
                String rangeDiff) {
            super(
                    encryption,
                    vcsClients,
                    enrichment,
                    taskContextEnrichment,
                    taskHistoryContext,
                    diffPreparation);
            this.provider = provider;
            this.baseSha = baseSha;
            this.headSha = headSha;
            this.mergeBaseSha = mergeBaseSha;
            this.rangeDiff = rangeDiff;
        }

        @Override
        public EVcsProvider getProvider() {
            return provider;
        }

        @Override
        protected PullRequestMetadata fetchPullRequestMetadata(
                OkHttpClient client,
                RepositoryInfo repository,
                long pullRequestId) {
            metadataReads++;
            return pullRequestMetadata(
                    "title", "description", baseSha, headSha, mergeBaseSha);
        }

        /**
         * The legacy hook includes a mutable PR diff. It remains implemented only
         * so this RED contract proves the immutable path never invokes it.
         */
        @Override
        protected PullRequestData fetchPullRequest(
                OkHttpClient client,
                RepositoryInfo repository,
                long pullRequestId) {
            mutablePullRequestDiffReads++;
            return pullRequestData("legacy title", "legacy description", FULL_DIFF, baseSha);
        }

        @Override
        protected String fetchCommitRangeDiff(
                OkHttpClient client,
                RepositoryInfo repository,
                String baseCommit,
                String headCommit) {
            exactRangeReads++;
            rangeBase = baseCommit;
            rangeHead = headCommit;
            return rangeDiff;
        }
    }
}
