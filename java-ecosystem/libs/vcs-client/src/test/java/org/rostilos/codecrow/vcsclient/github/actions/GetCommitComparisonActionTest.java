package org.rostilos.codecrow.vcsclient.github.actions;

import com.fasterxml.jackson.databind.JsonNode;
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

class GetCommitComparisonActionTest {
    private static final String BASE = "a".repeat(40);
    private static final String HEAD = "b".repeat(40);
    private static final String MERGE_BASE = "c".repeat(40);

    private MockWebServer server;
    private GetCommitComparisonAction action;

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
        action = new GetCommitComparisonAction(client);
    }

    @AfterEach
    void tearDown() throws IOException {
        server.shutdown();
    }

    @Test
    void parsesMergeBaseCommitFromExactComparison() throws Exception {
        server.enqueue(new MockResponse().setBody(
                "{\"merge_base_commit\":{\"sha\":\"" + MERGE_BASE + "\"}}"));

        JsonNode comparison = action.getCommitComparison("acme", "repo", BASE, HEAD);

        assertThat(comparison.path("merge_base_commit").path("sha").asText())
                .isEqualTo(MERGE_BASE);
        assertThat(server.takeRequest().getPath())
                .isEqualTo("/repos/acme/repo/compare/" + BASE + "..." + HEAD);
    }

    @Test
    void propagatesProviderError() {
        server.enqueue(new MockResponse().setResponseCode(502).setBody("upstream failed"));

        assertThatThrownBy(() -> action.getCommitComparison("acme", "repo", BASE, HEAD))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("502");
    }

    @Test
    void rejectsNonExactCommitBeforeCallingProvider() {
        assertThatThrownBy(() -> action.getCommitComparison("acme", "repo", "main", HEAD))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("baseCommitHash");
        assertThat(server.getRequestCount()).isZero();
    }
}
