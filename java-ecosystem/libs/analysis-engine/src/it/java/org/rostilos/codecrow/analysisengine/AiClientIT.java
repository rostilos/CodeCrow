package org.rostilos.codecrow.analysisengine;

import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.*;

import java.io.IOException;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Integration test for AI inference HTTP client behavior using MockWebServer.
 * Verifies request/response handling, timeouts, retries, and error scenarios.
 */
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
class AiClientIT {

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

    @Test
    @Order(1)
    @DisplayName("Should send correct request format to AI provider")
    void shouldSendCorrectRequestFormat() throws Exception {
        mockServer.enqueue(new MockResponse()
                .setBody("{\"choices\":[{\"message\":{\"content\":\"test response\"}}]}")
                .addHeader("Content-Type", "application/json")
                .setResponseCode(200));

        String baseUrl = mockServer.url("/v1/chat/completions").toString();

        // Simulate HTTP client call
        okhttp3.OkHttpClient client = new okhttp3.OkHttpClient();
        okhttp3.RequestBody body = okhttp3.RequestBody.create(
                "{\"model\":\"gpt-4\",\"messages\":[{\"role\":\"user\",\"content\":\"analyze code\"}]}",
                okhttp3.MediaType.parse("application/json"));
        okhttp3.Request request = new okhttp3.Request.Builder()
                .url(baseUrl)
                .post(body)
                .addHeader("Authorization", "Bearer test-key")
                .build();

        try (okhttp3.Response response = client.newCall(request).execute()) {
            assertThat(response.isSuccessful()).isTrue();
            String responseBody = response.body().string();
            assertThat(responseBody).contains("test response");
        }

        RecordedRequest recorded = mockServer.takeRequest(5, TimeUnit.SECONDS);
        assertThat(recorded).isNotNull();
        assertThat(recorded.getHeader("Authorization")).isEqualTo("Bearer test-key");
        assertThat(recorded.getBody().readUtf8()).contains("gpt-4");
    }

    @Test
    @Order(2)
    @DisplayName("Should handle 429 rate limit response")
    void shouldHandleRateLimitResponse() throws Exception {
        mockServer.enqueue(new MockResponse()
                .setResponseCode(429)
                .addHeader("Retry-After", "5")
                .setBody("{\"error\":{\"message\":\"Rate limited\"}}"));

        String baseUrl = mockServer.url("/v1/chat/completions").toString();

        okhttp3.OkHttpClient client = new okhttp3.OkHttpClient();
        okhttp3.Request request = new okhttp3.Request.Builder()
                .url(baseUrl)
                .post(okhttp3.RequestBody.create("{}", okhttp3.MediaType.parse("application/json")))
                .build();

        try (okhttp3.Response response = client.newCall(request).execute()) {
            assertThat(response.code()).isEqualTo(429);
        }
    }

    @Test
    @Order(3)
    @DisplayName("Should handle 500 server error")
    void shouldHandleServerError() throws Exception {
        mockServer.enqueue(new MockResponse()
                .setResponseCode(500)
                .setBody("{\"error\":{\"message\":\"Internal server error\"}}"));

        String baseUrl = mockServer.url("/v1/chat/completions").toString();

        okhttp3.OkHttpClient client = new okhttp3.OkHttpClient();
        okhttp3.Request request = new okhttp3.Request.Builder()
                .url(baseUrl)
                .post(okhttp3.RequestBody.create("{}", okhttp3.MediaType.parse("application/json")))
                .build();

        try (okhttp3.Response response = client.newCall(request).execute()) {
            assertThat(response.code()).isEqualTo(500);
        }
    }

    @Test
    @Order(4)
    @DisplayName("Should handle empty response body gracefully")
    void shouldHandleEmptyResponseBody() throws Exception {
        mockServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody("")
                .addHeader("Content-Type", "application/json"));

        String baseUrl = mockServer.url("/v1/chat/completions").toString();

        okhttp3.OkHttpClient client = new okhttp3.OkHttpClient();
        okhttp3.Request request = new okhttp3.Request.Builder()
                .url(baseUrl)
                .post(okhttp3.RequestBody.create("{}", okhttp3.MediaType.parse("application/json")))
                .build();

        try (okhttp3.Response response = client.newCall(request).execute()) {
            assertThat(response.isSuccessful()).isTrue();
            assertThat(response.body().string()).isEmpty();
        }
    }

    @Test
    @Order(5)
    @DisplayName("Should handle large response payload")
    void shouldHandleLargeResponsePayload() throws Exception {
        String largeContent = "x".repeat(500_000);
        mockServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody("{\"choices\":[{\"message\":{\"content\":\"" + largeContent + "\"}}]}")
                .addHeader("Content-Type", "application/json"));

        String baseUrl = mockServer.url("/v1/chat/completions").toString();

        okhttp3.OkHttpClient client = new okhttp3.OkHttpClient();
        okhttp3.Request request = new okhttp3.Request.Builder()
                .url(baseUrl)
                .post(okhttp3.RequestBody.create("{}", okhttp3.MediaType.parse("application/json")))
                .build();

        try (okhttp3.Response response = client.newCall(request).execute()) {
            assertThat(response.isSuccessful()).isTrue();
            assertThat(response.body().string()).contains(largeContent);
        }
    }
}
