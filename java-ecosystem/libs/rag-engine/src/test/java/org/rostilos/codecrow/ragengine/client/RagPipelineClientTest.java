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

    // ── indexRepository tests ────────────────────────────────────────────────

    @Test
    void testIndexRepository_Success() throws Exception {
        Map<String, Object> mockResponse = Map.of("document_count", 42, "status", "success");
        mockWebServer.enqueue(new MockResponse()
                .setBody(objectMapper.writeValueAsString(mockResponse))
                .addHeader("Content-Type", "application/json"));

        Map<String, Object> result = client.indexRepository(
                "/tmp/repo", "ws", "proj", "main", "abc123", null);

        assertThat(result).containsEntry("document_count", 42);
        RecordedRequest request = mockWebServer.takeRequest();
        assertThat(request.getPath()).endsWith("/index/repository");
        assertThat(request.getMethod()).isEqualTo("POST");
    }

    @Test
    void testIndexRepository_WithExcludePatterns() throws Exception {
        Map<String, Object> mockResponse = Map.of("document_count", 30);
        mockWebServer.enqueue(new MockResponse()
                .setBody(objectMapper.writeValueAsString(mockResponse))
                .addHeader("Content-Type", "application/json"));

        List<String> patterns = List.of("*.log", "vendor/**");
        Map<String, Object> result = client.indexRepository(
                "/tmp/repo", "ws", "proj", "main", "abc123", patterns);

        assertThat(result).containsEntry("document_count", 30);
        RecordedRequest request = mockWebServer.takeRequest();
        String body = request.getBody().readUtf8();
        assertThat(body).contains("exclude_patterns");
    }

    @Test
    void testIndexRepository_WhenDisabled() throws Exception {
        RagPipelineClient disabledClient = new RagPipelineClient(
                mockWebServer.url("/").toString(), false, 5, 10, 20, "");

        Map<String, Object> result = disabledClient.indexRepository(
                "/tmp/repo", "ws", "proj", "main", "abc123", null);

        assertThat(result).containsEntry("status", "skipped");
        assertThat(mockWebServer.getRequestCount()).isEqualTo(0);
    }

    // ── deleteBranch tests ───────────────────────────────────────────────────

    @Test
    void testDeleteBranch_Success() throws Exception {
        mockWebServer.enqueue(new MockResponse().setResponseCode(200).setBody("{}"));

        boolean result = client.deleteBranch("ws", "proj", "feature");

        assertThat(result).isTrue();
        RecordedRequest request = mockWebServer.takeRequest();
        assertThat(request.getMethod()).isEqualTo("DELETE");
        assertThat(request.getPath()).contains("/index/ws/proj/branch/feature");
    }

    @Test
    void testDeleteBranch_Failure() throws Exception {
        mockWebServer.enqueue(new MockResponse().setResponseCode(404).setBody("{\"error\":\"not found\"}"));

        boolean result = client.deleteBranch("ws", "proj", "feature");

        assertThat(result).isFalse();
    }

    @Test
    void testDeleteBranch_WhenDisabled() throws Exception {
        RagPipelineClient disabledClient = new RagPipelineClient(
                mockWebServer.url("/").toString(), false, 5, 10, 20, "");

        boolean result = disabledClient.deleteBranch("ws", "proj", "feature");

        assertThat(result).isFalse();
        assertThat(mockWebServer.getRequestCount()).isEqualTo(0);
    }

    @Test
    void testDeleteBranch_WithSlashInBranchName() throws Exception {
        mockWebServer.enqueue(new MockResponse().setResponseCode(200).setBody("{}"));

        boolean result = client.deleteBranch("ws", "proj", "feature/xyz");

        assertThat(result).isTrue();
        RecordedRequest request = mockWebServer.takeRequest();
        assertThat(request.getPath()).contains("feature%2Fxyz");
    }

    // ── getIndexedBranches tests ─────────────────────────────────────────────

    @Test
    void testGetIndexedBranches_Success() throws Exception {
        Map<String, Object> mockResponse = Map.of(
                "branches", List.of(
                        Map.of("branch", "main", "point_count", 100),
                        Map.of("branch", "develop", "point_count", 50)
                )
        );
        mockWebServer.enqueue(new MockResponse()
                .setBody(objectMapper.writeValueAsString(mockResponse))
                .addHeader("Content-Type", "application/json"));

        List<String> branches = client.getIndexedBranches("ws", "proj");

        assertThat(branches).containsExactly("main", "develop");
        RecordedRequest request = mockWebServer.takeRequest();
        assertThat(request.getMethod()).isEqualTo("GET");
    }

    @Test
    void testGetIndexedBranches_WhenDisabled() {
        RagPipelineClient disabledClient = new RagPipelineClient(
                mockWebServer.url("/").toString(), false, 5, 10, 20, "");

        List<String> branches = disabledClient.getIndexedBranches("ws", "proj");

        assertThat(branches).isEmpty();
    }

    @Test
    void testGetIndexedBranches_ServerError() throws Exception {
        mockWebServer.enqueue(new MockResponse().setResponseCode(500));

        List<String> branches = client.getIndexedBranches("ws", "proj");

        assertThat(branches).isEmpty();
    }

    // ── getIndexedBranchesWithStats tests ────────────────────────────────────

    @Test
    void testGetIndexedBranchesWithStats_Success() throws Exception {
        Map<String, Object> mockResponse = Map.of(
                "branches", List.of(
                        Map.of("branch", "main", "point_count", 100)
                ),
                "total_branches", 1
        );
        mockWebServer.enqueue(new MockResponse()
                .setBody(objectMapper.writeValueAsString(mockResponse))
                .addHeader("Content-Type", "application/json"));

        List<Map<String, Object>> result = client.getIndexedBranchesWithStats("ws", "proj");

        assertThat(result).hasSize(1);
        assertThat(result.get(0)).containsEntry("branch", "main");
    }

    @Test
    void testGetIndexedBranchesWithStats_WhenDisabled() {
        RagPipelineClient disabledClient = new RagPipelineClient(
                mockWebServer.url("/").toString(), false, 5, 10, 20, "");

        List<Map<String, Object>> result = disabledClient.getIndexedBranchesWithStats("ws", "proj");

        assertThat(result).isEmpty();
    }

    // ── cleanupStaleBranches tests ───────────────────────────────────────────

    @Test
    void testCleanupStaleBranches_Success() throws Exception {
        Map<String, Object> mockResponse = Map.of("status", "success", "deleted", 2);
        mockWebServer.enqueue(new MockResponse()
                .setBody(objectMapper.writeValueAsString(mockResponse))
                .addHeader("Content-Type", "application/json"));

        Map<String, Object> result = client.cleanupStaleBranches(
                "ws", "proj", List.of("main"), List.of("feature"));

        assertThat(result).containsEntry("status", "success");
    }

    @Test
    void testCleanupStaleBranches_WhenDisabled() {
        RagPipelineClient disabledClient = new RagPipelineClient(
                mockWebServer.url("/").toString(), false, 5, 10, 20, "");

        Map<String, Object> result = disabledClient.cleanupStaleBranches(
                "ws", "proj", null, null);

        assertThat(result).containsEntry("status", "disabled");
    }

    // ── getPRContext with multi-branch ────────────────────────────────────────

    @Test
    void testGetPRContext_WithBaseBranchAndDeletedFiles() throws Exception {
        Map<String, Object> mockResponse = Map.of("context", "multi-branch context");
        mockWebServer.enqueue(new MockResponse()
                .setBody(objectMapper.writeValueAsString(mockResponse))
                .addHeader("Content-Type", "application/json"));

        Map<String, Object> result = client.getPRContext(
                "ws", "proj", "feature", "main",
                List.of("src/Main.java"), "PR description", 10,
                List.of("old.java"));

        assertThat(result).containsEntry("context", "multi-branch context");
        RecordedRequest request = mockWebServer.takeRequest();
        String body = request.getBody().readUtf8();
        assertThat(body).contains("base_branch");
        assertThat(body).contains("deleted_files");
    }

    // ── deleteIndex tests ────────────────────────────────────────────────────

    @Test
    void testDeleteIndex_WhenDisabled() throws Exception {
        RagPipelineClient disabledClient = new RagPipelineClient(
                mockWebServer.url("/").toString(), false, 5, 10, 20, "");

        disabledClient.deleteIndex("ws", "proj", "main");

        assertThat(mockWebServer.getRequestCount()).isEqualTo(0);
    }

    @Test
    void testDeleteIndex_NonSuccessful() throws Exception {
        mockWebServer.enqueue(new MockResponse().setResponseCode(500));

        // Should not throw, just log warning
        client.deleteIndex("ws", "proj", "main");

        assertThat(mockWebServer.getRequestCount()).isEqualTo(1);
    }

    // ── isHealthy ────────────────────────────────────────────────────────────

    @Test
    void testIsHealthy_WhenDisabled() {
        RagPipelineClient disabledClient = new RagPipelineClient(
                mockWebServer.url("/").toString(), false, 5, 10, 20, "");

        assertThat(disabledClient.isHealthy()).isFalse();
    }

    // ── null service secret ──────────────────────────────────────────────────

    @Test
    void testConstructor_WithNullSecret() {
        RagPipelineClient nullSecretClient = new RagPipelineClient(
                "http://localhost:8001", true, 30, 120, 14400, null);
        assertThat(nullSecretClient).isNotNull();
    }

    // ── semanticSearch with language filter ───────────────────────────────────

    @Test
    void testSemanticSearch_WithLanguageFilter() throws Exception {
        Map<String, Object> mockResponse = Map.of("results", List.of());
        mockWebServer.enqueue(new MockResponse()
                .setBody(objectMapper.writeValueAsString(mockResponse))
                .addHeader("Content-Type", "application/json"));

        Map<String, Object> result = client.semanticSearch(
                "query", "ws", "proj", "main", 10, "java");

        assertThat(result).containsKey("results");
        RecordedRequest request = mockWebServer.takeRequest();
        String body = request.getBody().readUtf8();
        assertThat(body).contains("filter_language");
    }
}
