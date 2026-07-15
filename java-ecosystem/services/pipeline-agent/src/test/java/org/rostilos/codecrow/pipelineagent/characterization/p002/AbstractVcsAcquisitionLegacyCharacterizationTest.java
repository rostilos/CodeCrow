package org.rostilos.codecrow.pipelineagent.characterization.p002;

import okhttp3.OkHttpClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.analysisengine.dto.request.ai.AiAnalysisRequest;
import org.rostilos.codecrow.analysisengine.dto.request.processor.PrProcessRequest;
import org.rostilos.codecrow.analysisengine.service.pr.PullRequestDiffPreparationService;
import org.rostilos.codecrow.core.model.ai.AIConnection;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.ProjectAiConnectionBinding;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;
import org.rostilos.codecrow.pipelineagent.generic.service.AbstractVcsAiClientService;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;

import java.io.IOException;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@Tag("legacy-defect")
@ExtendWith(MockitoExtension.class)
class AbstractVcsAcquisitionLegacyCharacterizationTest {

    @Mock private TokenEncryptionService tokenEncryptionService;
    @Mock private VcsClientProvider vcsClientProvider;
    @Mock private PullRequestDiffPreparationService diffPreparationService;
    @Mock private Project project;
    @Mock private VcsRepoInfo repoInfo;
    @Mock private VcsConnection connection;
    @Mock private ProjectAiConnectionBinding aiBinding;
    @Mock private AIConnection aiConnection;

    private PrProcessRequest request;

    @BeforeEach
    void setUp() {
        request = new PrProcessRequest();
        request.projectId = 1L;
        request.pullRequestId = 42L;
        request.sourceBranchName = "feature";
        request.targetBranchName = "main";
        request.commitHash = "head-a";
        request.analysisType = AnalysisType.PR_REVIEW;

        lenient().when(project.getId()).thenReturn(1L);
        lenient().when(project.getEffectiveVcsRepoInfo()).thenReturn(repoInfo);
        lenient().when(repoInfo.getVcsConnection()).thenReturn(connection);
        lenient().when(repoInfo.getRepoWorkspace()).thenReturn("offline-workspace");
        lenient().when(repoInfo.getRepoSlug()).thenReturn("offline-repository");
        lenient().when(project.getAiBinding()).thenReturn(aiBinding);
        lenient().when(aiBinding.getAiConnection()).thenReturn(aiConnection);
        lenient().when(vcsClientProvider.getHttpClient(connection)).thenReturn(mock(OkHttpClient.class));
    }

    @Test
    void legacyDefectVcsIOExceptionCollapsesToTheSameEmptyListAsNoScopedChanges() throws Exception {
        AbstractVcsAiClientService service = serviceThatThrows(new IOException("legacy injected VCS timeout"));

        List<AiAnalysisRequest> result = service.buildAiAnalysisRequests(
                project, request, Optional.empty(), List.of());

        assertThat(result).isEmpty();
        verifyNoInteractions(diffPreparationService);
    }

    @Test
    void legitimateEmptyScopedDiffAlsoReturnsEmptyList() throws Exception {
        AbstractVcsAiClientService service = serviceThatThrows(null);
        when(diffPreparationService.prepare(
                eq(project), eq(42L), eq(""), isNull(), eq("head-a"), any()))
                .thenReturn(PullRequestDiffPreparationService.PreparedDiff.empty(null, "head-a"));

        List<AiAnalysisRequest> result = service.buildAiAnalysisRequests(
                project, request, Optional.empty(), List.of());

        assertThat(result).isEmpty();
        verify(diffPreparationService).prepare(
                eq(project), eq(42L), eq(""), isNull(), eq("head-a"), any());
    }

    private AbstractVcsAiClientService serviceThatThrows(IOException failure) {
        return new AbstractVcsAiClientService(
                tokenEncryptionService,
                vcsClientProvider,
                null,
                null,
                null,
                diffPreparationService) {
            @Override
            public EVcsProvider getProvider() {
                return EVcsProvider.GITHUB;
            }

            @Override
            protected PullRequestData fetchPullRequest(
                    OkHttpClient client, RepositoryInfo repository, long pullRequestId) throws IOException {
                if (failure != null) {
                    throw failure;
                }
                return pullRequestData("ordinary title", "ordinary description", "");
            }

            @Override
            protected String fetchCommitRangeDiff(
                    OkHttpClient client, RepositoryInfo repository, String baseCommit, String headCommit) {
                return "";
            }
        };
    }
}
