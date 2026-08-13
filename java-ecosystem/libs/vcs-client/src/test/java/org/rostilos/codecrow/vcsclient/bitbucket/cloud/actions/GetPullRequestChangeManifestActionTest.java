package org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions;

import okhttp3.OkHttpClient;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.vcsclient.model.VcsPullRequestChangeManifest;

import static org.assertj.core.api.Assertions.assertThat;

class GetPullRequestChangeManifestActionTest {

    @Test
    void followsDiffstatPaginationAndPreservesRenameSource() throws Exception {
        try (MockWebServer server = new MockWebServer()) {
            server.enqueue(json("""
                    {
                      "size": 2,
                      "values": [{
                        "status": "renamed",
                        "old": {"path": "src/Old.java"},
                        "new": {"path": "src/New.java"}
                      }],
                      "next": "%s"
                    }
                    """.formatted(server.url("/page-2"))));
            server.enqueue(json("""
                    {
                      "size": 2,
                      "values": [{
                        "status": "removed",
                        "old": {"path": "src/Deleted.java"},
                        "new": null
                      }]
                    }
                    """));

            // The first request uses Bitbucket's public hostname, so redirect it
            // to the mock server while preserving paginated next URLs.
            OkHttpClient client = new OkHttpClient.Builder()
                    .addInterceptor(chain -> {
                        var original = chain.request();
                        var redirected = original.newBuilder()
                                .url(server.url(original.url().encodedPath()
                                        + (original.url().encodedQuery() != null
                                                ? "?" + original.url().encodedQuery() : "")))
                                .build();
                        return chain.proceed(redirected);
                    })
                    .build();

            VcsPullRequestChangeManifest manifest =
                    new GetPullRequestChangeManifestAction(client)
                            .getPullRequestChangeManifest("team", "repo", 17);

            assertThat(manifest.isComplete()).isTrue();
            assertThat(manifest.currentPaths()).containsExactly("src/New.java");
            assertThat(manifest.removedPaths())
                    .containsExactly("src/Old.java", "src/Deleted.java");
            assertThat(manifest.receipt()).contains("pages=2", "expected=2");
        }
    }

    @Test
    void missingProviderCountNeverClaimsCompleteness() throws Exception {
        try (MockWebServer server = new MockWebServer()) {
            server.enqueue(json("""
                    {"values": [{
                      "status": "modified",
                      "old": {"path": "src/App.java"},
                      "new": {"path": "src/App.java"}
                    }]}
                    """));
            server.start();
            OkHttpClient client = new OkHttpClient.Builder()
                    .addInterceptor(chain -> chain.proceed(chain.request().newBuilder()
                            .url(server.url(chain.request().url().encodedPath()))
                            .build()))
                    .build();

            VcsPullRequestChangeManifest manifest =
                    new GetPullRequestChangeManifestAction(client)
                            .getPullRequestChangeManifest("team", "repo", 17);

            assertThat(manifest.completeness())
                    .isEqualTo(VcsPullRequestChangeManifest.Completeness.INCOMPLETE);
            assertThat(manifest.currentPaths()).containsExactly("src/App.java");
        }
    }

    private MockResponse json(String body) {
        return new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(body);
    }
}
