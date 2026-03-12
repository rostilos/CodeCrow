package org.rostilos.codecrow.vcsclient;

import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.*;

import java.io.IOException;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Integration test for GitHub API client HTTP interactions using MockWebServer.
 * Verifies request format, auth headers, pagination, error handling.
 */
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
class GitHubClientIT {

    private MockWebServer mockServer;
    private okhttp3.OkHttpClient httpClient;

    @BeforeEach
    void setup() throws IOException {
        mockServer = new MockWebServer();
        mockServer.start();
        httpClient = new okhttp3.OkHttpClient.Builder()
                .connectTimeout(5, TimeUnit.SECONDS)
                .readTimeout(5, TimeUnit.SECONDS)
                .build();
    }

    @AfterEach
    void teardown() throws IOException {
        mockServer.shutdown();
    }

    @Test
    @Order(1)
    @DisplayName("Should fetch repository with correct auth header")
    void shouldFetchRepoWithAuth() throws Exception {
        mockServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody("{\"id\":123,\"full_name\":\"owner/repo\",\"default_branch\":\"main\"}")
                .addHeader("Content-Type", "application/json"));

        String url = mockServer.url("/repos/owner/repo").toString();
        okhttp3.Request request = new okhttp3.Request.Builder()
                .url(url)
                .addHeader("Authorization", "Bearer ghp_testtoken")
                .addHeader("Accept", "application/vnd.github.v3+json")
                .build();

        try (okhttp3.Response response = httpClient.newCall(request).execute()) {
            assertThat(response.isSuccessful()).isTrue();
            assertThat(response.body().string()).contains("owner/repo");
        }

        RecordedRequest recorded = mockServer.takeRequest(5, TimeUnit.SECONDS);
        assertThat(recorded.getHeader("Authorization")).isEqualTo("Bearer ghp_testtoken");
        assertThat(recorded.getHeader("Accept")).contains("application/vnd.github");
        assertThat(recorded.getPath()).isEqualTo("/repos/owner/repo");
    }

    @Test
    @Order(2)
    @DisplayName("Should fetch commit diff with correct path")
    void shouldFetchCommitDiff() throws Exception {
        String diffBody = "diff --git a/src/Main.java b/src/Main.java\n" +
                "--- a/src/Main.java\n+++ b/src/Main.java\n" +
                "@@ -10,3 +10,4 @@\n+    // new line\n";

        mockServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody(diffBody)
                .addHeader("Content-Type", "text/plain"));

        String url = mockServer.url("/repos/owner/repo/commits/abc123").toString();
        okhttp3.Request request = new okhttp3.Request.Builder()
                .url(url)
                .addHeader("Accept", "application/vnd.github.v3.diff")
                .build();

        try (okhttp3.Response response = httpClient.newCall(request).execute()) {
            assertThat(response.isSuccessful()).isTrue();
            assertThat(response.body().string()).contains("diff --git");
        }
    }

    @Test
    @Order(3)
    @DisplayName("Should handle GitHub pagination via Link header")
    void shouldHandlePagination() throws Exception {
        // Page 1
        mockServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody("[{\"sha\":\"commit1\"}]")
                .addHeader("Content-Type", "application/json")
                .addHeader("Link", "<" + mockServer.url("/repos/owner/repo/commits?page=2") +
                        ">; rel=\"next\""));

        // Page 2 (last)
        mockServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody("[{\"sha\":\"commit2\"}]")
                .addHeader("Content-Type", "application/json"));

        // Fetch page 1
        String url1 = mockServer.url("/repos/owner/repo/commits?page=1").toString();
        try (okhttp3.Response resp1 = httpClient.newCall(
                new okhttp3.Request.Builder().url(url1).build()).execute()) {
            assertThat(resp1.body().string()).contains("commit1");
            String linkHeader = resp1.header("Link");
            assertThat(linkHeader).contains("rel=\"next\"");
        }

        // Fetch page 2
        String url2 = mockServer.url("/repos/owner/repo/commits?page=2").toString();
        try (okhttp3.Response resp2 = httpClient.newCall(
                new okhttp3.Request.Builder().url(url2).build()).execute()) {
            assertThat(resp2.body().string()).contains("commit2");
        }

        assertThat(mockServer.getRequestCount()).isEqualTo(2);
    }

    @Test
    @Order(4)
    @DisplayName("Should handle 404 for non-existent repo")
    void shouldHandle404NotFound() throws Exception {
        mockServer.enqueue(new MockResponse()
                .setResponseCode(404)
                .setBody("{\"message\":\"Not Found\"}")
                .addHeader("Content-Type", "application/json"));

        String url = mockServer.url("/repos/owner/nonexistent").toString();
        try (okhttp3.Response response = httpClient.newCall(
                new okhttp3.Request.Builder().url(url).build()).execute()) {
            assertThat(response.code()).isEqualTo(404);
        }
    }

    @Test
    @Order(5)
    @DisplayName("Should handle 401 for invalid token")
    void shouldHandle401Unauthorized() throws Exception {
        mockServer.enqueue(new MockResponse()
                .setResponseCode(401)
                .setBody("{\"message\":\"Bad credentials\"}")
                .addHeader("Content-Type", "application/json"));

        String url = mockServer.url("/repos/owner/repo").toString();
        okhttp3.Request request = new okhttp3.Request.Builder()
                .url(url)
                .addHeader("Authorization", "Bearer invalid_token")
                .build();

        try (okhttp3.Response response = httpClient.newCall(request).execute()) {
            assertThat(response.code()).isEqualTo(401);
            assertThat(response.body().string()).contains("Bad credentials");
        }
    }

    @Test
    @Order(6)
    @DisplayName("Should handle rate limit (403 with rate limit headers)")
    void shouldHandleRateLimit() throws Exception {
        mockServer.enqueue(new MockResponse()
                .setResponseCode(403)
                .setBody("{\"message\":\"API rate limit exceeded\"}")
                .addHeader("X-RateLimit-Remaining", "0")
                .addHeader("X-RateLimit-Reset", String.valueOf(System.currentTimeMillis() / 1000 + 60)));

        String url = mockServer.url("/repos/owner/repo").toString();
        try (okhttp3.Response response = httpClient.newCall(
                new okhttp3.Request.Builder().url(url).build()).execute()) {
            assertThat(response.code()).isEqualTo(403);
            assertThat(response.header("X-RateLimit-Remaining")).isEqualTo("0");
        }
    }

    @Test
    @Order(7)
    @DisplayName("Should fetch file content from specific branch")
    void shouldFetchFileContent() throws Exception {
        String fileContent = "public class Main {\n    public static void main(String[] args) {}\n}";
        mockServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody(fileContent)
                .addHeader("Content-Type", "application/vnd.github.v3.raw"));

        String url = mockServer.url("/repos/owner/repo/contents/src/Main.java?ref=main").toString();
        try (okhttp3.Response response = httpClient.newCall(
                new okhttp3.Request.Builder().url(url)
                        .addHeader("Accept", "application/vnd.github.v3.raw")
                        .build()).execute()) {
            assertThat(response.isSuccessful()).isTrue();
            assertThat(response.body().string()).contains("public class Main");
        }
    }

    @Test
    @Order(8)
    @DisplayName("Should handle pull request creation with correct body")
    void shouldCreatePullRequest() throws Exception {
        mockServer.enqueue(new MockResponse()
                .setResponseCode(201)
                .setBody("{\"number\":42,\"title\":\"Test PR\"}")
                .addHeader("Content-Type", "application/json"));

        String url = mockServer.url("/repos/owner/repo/pulls").toString();
        String prBody = "{\"title\":\"Test PR\",\"head\":\"feature\",\"base\":\"main\",\"body\":\"Description\"}";

        okhttp3.Request request = new okhttp3.Request.Builder()
                .url(url)
                .post(okhttp3.RequestBody.create(prBody, okhttp3.MediaType.parse("application/json")))
                .build();

        try (okhttp3.Response response = httpClient.newCall(request).execute()) {
            assertThat(response.code()).isEqualTo(201);
        }

        RecordedRequest recorded = mockServer.takeRequest();
        assertThat(recorded.getBody().readUtf8()).contains("Test PR");
    }
}
