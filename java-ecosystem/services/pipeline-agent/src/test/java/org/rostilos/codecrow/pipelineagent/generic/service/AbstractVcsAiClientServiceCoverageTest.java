package org.rostilos.codecrow.pipelineagent.generic.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.io.IOException;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import okhttp3.OkHttpClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiRequestPreviousIssueDTO;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.FileContentDto;
import org.rostilos.codecrow.analysisengine.dto.request.ai.enrichment.PrEnrichmentDataDto;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.service.pr.PrFileEnrichmentService;
import org.rostilos.codecrow.analysisengine.service.pr.PullRequestDiffPreparationService;
import org.rostilos.codecrow.analysisengine.service.pr.PullRequestDiffPreparationService.CommitRangeDiffFetcher;
import org.rostilos.codecrow.analysisengine.service.pr.PullRequestDiffPreparationService.PreparedDiff;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisMode;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.codeanalysis.CodeAnalysis;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.ProjectAiConnectionBinding;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.model.vcs.config.cloud.BitbucketCloudConfig;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.pipelineagent.generic.service.AbstractVcsAiClientService.PullRequestData;
import org.rostilos.codecrow.pipelineagent.generic.service.AbstractVcsAiClientService.PullRequestMetadata;
import org.rostilos.codecrow.pipelineagent.generic.service.AbstractVcsAiClientService.RepositoryInfo;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;

@ExtendWith(MockitoExtension.class)
class AbstractVcsAiClientServiceCoverageTest {
    private static final String BASE = "a".repeat(40);
    private static final String HEAD = "b".repeat(40);
    private static final String MERGE_BASE = "c".repeat(40);
    private static final String FULL_DIFF =
            "diff --git a/src/A.java b/src/A.java\n@@ -1 +1 @@\n-old\n+new\n";
    private static final List<String> CHANGED_FILES = List.of("src/A.java");

    @Mock private TokenEncryptionService encryption;
    @Mock private VcsClientProvider vcsClients;
    @Mock private PullRequestDiffPreparationService diffPreparation;
    @Mock private PrFileEnrichmentService enrichment;
    @Mock private TaskContextEnrichmentService taskContext;
    @Mock private TaskHistoryContextService taskHistory;
    @Mock private VcsClient vcsClient;
    @Mock private Project project;
    @Mock private VcsRepoInfo repoInfo;
    @Mock private VcsConnection connection;
    @Mock private ProjectAiConnectionBinding aiBinding;
    @Mock private AIConnection aiConnection;
    @Mock private Workspace workspace;

    @BeforeEach
    void setUp() throws Exception {
        lenient().when(project.getId()).thenReturn(1L);
        lenient().when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
        lenient().when(repoInfo.getVcsConnection()).thenReturn(connection);
        lenient().when(repoInfo.getRepoWorkspace()).thenReturn("workspace");
        lenient().when(repoInfo.getRepoSlug()).thenReturn("repository");
        lenient().when(connection.getProviderType()).thenReturn(EVcsProvider.GITHUB);
        lenient().when(connection.getConnectionType()).thenReturn(EVcsConnectionType.APP);
        lenient().when(connection.getAccessToken()).thenReturn("encrypted-vcs");
        lenient().when(project.getAiBinding()).thenReturn(aiBinding);
        lenient().when(aiBinding.getAiConnection()).thenReturn(aiConnection);
        lenient().when(aiConnection.getProviderKey()).thenReturn(AIProviderKey.OPENAI);
        lenient().when(aiConnection.getAiModel()).thenReturn("fixture-v1");
        lenient().when(aiConnection.getApiKeyEncrypted()).thenReturn("encrypted-ai");
        lenient().when(encryption.decrypt("encrypted-ai")).thenReturn("decrypted-ai");
        lenient().when(encryption.decrypt("encrypted-vcs")).thenReturn("decrypted-vcs");
        lenient().when(project.getEffectiveConfig()).thenReturn(new ProjectConfig());
        lenient().when(project.getWorkspace()).thenReturn(workspace);
        lenient().when(workspace.getName()).thenReturn("tenant");
        lenient().when(project.getNamespace()).thenReturn("namespace");
        lenient().when(vcsClients.getHttpClient(connection)).thenReturn(mock(OkHttpClient.class));
        lenient().when(vcsClients.getClient(connection)).thenReturn(vcsClient);
    }

    @Test
    void branchAndReconciliationEntryPointsPreserveEveryOptionalInputShape() throws Exception {
        ConfigurableService service = service(null, null, null);
        BranchProcessRequest branch = branchRequest();

        AiAnalysisRequest ordinary = service.buildAiAnalysisRequests(
                project, branch, Optional.empty()).get(0);

        AiRequestPreviousIssueDTO issue = new AiRequestPreviousIssueDTO(
                "1", "quality", "high", "title", "reason", null, null,
                "src/A.java", 1, "main", null, "open", "CODE_QUALITY",
                null, null, null, null, "line");
        AiAnalysisRequest reconciliation = service
                .buildAiAnalysisRequestsForBranchReconciliation(
                        project, branch, List.of(issue), Map.of("src/A.java", "class A {}"))
                .get(0);
        AiAnalysisRequest emptyOptionals = service
                .buildAiAnalysisRequestsForBranchReconciliation(
                        project, branch, List.of(), Map.of(), " ")
                .get(0);
        AiAnalysisRequest relevantDiff = service
                .buildAiAnalysisRequestsForBranchReconciliation(
                        project, branch, null, null, FULL_DIFF)
                .get(0);

        assertThat(ordinary.getTargetBranchName()).isEqualTo("main");
        assertThat(ordinary.getCurrentCommitHash()).isEqualTo(HEAD);
        assertThat(reconciliation.getPreviousCodeAnalysisIssues()).containsExactly(issue);
        assertThat(reconciliation.getReconciliationFileContents())
                .containsEntry("src/A.java", "class A {}");
        assertThat(emptyOptionals.getPreviousCodeAnalysisIssues()).isNull();
        assertThat(emptyOptionals.getReconciliationFileContents()).isNull();
        assertThat(emptyOptionals.getRawDiff()).isNull();
        assertThat(relevantDiff.getRawDiff()).isEqualTo(FULL_DIFF);
    }

    @Test
    void exactBuilderRejectsNonReviewRequestsBeforeAnyProviderRead() {
        ConfigurableService service = service(enrichment, null, null);

        assertThatThrownBy(() -> service.buildExactAiAnalysisRequests(
                project, branchRequest(), Optional.empty(), List.of()))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("pull-request review");

        assertThat(service.metadataReads).isZero();
        assertThat(service.rangeReads).isZero();
    }

    @Test
    void directPushNormalizesNullInputsAndPreservesPopulatedInputs() throws Exception {
        ConfigurableService service = service(null, null, null);
        BranchProcessRequest branch = branchRequest();

        AiAnalysisRequest empty = service.buildDirectPushAnalysisRequests(
                project, branch, null, Map.of(), null).get(0);
        AiAnalysisRequest populated = service.buildDirectPushAnalysisRequests(
                project, branch, FULL_DIFF, Map.of("src/A.java", "class A {}"), CHANGED_FILES)
                .get(0);

        assertThat(empty.getChangedFiles()).isEmpty();
        assertThat(empty.getDeletedFiles()).isEmpty();
        assertThat(empty.getDiffSnippets()).isEmpty();
        assertThat(empty.getRawDiff()).isNull();
        assertThat(populated.getChangedFiles()).containsExactly("src/A.java");
        assertThat(populated.getRawDiff()).isEqualTo(FULL_DIFF);
        assertThat(populated.getAnalysisMode()).isEqualTo(AnalysisMode.FULL);
    }

    @Test
    void legacyAndExactPreparationCallbacksKeepBaseToHeadDirection() throws Exception {
        CodeAnalysis previous = mock(CodeAnalysis.class);
        when(previous.getCommitHash()).thenReturn(BASE);
        ConfigurableService legacy = service(null, null, null);
        when(diffPreparation.prepare(
                        eq(project), eq(42L), eq(FULL_DIFF), eq(BASE), eq(HEAD), any()))
                .thenAnswer(invocation -> {
                    CommitRangeDiffFetcher fetcher = invocation.getArgument(5);
                    assertThat(fetcher.fetch(BASE, HEAD)).isEqualTo(FULL_DIFF);
                    return prepared(CHANGED_FILES);
                });

        assertThat(legacy.buildAiAnalysisRequests(
                project, prRequest(), Optional.of(previous), List.of())).hasSize(1);
        assertThat(legacy.rangeBase).isEqualTo(BASE);
        assertThat(legacy.rangeHead).isEqualTo(HEAD);

        ConfigurableService exact = service(enrichment, null, null);
        when(diffPreparation.prepare(
                        eq(project), eq(42L), eq(FULL_DIFF), isNull(), eq(HEAD), any()))
                .thenAnswer(invocation -> {
                    CommitRangeDiffFetcher fetcher = invocation.getArgument(5);
                    assertThat(fetcher.fetch(BASE, HEAD)).isEqualTo(FULL_DIFF);
                    return prepared(CHANGED_FILES);
                });
        when(enrichment.fetchFileContentsOnly(
                vcsClient, "workspace", "repository", HEAD, CHANGED_FILES))
                .thenReturn(validEnrichment());

        assertThat(exact.buildExactAiAnalysisRequests(
                project, prRequest(), Optional.empty(), List.of())).hasSize(1);
        assertThat(exact.rangeReads).isEqualTo(2);
        assertThat(exact.rangeBase).isEqualTo(BASE);
        assertThat(exact.rangeHead).isEqualTo(HEAD);
    }

    @Test
    void legacyPullRequestFailureAndIncrementalSelectionCoverBothPreparationOutcomes()
            throws Exception {
        ConfigurableService unavailable = service(null, null, null);
        unavailable.pullRequestFailure = new IOException("pull request unavailable");

        assertThat(unavailable.buildAiAnalysisRequests(
                project, prRequest(), Optional.empty(), List.of())).isEmpty();

        CodeAnalysis previous = mock(CodeAnalysis.class);
        when(previous.getCommitHash()).thenReturn(BASE);
        when(diffPreparation.prepare(
                        eq(project), eq(42L), eq(FULL_DIFF), eq(BASE), eq(HEAD), any()))
                .thenReturn(new PreparedDiff(
                        FULL_DIFF,
                        "diff --git a/src/A.java b/src/A.java\n@@ -2 +2 @@\n-before\n+after\n",
                        AnalysisMode.INCREMENTAL,
                        CHANGED_FILES,
                        List.of(),
                        BASE,
                        HEAD));

        AiAnalysisRequest incremental = service(null, null, null)
                .buildAiAnalysisRequests(
                        project, prRequest(), Optional.of(previous), List.of())
                .get(0);

        assertThat(incremental.getAnalysisMode()).isEqualTo(AnalysisMode.INCREMENTAL);
        assertThat(incremental.getPreviousCommitHash()).isEqualTo(BASE);
        assertThat(incremental.getDeltaDiff()).contains("before");
    }

    @Test
    void metadataAndRangeFailuresAreReportedAtTheExactAcquisitionBoundary() {
        DefaultMetadataService unsupported = new DefaultMetadataService();
        assertExactFailure(unsupported, "metadata");

        ConfigurableService missingMetadata = service(enrichment, null, null);
        missingMetadata.metadata = null;
        assertExactFailure(missingMetadata, "metadata");

        ConfigurableService metadataIoFailure = service(enrichment, null, null);
        metadataIoFailure.metadataFailure = new IOException("metadata unavailable");
        assertExactFailure(metadataIoFailure, "metadata");

        ConfigurableService missingDiff = service(enrichment, null, null);
        missingDiff.rangeDiff = null;
        assertExactFailure(missingDiff, "diff");

        ConfigurableService diffIoFailure = service(enrichment, null, null);
        diffIoFailure.rangeFailure = new IOException("range unavailable");
        assertExactFailure(diffIoFailure, "diff");
    }

    @Test
    void exactAcquisitionRejectsHeadDriftAndKeepsAuthoritativeTypedInventory()
            throws Exception {
        ConfigurableService drifted = service(enrichment, null, null);
        drifted.metadata = new PullRequestMetadata(
                "title", "description", BASE, "d".repeat(40), MERGE_BASE);

        assertThatThrownBy(() -> drifted.buildExactAiAnalysisRequests(
                project, prRequest(), Optional.empty(), List.of()))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("webhook head");

        ConfigurableService emptyScope = service(enrichment, null, null);
        when(diffPreparation.prepare(
                        eq(project), eq(42L), eq(FULL_DIFF), isNull(), eq(HEAD), any()))
                .thenReturn(PreparedDiff.empty(null, HEAD));
        when(enrichment.fetchFileContentsOnly(
                        vcsClient,
                        "workspace",
                        "repository",
                        HEAD,
                        CHANGED_FILES))
                .thenReturn(validEnrichment());

        AiAnalysisRequest request = emptyScope.buildExactAiAnalysisRequests(
                project, prRequest(), Optional.empty(), List.of()).get(0);

        assertThat(request.getChangedFiles()).isEqualTo(CHANGED_FILES);
        assertThat(request.getRawDiff()).isEqualTo(FULL_DIFF);
    }

    @Test
    void exactHeadAdmissionRunsAfterLiveEqualityAndBeforeSlowSnapshotReads() throws Exception {
        stubExactPreparation(CHANGED_FILES);
        when(enrichment.fetchFileContentsOnly(
                vcsClient, "workspace", "repository", HEAD, CHANGED_FILES))
                .thenReturn(validEnrichment());
        ConfigurableService current = service(enrichment, null, null);
        List<String> admitted = new ArrayList<>();

        assertThat(current.buildExactAiAnalysisRequests(
                project,
                prRequest(),
                Optional.empty(),
                List.of(),
                verifiedHead -> {
                    assertThat(current.metadataReads).isOne();
                    assertThat(current.rangeReads).isZero();
                    admitted.add(verifiedHead);
                })).hasSize(1);

        assertThat(admitted).containsExactly(HEAD);
        assertThat(current.rangeReads).isOne();

        ConfigurableService drifted = service(enrichment, null, null);
        drifted.metadata = new PullRequestMetadata(
                "title", "description", BASE, "d".repeat(40), MERGE_BASE);
        List<String> driftAdmissions = new ArrayList<>();

        assertThatThrownBy(() -> drifted.buildExactAiAnalysisRequests(
                project,
                prRequest(),
                Optional.empty(),
                List.of(),
                driftAdmissions::add))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("webhook head");
        assertThat(driftAdmissions).isEmpty();
        assertThat(drifted.rangeReads).isZero();

        ConfigurableService superseded = service(enrichment, null, null);
        assertThatThrownBy(() -> superseded.buildExactAiAnalysisRequests(
                project,
                prRequest(),
                Optional.empty(),
                List.of(),
                ignored -> {
                    throw new IllegalStateException("superseded at admission");
                }))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("superseded at admission");
        assertThat(superseded.rangeReads).isZero();
    }

    @Test
    void exactFileAcquisitionRejectsMissingServiceAndMissingResult() {
        stubExactPreparation(CHANGED_FILES);
        ConfigurableService missingService = service(null, null, null);

        assertThatThrownBy(() -> missingService.buildExactAiAnalysisRequests(
                project, prRequest(), Optional.empty(), List.of()))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("file acquisition is unavailable");

        PrFileEnrichmentService missingResult = mock(PrFileEnrichmentService.class);
        ConfigurableService noResult = service(missingResult, null, null);
        when(missingResult.fetchFileContentsOnly(
                vcsClient, "workspace", "repository", HEAD, CHANGED_FILES))
                .thenReturn(null);

        assertThatThrownBy(() -> noResult.buildExactAiAnalysisRequests(
                project, prRequest(), Optional.empty(), List.of()))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("no result");
    }

    @Test
    void bestEffortEnrichmentCoversDisabledSuccessfulAndFailureFallbacks() {
        RepositoryInfo repository = repository();

        assertThat(invokeEnrich(service(null, null, null), repository, CHANGED_FILES))
                .isEqualTo(PrEnrichmentDataDto.empty());
        assertThat(invokeEnrich(service(enrichment, null, null), repository, null))
                .isEqualTo(PrEnrichmentDataDto.empty());
        assertThat(invokeEnrich(service(enrichment, null, null), repository, List.of()))
                .isEqualTo(PrEnrichmentDataDto.empty());
        assertThat(invokeExactEnrich(service(enrichment, null, null), repository, null))
                .isEqualTo(PrEnrichmentDataDto.empty());
        assertThat(invokeExactEnrich(service(enrichment, null, null), repository, List.of()))
                .isEqualTo(PrEnrichmentDataDto.empty());

        VcsClientProvider failingProvider = mock(VcsClientProvider.class);
        when(failingProvider.getClient(connection)).thenThrow(new IllegalStateException("offline"));
        assertThat(invokeEnrich(
                service(failingProvider, enrichment, null, null), repository, CHANGED_FILES))
                .isEqualTo(PrEnrichmentDataDto.empty());

        PrFileEnrichmentService disabled = mock(PrFileEnrichmentService.class);
        when(disabled.isEnrichmentEnabled()).thenReturn(false);
        when(disabled.fetchFileContentsOnly(
                vcsClient, "workspace", "repository", HEAD, CHANGED_FILES))
                .thenReturn(validEnrichment());
        assertThat(invokeEnrich(service(disabled, null, null), repository, CHANGED_FILES))
                .isEqualTo(validEnrichment());
        verify(disabled, never()).enrichPrFiles(any(), any(), any(), any(), any());

        PrFileEnrichmentService complete = mock(PrFileEnrichmentService.class);
        when(complete.isEnrichmentEnabled()).thenReturn(true);
        when(complete.enrichPrFiles(
                vcsClient, "workspace", "repository", HEAD, CHANGED_FILES))
                .thenReturn(validEnrichment());
        assertThat(invokeEnrich(service(complete, null, null), repository, CHANGED_FILES))
                .isEqualTo(validEnrichment());
        verify(complete, never()).fetchFileContentsOnly(any(), any(), any(), any(), any());

        PrFileEnrichmentService parserFailure = mock(PrFileEnrichmentService.class);
        when(parserFailure.isEnrichmentEnabled()).thenReturn(true);
        when(parserFailure.enrichPrFiles(
                vcsClient, "workspace", "repository", HEAD, CHANGED_FILES))
                .thenThrow(new IllegalStateException("parser offline"));
        when(parserFailure.fetchFileContentsOnly(
                vcsClient, "workspace", "repository", HEAD, CHANGED_FILES))
                .thenReturn(validEnrichment());
        assertThat(invokeEnrich(service(parserFailure, null, null), repository, CHANGED_FILES))
                .isEqualTo(validEnrichment());

        PrFileEnrichmentService fetchFailure = mock(PrFileEnrichmentService.class);
        when(fetchFailure.isEnrichmentEnabled()).thenReturn(true);
        when(fetchFailure.enrichPrFiles(
                vcsClient, "workspace", "repository", HEAD, CHANGED_FILES))
                .thenReturn(PrEnrichmentDataDto.empty());
        when(fetchFailure.fetchFileContentsOnly(
                vcsClient, "workspace", "repository", HEAD, CHANGED_FILES))
                .thenThrow(new IllegalStateException("fetch offline"));
        assertThat(invokeEnrich(service(fetchFailure, null, null), repository, CHANGED_FILES))
                .isEqualTo(PrEnrichmentDataDto.empty());
    }

    @Test
    void taskContextAndHistoryRemainOptionalAndComposeWhenBothAreAvailable() throws Exception {
        when(diffPreparation.prepare(
                        eq(project), eq(42L), eq(FULL_DIFF), isNull(), eq(HEAD), any()))
                .thenReturn(prepared(CHANGED_FILES));
        when(taskContext.resolveTaskContext(
                project, "feature", "title", "description"))
                .thenReturn(Map.of("taskKey", "TASK-1"));
        when(taskContext.resolveTaskKey(
                project, "feature", "title", "description"))
                .thenReturn(Optional.of("TASK-1"));
        when(taskHistory.buildTaskHistoryContext(
                1L, 42L, Map.of("taskKey", "TASK-1"), "TASK-1"))
                .thenReturn("bounded history");

        AiAnalysisRequest complete = service(null, taskContext, taskHistory)
                .buildAiAnalysisRequests(project, prRequest(), Optional.empty(), List.of())
                .get(0);
        AiAnalysisRequest historyWithoutResolver = service(null, null, taskHistory)
                .buildAiAnalysisRequests(project, prRequest(), Optional.empty(), List.of())
                .get(0);
        AiAnalysisRequest neither = service(null, null, null)
                .buildAiAnalysisRequests(project, prRequest(), Optional.empty(), List.of())
                .get(0);

        assertThat(complete.getTaskContext()).containsEntry("taskKey", "TASK-1");
        assertThat(complete.getTaskHistoryContext()).isEqualTo("bounded history");
        assertThat(historyWithoutResolver.getTaskContext()).isEmpty();
        assertThat(historyWithoutResolver.getTaskHistoryContext()).isEmpty();
        assertThat(neither.getTaskContext()).isEmpty();
        assertThat(neither.getTaskHistoryContext()).isEmpty();
    }

    @Test
    void missingRepositoryConfigurationFailsBeforeRequestConstruction() {
        ConfigurableService service = service(null, null, null);
        Project missingRepository = mock(Project.class);
        when(missingRepository.getId()).thenReturn(9L);
        when(missingRepository.getEffectiveVcsRepoInfo()).thenReturn(null);

        assertThatThrownBy(() -> service.buildAiAnalysisRequests(
                missingRepository, branchRequest(), Optional.empty()))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("No VCS connection");

        Project missingConnection = mock(Project.class);
        VcsRepoInfo incomplete = mock(VcsRepoInfo.class);
        when(missingConnection.getId()).thenReturn(10L);
        when(missingConnection.getEffectiveVcsRepoInfo()).thenReturn(incomplete);
        when(incomplete.getVcsConnection()).thenReturn(null);

        assertThatThrownBy(() -> service.buildAiAnalysisRequests(
                missingConnection, branchRequest(), Optional.empty()))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("No VCS connection");
    }

    @Test
    void oauthAndMissingCredentialBranchesAreExplicit() throws Exception {
        when(connection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
        when(connection.getConnectionType()).thenReturn(EVcsConnectionType.OAUTH_MANUAL);
        when(connection.getConfiguration()).thenReturn(
                new BitbucketCloudConfig("encrypted-client", "encrypted-secret", "workspace"));
        when(encryption.decrypt("encrypted-client")).thenReturn("oauth-client");
        when(encryption.decrypt("encrypted-secret")).thenReturn("oauth-secret");

        AiAnalysisRequest oauth = service(null, null, null)
                .buildAiAnalysisRequests(project, branchRequest(), Optional.empty()).get(0);

        assertThat(oauth.getOAuthClient()).isEqualTo("oauth-client");
        assertThat(oauth.getOAuthSecret()).isEqualTo("oauth-secret");
        assertThat(oauth.getAccessToken()).isNull();

        when(connection.getProviderType()).thenReturn(EVcsProvider.GITHUB);
        AiAnalysisRequest missing = service(null, null, null)
                .buildAiAnalysisRequests(project, branchRequest(), Optional.empty()).get(0);

        assertThat(missing.getOAuthClient()).isNull();
        assertThat(missing.getOAuthSecret()).isNull();
        assertThat(missing.getAccessToken()).isNull();
    }

    @Test
    void exactFileAccountingRejectsEveryMalformedInventoryAndStatistic() {
        String content = "é";
        FileContentDto valid = FileContentDto.of("src/A.java", content);
        FileContentDto skipped = FileContentDto.skipped("src/B.java", "binary");
        PrEnrichmentDataDto validOne = enrichment(
                List.of(valid), stats(1, 1, 0, contentBytes(content)));
        PrEnrichmentDataDto validTwo = enrichment(
                List.of(valid, skipped), stats(2, 1, 1, contentBytes(content)));

        requireAccounting(validOne, CHANGED_FILES);
        requireAccounting(validTwo, List.of("src/A.java", "src/B.java"));

        assertAccountingRejected(null, CHANGED_FILES, "complete accounting");
        assertAccountingRejected(validOne, null, "complete accounting");
        assertAccountingRejected(validOne, Collections.singletonList(null), "inventory is invalid");
        assertAccountingRejected(validOne, List.of(" "), "inventory is invalid");
        assertAccountingRejected(validOne, List.of("src/A.java\0suffix"), "inventory is invalid");
        assertAccountingRejected(validOne, List.of("src/A.java", "src/A.java"), "inventory is invalid");

        assertAccountingRejected(
                enrichment(null, stats(1, 1, 0, contentBytes(content))),
                CHANGED_FILES,
                "complete accounting");
        assertAccountingRejected(
                enrichment(List.of(), stats(1, 0, 0, 0)),
                CHANGED_FILES,
                "complete accounting");
        assertAccountingRejected(
                enrichment(Collections.singletonList(null), stats(1, 0, 0, 0)),
                CHANGED_FILES,
                "conflicting paths");
        assertAccountingRejected(
                enrichment(
                        List.of(new FileContentDto(null, "x", 1, false, null)),
                        stats(1, 1, 0, 1)),
                CHANGED_FILES,
                "conflicting paths");
        assertAccountingRejected(
                enrichment(List.of(FileContentDto.of("src/Other.java", "x")),
                        stats(1, 1, 0, 1)),
                CHANGED_FILES,
                "conflicting paths");
        assertAccountingRejected(
                enrichment(
                        List.of(FileContentDto.of("src/A.java", "x"),
                                FileContentDto.of("src/A.java", "x")),
                        stats(2, 2, 0, 2)),
                List.of("src/A.java", "src/B.java"),
                "conflicting paths");

        assertAccountingRejected(
                enrichment(
                        List.of(new FileContentDto("src/A.java", "x", 1, true, "reason")),
                        stats(1, 0, 1, 0)),
                CHANGED_FILES,
                "gap reason");
        assertAccountingRejected(
                enrichment(List.of(FileContentDto.skipped("src/A.java", null)),
                        stats(1, 0, 1, 0)),
                CHANGED_FILES,
                "gap reason");
        assertAccountingRejected(
                enrichment(List.of(FileContentDto.skipped("src/A.java", " ")),
                        stats(1, 0, 1, 0)),
                CHANGED_FILES,
                "gap reason");
        assertAccountingRejected(
                enrichment(
                        List.of(new FileContentDto("src/A.java", null, 0, false, null)),
                        stats(1, 1, 0, 0)),
                CHANGED_FILES,
                "byte length");
        assertAccountingRejected(
                enrichment(
                        List.of(new FileContentDto("src/A.java", content, 1, false, null)),
                        stats(1, 1, 0, 1)),
                CHANGED_FILES,
                "byte length");

        assertAccountingRejected(enrichment(List.of(valid), null), CHANGED_FILES, "complete accounting");
        assertAccountingRejected(
                enrichment(List.of(valid), stats(2, 1, 0, contentBytes(content))),
                CHANGED_FILES,
                "complete accounting");
        assertAccountingRejected(
                enrichment(List.of(valid), stats(1, 0, 0, contentBytes(content))),
                CHANGED_FILES,
                "complete accounting");
        assertAccountingRejected(
                enrichment(List.of(valid), stats(1, 1, 1, contentBytes(content))),
                CHANGED_FILES,
                "complete accounting");
        assertAccountingRejected(
                enrichment(List.of(valid), stats(1, 1, 0, 1)),
                CHANGED_FILES,
                "complete accounting");
    }

    @Test
    void canonicalExactFilesSortPathsAndNormalizeNullSkipReasonsAndTiming() {
        PrEnrichmentDataDto acquired = new PrEnrichmentDataDto(
                List.of(
                        FileContentDto.skipped("src/B.java", "binary"),
                        FileContentDto.skipped("src/A.java", "binary")),
                List.of(),
                List.of(),
                new PrEnrichmentDataDto.EnrichmentStats(2, 0, 2, 9, 0, 123, null));

        PrEnrichmentDataDto canonical = canonicalize(acquired);

        assertThat(canonical.fileContents())
                .extracting(FileContentDto::path)
                .containsExactly("src/A.java", "src/B.java");
        assertThat(canonical.stats().relationshipsFound()).isZero();
        assertThat(canonical.stats().processingTimeMs()).isZero();
        assertThat(canonical.stats().skipReasons()).isEmpty();
    }

    @Test
    void exactRevisionProviderAliasAndPullRequestFactoryCoverBoundaryShapes() throws Exception {
        assertThatThrownBy(() -> invokePrivate(
                null,
                "requireExactRevision",
                new Class<?>[]{String.class, String.class},
                null,
                "head"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("exact lowercase SHA");
        assertThatThrownBy(() -> invokePrivate(
                null,
                "requireExactRevision",
                new Class<?>[]{String.class, String.class},
                "A".repeat(40),
                "head"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("exact lowercase SHA");

        ConfigurableService bitbucketCloud = new ConfigurableService(
                vcsClients, null, null, null, EVcsProvider.BITBUCKET_CLOUD);
        AiAnalysisRequest request = bitbucketCloud.buildAiAnalysisRequests(
                project, branchRequest(), Optional.empty()).get(0);
        assertThat(request.getVcsProvider()).isEqualTo("bitbucket_cloud");

        PullRequestData minimal = bitbucketCloud.pullRequestData(
                "title", "description", FULL_DIFF);
        assertThat(minimal.baseRevision()).isNull();
    }

    private void assertExactFailure(AbstractVcsAiClientService service, String message) {
        assertThatThrownBy(() -> service.buildExactAiAnalysisRequests(
                project, prRequest(), Optional.empty(), List.of()))
                .isInstanceOf(RuntimeException.class)
                .hasMessageContaining(message);
    }

    private PrEnrichmentDataDto invokeEnrich(
            AbstractVcsAiClientService service,
            RepositoryInfo repository,
            List<String> changedFiles) {
        return (PrEnrichmentDataDto) invokePrivate(
                service,
                "enrichFiles",
                new Class<?>[]{RepositoryInfo.class, String.class, List.class, String.class},
                repository, HEAD, changedFiles, "coverage probe");
    }

    private PrEnrichmentDataDto invokeExactEnrich(
            AbstractVcsAiClientService service,
            RepositoryInfo repository,
            List<String> changedFiles) {
        return (PrEnrichmentDataDto) invokePrivate(
                service,
                "enrichPullRequestFiles",
                new Class<?>[]{RepositoryInfo.class, String.class, List.class},
                repository, HEAD, changedFiles);
    }

    private static void requireAccounting(
            PrEnrichmentDataDto enrichment,
            List<String> changedFiles) {
        invokePrivate(
                null,
                "requireCompleteExactFileAccounting",
                new Class<?>[]{PrEnrichmentDataDto.class, List.class},
                enrichment, changedFiles);
    }

    private static void assertAccountingRejected(
            PrEnrichmentDataDto enrichment,
            List<String> changedFiles,
            String message) {
        assertThatThrownBy(() -> requireAccounting(enrichment, changedFiles))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining(message);
    }

    private static PrEnrichmentDataDto canonicalize(PrEnrichmentDataDto acquired) {
        return (PrEnrichmentDataDto) invokePrivate(
                null,
                "canonicalExactFileContents",
                new Class<?>[]{PrEnrichmentDataDto.class},
                acquired);
    }

    private static Object invokePrivate(
            Object target,
            String methodName,
            Class<?>[] parameterTypes,
            Object... arguments) {
        try {
            Method method = AbstractVcsAiClientService.class.getDeclaredMethod(
                    methodName, parameterTypes);
            method.setAccessible(true);
            return method.invoke(target, arguments);
        } catch (InvocationTargetException error) {
            if (error.getCause() instanceof RuntimeException runtime) {
                throw runtime;
            }
            if (error.getCause() instanceof Error fatal) {
                throw fatal;
            }
            throw new AssertionError(error.getCause());
        } catch (ReflectiveOperationException error) {
            throw new AssertionError(error);
        }
    }

    private ConfigurableService service(
            PrFileEnrichmentService fileEnrichment,
            TaskContextEnrichmentService context,
            TaskHistoryContextService history) {
        return service(vcsClients, fileEnrichment, context, history);
    }

    private ConfigurableService service(
            VcsClientProvider clients,
            PrFileEnrichmentService fileEnrichment,
            TaskContextEnrichmentService context,
            TaskHistoryContextService history) {
        return new ConfigurableService(
                clients, fileEnrichment, context, history, EVcsProvider.GITHUB);
    }

    private RepositoryInfo repository() {
        return new RepositoryInfo(connection, "workspace", "repository");
    }

    private BranchProcessRequest branchRequest() {
        BranchProcessRequest request = new BranchProcessRequest();
        request.projectId = 1L;
        request.targetBranchName = "main";
        request.commitHash = HEAD;
        request.analysisType = AnalysisType.BRANCH_ANALYSIS;
        return request;
    }

    private PrProcessRequest prRequest() {
        PrProcessRequest request = new PrProcessRequest();
        request.projectId = 1L;
        request.pullRequestId = 42L;
        request.sourceBranchName = "feature";
        request.targetBranchName = "main";
        request.commitHash = HEAD;
        request.analysisType = AnalysisType.PR_REVIEW;
        return request;
    }

    private void stubExactPreparation(List<String> changedFiles) {
        when(diffPreparation.prepare(
                        eq(project), eq(42L), eq(FULL_DIFF), isNull(), eq(HEAD), any()))
                .thenReturn(prepared(changedFiles));
    }

    private static PreparedDiff prepared(List<String> changedFiles) {
        return new PreparedDiff(
                FULL_DIFF, null, AnalysisMode.FULL, changedFiles, List.of(), null, HEAD);
    }

    private static PrEnrichmentDataDto validEnrichment() {
        String content = "class A {}";
        return enrichment(
                List.of(FileContentDto.of("src/A.java", content)),
                stats(1, 1, 0, contentBytes(content)));
    }

    private static PrEnrichmentDataDto enrichment(
            List<FileContentDto> files,
            PrEnrichmentDataDto.EnrichmentStats stats) {
        return new PrEnrichmentDataDto(files, List.of(), List.of(), stats);
    }

    private static PrEnrichmentDataDto.EnrichmentStats stats(
            int total,
            int enriched,
            int skipped,
            long bytes) {
        return new PrEnrichmentDataDto.EnrichmentStats(
                total, enriched, skipped, 0, bytes, 1, Map.of());
    }

    private static long contentBytes(String content) {
        return content.getBytes(StandardCharsets.UTF_8).length;
    }

    private final class ConfigurableService extends AbstractVcsAiClientService {
        private final EVcsProvider provider;
        private PullRequestData pullRequest = pullRequestData(
                "title", "description", FULL_DIFF, BASE);
        private PullRequestMetadata metadata = pullRequestMetadata(
                "title", "description", BASE, HEAD, MERGE_BASE);
        private IOException pullRequestFailure;
        private IOException metadataFailure;
        private IOException rangeFailure;
        private String rangeDiff = FULL_DIFF;
        private int metadataReads;
        private int rangeReads;
        private String rangeBase;
        private String rangeHead;

        private ConfigurableService(
                VcsClientProvider clients,
                PrFileEnrichmentService fileEnrichment,
                TaskContextEnrichmentService context,
                TaskHistoryContextService history,
                EVcsProvider provider) {
            super(encryption, clients, fileEnrichment, context, history, diffPreparation);
            this.provider = provider;
        }

        @Override
        public EVcsProvider getProvider() {
            return provider;
        }

        @Override
        protected PullRequestData fetchPullRequest(
                OkHttpClient client,
                RepositoryInfo repository,
                long pullRequestId) throws IOException {
            if (pullRequestFailure != null) {
                throw pullRequestFailure;
            }
            return pullRequest;
        }

        @Override
        protected PullRequestMetadata fetchPullRequestMetadata(
                OkHttpClient client,
                RepositoryInfo repository,
                long pullRequestId) throws IOException {
            metadataReads++;
            if (metadataFailure != null) {
                throw metadataFailure;
            }
            return metadata;
        }

        @Override
        protected String fetchCommitRangeDiff(
                OkHttpClient client,
                RepositoryInfo repository,
                String baseCommit,
                String headCommit) throws IOException {
            rangeReads++;
            rangeBase = baseCommit;
            rangeHead = headCommit;
            if (rangeFailure != null) {
                throw rangeFailure;
            }
            return rangeDiff;
        }
    }

    private final class DefaultMetadataService extends AbstractVcsAiClientService {
        private DefaultMetadataService() {
            super(encryption, vcsClients, enrichment, null, null, diffPreparation);
        }

        @Override
        public EVcsProvider getProvider() {
            return EVcsProvider.BITBUCKET_SERVER;
        }

        @Override
        protected PullRequestData fetchPullRequest(
                OkHttpClient client,
                RepositoryInfo repository,
                long pullRequestId) {
            return pullRequestData("title", "description", FULL_DIFF);
        }

        @Override
        protected String fetchCommitRangeDiff(
                OkHttpClient client,
                RepositoryInfo repository,
                String baseCommit,
                String headCommit) {
            return FULL_DIFF;
        }
    }
}
