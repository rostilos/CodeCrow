package org.rostilos.codecrow.pipelineagent.generic.service;

import okhttp3.OkHttpClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AgenticRepositoryArchive;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.service.pr.PrFileEnrichmentService;
import org.rostilos.codecrow.analysisengine.service.pr.PullRequestDiffPreparationService;
import org.rostilos.codecrow.analysisengine.service.pr.PullRequestDiffPreparationService.PreparedDiff;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisMode;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.ProjectAiConnectionBinding;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.project.config.ReviewApproach;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.pipelineagent.agentic.AgenticRepositoryArchiveService;
import org.rostilos.codecrow.pipelineagent.generic.service.AbstractVcsAiClientService.ExpectedFileChange;
import org.rostilos.codecrow.pipelineagent.generic.service.AbstractVcsAiClientService.PullRequestMetadata;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.VcsClient;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;

import java.io.IOException;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class AbstractVcsAiClientServiceAgenticTest {
    private static final String BASE = "a".repeat(40);
    private static final String HEAD = "b".repeat(40);
    private static final String MERGE_BASE = "c".repeat(40);
    private static final String EXACT_DIFF =
            "diff --git a/src/A.java b/src/A.java\n@@ -1 +1 @@\n-old\n+new\n";
    private static final String PREPARED_DIFF =
            "diff --git a/src/A.java b/src/A.java\n@@ -1 +1 @@\n-old\n+prepared\n";

    @Mock private TokenEncryptionService encryption;
    @Mock private VcsClientProvider vcsClients;
    @Mock private PrFileEnrichmentService enrichment;
    @Mock private PullRequestDiffPreparationService diffPreparation;
    @Mock private AgenticRepositoryArchiveService archiveService;
    @Mock private Project project;
    @Mock private VcsRepoInfo repoInfo;
    @Mock private VcsConnection connection;
    @Mock private ProjectAiConnectionBinding aiBinding;
    @Mock private AIConnection aiConnection;
    @Mock private Workspace workspace;
    @Mock private OkHttpClient httpClient;
    @Mock private VcsClient vcsClient;

    private ProjectConfig config;
    private PrProcessRequest request;

    @BeforeEach
    void setUp() throws Exception {
        config = new ProjectConfig();
        config.setReviewApproach(ReviewApproach.AGENTIC);

        request = new PrProcessRequest();
        request.projectId = 1L;
        request.pullRequestId = 42L;
        request.commitHash = HEAD;
        request.sourceBranchName = "feature/test";
        request.targetBranchName = "main";
        request.analysisType = AnalysisType.PR_REVIEW;

        when(project.getId()).thenReturn(1L);
        when(project.getEffectiveConfig()).thenReturn(config);
        when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
        when(repoInfo.getVcsConnection()).thenReturn(connection);
        when(repoInfo.getRepoWorkspace()).thenReturn("workspace");
        when(repoInfo.getRepoSlug()).thenReturn("repository");
        when(connection.getProviderType()).thenReturn(EVcsProvider.GITHUB);
        when(connection.getConnectionType()).thenReturn(EVcsConnectionType.APP);
        when(connection.getAccessToken()).thenReturn("encrypted-vcs-token");
        when(project.getAiBinding()).thenReturn(aiBinding);
        when(aiBinding.getAiConnection()).thenReturn(aiConnection);
        when(aiConnection.getProviderKey()).thenReturn(AIProviderKey.OPENAI);
        when(aiConnection.getApiKeyEncrypted()).thenReturn("encrypted-ai-key");
        when(encryption.decrypt("encrypted-ai-key")).thenReturn("ai-key");
        when(encryption.decrypt("encrypted-vcs-token")).thenReturn("vcs-token");
        when(project.getWorkspace()).thenReturn(workspace);
        when(workspace.getName()).thenReturn("tenant");
        when(project.getNamespace()).thenReturn("namespace");
        when(vcsClients.getHttpClient(connection)).thenReturn(httpClient);
        when(vcsClients.getClient(connection)).thenReturn(vcsClient);
    }

    @Test
    void agenticPathQueuesThePreparedExactDiffAndExactArchive() throws Exception {
        RecordingService service = new RecordingService(BASE, HEAD, MERGE_BASE);
        service.setAgenticRepositoryArchiveService(archiveService);
        when(diffPreparation.prepareAgenticExact(
                eq(project), eq(42L), eq(EXACT_DIFF), eq(MERGE_BASE), eq(HEAD)))
                .thenReturn(new PreparedDiff(
                        PREPARED_DIFF, null, AnalysisMode.FULL,
                        List.of("src/A.java"), List.of(), null, HEAD));
        AgenticRepositoryArchive archive = new AgenticRepositoryArchive(
                "d".repeat(64), HEAD, "e".repeat(64), 100L);
        when(archiveService.stage(
                eq(vcsClient), anyString(), eq("workspace"), eq("repository"), eq(HEAD)))
                .thenReturn(archive);

        AiAnalysisRequest built = service.buildAiAnalysisRequests(
                project, request, Optional.empty(), List.of()).get(0);

        assertThat(service.mutablePullRequestReads).isZero();
        assertThat(service.metadataReads).isEqualTo(1);
        assertThat(service.diffBase).isEqualTo(MERGE_BASE);
        assertThat(service.diffHead).isEqualTo(HEAD);
        assertThat(built.getRawDiff()).isEqualTo(PREPARED_DIFF);
        assertThat(built.getReviewApproach()).isEqualTo(ReviewApproach.AGENTIC);
        assertThat(built.getAgenticRepository()).isSameAs(archive);
        assertThat(built.getPreviousCommitHash()).isEqualTo(MERGE_BASE);
        assertThat(built.getCurrentCommitHash()).isEqualTo(HEAD);
        assertThat(built.getUseLocalMcp()).isFalse();
        assertThat(built.getUseMcpTools()).isFalse();
        assertThat(built.getOAuthClient()).isNull();
        assertThat(built.getOAuthSecret()).isNull();
        assertThat(built.getAccessToken()).isNull();
        verifyNoInteractions(enrichment);

        service.discardUndispatchedAiAnalysisRequest(built);
        verify(archiveService).cleanup(archive.workspaceKey());
    }

    @Test
    void agenticPathRejectsAHeadThatAdvancedBeforeDiffOrArchiveAcquisition() throws Exception {
        String advancedHead = "f".repeat(40);
        RecordingService service = new RecordingService(BASE, advancedHead, MERGE_BASE);
        service.setAgenticRepositoryArchiveService(archiveService);

        assertThatThrownBy(() -> service.buildAiAnalysisRequests(
                project, request, Optional.empty(), List.of()))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("no longer matches");

        assertThat(service.diffBase).isNull();
        verify(diffPreparation, never()).prepareAgenticExact(
                any(), any(), any(), any(), any());
        verify(archiveService, never()).stage(
                any(), anyString(), anyString(), anyString(), anyString());
    }

    @Test
    void agenticPathRejectsDiffThatDoesNotMatchProviderInventory() throws Exception {
        RecordingService service = new RecordingService(BASE, HEAD, MERGE_BASE);
        service.expectedFiles = List.of(new ExpectedFileChange("src/Missing.java", 1, 1));

        assertThatThrownBy(() -> service.buildAiAnalysisRequests(
                project, request, Optional.empty(), List.of()))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("Unable to acquire exact");

        verify(diffPreparation, never()).prepareAgenticExact(
                any(), any(), any(), any(), any());
        verify(archiveService, never()).stage(
                any(), anyString(), anyString(), anyString(), anyString());
    }

    @Test
    void exactValidationRejectsCompleteButMissingHunks() {
        PullRequestMetadata metadata = new PullRequestMetadata(
                "title", "description", BASE, HEAD, MERGE_BASE, true,
                List.of(new ExpectedFileChange("src/A.java", 2, 2)));

        assertThatThrownBy(() -> ExactDiffIntegrityValidator.validate(metadata, EXACT_DIFF))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("line counts");
    }

    @Test
    void exactValidationDoesNotConfuseAnEmptyInventoryWithNoInventory() {
        PullRequestMetadata metadata = new PullRequestMetadata(
                "title", "description", BASE, HEAD, MERGE_BASE, true, List.of());

        assertThatThrownBy(() -> ExactDiffIntegrityValidator.validate(
                metadata, EXACT_DIFF))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("changed-file inventory");
    }

    @Test
    void exactValidationStillRejectsStructurallyTruncatedHunksWithoutAnInventory() {
        String truncated = """
                diff --git a/src/A.java b/src/A.java
                @@ -1,2 +1,2 @@
                -old
                +new
                """;
        PullRequestMetadata metadata = new PullRequestMetadata(
                "title", "description", BASE, HEAD, MERGE_BASE, false, List.of());

        assertThatThrownBy(() -> ExactDiffIntegrityValidator.validate(
                metadata, truncated))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("truncated hunk");
    }

    @Test
    void exactValidationChecksCountsPerFileRatherThanOnlyInAggregate() {
        String diff = """
                diff --git a/src/A.java b/src/A.java
                @@ -0,0 +1 @@
                +added
                diff --git a/src/B.java b/src/B.java
                @@ -1 +0,0 @@
                -removed
                """;
        PullRequestMetadata metadata = new PullRequestMetadata(
                "title", "description", BASE, HEAD, MERGE_BASE, true,
                List.of(
                        new ExpectedFileChange("src/A.java", 0, 1),
                        new ExpectedFileChange("src/B.java", 1, 0)));

        assertThatThrownBy(() -> ExactDiffIntegrityValidator.validate(metadata, diff))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("src/A.java");
    }

    @Test
    void exactValidationTreatsRenameModeAndBinaryOnlyChangesAsZeroLineChanges() {
        String diff = """
                diff --git a/old.java b/new.java
                similarity index 100%
                rename from old.java
                rename to new.java
                diff --git a/script.sh b/script.sh
                old mode 100644
                new mode 100755
                diff --git a/image.png b/image.png
                index 1111111..2222222 100644
                Binary files a/image.png and b/image.png differ
                """;
        PullRequestMetadata metadata = new PullRequestMetadata(
                "title", "description", BASE, HEAD, MERGE_BASE, true,
                List.of(
                        new ExpectedFileChange("new.java", 0, 0),
                        new ExpectedFileChange("script.sh", 0, 0),
                        new ExpectedFileChange("image.png", 0, 0)));

        assertThatCode(() -> ExactDiffIntegrityValidator.validate(metadata, diff))
                .doesNotThrowAnyException();
    }

    @Test
    void classicPathRemainsOnTheExistingPullRequestFlow() throws Exception {
        config.setReviewApproach(ReviewApproach.CLASSIC);
        RecordingService service = new RecordingService(BASE, HEAD, MERGE_BASE);
        when(diffPreparation.prepare(
                eq(project), eq(42L), eq("mutable PR diff"), isNull(), eq(HEAD), any()))
                .thenReturn(new PreparedDiff(
                        PREPARED_DIFF, null, AnalysisMode.FULL,
                        List.of(), List.of(), null, HEAD));

        List<AiAnalysisRequest> built = service.buildAiAnalysisRequests(
                project, request, Optional.empty(), List.of());

        assertThat(built).hasSize(1);
        assertThat(built.get(0).getReviewApproach()).isEqualTo(ReviewApproach.CLASSIC);
        assertThat(service.mutablePullRequestReads).isEqualTo(1);
        assertThat(service.metadataReads).isZero();
        verifyNoInteractions(archiveService);
    }

    private final class RecordingService extends AbstractVcsAiClientService {
        private final String base;
        private final String head;
        private final String mergeBase;
        private int mutablePullRequestReads;
        private int metadataReads;
        private String diffBase;
        private String diffHead;
        private List<ExpectedFileChange> expectedFiles =
                List.of(new ExpectedFileChange("src/A.java", 1, 1));

        private RecordingService(String base, String head, String mergeBase) {
            super(encryption, vcsClients, enrichment, null, null, diffPreparation);
            this.base = base;
            this.head = head;
            this.mergeBase = mergeBase;
        }

        @Override
        public EVcsProvider getProvider() {
            return EVcsProvider.GITHUB;
        }

        @Override
        protected PullRequestData fetchPullRequest(
                OkHttpClient client,
                RepositoryInfo repository,
                long pullRequestId) {
            mutablePullRequestReads++;
            return pullRequestData("title", "description", "mutable PR diff");
        }

        @Override
        protected PullRequestMetadata fetchPullRequestMetadata(
                OkHttpClient client,
                RepositoryInfo repository,
                long pullRequestId) {
            metadataReads++;
            return pullRequestMetadata(
                    "title", "description", base, head, mergeBase, expectedFiles);
        }

        @Override
        protected String fetchCommitRangeDiff(
                OkHttpClient client,
                RepositoryInfo repository,
                String baseCommit,
                String headCommit) throws IOException {
            diffBase = baseCommit;
            diffHead = headCommit;
            return EXACT_DIFF;
        }
    }
}
