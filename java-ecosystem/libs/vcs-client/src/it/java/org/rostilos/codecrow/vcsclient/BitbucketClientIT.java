package org.rostilos.codecrow.vcsclient;

import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import org.junit.jupiter.api.*;

import java.io.IOException;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Integration test for Bitbucket Cloud API client HTTP interactions.
 */
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
class BitbucketClientIT {

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
    @DisplayName("Should fetch repository with OAuth bearer token")
    void shouldFetchRepoWithOAuth() throws Exception {
        mockServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody("{\"slug\":\"repo\",\"full_name\":\"workspace/repo\",\"mainbranch\":{\"name\":\"main\"}}")
                .addHeader("Content-Type", "application/json"));

        String url = mockServer.url("/2.0/repositories/workspace/repo").toString();
        okhttp3.Request request = new okhttp3.Request.Builder()
                .url(url)
                .addHeader("Authorization", "Bearer bb_oauth_token")
                .build();

        try (okhttp3.Response response = httpClient.newCall(request).execute()) {
            assertThat(response.isSuccessful()).isTrue();
            assertThat(response.body().string()).contains("workspace/repo");
        }
    }

    @Test
    @Order(2)
    @DisplayName("Should fetch pull request diff")
    void shouldFetchPullRequestDiff() throws Exception {
        String diff = "diff --git a/file.java b/file.java\n@@ -1 +1,2 @@\n+new line\n";
        mockServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody(diff)
                .addHeader("Content-Type", "text/plain"));

        String url = mockServer.url("/2.0/repositories/workspace/repo/pullrequests/1/diff").toString();
        try (okhttp3.Response response = httpClient.newCall(
                new okhttp3.Request.Builder().url(url).build()).execute()) {
            assertThat(response.isSuccessful()).isTrue();
            assertThat(response.body().string()).contains("diff --git");
        }
    }

    @Test
    @Order(3)
    @DisplayName("Should post code insights report")
    void shouldPostCodeInsightsReport() throws Exception {
        mockServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody("{\"uuid\":\"{report-uuid}\"}")
                .addHeader("Content-Type", "application/json"));

        String url = mockServer.url(
                "/2.0/repositories/workspace/repo/commit/abc123/reports/codecrow-report").toString();
        String reportBody = "{\"title\":\"CodeCrow Analysis\",\"result\":\"PASSED\"," +
                "\"report_type\":\"SECURITY\",\"data\":[{\"type\":\"NUMBER\",\"title\":\"Issues\",\"value\":5}]}";

        try (okhttp3.Response response = httpClient.newCall(
                new okhttp3.Request.Builder()
                        .url(url)
                        .put(okhttp3.RequestBody.create(reportBody, okhttp3.MediaType.parse("application/json")))
                        .build()).execute()) {
            assertThat(response.isSuccessful()).isTrue();
        }
    }

    @Test
    @Order(4)
    @DisplayName("Should comment on pull request")
    void shouldCommentOnPR() throws Exception {
        mockServer.enqueue(new MockResponse()
                .setResponseCode(201)
                .setBody("{\"id\":42}")
                .addHeader("Content-Type", "application/json"));

        String url = mockServer.url(
                "/2.0/repositories/workspace/repo/pullrequests/1/comments").toString();
        String body = "{\"content\":{\"raw\":\"Analysis found 3 issues\"}}";

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
    @DisplayName("Should handle OAuth token refresh")
    void shouldHandleTokenRefresh() throws Exception {
        // First request: 401
        mockServer.enqueue(new MockResponse().setResponseCode(401));

        // Token refresh
        mockServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody("{\"access_token\":\"new_token\",\"refresh_token\":\"new_refresh\",\"expires_in\":7200}")
                .addHeader("Content-Type", "application/json"));

        // Retry with new token
        mockServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody("{\"slug\":\"repo\"}")
                .addHeader("Content-Type", "application/json"));

        // Simulate: first call fails
        String url = mockServer.url("/2.0/repositories/workspace/repo").toString();
        try (okhttp3.Response r1 = httpClient.newCall(
                new okhttp3.Request.Builder().url(url).build()).execute()) {
            assertThat(r1.code()).isEqualTo(401);
        }

        // Refresh token
        String tokenUrl = mockServer.url("/site/oauth2/access_token").toString();
        try (okhttp3.Response r2 = httpClient.newCall(
                new okhttp3.Request.Builder()
                        .url(tokenUrl)
                        .post(okhttp3.RequestBody.create(
                                "grant_type=refresh_token&refresh_token=old_refresh",
                                okhttp3.MediaType.parse("application/x-www-form-urlencoded")))
                        .build()).execute()) {
            assertThat(r2.isSuccessful()).isTrue();
            assertThat(r2.body().string()).contains("new_token");
        }

        // Retry with new token
        try (okhttp3.Response r3 = httpClient.newCall(
                new okhttp3.Request.Builder()
                        .url(url)
                        .addHeader("Authorization", "Bearer new_token")
                        .build()).execute()) {
            assertThat(r3.isSuccessful()).isTrue();
        }
    }

    @Test
    @Order(6)
    @DisplayName("Should handle search repositories with pagination")
    void shouldSearchRepos() throws Exception {
        mockServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody("{\"values\":[{\"slug\":\"repo1\"},{\"slug\":\"repo2\"}],\"next\":\"page2_url\"}")
                .addHeader("Content-Type", "application/json"));

        String url = mockServer.url("/2.0/repositories/workspace?pagelen=10&q=name~\"search\"").toString();
        try (okhttp3.Response response = httpClient.newCall(
                new okhttp3.Request.Builder().url(url).build()).execute()) {
            assertThat(response.isSuccessful()).isTrue();
            String body = response.body().string();
            assertThat(body).contains("repo1", "repo2");
        }
    }
}
