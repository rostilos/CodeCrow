package org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class GetPullRequestActionTest {
    private static final String SOURCE = "a".repeat(40);
    private static final String DESTINATION = "b".repeat(40);

    private MockWebServer server;
    private GetPullRequestAction action;

    @BeforeEach
    void setUp() throws IOException {
        server = new MockWebServer();
        server.start();
        OkHttpClient client = new OkHttpClient.Builder()
                .addInterceptor(chain -> {
                    Request original = chain.request();
                    return chain.proceed(original.newBuilder()
                            .url(server.url(original.url().encodedPath()))
                            .build());
                })
                .build();
        action = new GetPullRequestAction(client);
    }

    @AfterEach
    void tearDown() throws IOException {
        server.shutdown();
    }

    @Test
    void parsesBranchAndExactCommitMetadata() throws Exception {
        server.enqueue(new MockResponse().setBody("""
                {
                  "title":"Test PR",
                  "description":"Test description",
                  "state":"OPEN",
                  "source":{"branch":{"name":"feature"},"commit":{"hash":"%s"}},
                  "destination":{"branch":{"name":"main"},"commit":{"hash":"%s"}}
                }
                """.formatted(SOURCE, DESTINATION)));

        GetPullRequestAction.PullRequestMetadata result =
                action.getPullRequest("workspace", "repo", "123");

        assertThat(result.getTitle()).isEqualTo("Test PR");
        assertThat(result.getSourceRef()).isEqualTo("feature");
        assertThat(result.getDestRef()).isEqualTo("main");
        assertThat(result.getSourceCommit()).isEqualTo(SOURCE);
        assertThat(result.getDestinationCommit()).isEqualTo(DESTINATION);
        assertThat(server.takeRequest().getPath()).isEqualTo(
                "/2.0/repositories/workspace/repo/pullrequests/123");
    }

    @Test
    void propagatesProviderError() {
        server.enqueue(new MockResponse().setResponseCode(404).setBody("Not found"));

        assertThatThrownBy(() -> action.getPullRequest("workspace", "repo", "123"))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("404");
    }
}
