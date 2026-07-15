package org.rostilos.codecrow.pipelineagent.generic.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import java.util.List;
import java.util.Optional;

import okhttp3.OkHttpClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.service.pr.PullRequestDiffPreparationService;
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
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.pipelineagent.generic.service.AbstractVcsAiClientService.PullRequestData;
import org.rostilos.codecrow.pipelineagent.generic.service.AbstractVcsAiClientService.RepositoryInfo;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;

@ExtendWith(MockitoExtension.class)
class AbstractVcsTelemetryAttributionTest {
    private static final String PR_BASE = "a".repeat(40);
    private static final String PRIOR_ANALYSIS = "b".repeat(40);
    private static final String HEAD = "c".repeat(40);
    private static final String FULL_DIFF = "diff --git a/a.py b/a.py\n@@ -1 +1 @@\n-old\n+new\n";

    @Mock private TokenEncryptionService encryption;
    @Mock private VcsClientProvider vcsClients;
    @Mock private PullRequestDiffPreparationService diffPreparation;
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
        request.sourceBranchName = "feature";
        request.targetBranchName = "main";
        request.commitHash = HEAD;
        request.analysisType = AnalysisType.PR_REVIEW;

        when(project.getId()).thenReturn(1L);
        when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
        when(repoInfo.getVcsConnection()).thenReturn(connection);
        when(repoInfo.getRepoWorkspace()).thenReturn("workspace");
        when(repoInfo.getRepoSlug()).thenReturn("repository");
        when(connection.getProviderType()).thenReturn(EVcsProvider.GITHUB);
        when(connection.getConnectionType()).thenReturn(EVcsConnectionType.APP);
        when(connection.getAccessToken()).thenReturn("encrypted");
        when(project.getAiBinding()).thenReturn(aiBinding);
        when(aiBinding.getAiConnection()).thenReturn(aiConnection);
        when(aiConnection.getProviderKey()).thenReturn(AIProviderKey.OPENAI);
        when(aiConnection.getAiModel()).thenReturn("fixture-v1");
        when(aiConnection.getApiKeyEncrypted()).thenReturn("encrypted");
        when(encryption.decrypt("encrypted")).thenReturn("decrypted");
        when(project.getEffectiveConfig()).thenReturn(new ProjectConfig());
        when(project.getWorkspace()).thenReturn(workspace);
        when(workspace.getName()).thenReturn("tenant");
        when(project.getNamespace()).thenReturn("namespace");
        when(vcsClients.getHttpClient(connection)).thenReturn(mock(OkHttpClient.class));
    }

    @Test
    void fullPullRequestDiffUsesTheProviderComparisonBaseNotThePriorAnalysis() throws Exception {
        when(diffPreparation.prepare(
                eq(project), eq(42L), eq(FULL_DIFF), eq(PRIOR_ANALYSIS), eq(HEAD), any()))
                .thenReturn(new PreparedDiff(
                        FULL_DIFF, null, AnalysisMode.FULL, List.of("a.py"), List.of(),
                        PRIOR_ANALYSIS, HEAD));
        CodeAnalysis previous = mock(CodeAnalysis.class);
        when(previous.getCommitHash()).thenReturn(PRIOR_ANALYSIS);

        AiAnalysisRequest built = service().buildAiAnalysisRequests(
                project, request, Optional.of(previous), List.of()).get(0);

        assertThat(built.getAnalysisMode()).isEqualTo(AnalysisMode.FULL);
        assertThat(built.getPreviousCommitHash()).isEqualTo(PR_BASE);
        assertThat(built.getCurrentCommitHash()).isEqualTo(HEAD);
    }

    @Test
    void incrementalDiffUsesThePriorAnalyzedRevisionAsItsComparisonBase() throws Exception {
        when(diffPreparation.prepare(
                eq(project), eq(42L), eq(FULL_DIFF), eq(PRIOR_ANALYSIS), eq(HEAD), any()))
                .thenReturn(new PreparedDiff(
                        FULL_DIFF, "+ incremental body large enough", AnalysisMode.INCREMENTAL,
                        List.of("a.py"), List.of(), PRIOR_ANALYSIS, HEAD));
        CodeAnalysis previous = mock(CodeAnalysis.class);
        when(previous.getCommitHash()).thenReturn(PRIOR_ANALYSIS);

        AiAnalysisRequest built = service().buildAiAnalysisRequests(
                project, request, Optional.of(previous), List.of()).get(0);

        assertThat(built.getAnalysisMode()).isEqualTo(AnalysisMode.INCREMENTAL);
        assertThat(built.getPreviousCommitHash()).isEqualTo(PRIOR_ANALYSIS);
    }

    private AbstractVcsAiClientService service() {
        return new AbstractVcsAiClientService(
                encryption, vcsClients, null, null, null, diffPreparation) {
            @Override
            public EVcsProvider getProvider() {
                return EVcsProvider.GITHUB;
            }

            @Override
            protected PullRequestData fetchPullRequest(
                    OkHttpClient client, RepositoryInfo repository, long pullRequestId) {
                return pullRequestData("title", "description", FULL_DIFF, PR_BASE);
            }

            @Override
            protected String fetchCommitRangeDiff(
                    OkHttpClient client,
                    RepositoryInfo repository,
                    String baseCommit,
                    String headCommit) {
                return "+delta";
            }
        };
    }

}
