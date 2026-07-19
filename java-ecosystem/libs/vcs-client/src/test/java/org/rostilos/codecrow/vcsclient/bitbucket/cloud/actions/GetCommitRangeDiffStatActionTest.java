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

class GetCommitRangeDiffStatActionTest {
    private static final String MERGE_BASE = "a".repeat(40);
    private static final String HEAD = "b".repeat(40);

    private MockWebServer server;
    private GetCommitRangeDiffStatAction action;

    @BeforeEach
    void setUp() throws IOException {
        server = new MockWebServer();
        server.start();
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
        action = new GetCommitRangeDiffStatAction(client);
    }

    @AfterEach
    void tearDown() throws IOException {
        server.shutdown();
    }

    @Test
    void followsAllPagesAndNormalizesRemovedPaths() throws Exception {
        String next = "https://api.bitbucket.org/2.0/repositories/ws/repo/diffstat/"
                + HEAD + ".." + MERGE_BASE + "?page=2";
        server.enqueue(json("""
                {"values":[{"status":"modified","lines_added":2,"lines_removed":1,
                 "new":{"path":"src/A.java"}}],
                 "next":"%s"}
                """.formatted(next)));
        server.enqueue(json("""
                {"values":[{"status":"removed","lines_added":0,"lines_removed":3,
                 "old":{"path":"src/Old.java"}}]}
                """));

        assertThat(action.getFileStats("ws", "repo", MERGE_BASE, HEAD))
                .containsExactly(
                        new GetCommitRangeDiffStatAction.FileStat("src/A.java", 2, 1),
                        new GetCommitRangeDiffStatAction.FileStat("src/Old.java", 0, 3));
        assertThat(server.takeRequest().getPath()).contains(
                "/diffstat/" + HEAD + ".." + MERGE_BASE);
        assertThat(server.takeRequest().getPath()).contains("page=2");
    }

    @Test
    void rejectsDuplicateInventoryPaths() {
        server.enqueue(json("""
                {"values":[
                  {"status":"modified","lines_added":1,"lines_removed":1,
                   "new":{"path":"src/A.java"}},
                  {"status":"modified","lines_added":1,"lines_removed":1,
                   "new":{"path":"src/A.java"}}
                ]}
                """));

        assertThatThrownBy(() -> action.getFileStats("ws", "repo", MERGE_BASE, HEAD))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("duplicate path");
    }

    @Test
    void rejectsMissingOrInvalidLineCounts() {
        server.enqueue(json("""
                {"values":[{"status":"modified","lines_added":1,
                 "new":{"path":"src/A.java"}}]}
                """));

        assertThatThrownBy(() -> action.getFileStats("ws", "repo", MERGE_BASE, HEAD))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("invalid lines_removed");
    }

    @Test
    void rejectsUnsafePaginationUrl() {
        server.enqueue(json("""
                {"values":[],"next":"https://example.com/stolen"}
                """));

        assertThatThrownBy(() -> action.getFileStats("ws", "repo", MERGE_BASE, HEAD))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("unsafe next URL");
    }

    private MockResponse json(String body) {
        return new MockResponse().setHeader("Content-Type", "application/json").setBody(body);
    }
}
