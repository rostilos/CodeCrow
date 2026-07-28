package org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions;

import okhttp3.OkHttpClient;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class GetCommitActionTest {
    private MockWebServer server;

    private GetCommitAction action() throws IOException {
        server = new MockWebServer();
        server.start();
        return new GetCommitAction(new OkHttpClient(), server.url("/").toString());
    }

    @AfterEach
    void tearDown() throws IOException {
        if (server != null) {
            server.shutdown();
        }
    }

    @Test
    void resolvesAbbreviatedReferenceToCanonicalHash() throws Exception {
        String canonicalHash = "eb59a730e56532cc96d0e9fbb6b7616d6ca9897e";
        GetCommitAction action = action();
        server.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"hash\":\"" + canonicalHash + "\"}"));

        String result = action.resolveCommitHash("codecrowai", "test", "eb59a730e565");

        assertThat(result).isEqualTo(canonicalHash);
        RecordedRequest request = server.takeRequest();
        assertThat(request.getMethod()).isEqualTo("GET");
        assertThat(request.getPath())
                .isEqualTo("/repositories/codecrowai/test/commit/eb59a730e565");
    }

    @Test
    void rejectsMissingCommitReferenceWithoutRemoteCall() throws IOException {
        GetCommitAction action = action();
        assertThatThrownBy(() -> action.resolveCommitHash("workspace", "repo", " "))
                .isInstanceOf(IOException.class)
                .hasMessage("Bitbucket commit reference is required");
        assertThat(server.getRequestCount()).isZero();
    }

    @Test
    void rejectsSuccessfulResponseWithoutCanonicalHash() throws IOException {
        GetCommitAction action = action();
        server.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{}"));

        assertThatThrownBy(() -> action.resolveCommitHash("workspace", "repo", "abc123def456"))
                .isInstanceOf(IOException.class)
                .hasMessage("Bitbucket commit response did not contain a canonical hash");
    }

    @Test
    void propagatesProviderFailureWithoutEmbeddingRequestUrl() throws IOException {
        GetCommitAction action = action();
        server.enqueue(new MockResponse()
                .setResponseCode(404)
                .setBody("Not found"));

        assertThatThrownBy(() -> action.resolveCommitHash("workspace", "repo", "missing"))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("404")
                .hasMessageContaining("Not found")
                .hasMessageNotContaining("https://");
    }
}
