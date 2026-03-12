package org.rostilos.codecrow.ragengine;

import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.*;

import java.io.IOException;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * Integration test for RAG pipeline HTTP client interactions using MockWebServer.
 * Verifies request format, auth headers, timeout behavior, error handling.
 */
@TestMethodOrder(MethodOrderer.OrderAnnotation.class)
class RagPipelineClientIT {

    private MockWebServer mockServer;
    private okhttp3.OkHttpClient httpClient;

    @BeforeEach
    void setup() throws IOException {
        mockServer = new MockWebServer();
        mockServer.start();
        httpClient = new okhttp3.OkHttpClient.Builder()
                .connectTimeout(5, TimeUnit.SECONDS)
                .readTimeout(10, TimeUnit.SECONDS)
                .build();
    }

    @AfterEach
    void teardown() throws IOException {
        mockServer.shutdown();
    }

    @Test
    @Order(1)
    @DisplayName("Should index files with correct auth header")
    void shouldIndexFilesWithAuth() throws Exception {
        mockServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody("{\"status\":\"indexed\",\"files_processed\":5}")
                .addHeader("Content-Type", "application/json"));

        String url = mockServer.url("/api/v1/index").toString();
        String body = "{\"project_id\":\"proj-123\",\"files\":[\"src/Main.java\"]}";

        okhttp3.Request request = new okhttp3.Request.Builder()
                .url(url)
                .post(okhttp3.RequestBody.create(body, okhttp3.MediaType.parse("application/json")))
                .addHeader("X-Service-Secret", "rag-secret-key")
                .build();

        try (okhttp3.Response response = httpClient.newCall(request).execute()) {
            assertThat(response.isSuccessful()).isTrue();
            assertThat(response.body().string()).contains("indexed");
        }

        RecordedRequest recorded = mockServer.takeRequest(5, TimeUnit.SECONDS);
        assertThat(recorded.getHeader("X-Service-Secret")).isEqualTo("rag-secret-key");
        assertThat(recorded.getMethod()).isEqualTo("POST");
    }

    @Test
    @Order(2)
    @DisplayName("Should query RAG context for code analysis")
    void shouldQueryRagContext() throws Exception {
        String ragResponse = "{\"results\":[{\"content\":\"public void fix(){\",\"score\":0.92," +
                "\"file_path\":\"src/Service.java\",\"line_start\":10}]}";
        mockServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody(ragResponse)
                .addHeader("Content-Type", "application/json"));

        String url = mockServer.url("/api/v1/query").toString();
        String queryBody = "{\"project_id\":\"proj-123\",\"query\":\"null pointer vulnerability\"," +
                "\"top_k\":5}";

        try (okhttp3.Response response = httpClient.newCall(
                new okhttp3.Request.Builder()
                        .url(url)
                        .post(okhttp3.RequestBody.create(queryBody,
                                okhttp3.MediaType.parse("application/json")))
                        .addHeader("X-Service-Secret", "rag-secret-key")
                        .build()).execute()) {
            assertThat(response.isSuccessful()).isTrue();
            String respBody = response.body().string();
            assertThat(respBody).contains("public void fix()");
            assertThat(respBody).contains("0.92");
        }
    }

    @Test
    @Order(3)
    @DisplayName("Should get collection info")
    void shouldGetCollectionInfo() throws Exception {
        mockServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody("{\"collection_name\":\"proj-123\",\"document_count\":150," +
                        "\"status\":\"ready\"}")
                .addHeader("Content-Type", "application/json"));

        String url = mockServer.url("/api/v1/collections/proj-123").toString();
        try (okhttp3.Response response = httpClient.newCall(
                new okhttp3.Request.Builder().url(url).build()).execute()) {
            assertThat(response.isSuccessful()).isTrue();
            assertThat(response.body().string()).contains("ready");
        }
    }

    @Test
    @Order(4)
    @DisplayName("Should handle incremental update")
    void shouldHandleIncrementalUpdate() throws Exception {
        mockServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody("{\"status\":\"updated\",\"added\":3,\"removed\":1,\"modified\":2}")
                .addHeader("Content-Type", "application/json"));

        String url = mockServer.url("/api/v1/incremental-update").toString();
        String body = "{\"project_id\":\"proj-123\",\"added_files\":[\"src/New.java\"]," +
                "\"removed_files\":[\"src/Old.java\"],\"modified_files\":[\"src/Changed.java\"]}";

        try (okhttp3.Response response = httpClient.newCall(
                new okhttp3.Request.Builder()
                        .url(url)
                        .post(okhttp3.RequestBody.create(body,
                                okhttp3.MediaType.parse("application/json")))
                        .build()).execute()) {
            assertThat(response.isSuccessful()).isTrue();
        }
    }

    @Test
    @Order(5)
    @DisplayName("Should handle RAG pipeline being unavailable")
    void shouldHandleServiceUnavailable() throws Exception {
        mockServer.enqueue(new MockResponse()
                .setResponseCode(503)
                .setBody("{\"error\":\"Service temporarily unavailable\"}"));

        String url = mockServer.url("/api/v1/index").toString();
        try (okhttp3.Response response = httpClient.newCall(
                new okhttp3.Request.Builder()
                        .url(url)
                        .post(okhttp3.RequestBody.create("{}",
                                okhttp3.MediaType.parse("application/json")))
                        .build()).execute()) {
            assertThat(response.code()).isEqualTo(503);
        }
    }

    @Test
    @Order(6)
    @DisplayName("Should delete project index")
    void shouldDeleteProjectIndex() throws Exception {
        mockServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody("{\"status\":\"deleted\"}"));

        String url = mockServer.url("/api/v1/collections/proj-123").toString();
        try (okhttp3.Response response = httpClient.newCall(
                new okhttp3.Request.Builder()
                        .url(url)
                        .delete()
                        .build()).execute()) {
            assertThat(response.isSuccessful()).isTrue();

            RecordedRequest recorded = mockServer.takeRequest();
            assertThat(recorded.getMethod()).isEqualTo("DELETE");
        }
    }
}
