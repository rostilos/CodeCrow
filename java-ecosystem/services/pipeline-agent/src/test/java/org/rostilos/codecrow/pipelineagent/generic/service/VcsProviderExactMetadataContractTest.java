package org.rostilos.codecrow.pipelineagent.generic.service;

import okhttp3.Call;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.ResponseBody;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.rostilos.codecrow.analysisengine.service.pr.PullRequestDiffPreparationService;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.pipelineagent.bitbucket.service.BitbucketAiClientService;
import org.rostilos.codecrow.pipelineagent.github.service.GitHubAiClientService;
import org.rostilos.codecrow.pipelineagent.gitlab.service.GitLabAiClientService;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;
import org.rostilos.codecrow.vcsclient.VcsClientProvider;

import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/** Provider HTTP fakes for the P1-01 exact snapshot coordinates. */
class VcsProviderExactMetadataContractTest {
    private static final String BASE = "a".repeat(40);
    private static final String HEAD = "b".repeat(40);
    private static final String MERGE_BASE = "c".repeat(40);
    private static final String RANGE_DIFF = """
            diff --git a/A.java b/A.java
            index 1111111..2222222 100644
            --- a/A.java
            +++ b/A.java
            @@ -1 +1 @@
            -old
            +new
            """;

    @Test
    void githubUsesExactPullRequestHeadsAndAnExactCompareMergeBase() throws Exception {
        Invocation invocation = fetchMetadata(
                new GitHubAiClientService(
                        mock(TokenEncryptionService.class), mock(VcsClientProvider.class),
                        null, null, null, mock(PullRequestDiffPreparationService.class)),
                """
                        {"title":"title","body":"body",
                         "base":{"sha":"%s"},"head":{"sha":"%s"}}
                        """.formatted(BASE, HEAD),
                """
                        {"merge_base_commit":{"sha":"%s"}}
                        """.formatted(MERGE_BASE));

        assertCoordinates(invocation.metadata());
        assertThat(invocation.urls().get(1))
                .contains("/compare/" + BASE + "..." + HEAD);
    }

    @Test
    void gitlabUsesStartHeadAndDocumentedMergeBaseDiffRefs() throws Exception {
        Invocation invocation = fetchMetadata(
                new GitLabAiClientService(
                        mock(TokenEncryptionService.class), mock(VcsClientProvider.class),
                        null, null, null, mock(PullRequestDiffPreparationService.class)),
                """
                        {"title":"title","description":"body","diff_refs":{
                         "start_sha":"%s","head_sha":"%s","base_sha":"%s"}}
                        """.formatted(BASE, HEAD, MERGE_BASE));

        assertCoordinates(invocation.metadata());
        assertThat(invocation.urls()).hasSize(1);
    }

    @Test
    void bitbucketUsesExactSourceDestinationAndCommonAncestorEndpoint() throws Exception {
        Invocation invocation = fetchMetadata(
                new BitbucketAiClientService(
                        mock(TokenEncryptionService.class), mock(VcsClientProvider.class),
                        null, null, null, mock(PullRequestDiffPreparationService.class)),
                """
                        {"title":"title","description":"body","state":"OPEN",
                         "source":{"commit":{"hash":"%s"}},
                         "destination":{"commit":{"hash":"%s"}}}
                        """.formatted(HEAD, BASE),
                """
                        {"hash":"%s"}
                        """.formatted(MERGE_BASE));

        assertCoordinates(invocation.metadata());
        assertThat(invocation.urls().get(1))
                .contains("/merge-base/" + BASE + ".." + HEAD);
    }

    @Test
    void providerRangeAdaptersDispatchTheExactBaseAndHeadCoordinates() throws Exception {
        RangeInvocation github = fetchRange(
                new GitHubAiClientService(
                        mock(TokenEncryptionService.class), mock(VcsClientProvider.class),
                        null, null, null, mock(PullRequestDiffPreparationService.class)),
                RANGE_DIFF);
        RangeInvocation gitlab = fetchRange(
                new GitLabAiClientService(
                        mock(TokenEncryptionService.class), mock(VcsClientProvider.class),
                        null, null, null, mock(PullRequestDiffPreparationService.class)),
                "{\"diffs\":[{\"old_path\":\"A.java\",\"new_path\":\"A.java\","
                        + "\"diff\":\"@@ -1 +1 @@\\n-old\\n+new\\n\"}]}");
        RangeInvocation bitbucket = fetchRange(
                new BitbucketAiClientService(
                        mock(TokenEncryptionService.class), mock(VcsClientProvider.class),
                        null, null, null, mock(PullRequestDiffPreparationService.class)),
                RANGE_DIFF);

        assertThat(github.result()).isEqualTo(RANGE_DIFF);
        assertThat(github.url()).contains("/compare/" + BASE + "..." + HEAD);
        assertThat(github.accept()).isEqualTo("application/vnd.github.v3.diff");

        assertThat(gitlab.result()).contains("diff --git a/A.java b/A.java");
        assertThat(gitlab.url()).contains("from=" + BASE).contains("to=" + HEAD);
        assertThat(gitlab.accept()).isEqualTo("application/json");

        assertThat(bitbucket.result()).isEqualTo(RANGE_DIFF);
        assertThat(bitbucket.url()).contains("/diff/" + HEAD + ".." + BASE);
    }

    private static void assertCoordinates(
            AbstractVcsAiClientService.PullRequestMetadata metadata) {
        assertThat(metadata.title()).isEqualTo("title");
        assertThat(metadata.description()).isEqualTo("body");
        assertThat(metadata.baseSha()).isEqualTo(BASE);
        assertThat(metadata.headSha()).isEqualTo(HEAD);
        assertThat(metadata.mergeBaseSha()).isEqualTo(MERGE_BASE);
    }

    private static Invocation fetchMetadata(
            AbstractVcsAiClientService service,
            String... responseBodies) throws Exception {
        OkHttpClient client = mock(OkHttpClient.class);
        List<Call> calls = new ArrayList<>();
        for (String body : responseBodies) {
            Call call = mock(Call.class);
            Response response = successfulResponse(body);
            when(call.execute()).thenReturn(response);
            calls.add(call);
        }
        when(client.newCall(any())).thenReturn(
                calls.get(0),
                calls.size() > 1 ? calls.get(1) : calls.get(0));

        Method method = service.getClass().getDeclaredMethod(
                "fetchPullRequestMetadata",
                OkHttpClient.class,
                AbstractVcsAiClientService.RepositoryInfo.class,
                long.class);
        method.setAccessible(true);
        var metadata = (AbstractVcsAiClientService.PullRequestMetadata) method.invoke(
                service,
                client,
                new AbstractVcsAiClientService.RepositoryInfo(
                        mock(VcsConnection.class), "workspace", "repository"),
                42L);

        ArgumentCaptor<Request> requests = ArgumentCaptor.forClass(Request.class);
        verify(client, times(responseBodies.length)).newCall(requests.capture());
        return new Invocation(
                metadata,
                requests.getAllValues().stream()
                        .map(request -> request.url().toString())
                        .toList());
    }

    private static RangeInvocation fetchRange(
            AbstractVcsAiClientService service,
            String responseBody) throws Exception {
        OkHttpClient client = mock(OkHttpClient.class);
        Call call = mock(Call.class);
        Response response = successfulResponse(responseBody);
        when(call.execute()).thenReturn(response);
        when(client.newCall(any())).thenReturn(call);

        Method method = service.getClass().getDeclaredMethod(
                "fetchCommitRangeDiff",
                OkHttpClient.class,
                AbstractVcsAiClientService.RepositoryInfo.class,
                String.class,
                String.class);
        method.setAccessible(true);
        String result = (String) method.invoke(
                service,
                client,
                new AbstractVcsAiClientService.RepositoryInfo(
                        mock(VcsConnection.class), "workspace", "repository"),
                BASE,
                HEAD);

        ArgumentCaptor<Request> request = ArgumentCaptor.forClass(Request.class);
        verify(client).newCall(request.capture());
        return new RangeInvocation(
                result,
                request.getValue().url().toString(),
                request.getValue().header("Accept"));
    }

    private static Response successfulResponse(String body) throws Exception {
        Response response = mock(Response.class);
        ResponseBody responseBody = mock(ResponseBody.class);
        when(response.isSuccessful()).thenReturn(true);
        when(response.code()).thenReturn(200);
        when(response.body()).thenReturn(responseBody);
        when(responseBody.string()).thenReturn(body);
        return response;
    }

    private record Invocation(
            AbstractVcsAiClientService.PullRequestMetadata metadata,
            List<String> urls) {
    }

    private record RangeInvocation(String result, String url, String accept) {
    }
}
