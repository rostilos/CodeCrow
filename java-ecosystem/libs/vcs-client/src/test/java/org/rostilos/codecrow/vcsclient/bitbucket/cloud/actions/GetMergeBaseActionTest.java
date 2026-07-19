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

class GetMergeBaseActionTest {
    private static final String BASE = "a".repeat(40);
    private static final String HEAD = "b".repeat(40);
    private static final String MERGE_BASE = "c".repeat(40);

    private MockWebServer server;
    private GetMergeBaseAction action;

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
        action = new GetMergeBaseAction(client);
    }

    @AfterEach
    void tearDown() throws IOException {
        server.shutdown();
    }

    @Test
    void parsesHashFromExactMergeBaseResponse() throws Exception {
        server.enqueue(new MockResponse().setBody("{\"hash\":\"" + MERGE_BASE + "\"}"));

        assertThat(action.getMergeBase("acme", "repo", BASE, HEAD))
                .isEqualTo(MERGE_BASE);
        assertThat(server.takeRequest().getPath()).isEqualTo(
                "/2.0/repositories/acme/repo/merge-base/" + BASE + ".." + HEAD);
    }

    @Test
    void rejectsMissingHashResponse() {
        server.enqueue(new MockResponse().setBody("{}"));

        assertThatThrownBy(() -> action.getMergeBase("acme", "repo", BASE, HEAD))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("omitted hash");
    }

    @Test
    void rejectsNonExactCommitBeforeCallingProvider() {
        assertThatThrownBy(() -> action.getMergeBase("acme", "repo", BASE, "feature"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("headCommit");
        assertThat(server.getRequestCount()).isZero();
    }
}
