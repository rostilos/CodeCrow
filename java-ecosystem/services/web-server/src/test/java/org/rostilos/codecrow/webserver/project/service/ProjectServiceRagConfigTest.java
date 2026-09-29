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
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;

import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;
import static org.mockito.Mockito.verify;

@ExtendWith(MockitoExtension.class)
class ProjectServiceRagConfigTest {

    @Mock
    private ProjectRepository projectRepository;

    @Mock
    private RepositoryIndexBootstrapService repositoryIndexBootstrapService;

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
                        List.of("target/**"))));
        project.setVcsRepoBinding(new VcsRepoBinding());
        when(projectRepository.findByWorkspaceIdAndId(10L, 20L))
                .thenReturn(Optional.of(project));
        when(projectRepository.save(project)).thenReturn(project);
    }

    @Test
    void updatePersistsOnlyMaterialIndexInputs() {
        Project updated = projectService.updateRagConfig(
                10L, 20L, true, "main",
                List.of("app/**"), List.of("build/**"));

        RagConfig rag = updated.getConfiguration().ragConfig();
        assertThat(rag.enabled()).isTrue();
        assertThat(rag.branch()).isEqualTo("main");
        assertThat(rag.includePatterns()).containsExactly("app/**");
        assertThat(rag.excludePatterns()).containsExactly("build/**");
        assertThat(updated.getConfiguration().useMcpTools()).isTrue();
        verify(repositoryIndexBootstrapService).enqueueAfterCommit(project);
    }

    @Test
    void updateCanDisableRagAndChangeScope() {
        Project updated = projectService.updateRagConfig(
                10L, 20L, false, "develop",
                List.of("service/**"), List.of("generated/**"));

        RagConfig rag = updated.getConfiguration().ragConfig();
        assertThat(rag.enabled()).isFalse();
        assertThat(rag.branch()).isEqualTo("develop");
        assertThat(rag.includePatterns()).containsExactly("service/**");
        assertThat(rag.excludePatterns()).containsExactly("generated/**");
    }

    @Test
    void ragUpdatePreservesExplicitlyDisabledMcpReviewSetting() {
        project.getConfiguration().setUseMcpTools(false);

        Project updated = projectService.updateRagConfig(
                10L, 20L, false, "develop",
                List.of("service/**"), List.of("generated/**"));

        assertThat(updated.getConfiguration().useMcpTools()).isFalse();
    }
}
