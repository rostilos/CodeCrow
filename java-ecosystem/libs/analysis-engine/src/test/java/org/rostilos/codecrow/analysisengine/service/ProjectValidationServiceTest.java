package org.rostilos.codecrow.analysisengine.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.ProjectAiConnectionBinding;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;

import java.io.IOException;
import java.lang.reflect.Field;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class ProjectValidationServiceTest {

    @Mock private ProjectRepository projectRepository;

    private ProjectValidationService service;

    private static void setId(Object entity, Long id) throws Exception {
        Field f = entity.getClass().getDeclaredField("id");
        f.setAccessible(true);
        f.set(entity, id);
    }

    @BeforeEach
    void setUp() {
        service = new ProjectValidationService(projectRepository);
    }

    @Test
    void getProjectWithConnections_projectNotFound_shouldThrow() {
        when(projectRepository.findByIdWithFullDetails(1L)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.getProjectWithConnections(1L))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("Project doesn't exist");
    }

    @Test
    void getProjectWithConnections_noVcsBinding_shouldThrow() throws Exception {
        Project project = spy(new Project());
        setId(project, 1L);
        doReturn(false).when(project).hasVcsBinding();

        when(projectRepository.findByIdWithFullDetails(1L)).thenReturn(Optional.of(project));

        assertThatThrownBy(() -> service.getProjectWithConnections(1L))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("VCS connection is not configured");
    }

    @Test
    void getProjectWithConnections_noAiBinding_shouldThrow() throws Exception {
        Project project = spy(new Project());
        setId(project, 1L);
        doReturn(true).when(project).hasVcsBinding();
        doReturn(null).when(project).getAiBinding();

        when(projectRepository.findByIdWithFullDetails(1L)).thenReturn(Optional.of(project));

        assertThatThrownBy(() -> service.getProjectWithConnections(1L))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("AI connection is not configured");
    }

    @Test
    void getProjectWithConnections_fullyConfigured_shouldReturnProject() throws Exception {
        Project project = spy(new Project());
        setId(project, 1L);
        doReturn(true).when(project).hasVcsBinding();
        doReturn(mock(ProjectAiConnectionBinding.class)).when(project).getAiBinding();

        when(projectRepository.findByIdWithFullDetails(1L)).thenReturn(Optional.of(project));

        Project result = service.getProjectWithConnections(1L);

        assertThat(result).isSameAs(project);
    }
}
