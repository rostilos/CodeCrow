package org.rostilos.codecrow.pipelineagent.generic.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import java.lang.reflect.Method;

import okhttp3.Call;
import okhttp3.OkHttpClient;
import okhttp3.Response;
import okhttp3.ResponseBody;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisengine.service.pr.PullRequestDiffPreparationService;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.pipelineagent.bitbucket.service.BitbucketAiClientService;
import org.rostilos.codecrow.pipelineagent.generic.service.AbstractVcsAiClientService.PullRequestData;
import org.rostilos.codecrow.pipelineagent.generic.service.AbstractVcsAiClientService.RepositoryInfo;
import org.rostilos.codecrow.pipelineagent.github.service.GitHubAiClientService;
import org.rostilos.codecrow.pipelineagent.gitlab.service.GitLabAiClientService;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;

class VcsProviderTelemetryAttributionTest {
    private static final String PR_BASE = "a".repeat(40);
    private static final String FULL_DIFF = "diff --git a/a.py b/a.py\n@@ -1 +1 @@\n-old\n+new\n";
    private static final String GITLAB_DIFF = """
            [{
              "old_path":"a.py",
              "new_path":"a.py",
              "diff":"@@ -1 +1 @@\\n-old\\n+new",
              "new_file":false,
              "renamed_file":false,
              "deleted_file":false
            }]
            """;

    @Test
    void legacyPullRequestDataOverloadLeavesTheComparisonBaseUnavailable() {
        GitHubAiClientService service = new GitHubAiClientService(
                mock(TokenEncryptionService.class), mock(VcsClientProvider.class),
                null, null, null, mock(PullRequestDiffPreparationService.class));

        assertThat(service.pullRequestData("title", "body", FULL_DIFF).baseRevision()).isNull();
    }

    @Test
    void githubReadsTheExactProviderComparisonBase() throws Exception {
        assertThat(fetchProviderPullRequest(
                new GitHubAiClientService(
                        mock(TokenEncryptionService.class), mock(VcsClientProvider.class),
                        null, null, null, mock(PullRequestDiffPreparationService.class)),
                """
                        {"title":"title","body":"body","base":{"sha":"%s"}}
                        """.formatted(PR_BASE),
                FULL_DIFF))
                .extracting(PullRequestData::baseRevision)
                .isEqualTo(PR_BASE);
    }

    @Test
    void gitlabReadsTheExactProviderComparisonBase() throws Exception {
        assertThat(fetchProviderPullRequest(
                new GitLabAiClientService(
                        mock(TokenEncryptionService.class), mock(VcsClientProvider.class),
                        null, null, null, mock(PullRequestDiffPreparationService.class)),
                """
                        {"title":"title","description":"body","diff_refs":{"base_sha":"%s"}}
                        """.formatted(PR_BASE),
                GITLAB_DIFF))
                .extracting(PullRequestData::baseRevision)
                .isEqualTo(PR_BASE);
    }

    @Test
    void bitbucketReadsTheExactProviderComparisonBase() throws Exception {
        assertThat(fetchProviderPullRequest(
                new BitbucketAiClientService(
                        mock(TokenEncryptionService.class), mock(VcsClientProvider.class),
                        null, null, null, mock(PullRequestDiffPreparationService.class)),
                """
                        {
                          "title":"title",
                          "description":"body",
                          "state":"OPEN",
                          "destination":{"commit":{"hash":"%s"}}
                        }
                        """.formatted(PR_BASE),
                FULL_DIFF))
                .extracting(PullRequestData::baseRevision)
                .isEqualTo(PR_BASE);
    }

    private PullRequestData fetchProviderPullRequest(
            AbstractVcsAiClientService service,
            String metadataJson,
            String diffBody) throws Exception {
        OkHttpClient client = mock(OkHttpClient.class);
        Call metadataCall = mock(Call.class);
        Call diffCall = mock(Call.class);
        Response metadataResponse = successfulResponse(metadataJson);
        Response diffResponse = successfulResponse(diffBody);
        when(client.newCall(any())).thenReturn(metadataCall, diffCall);
        when(metadataCall.execute()).thenReturn(metadataResponse);
        when(diffCall.execute()).thenReturn(diffResponse);

        Method method = service.getClass().getDeclaredMethod(
                "fetchPullRequest",
                OkHttpClient.class,
                RepositoryInfo.class,
                long.class);
        method.setAccessible(true);
        return (PullRequestData) method.invoke(
                service,
                client,
                new RepositoryInfo(mock(VcsConnection.class), "workspace", "repository"),
                42L);
    }

    private Response successfulResponse(String body) throws Exception {
        Response response = mock(Response.class);
        ResponseBody responseBody = mock(ResponseBody.class);
        when(response.isSuccessful()).thenReturn(true);
        when(response.code()).thenReturn(200);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn(body);
        return response;
    }
}
