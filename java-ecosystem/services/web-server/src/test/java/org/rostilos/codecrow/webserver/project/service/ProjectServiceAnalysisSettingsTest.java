package org.rostilos.codecrow.webserver.project.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.config.ProjectConfig;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class ProjectServiceAnalysisSettingsTest {

    @Mock
    private ProjectRepository projectRepository;

    @InjectMocks
    private ProjectService projectService;

    private Project project;

    @BeforeEach
    void setUp() {
        project = new Project();
        when(projectRepository.findByWorkspaceIdAndId(10L, 20L))
                .thenReturn(Optional.of(project));
        when(projectRepository.save(project)).thenReturn(project);
    }

    @Test
    void omittedMcpSettingUsesEnabledDefaultWhenConfigurationIsMissing() {
        Project updated = updateMcpSetting(null);

        assertThat(updated.getConfiguration().useMcpTools()).isTrue();
    }

    @Test
    void omittedMcpSettingPreservesExplicitlyDisabledValue() {
        ProjectConfig config = new ProjectConfig();
        config.setUseMcpTools(false);
        project.setConfiguration(config);

        Project updated = updateMcpSetting(null);

        assertThat(updated.getConfiguration().useMcpTools()).isFalse();
    }

    @Test
    void explicitMcpDisableOverridesEnabledDefault() {
        project.setConfiguration(new ProjectConfig());

        Project updated = updateMcpSetting(false);

        assertThat(updated.getConfiguration().useMcpTools()).isFalse();
    }

    private Project updateMcpSetting(Boolean useMcpTools) {
        return projectService.updateAnalysisSettings(
                10L,
                20L,
                null,
                null,
                null,
                null,
                useMcpTools,
                null);
    }
}
