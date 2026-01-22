package org.rostilos.codecrow.ragengine.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.analysis.RagIndexStatus;
import org.rostilos.codecrow.core.model.analysis.RagIndexingStatus;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.workspace.Workspace;
import org.rostilos.codecrow.core.persistence.repository.analysis.RagIndexStatusRepository;
import org.springframework.test.util.ReflectionTestUtils;

import java.time.OffsetDateTime;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class RagIndexTrackingServiceTest {

    @Mock
    private RagIndexStatusRepository ragIndexStatusRepository;

    private RagIndexTrackingService service;
    private Project testProject;
    private Workspace testWorkspace;

    @BeforeEach
    void setUp() {
        service = new RagIndexTrackingService(ragIndexStatusRepository);
        
        testWorkspace = new Workspace();
        ReflectionTestUtils.setField(testWorkspace, "id", 1L);
        testWorkspace.setName("test-workspace");
        
        testProject = new Project();
        ReflectionTestUtils.setField(testProject, "id", 100L);
        testProject.setName("test-project");
        testProject.setWorkspace(testWorkspace);
    }

    @Test
    void testIsProjectIndexed_ReturnsTrue() {
        when(ragIndexStatusRepository.isProjectIndexed(100L)).thenReturn(true);

        boolean result = service.isProjectIndexed(testProject);

        assertThat(result).isTrue();
        verify(ragIndexStatusRepository).isProjectIndexed(100L);
    }

    @Test
    void testIsProjectIndexed_ReturnsFalse() {
        when(ragIndexStatusRepository.isProjectIndexed(100L)).thenReturn(false);

        boolean result = service.isProjectIndexed(testProject);

        assertThat(result).isFalse();
    }

    @Test
    void testGetIndexStatus_Found() {
        RagIndexStatus status = new RagIndexStatus();
        status.setProject(testProject);
        status.setStatus(RagIndexingStatus.INDEXED);
        when(ragIndexStatusRepository.findByProjectId(100L)).thenReturn(Optional.of(status));

        Optional<RagIndexStatus> result = service.getIndexStatus(testProject);

        assertThat(result).isPresent();
        assertThat(result.get().getStatus()).isEqualTo(RagIndexingStatus.INDEXED);
    }

    @Test
    void testGetIndexStatus_NotFound() {
        when(ragIndexStatusRepository.findByProjectId(100L)).thenReturn(Optional.empty());

        Optional<RagIndexStatus> result = service.getIndexStatus(testProject);

        assertThat(result).isEmpty();
    }

    @Test
    void testMarkIndexingStarted_NewStatus() {
        when(ragIndexStatusRepository.findByProjectId(100L)).thenReturn(Optional.empty());
        when(ragIndexStatusRepository.save(any(RagIndexStatus.class))).thenAnswer(i -> i.getArgument(0));

        RagIndexStatus result = service.markIndexingStarted(testProject, "main", "abc123");

        ArgumentCaptor<RagIndexStatus> captor = ArgumentCaptor.forClass(RagIndexStatus.class);
        verify(ragIndexStatusRepository).save(captor.capture());
        
        RagIndexStatus saved = captor.getValue();
        assertThat(saved.getProject()).isEqualTo(testProject);
        assertThat(saved.getStatus()).isEqualTo(RagIndexingStatus.INDEXING);
        assertThat(saved.getIndexedBranch()).isEqualTo("main");
        assertThat(saved.getIndexedCommitHash()).isEqualTo("abc123");
        assertThat(saved.getWorkspaceName()).isEqualTo("test-workspace");
        assertThat(saved.getProjectName()).isEqualTo("test-project");
    }

    @Test
    void testMarkIndexingStarted_ExistingStatus() {
        RagIndexStatus existing = new RagIndexStatus();
        existing.setProject(testProject);
        existing.setStatus(RagIndexingStatus.FAILED);
        existing.setErrorMessage("Previous error");
        
        when(ragIndexStatusRepository.findByProjectId(100L)).thenReturn(Optional.of(existing));
        when(ragIndexStatusRepository.save(any(RagIndexStatus.class))).thenAnswer(i -> i.getArgument(0));

        service.markIndexingStarted(testProject, "develop", "xyz789");

        ArgumentCaptor<RagIndexStatus> captor = ArgumentCaptor.forClass(RagIndexStatus.class);
        verify(ragIndexStatusRepository).save(captor.capture());
        
        RagIndexStatus saved = captor.getValue();
        assertThat(saved.getStatus()).isEqualTo(RagIndexingStatus.INDEXING);
        assertThat(saved.getIndexedBranch()).isEqualTo("develop");
        assertThat(saved.getIndexedCommitHash()).isEqualTo("xyz789");
        assertThat(saved.getErrorMessage()).isNull();
    }

    @Test
    void testMarkIndexingCompleted() {
        RagIndexStatus existing = new RagIndexStatus();
        existing.setProject(testProject);
        existing.setStatus(RagIndexingStatus.INDEXING);
        
        when(ragIndexStatusRepository.findByProjectId(100L)).thenReturn(Optional.of(existing));
        when(ragIndexStatusRepository.save(any(RagIndexStatus.class))).thenAnswer(i -> i.getArgument(0));

        RagIndexStatus result = service.markIndexingCompleted(testProject, "main", "abc123", 150);

        ArgumentCaptor<RagIndexStatus> captor = ArgumentCaptor.forClass(RagIndexStatus.class);
        verify(ragIndexStatusRepository).save(captor.capture());
        
        RagIndexStatus saved = captor.getValue();
        assertThat(saved.getStatus()).isEqualTo(RagIndexingStatus.INDEXED);
        assertThat(saved.getIndexedBranch()).isEqualTo("main");
        assertThat(saved.getIndexedCommitHash()).isEqualTo("abc123");
        assertThat(saved.getTotalFilesIndexed()).isEqualTo(150);
        assertThat(saved.getLastIndexedAt()).isNotNull();
        assertThat(saved.getErrorMessage()).isNull();
    }

    @Test
    void testMarkIndexingCompleted_ThrowsWhenNotFound() {
        when(ragIndexStatusRepository.findByProjectId(100L)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.markIndexingCompleted(testProject, "main", "abc123", 150))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("RAG index status not found");
    }

    @Test
    void testMarkIndexingFailed_ExistingStatus() {
        RagIndexStatus existing = new RagIndexStatus();
        existing.setProject(testProject);
        existing.setStatus(RagIndexingStatus.INDEXING);
        
        when(ragIndexStatusRepository.findByProjectId(100L)).thenReturn(Optional.of(existing));
        when(ragIndexStatusRepository.save(any(RagIndexStatus.class))).thenAnswer(i -> i.getArgument(0));

        service.markIndexingFailed(testProject, "Test error message");

        ArgumentCaptor<RagIndexStatus> captor = ArgumentCaptor.forClass(RagIndexStatus.class);
        verify(ragIndexStatusRepository).save(captor.capture());
        
        RagIndexStatus saved = captor.getValue();
        assertThat(saved.getStatus()).isEqualTo(RagIndexingStatus.FAILED);
        assertThat(saved.getErrorMessage()).isEqualTo("Test error message");
    }

    @Test
    void testMarkIndexingFailed_NewStatus() {
        when(ragIndexStatusRepository.findByProjectId(100L)).thenReturn(Optional.empty());
        when(ragIndexStatusRepository.save(any(RagIndexStatus.class))).thenAnswer(i -> i.getArgument(0));

        service.markIndexingFailed(testProject, "New error");

        ArgumentCaptor<RagIndexStatus> captor = ArgumentCaptor.forClass(RagIndexStatus.class);
        verify(ragIndexStatusRepository).save(captor.capture());
        
        RagIndexStatus saved = captor.getValue();
        assertThat(saved.getProject()).isEqualTo(testProject);
        assertThat(saved.getStatus()).isEqualTo(RagIndexingStatus.FAILED);
        assertThat(saved.getErrorMessage()).isEqualTo("New error");
    }

    @Test
    void testConstructor() {
        RagIndexTrackingService newService = new RagIndexTrackingService(ragIndexStatusRepository);
        assertThat(newService).isNotNull();
    }
}
