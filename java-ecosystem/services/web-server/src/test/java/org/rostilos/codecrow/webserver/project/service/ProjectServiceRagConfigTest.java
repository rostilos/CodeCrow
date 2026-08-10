package org.rostilos.codecrow.webserver.project.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.model.project.config.RagConfig;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;

import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class ProjectServiceRagConfigTest {

    @Mock
    private ProjectRepository projectRepository;

    @InjectMocks
    private ProjectService projectService;

    private Project project;

    @BeforeEach
    void setUp() {
        project = new Project();
        project.setConfiguration(new ProjectConfig(
                false,
                "main",
                null,
                new RagConfig(
                        true,
                        "main",
                        List.of("src/**"),
                        List.of("target/**"),
                        true,
                        30,
                        List.of("develop", "release"),
                        true)));
        when(projectRepository.findByWorkspaceIdAndId(10L, 20L))
                .thenReturn(Optional.of(project));
        when(projectRepository.save(project)).thenReturn(project);
    }

    @Test
    void eightArgumentCompatibilityUpdatePreservesNewerBranchSettings() {
        Project updated = projectService.updateRagConfig(
                10L, 20L, true, "main",
                List.of("app/**"), List.of("build/**"), false, 14);

        RagConfig rag = updated.getConfiguration().ragConfig();
        assertThat(rag.multiBranchEnabled()).isFalse();
        assertThat(rag.branchRetentionDays()).isEqualTo(14);
        assertThat(rag.indexedBranches()).containsExactly("develop", "release");
        assertThat(rag.transientBranchIndexesEnabled()).isTrue();
    }

    @Test
    void sixArgumentCompatibilityUpdatePreservesAllMultiBranchSettings() {
        Project updated = projectService.updateRagConfig(
                10L, 20L, false, "develop",
                List.of("service/**"), List.of("generated/**"));

        RagConfig rag = updated.getConfiguration().ragConfig();
        assertThat(rag.enabled()).isFalse();
        assertThat(rag.branch()).isEqualTo("develop");
        assertThat(rag.multiBranchEnabled()).isTrue();
        assertThat(rag.branchRetentionDays()).isEqualTo(30);
        assertThat(rag.indexedBranches()).containsExactly("develop", "release");
        assertThat(rag.transientBranchIndexesEnabled()).isTrue();
    }
}
