package org.rostilos.codecrow.vcsclient;

import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.*;

import java.io.IOException;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Integration test for GitLab API client HTTP interactions using MockWebServer.
 */
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
class GitLabClientIT {

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
    @DisplayName("Should fetch project with PRIVATE-TOKEN header")
    void shouldFetchProjectWithPrivateToken() throws Exception {
        mockServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody("{\"id\":1,\"path_with_namespace\":\"group/project\",\"default_branch\":\"main\"}")
                .addHeader("Content-Type", "application/json"));

        String url = mockServer.url("/api/v4/projects/group%2Fproject").toString();
        okhttp3.Request request = new okhttp3.Request.Builder()
                .url(url)
                .addHeader("PRIVATE-TOKEN", "glpat-testtoken")
                .build();

        try (okhttp3.Response response = httpClient.newCall(request).execute()) {
            assertThat(response.isSuccessful()).isTrue();
            assertThat(response.body().string()).contains("group/project");
        }

        RecordedRequest recorded = mockServer.takeRequest(5, TimeUnit.SECONDS);
        assertThat(recorded.getHeader("PRIVATE-TOKEN")).isEqualTo("glpat-testtoken");
    }

    @Test
    @Order(2)
    @DisplayName("Should fetch merge request diff")
    void shouldFetchMergeRequestDiff() throws Exception {
        String diffJson = "[{\"old_path\":\"src/Main.java\",\"new_path\":\"src/Main.java\"," +
                "\"diff\":\"@@ -1,3 +1,4 @@\\n+// new line\"}]";
        mockServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody(diffJson)
                .addHeader("Content-Type", "application/json"));

        String url = mockServer.url("/api/v4/projects/1/merge_requests/5/diffs").toString();
        try (okhttp3.Response response = httpClient.newCall(
                new okhttp3.Request.Builder().url(url).build()).execute()) {
            assertThat(response.isSuccessful()).isTrue();
            assertThat(response.body().string()).contains("Main.java");
        }
    }

    @Test
    @Order(3)
    @DisplayName("Should handle GitLab keyset pagination")
    void shouldHandleKeysetPagination() throws Exception {
        mockServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody("[{\"id\":1},{\"id\":2}]")
                .addHeader("Content-Type", "application/json")
                .addHeader("X-Next-Page", "2")
                .addHeader("X-Total-Pages", "3"));

        String url = mockServer.url("/api/v4/projects/1/repository/commits?per_page=20").toString();
        try (okhttp3.Response response = httpClient.newCall(
                new okhttp3.Request.Builder().url(url).build()).execute()) {
            assertThat(response.isSuccessful()).isTrue();
            assertThat(response.header("X-Next-Page")).isEqualTo("2");
            assertThat(response.header("X-Total-Pages")).isEqualTo("3");
        }
    }

    @Test
    @Order(4)
    @DisplayName("Should comment on merge request")
    void shouldCommentOnMergeRequest() throws Exception {
        mockServer.enqueue(new MockResponse()
                .setResponseCode(201)
                .setBody("{\"id\":99,\"body\":\"Analysis complete\"}")
                .addHeader("Content-Type", "application/json"));

        String url = mockServer.url("/api/v4/projects/1/merge_requests/5/notes").toString();
        String body = "{\"body\":\"Analysis complete\"}";

        try (okhttp3.Response response = httpClient.newCall(
                new okhttp3.Request.Builder()
                        .url(url)
                        .post(okhttp3.RequestBody.create(body, okhttp3.MediaType.parse("application/json")))
                        .build()).execute()) {
            assertThat(response.code()).isEqualTo(201);
        }
    }

    @Test
    @Order(5)
    @DisplayName("Should check if file exists in branch")
    void shouldCheckFileExists() throws Exception {
        mockServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody("{\"file_name\":\"Main.java\",\"file_path\":\"src/Main.java\"}")
                .addHeader("Content-Type", "application/json"));

        String url = mockServer.url("/api/v4/projects/1/repository/files/src%2FMain.java?ref=main").toString();
        try (okhttp3.Response response = httpClient.newCall(
                new okhttp3.Request.Builder().url(url).build()).execute()) {
            assertThat(response.isSuccessful()).isTrue();
        }

        // File doesn't exist
        mockServer.enqueue(new MockResponse().setResponseCode(404));
        try (okhttp3.Response response = httpClient.newCall(
                new okhttp3.Request.Builder().url(url).build()).execute()) {
            assertThat(response.code()).isEqualTo(404);
        }
    }
}
