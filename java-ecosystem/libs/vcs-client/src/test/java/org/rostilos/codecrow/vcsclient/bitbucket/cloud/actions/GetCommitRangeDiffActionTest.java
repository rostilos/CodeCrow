package org.rostilos.codecrow.vcsclient.bitbucket.cloud.actions;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okio.Buffer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class GetCommitRangeDiffActionTest {
    private MockWebServer server;
    private GetCommitRangeDiffAction action;

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
        action = new GetCommitRangeDiffAction(client);
    }

    @AfterEach
    void tearDown() throws IOException {
        server.shutdown();
    }

    @Test
    void putsBitbucketSourceHeadBeforeDestinationBase() throws Exception {
        server.enqueue(new MockResponse().setBody("diff content"));

        assertThat(action.getCommitRangeDiff(
                "workspace", "repo", "base123", "head456"))
                .isEqualTo("diff content");
        assertThat(server.takeRequest().getPath()).isEqualTo(
                "/2.0/repositories/workspace/repo/diff/head456..base123");
    }

    @Test
    void propagatesProviderError() {
        server.enqueue(new MockResponse().setResponseCode(404).setBody("Not found"));

        assertThatThrownBy(() -> action.getCommitRangeDiff(
                "workspace", "repo", "base123", "head456"))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("404");
    }

    @Test
    void preservesAnEmptyWorkspaceInTheRequestPath() throws Exception {
        server.enqueue(new MockResponse().setBody("diff content"));

        action.getCommitRangeDiff(null, "repo", "base123", "head456");

        assertThat(server.takeRequest().getPath()).isEqualTo(
                "/2.0/repositories//repo/diff/head456..base123");
    }

    @Test
    void rejectsMalformedUtf8DiffBytes() {
        Buffer malformed = new Buffer().writeByte(0xff);
        server.enqueue(new MockResponse().setBody(malformed));

        assertThatThrownBy(() -> action.getCommitRangeDiff(
                "workspace", "repo", "base123", "head456"))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("valid UTF-8");
    }
}
