package org.rostilos.codecrow.ragengine.service;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.project.config.RagConfig;
import org.rostilos.codecrow.ragengine.client.RagPipelineClient;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

class RagRepresentationIdentityServiceTest {

    @Test
    void duplicateAndReorderedPatternsHaveOneCanonicalIdentity() {
        RagRepresentationIdentityService service =
                new RagRepresentationIdentityService(mock(RagPipelineClient.class));
        Project duplicates = project(new RagConfig(
                true,
                "main",
                List.of(" src/** ", "test/**", "src/**"),
                List.of("vendor/**", " build/** ", "vendor/**")));
        Project canonical = project(new RagConfig(
                true,
                "main",
                List.of("test/**", "src/**"),
                List.of("build/**", "vendor/**")));

        assertThat(service.projectFingerprint("runtime-a", duplicates))
                .isEqualTo(service.projectFingerprint("runtime-a", canonical));
    }

    @Test
    void materialProjectConfigChangesIdentityAtTheSameRuntime() {
        RagRepresentationIdentityService service =
                new RagRepresentationIdentityService(mock(RagPipelineClient.class));
        Project first = project(new RagConfig(
                true, "main", List.of("src/**"), List.of("vendor/**")));
        Project changed = project(new RagConfig(
                true, "main", List.of("app/**"), List.of("vendor/**")));

        assertThat(service.projectFingerprint("runtime-a", first))
                .isNotEqualTo(service.projectFingerprint("runtime-a", changed));
    }

    @Test
    void successfulRuntimeIdentityIsCachedAcrossProjects() throws Exception {
        RagPipelineClient client = mock(RagPipelineClient.class);
        when(client.getCurrentRepresentationIdentity()).thenReturn(
                new RagPipelineClient.RepresentationIdentity(
                        "runtime-a", "index-a", "catalog-a", "impl-a", List.of("java")));
        RagRepresentationIdentityService service =
                new RagRepresentationIdentityService(client);

        assertThat(service.currentRuntimeIdentity()).contains("runtime-a");
        assertThat(service.currentRuntimeIdentity()).contains("runtime-a");
        verify(client, times(1)).getCurrentRepresentationIdentity();
    }

    private static Project project(RagConfig ragConfig) {
        Project project = new Project();
        ProjectConfig config = new ProjectConfig();
        config.setRagConfig(ragConfig);
        project.setConfiguration(config);
        return project;
    }
}
