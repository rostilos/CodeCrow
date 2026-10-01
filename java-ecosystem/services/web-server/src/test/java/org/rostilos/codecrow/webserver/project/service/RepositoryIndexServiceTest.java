package org.rostilos.codecrow.webserver.project.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.rag.RagBranchIndexRepository;

import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class RepositoryIndexServiceTest {
    private static final TypeReference<Map<String, Object>> MAP_TYPE = new TypeReference<>() {
    };

    @Test
    void postsExactCollectionTargetInGraphAndPointRequestBodies() throws Exception {
        ObjectMapper objectMapper = new ObjectMapper();
        RagBranchIndexRepository branchIndexes = mock(RagBranchIndexRepository.class);
        RagBranchIndexRepository.ActiveGenerationCoordinates coordinates =
                mock(RagBranchIndexRepository.ActiveGenerationCoordinates.class);
        Workspace workspace = mock(Workspace.class);
        Project project = mock(Project.class);

        when(workspace.getName()).thenReturn("acme");
        when(project.getId()).thenReturn(42L);
        when(project.getNamespace()).thenReturn("repository");
        when(coordinates.getCollectionName()).thenReturn("opaque-generation-target");
        when(branchIndexes.findActiveGenerationCoordinates(42L, "feature/exact"))
                .thenReturn(Optional.of(coordinates));

        try (MockWebServer ragApi = new MockWebServer()) {
            ragApi.enqueue(new MockResponse().setBody("{}")
                    .addHeader("Content-Type", "application/json"));
            ragApi.enqueue(new MockResponse().setBody("{}")
                    .addHeader("Content-Type", "application/json"));
            ragApi.enqueue(new MockResponse()
                    .setBody("{\"collection\":\"opaque-generation-target\",\"total_points\":1}")
                    .addHeader("Content-Type", "application/json"));
            ragApi.start();

            RepositoryIndexService service = new RepositoryIndexService(
                    objectMapper,
                    branchIndexes,
                    ragApi.url("/").toString(),
                    true,
                    5,
                    5,
                    "service-secret");
            Map<String, Object> request = Map.of(
                    "filters", Map.of("branches", List.of("feature/exact")));

            service.getGraph(workspace, project, request);
            service.getPoint(workspace, project, "point/id", request);
            Map<String, Object> overview = service.getOverview(
                    workspace, project, "feature/exact");

            assertExactTargetBody(objectMapper, ragApi.takeRequest(),
                    "/repository-index/acme/repository/graph");
            assertExactTargetBody(objectMapper, ragApi.takeRequest(),
                    "/repository-index/acme/repository/points/point%2Fid");
            assertThat(ragApi.takeRequest().getRequestUrl().encodedPath())
                    .isEqualTo("/repository-index/acme/repository/overview");
            assertThat(overview)
                    .doesNotContainKey("collection")
                    .doesNotContainKey("selected_branch")
                    .containsEntry("selectedBranch", "feature/exact")
                    .containsEntry("total_points", 1);
        }
    }

    private static void assertExactTargetBody(
            ObjectMapper objectMapper,
            RecordedRequest request,
            String expectedPath) throws Exception {
        assertThat(request.getRequestUrl().encodedPath()).isEqualTo(expectedPath);
        assertThat(request.getRequestUrl().queryParameter("collection_target")).isNull();
        assertThat(request.getHeader("x-service-secret")).isEqualTo("service-secret");

        Map<String, Object> body = objectMapper.readValue(request.getBody().readUtf8(), MAP_TYPE);
        assertThat(body).containsEntry("collection_target", "opaque-generation-target");
        Map<?, ?> filters = (Map<?, ?>) body.get("filters");
        assertThat(filters.get("branches")).isEqualTo(List.of("feature/exact"));
    }
}
