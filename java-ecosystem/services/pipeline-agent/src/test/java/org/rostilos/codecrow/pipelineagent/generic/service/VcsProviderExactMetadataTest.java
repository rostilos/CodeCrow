package org.rostilos.codecrow.pipelineagent.generic.service;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import org.junit.jupiter.api.Test;
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
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;

class VcsProviderExactMetadataTest {
    private static final String BASE = "a".repeat(40);
    private static final String HEAD = "b".repeat(40);
    private static final String MERGE_BASE = "c".repeat(40);

    @Test
    void githubReadsExactHeadsMergeBaseAndCompleteInventory() throws Exception {
        Invocation invocation = fetchMetadata(
                new GitHubAiClientService(
                        mock(TokenEncryptionService.class), mock(VcsClientProvider.class),
                        null, null, null, mock(PullRequestDiffPreparationService.class)),
                """
                        {"title":"title","body":"body","changed_files":1,
                         "base":{"sha":"%s"},"head":{"sha":"%s"}}
                        """.formatted(BASE, HEAD),
                """
                        {"merge_base_commit":{"sha":"%s"},
                         "files":[{"filename":"src/A.java","additions":2,"deletions":1}]}
                        """.formatted(MERGE_BASE));

        assertCoordinates(invocation.metadata());
        assertThat(invocation.metadata().expectedFileChanges())
                .containsExactly(new AbstractVcsAiClientService.ExpectedFileChange(
                        "src/A.java", 2, 1));
        assertThat(invocation.urls().get(1))
                .contains("/compare/" + BASE + "..." + HEAD);
    }

    @Test
    void githubRejectsInventoryWithoutIndependentLineCounts() {
        assertThatThrownBy(() -> fetchMetadata(
                new GitHubAiClientService(
                        mock(TokenEncryptionService.class), mock(VcsClientProvider.class),
                        null, null, null, mock(PullRequestDiffPreparationService.class)),
                """
                        {"title":"title","body":"body","changed_files":1,
                         "base":{"sha":"%s"},"head":{"sha":"%s"}}
                        """.formatted(BASE, HEAD),
                """
                        {"merge_base_commit":{"sha":"%s"},
                         "files":[{"filename":"src/A.java"}]}
                        """.formatted(MERGE_BASE)))
                .hasRootCauseInstanceOf(java.io.IOException.class)
                .hasRootCauseMessage("GitHub comparison file inventory has invalid additions");
    }

    @Test
    void gitlabMapsDocumentedDiffRefsToExactCoordinates() throws Exception {
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
    void bitbucketReadsExactHeadsAndCommonAncestor() throws Exception {
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
                        """.formatted(MERGE_BASE),
                """
                        {"values":[{"status":"modified","lines_added":4,"lines_removed":3,
                         "new":{"path":"src/A.java"}}]}
                        """);

        assertCoordinates(invocation.metadata());
        assertThat(invocation.metadata().expectedFileChanges())
                .containsExactly(new AbstractVcsAiClientService.ExpectedFileChange(
                        "src/A.java", 4, 3));
        assertThat(invocation.urls().get(1))
                .contains("/merge-base/" + BASE + ".." + HEAD);
        assertThat(invocation.urls().get(2))
                .contains("/diffstat/" + HEAD + ".." + MERGE_BASE);
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
        try (MockWebServer server = new MockWebServer()) {
            server.start();
            for (String body : responseBodies) {
                server.enqueue(new MockResponse()
                        .setHeader("Content-Type", "application/json")
                        .setBody(body));
            }
            OkHttpClient client = new OkHttpClient.Builder()
                    .addInterceptor(chain -> {
                        Request original = chain.request();
                        String path = original.url().encodedPath();
                        String query = original.url().encodedQuery();
                        return chain.proceed(original.newBuilder()
                                .url(server.url(path + (query != null ? "?" + query : "")))
                                .build());
                    })
                    .build();

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

            List<String> urls = new ArrayList<>();
            for (int index = 0; index < responseBodies.length; index++) {
                urls.add(server.takeRequest().getRequestUrl().toString());
            }
            return new Invocation(metadata, List.copyOf(urls));
        }
    }

    private record Invocation(
            AbstractVcsAiClientService.PullRequestMetadata metadata,
            List<String> urls) {
    }
}
