package org.rostilos.codecrow.ragengine.client;

import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class RagPipelineClientTest {

    private MockWebServer mockWebServer;
    private RagPipelineClient client;
    private ObjectMapper objectMapper;

    @BeforeEach
    void setUp() throws IOException {
        mockWebServer = new MockWebServer();
        mockWebServer.start();
        
        String baseUrl = mockWebServer.url("/").toString();
        client = new RagPipelineClient(
                baseUrl,
                true,  // enabled
                5,     // connect timeout
                10,    // read timeout
                20,    // indexing timeout
                "test-secret"  // service secret
        );
        
        objectMapper = new ObjectMapper();
    }

    @AfterEach
    void tearDown() throws IOException {
        mockWebServer.shutdown();
    }

    @Test
    void testDeleteFiles_Success() throws Exception {
        Map<String, Object> mockResponse = Map.of(
                "status", "success",
                "deletedCount", 5
        );
        
        mockWebServer.enqueue(new MockResponse()
                .setBody(objectMapper.writeValueAsString(mockResponse))
                .addHeader("Content-Type", "application/json"));

        List<String> files = List.of("file1.java", "file2.java");
        Map<String, Object> result = client.deleteFiles(files, "workspace", "project", "main");

        assertThat(result).containsEntry("status", "success");
        assertThat(result).containsEntry("deletedCount", 5);
        
        RecordedRequest request = mockWebServer.takeRequest();
        assertThat(request.getPath()).contains("/delete");
        assertThat(request.getMethod()).isEqualTo("POST");
    }

    @Test
    void testDeleteFiles_WhenDisabled() throws Exception {
        RagPipelineClient disabledClient = new RagPipelineClient(
                mockWebServer.url("/").toString(),
                false,  // disabled
                5, 10, 20, ""
        );

        List<String> files = List.of("file1.java");
        Map<String, Object> result = disabledClient.deleteFiles(files, "workspace", "project", "main");

        assertThat(result).containsEntry("status", "skipped");
        assertThat(mockWebServer.getRequestCount()).isEqualTo(0);
    }

    @Test
    void testDeleteFiles_EmptyList() throws Exception {
        Map<String, Object> mockResponse = Map.of("status", "success", "deletedCount", 0);
        
        mockWebServer.enqueue(new MockResponse()
                .setBody(objectMapper.writeValueAsString(mockResponse))
                .addHeader("Content-Type", "application/json"));

        List<String> files = new ArrayList<>();
        Map<String, Object> result = client.deleteFiles(files, "workspace", "project", "main");

        assertThat(result).containsEntry("status", "success");
    }

    @Test
    void testSemanticSearch_Success() throws Exception {
        Map<String, Object> mockResponse = Map.of(
                "results", List.of(
                        Map.of("content", "search result", "score", 0.95)
                ),
                "total", 1
        );
        
        mockWebServer.enqueue(new MockResponse()
                .setBody(objectMapper.writeValueAsString(mockResponse))
                .addHeader("Content-Type", "application/json"));

        Map<String, Object> result = client.semanticSearch(
                "search query", "workspace", "project", "main", 10, null
        );

        assertThat(result).containsKey("results");
        
        RecordedRequest request = mockWebServer.takeRequest();
        assertThat(request.getMethod()).isEqualTo("POST");
    }

    @Test
    void testSemanticSearch_WhenDisabled() throws Exception {
        RagPipelineClient disabledClient = new RagPipelineClient(
                mockWebServer.url("/").toString(),
                false,
                5, 10, 20, ""
        );

        Map<String, Object> result = disabledClient.semanticSearch(
                "query", "workspace", "project", "main", 10, null
        );

        assertThat(result).containsKey("results");
    }

    @Test
    void testGetPRContext_Success() throws Exception {
        Map<String, Object> mockResponse = Map.of(
                "context", "relevant code context",
                "fileCount", 5
        );
        
        mockWebServer.enqueue(new MockResponse()
                .setBody(objectMapper.writeValueAsString(mockResponse))
                .addHeader("Content-Type", "application/json"));

        Map<String, Object> result = client.getPRContext(
                "workspace", "project", "main", List.of("file1.java"), "pr description", 10
        );

        assertThat(result).containsKey("context");
        
        RecordedRequest request = mockWebServer.takeRequest();
        assertThat(request.getMethod()).isEqualTo("POST");
    }

    @Test
    void testGetPRContext_WhenDisabled() throws Exception {
        RagPipelineClient disabledClient = new RagPipelineClient(
                mockWebServer.url("/").toString(),
                false,
                5, 10, 20, ""
        );

        Map<String, Object> result = disabledClient.getPRContext(
                "workspace", "project", "main", List.of("file.java"), "description", 10
        );

        assertThat(result).containsKey("context");
    }

    @Test
    void testDeleteIndex_Success() throws Exception {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody("{}"));

        client.deleteIndex("workspace", "project", "main");

        RecordedRequest request = mockWebServer.takeRequest();
        assertThat(request.getMethod()).isEqualTo("DELETE");
    }

    @Test
    void testIsHealthy_True() throws Exception {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setBody("{\"status\": \"healthy\"}"));

        boolean healthy = client.isHealthy();

        assertThat(healthy).isTrue();
    }

    @Test
    void testIsHealthy_False() throws Exception {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(500));

        boolean healthy = client.isHealthy();

        assertThat(healthy).isFalse();
    }

    @Test
    void testUpdateFiles_Success() throws Exception {
        Map<String, Object> mockResponse = Map.of(
                "status", "success",
                "updatedFiles", 3
        );
        
        mockWebServer.enqueue(new MockResponse()
                .setBody(objectMapper.writeValueAsString(mockResponse))
                .addHeader("Content-Type", "application/json"));

        List<String> files = List.of("file1.java", "file2.java");
        Map<String, Object> result = client.updateFiles(
                files, "/tmp/dir", "workspace", "project", "main", "commit123"
        );

        assertThat(result).containsEntry("status", "success");
    }

    @Test
    void testUpdateFiles_WhenDisabled() throws Exception {
        RagPipelineClient disabledClient = new RagPipelineClient(
                mockWebServer.url("/").toString(),
                false,
                5, 10, 20, ""
        );

        Map<String, Object> result = disabledClient.updateFiles(
                List.of("file.java"), "/tmp", "ws", "proj", "main", "commit"
        );

        assertThat(result).containsEntry("status", "skipped");
    }

    @Test
    void testConstructor_WithDefaults() {
        RagPipelineClient defaultClient = new RagPipelineClient(
                "http://localhost:8001",
                true,
                30,
                120,
                14400,
                ""
        );
        
        assertThat(defaultClient).isNotNull();
    }

    @Test
    void testHttpError_ThrowsIOException() {
        mockWebServer.enqueue(new MockResponse().setResponseCode(500));

        assertThatThrownBy(() -> client.deleteFiles(
                List.of("file.java"), "ws", "proj", "main"
        ))
                .isInstanceOf(IOException.class);
    }

    @Test
    void testNetworkError_ThrowsIOException() throws IOException {
        mockWebServer.shutdown();

        assertThatThrownBy(() -> client.deleteFiles(
                List.of("file.java"), "ws", "proj", "main"
        ))
                .isInstanceOf(IOException.class);
    }

    @Test
    void testServiceSecretHeader_SentOnRequests() throws Exception {
        Map<String, Object> mockResponse = Map.of("status", "success");
        mockWebServer.enqueue(new MockResponse()
                .setBody(objectMapper.writeValueAsString(mockResponse))
                .addHeader("Content-Type", "application/json"));

        client.deleteFiles(List.of("file.java"), "ws", "proj", "main");

        RecordedRequest request = mockWebServer.takeRequest();
        assertThat(request.getHeader("x-service-secret")).isEqualTo("test-secret");
    }

    @Test
    void testServiceSecretHeader_NotSentWhenEmpty() throws Exception {
        RagPipelineClient noSecretClient = new RagPipelineClient(
                mockWebServer.url("/").toString(),
                true, 5, 10, 20, ""
        );

        Map<String, Object> mockResponse = Map.of("status", "success");
        mockWebServer.enqueue(new MockResponse()
                .setBody(objectMapper.writeValueAsString(mockResponse))
                .addHeader("Content-Type", "application/json"));

        noSecretClient.deleteFiles(List.of("file.java"), "ws", "proj", "main");

        RecordedRequest request = mockWebServer.takeRequest();
        assertThat(request.getHeader("x-service-secret")).isNull();
    }
}
