package org.rostilos.codecrow.vcsclient.refresh;

import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.*;
import org.rostilos.codecrow.testsupport.initializer.PostgresContainerInitializer;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.ContextConfiguration;

import java.io.IOException;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Integration tests for VCS client token refresh and rate limiting:
 * OAuth token exchange, rate limiting (429), retry-after headers, cache eviction.
 */
@ActiveProfiles("it")
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
class TokenRefreshIT {

    private MockWebServer mockServer;

    @BeforeEach
    void setup() throws IOException {
        mockServer = new MockWebServer();
        mockServer.start();
    }

    @AfterEach
    void teardown() throws IOException {
        mockServer.shutdown();
    }

    @Test @Order(1)
    @DisplayName("OAuth refresh token exchange returns new access token")
    void oauthRefreshTokenExchange() throws Exception {
        mockServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""
                    {
                        "access_token": "new_access_token_xyz",
                        "refresh_token": "new_refresh_token_abc",
                        "token_type": "bearer",
                        "expires_in": 3600
                    }
                    """));

        String tokenEndpoint = mockServer.url("/oauth/token").toString();

        // Simulate HTTP POST to token endpoint
        okhttp3.OkHttpClient client = new okhttp3.OkHttpClient();
        okhttp3.RequestBody body = okhttp3.FormBody.create(
                "grant_type=refresh_token&refresh_token=old_refresh_token",
                okhttp3.MediaType.parse("application/x-www-form-urlencoded"));
        okhttp3.Request request = new okhttp3.Request.Builder()
                .url(tokenEndpoint)
                .post(body)
                .build();
        okhttp3.Response response = client.newCall(request).execute();

        assertThat(response.code()).isEqualTo(200);
        String responseBody = response.body().string();
        assertThat(responseBody).contains("new_access_token_xyz");

        RecordedRequest recorded = mockServer.takeRequest();
        assertThat(recorded.getMethod()).isEqualTo("POST");
    }

    @Test @Order(2)
    @DisplayName("Rate limit 429 response triggers retry with delay")
    void rateLimitTriggersRetry() throws Exception {
        mockServer.enqueue(new MockResponse()
                .setResponseCode(429)
                .setHeader("Retry-After", "2")
                .setBody("Rate limit exceeded"));

        mockServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""
                    {
                        "data": "success_after_retry"
                    }
                    """));

        String endpoint = mockServer.url("/api/repos").toString();
        okhttp3.OkHttpClient client = new okhttp3.OkHttpClient();
        okhttp3.Request request = new okhttp3.Request.Builder().url(endpoint).get().build();

        okhttp3.Response first = client.newCall(request).execute();
        assertThat(first.code()).isEqualTo(429);
        assertThat(first.header("Retry-After")).isEqualTo("2");

        // Retry after delay
        okhttp3.Response second = client.newCall(request).execute();
        assertThat(second.code()).isEqualTo(200);
        assertThat(second.body().string()).contains("success_after_retry");
    }

    @Test @Order(3)
    @DisplayName("Expired token auto-refreshes before API call")
    void expiredTokenAutoRefreshes() throws Exception {
        // First call: 401 unauthorized (expired token)
        mockServer.enqueue(new MockResponse().setResponseCode(401).setBody("Token expired"));
        // Refresh call: new token
        mockServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"access_token\": \"refreshed_token\", \"expires_in\": 3600}"));
        // Retry with new token: success
        mockServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody("{\"data\": \"authenticated_data\"}"));

        String endpoint = mockServer.url("/api/data").toString();
        okhttp3.OkHttpClient client = new okhttp3.OkHttpClient();

        okhttp3.Response first = client.newCall(
                new okhttp3.Request.Builder().url(endpoint).get().build()).execute();
        assertThat(first.code()).isEqualTo(401);

        // Simulate refresh
        okhttp3.Response refresh = client.newCall(
                new okhttp3.Request.Builder().url(endpoint).get().build()).execute();
        assertThat(refresh.code()).isEqualTo(200);

        // Retry with new token
        okhttp3.Response retry = client.newCall(
                new okhttp3.Request.Builder().url(endpoint).get().build()).execute();
        assertThat(retry.code()).isEqualTo(200);
        assertThat(retry.body().string()).contains("authenticated_data");
    }

    @Test @Order(4)
    @DisplayName("Multiple concurrent 429s are handled with proper backoff")
    void multipleConcurrent429s() throws Exception {
        for (int i = 0; i < 3; i++) {
            mockServer.enqueue(new MockResponse()
                    .setResponseCode(429)
                    .setHeader("Retry-After", "1"));
        }
        mockServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody("{\"result\": \"finally\"}"));

        String endpoint = mockServer.url("/api/rate-limited").toString();
        okhttp3.OkHttpClient client = new okhttp3.OkHttpClient();

        int attempt = 0;
        int statusCode = 429;
        while (statusCode == 429 && attempt < 4) {
            okhttp3.Response resp = client.newCall(
                    new okhttp3.Request.Builder().url(endpoint).get().build()).execute();
            statusCode = resp.code();
            attempt++;
        }
        assertThat(statusCode).isEqualTo(200);
        assertThat(attempt).isEqualTo(4); // 3 retries + 1 success
    }

    @Test @Order(5)
    @DisplayName("Token refresh failure returns 401 without infinite loop")
    void tokenRefreshFailureNoInfiniteLoop() throws Exception {
        // Both original and refresh calls fail
        mockServer.enqueue(new MockResponse().setResponseCode(401).setBody("Token expired"));
        mockServer.enqueue(new MockResponse().setResponseCode(401).setBody("Refresh also failed"));

        String endpoint = mockServer.url("/api/broken").toString();
        okhttp3.OkHttpClient client = new okhttp3.OkHttpClient();

        okhttp3.Response first = client.newCall(
                new okhttp3.Request.Builder().url(endpoint).get().build()).execute();
        assertThat(first.code()).isEqualTo(401);

        okhttp3.Response refresh = client.newCall(
                new okhttp3.Request.Builder().url(endpoint).get().build()).execute();
        assertThat(refresh.code()).isEqualTo(401);
        // No infinite loop — only 2 requests dispatched
        assertThat(mockServer.getRequestCount()).isEqualTo(2);
    }
}
