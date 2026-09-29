package org.rostilos.codecrow.ragengine.client;

import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class RagPipelineClientTest {

    private MockWebServer server;
    private RagPipelineClient client;
    private ObjectMapper mapper;

    @TempDir
    Path repository;

    @BeforeEach
    void setUp() throws IOException {
        server = new MockWebServer();
        server.start();
        client = new RagPipelineClient(
                server.url("/").toString(), true, 5, 10, 20, "test-secret");
        mapper = new ObjectMapper();
    }

    @AfterEach
    void tearDown() throws IOException {
        server.shutdown();
    }

    @Test
    @SuppressWarnings("unchecked")
    void fullBuildTargetsOnlyTheRequestedImmutableGeneration() throws Exception {
        server.enqueue(json("{\"status\":\"success\",\"generation_manifest_sha256\":\"digest\"}"));

        client.indexRepository(
                repository.toString(), "ws", "repo", "main", "revision",
                List.of("src/**"), List.of("target/**"), "physical-generation",
                "java", "src/main/java");

        RecordedRequest request = server.takeRequest();
        Map<String, Object> payload = mapper.readValue(request.getBody().readUtf8(), Map.class);
        assertThat(request.getPath()).isEqualTo("/index/repository");
        assertThat(request.getHeader("x-service-secret")).isEqualTo("test-secret");
        assertThat(payload)
                .containsEntry("collection_target", "physical-generation")
                .containsEntry("project_type", "java")
                .containsEntry("source_root", "src/main/java")
                .doesNotContainKeys(
                        "reuse_collection_target",
                        "publish_legacy_project_alias");
    }

    @Test
    void branchCleanupRequiresAndSendsRegistryOwnershipProof() throws Exception {
        assertThatThrownBy(() -> client.deleteBranch(
                "ws", "repo", "feature/x", "generation", "revision", null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("generationManifestSha256");

        server.enqueue(json("{\"status\":\"deleted\"}"));
        assertThat(client.deleteBranch(
                "ws", "repo", "feature/x", "generation", "revision", "manifest"))
                .isTrue();

        RecordedRequest request = server.takeRequest();
        assertThat(request.getRequestUrl().queryParameter("collection_target"))
                .isEqualTo("generation");
        assertThat(request.getRequestUrl().queryParameter("generation_revision"))
                .isEqualTo("revision");
        assertThat(request.getRequestUrl().queryParameter("generation_manifest_sha256"))
                .isEqualTo("manifest");
        assertThat(request.getPath()).contains("feature%2Fx");
    }

    private static MockResponse json(String body) {
        return new MockResponse()
                .setResponseCode(200)
                .setBody(body)
                .addHeader("Content-Type", "application/json");
    }
}
