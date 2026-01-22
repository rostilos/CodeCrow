package org.rostilos.codecrow.analysisengine.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.project.ProjectAiConnectionBinding;
import org.rostilos.codecrow.core.model.project.ProjectVcsConnectionBinding;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;

import java.io.IOException;
import java.lang.reflect.Field;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class ProjectServiceTest {

    @Mock
    private ProjectRepository projectRepository;

    @InjectMocks
    private ProjectService projectService;

    private Project testProject;
    private ProjectVcsConnectionBinding vcsBinding;
    private ProjectAiConnectionBinding aiBinding;

    @BeforeEach
    void setUp() throws Exception {
        testProject = new Project();
        setId(testProject, 1L);
        testProject.setName("test-project");

        vcsBinding = new ProjectVcsConnectionBinding();
        setId(vcsBinding, 10L);

        aiBinding = new ProjectAiConnectionBinding();
        setId(aiBinding, 20L);
    }

    private void setId(Object obj, Long id) throws Exception {
        Field field = obj.getClass().getDeclaredField("id");
        field.setAccessible(true);
        field.set(obj, id);
    }

    @Test
    void testGetProjectWithConnections_Success() throws IOException {
        testProject.setVcsBinding(vcsBinding);
        testProject.setAiConnectionBinding(aiBinding);

        when(projectRepository.findByIdWithFullDetails(1L))
                .thenReturn(Optional.of(testProject));

        Project result = projectService.getProjectWithConnections(1L);

        assertThat(result).isNotNull();
        assertThat(result.getId()).isEqualTo(1L);
        assertThat(result.getVcsBinding()).isEqualTo(vcsBinding);
        assertThat(result.getAiBinding()).isEqualTo(aiBinding);
        verify(projectRepository).findByIdWithFullDetails(1L);
    }

    @Test
    void testGetProjectWithConnections_ProjectNotFound_ThrowsIOException() {
        when(projectRepository.findByIdWithFullDetails(999L))
                .thenReturn(Optional.empty());

        assertThatThrownBy(() -> projectService.getProjectWithConnections(999L))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("Project doesn't exist or authorization has not been passed");

        verify(projectRepository).findByIdWithFullDetails(999L);
    }

    @Test
    void testGetProjectWithConnections_VcsBindingMissing_ThrowsIOException() {
        testProject.setVcsBinding(null);
        testProject.setAiConnectionBinding(aiBinding);

        when(projectRepository.findByIdWithFullDetails(1L))
                .thenReturn(Optional.of(testProject));

        assertThatThrownBy(() -> projectService.getProjectWithConnections(1L))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("VCS connection is not configured");

        verify(projectRepository).findByIdWithFullDetails(1L);
    }

    @Test
    void testGetProjectWithConnections_AiBindingMissing_ThrowsIOException() {
        testProject.setVcsBinding(vcsBinding);
        testProject.setAiConnectionBinding(null);

        when(projectRepository.findByIdWithFullDetails(1L))
                .thenReturn(Optional.of(testProject));

        assertThatThrownBy(() -> projectService.getProjectWithConnections(1L))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("AI connection is not configured");

        verify(projectRepository).findByIdWithFullDetails(1L);
    }

    @Test
    void testGetProjectWithConnections_BothBindingsMissing_ThrowsIOException() {
        testProject.setVcsBinding(null);
        testProject.setAiConnectionBinding(null);

        when(projectRepository.findByIdWithFullDetails(1L))
                .thenReturn(Optional.of(testProject));

        assertThatThrownBy(() -> projectService.getProjectWithConnections(1L))
                .isInstanceOf(IOException.class)
                .hasMessageContaining("VCS connection is not configured");
    }

    @Test
    void testGetProjectWithConnections_ValidatesBeforeReturning() throws IOException {
        testProject.setVcsBinding(vcsBinding);
        testProject.setAiConnectionBinding(aiBinding);

        when(projectRepository.findByIdWithFullDetails(1L))
                .thenReturn(Optional.of(testProject));

        Project result = projectService.getProjectWithConnections(1L);

        assertThat(result).isSameAs(testProject);
        assertThat(result.getVcsBinding()).isNotNull();
        assertThat(result.getAiBinding()).isNotNull();
    }

    @Test
    void testGetProjectWithConnections_CallsRepositoryMethod() throws IOException {
        testProject.setVcsBinding(vcsBinding);
        testProject.setAiConnectionBinding(aiBinding);

        when(projectRepository.findByIdWithFullDetails(100L))
                .thenReturn(Optional.of(testProject));

        projectService.getProjectWithConnections(100L);

        verify(projectRepository).findByIdWithFullDetails(100L);
        verify(projectRepository, never()).findById(anyLong());
    }
}
