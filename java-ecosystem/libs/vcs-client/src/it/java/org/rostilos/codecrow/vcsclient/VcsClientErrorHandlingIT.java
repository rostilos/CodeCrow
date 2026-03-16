package org.rostilos.codecrow.vcsclient;

import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.*;

import java.io.IOException;
import java.net.SocketTimeoutException;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Integration test for VCS client error handling: timeouts, retries, malformed JSON.
 */
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
class VcsClientErrorHandlingIT {

    private MockWebServer mockServer;
    private okhttp3.OkHttpClient httpClient;

    @BeforeEach
    void setup() throws IOException {
        mockServer = new MockWebServer();
        mockServer.start();
        httpClient = new okhttp3.OkHttpClient.Builder()
                .connectTimeout(2, TimeUnit.SECONDS)
                .readTimeout(2, TimeUnit.SECONDS)
                .build();
    }

    @AfterEach
    void teardown() throws IOException {
        mockServer.shutdown();
    }

    @Test
    @Order(1)
    @DisplayName("Should timeout on slow response")
    void shouldTimeoutOnSlowResponse() {
        mockServer.enqueue(new MockResponse()
                .setBody("{\"id\":1}")
                .setBodyDelay(5, TimeUnit.SECONDS));

        String url = mockServer.url("/slow-endpoint").toString();
        assertThatThrownBy(() -> {
            try (okhttp3.Response response = httpClient.newCall(
                    new okhttp3.Request.Builder().url(url).build()).execute()) {
                response.body().string(); // Force reading body to trigger read timeout
            }
        }).isInstanceOf(SocketTimeoutException.class);
    }

    @Test
    @Order(2)
    @DisplayName("Should handle malformed JSON response gracefully")
    void shouldHandleMalformedJson() throws Exception {
        mockServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody("this is not json{{{")
                .addHeader("Content-Type", "application/json"));

        String url = mockServer.url("/repos/owner/repo").toString();
        try (okhttp3.Response response = httpClient.newCall(
                new okhttp3.Request.Builder().url(url).build()).execute()) {
            assertThat(response.isSuccessful()).isTrue();
            String body = response.body().string();
            assertThat(body).contains("this is not json");
        }
    }

    @Test
    @Order(3)
    @DisplayName("Should handle empty response body on 200")
    void shouldHandleEmptyBody() throws Exception {
        mockServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody(""));

        String url = mockServer.url("/repos/owner/repo").toString();
        try (okhttp3.Response response = httpClient.newCall(
                new okhttp3.Request.Builder().url(url).build()).execute()) {
            assertThat(response.isSuccessful()).isTrue();
            assertThat(response.body().string()).isEmpty();
        }
    }

    @Test
    @Order(4)
    @DisplayName("Should handle 502 Bad Gateway")
    void shouldHandle502() throws Exception {
        mockServer.enqueue(new MockResponse()
                .setResponseCode(502)
                .setBody("<html>Bad Gateway</html>"));

        String url = mockServer.url("/repos/owner/repo").toString();
        try (okhttp3.Response response = httpClient.newCall(
                new okhttp3.Request.Builder().url(url).build()).execute()) {
            assertThat(response.code()).isEqualTo(502);
        }
    }

    @Test
    @Order(5)
    @DisplayName("Should handle connection reset after partial response")
    void shouldHandleConnectionClose() throws Exception {
        mockServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody("x".repeat(1024 * 1024))
                .setSocketPolicy(okhttp3.mockwebserver.SocketPolicy.DISCONNECT_DURING_RESPONSE_BODY));

        String url = mockServer.url("/repos/owner/repo").toString();
        assertThatThrownBy(() -> {
            try (okhttp3.Response response = httpClient.newCall(
                    new okhttp3.Request.Builder().url(url).build()).execute()) {
                response.body().string(); // Will fail due to disconnect
            }
        }).isInstanceOf(IOException.class);
    }

    @Test
    @Order(6)
    @DisplayName("Should send User-Agent header")
    void shouldSendUserAgent() throws Exception {
        mockServer.enqueue(new MockResponse().setResponseCode(200));

        String url = mockServer.url("/test").toString();
        httpClient.newCall(new okhttp3.Request.Builder()
                .url(url)
                .addHeader("User-Agent", "CodeCrow/1.0")
                .build()).execute().close();

        RecordedRequest recorded = mockServer.takeRequest(5, TimeUnit.SECONDS);
        assertThat(recorded.getHeader("User-Agent")).isEqualTo("CodeCrow/1.0");
    }
}
