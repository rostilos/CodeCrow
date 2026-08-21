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
        when(ragIndexStatusRepository.findByProjectIdForUpdate(100L)).thenReturn(Optional.empty());
        when(ragIndexStatusRepository.save(any(RagIndexStatus.class))).thenAnswer(i -> i.getArgument(0));

        RagIndexStatus result = service.markIndexingStarted(testProject, "main", "abc123", 91L);

        ArgumentCaptor<RagIndexStatus> captor = ArgumentCaptor.forClass(RagIndexStatus.class);
        verify(ragIndexStatusRepository).save(captor.capture());
        
        RagIndexStatus saved = captor.getValue();
        assertThat(saved.getProject()).isEqualTo(testProject);
        assertThat(saved.getStatus()).isEqualTo(RagIndexingStatus.INDEXING);
        assertThat(saved.getIndexedBranch()).isEqualTo("main");
        assertThat(saved.getIndexedCommitHash()).isEqualTo("abc123");
        assertThat(saved.getWorkspaceName()).isEqualTo("test-workspace");
        assertThat(saved.getProjectName()).isEqualTo("test-project");
        assertThat(saved.getActiveJobId()).isEqualTo(91L);
    }

    @Test
    void testMarkIndexingStarted_ExistingStatus() {
        RagIndexStatus existing = new RagIndexStatus();
        existing.setProject(testProject);
        existing.setStatus(RagIndexingStatus.FAILED);
        existing.setErrorMessage("Previous error");
        
        when(ragIndexStatusRepository.findByProjectIdForUpdate(100L)).thenReturn(Optional.of(existing));
        when(ragIndexStatusRepository.save(any(RagIndexStatus.class))).thenAnswer(i -> i.getArgument(0));

        service.markIndexingStarted(testProject, "develop", "xyz789", 92L);

        ArgumentCaptor<RagIndexStatus> captor = ArgumentCaptor.forClass(RagIndexStatus.class);
        verify(ragIndexStatusRepository).save(captor.capture());
        
        RagIndexStatus saved = captor.getValue();
        assertThat(saved.getStatus()).isEqualTo(RagIndexingStatus.INDEXING);
        assertThat(saved.getIndexedBranch()).isEqualTo("develop");
        assertThat(saved.getIndexedCommitHash()).isEqualTo("xyz789");
        assertThat(saved.getErrorMessage()).isNull();
        assertThat(saved.getActiveJobId()).isEqualTo(92L);
    }

    @Test
    void testMarkIndexingCompleted() {
        RagIndexStatus existing = new RagIndexStatus();
        existing.setProject(testProject);
        existing.setStatus(RagIndexingStatus.INDEXING);
        existing.setActiveJobId(91L);
        
        when(ragIndexStatusRepository.findByProjectIdForUpdate(100L)).thenReturn(Optional.of(existing));
        when(ragIndexStatusRepository.save(any(RagIndexStatus.class))).thenAnswer(i -> i.getArgument(0));

        RagIndexStatus result = service.markIndexingCompleted(
                testProject, "main", "abc123", 150, null, 91L);

        ArgumentCaptor<RagIndexStatus> captor = ArgumentCaptor.forClass(RagIndexStatus.class);
        verify(ragIndexStatusRepository).save(captor.capture());
        
        RagIndexStatus saved = captor.getValue();
        assertThat(saved.getStatus()).isEqualTo(RagIndexingStatus.INDEXED);
        assertThat(saved.getIndexedBranch()).isEqualTo("main");
        assertThat(saved.getIndexedCommitHash()).isEqualTo("abc123");
        assertThat(saved.getTotalFilesIndexed()).isEqualTo(150);
        assertThat(saved.getLastIndexedAt()).isNotNull();
        assertThat(saved.getErrorMessage()).isNull();
        assertThat(saved.getActiveJobId()).isNull();
    }

    @Test
    void testMarkIndexingCompleted_ThrowsWhenNotFound() {
        when(ragIndexStatusRepository.findByProjectIdForUpdate(100L)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.markIndexingCompleted(
                testProject, "main", "abc123", 150, null, 91L))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("RAG index status not found");
    }

    @Test
    void testMarkIndexingFailed_ExistingStatus() {
        RagIndexStatus existing = new RagIndexStatus();
        existing.setProject(testProject);
        existing.setStatus(RagIndexingStatus.INDEXING);
        
        when(ragIndexStatusRepository.findByProjectIdForUpdate(100L)).thenReturn(Optional.of(existing));
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
        when(ragIndexStatusRepository.findByProjectIdForUpdate(100L)).thenReturn(Optional.empty());
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
    void testMarkIndexingHeartbeat_RefreshesLiveStatusOnly() {
        RagIndexStatus existing = new RagIndexStatus();
        existing.setProject(testProject);
        existing.setStatus(RagIndexingStatus.INDEXING);
        existing.setUpdatedAt(OffsetDateTime.now().minusMinutes(5));
        OffsetDateTime previousActivity = existing.getUpdatedAt();

        when(ragIndexStatusRepository.findByProjectIdForUpdate(100L))
                .thenReturn(Optional.of(existing));
        when(ragIndexStatusRepository.save(any(RagIndexStatus.class)))
                .thenAnswer(i -> i.getArgument(0));

        assertThat(service.markIndexingHeartbeat(testProject)).isTrue();
        assertThat(existing.getUpdatedAt()).isAfter(previousActivity);
        assertThat(existing.getStatus()).isEqualTo(RagIndexingStatus.INDEXING);
        verify(ragIndexStatusRepository).save(existing);
    }

    @Test
    void testMarkIndexingHeartbeat_RefreshesIncrementalUpdate() {
        RagIndexStatus existing = new RagIndexStatus();
        existing.setProject(testProject);
        existing.setStatus(RagIndexingStatus.UPDATING);
        existing.setUpdatedAt(OffsetDateTime.now().minusMinutes(5));
        OffsetDateTime previousActivity = existing.getUpdatedAt();

        when(ragIndexStatusRepository.findByProjectIdForUpdate(100L))
                .thenReturn(Optional.of(existing));
        when(ragIndexStatusRepository.save(any(RagIndexStatus.class)))
                .thenAnswer(i -> i.getArgument(0));

        assertThat(service.markIndexingHeartbeat(testProject)).isTrue();
        assertThat(existing.getUpdatedAt()).isAfter(previousActivity);
        assertThat(existing.getStatus()).isEqualTo(RagIndexingStatus.UPDATING);
        verify(ragIndexStatusRepository).save(existing);
    }

    @Test
    void staleHeartbeatCannotRefreshANewerJobOwner() {
        RagIndexStatus existing = new RagIndexStatus();
        existing.setProject(testProject);
        existing.setStatus(RagIndexingStatus.INDEXING);
        existing.setActiveJobId(92L);
        OffsetDateTime previousActivity = OffsetDateTime.now().minusMinutes(5);
        existing.setUpdatedAt(previousActivity);
        when(ragIndexStatusRepository.findByProjectIdForUpdate(100L))
                .thenReturn(Optional.of(existing));

        assertThat(service.markIndexingHeartbeat(testProject, 91L)).isFalse();
        assertThat(existing.getUpdatedAt()).isEqualTo(previousActivity);
        assertThat(existing.getActiveJobId()).isEqualTo(92L);
        verify(ragIndexStatusRepository, never()).save(any());
    }

    @Test
    void testMarkIndexingHeartbeat_DoesNotMutateTerminalStatus() {
        RagIndexStatus existing = new RagIndexStatus();
        existing.setProject(testProject);
        existing.setStatus(RagIndexingStatus.INDEXED);
        OffsetDateTime previousActivity = existing.getUpdatedAt();

        when(ragIndexStatusRepository.findByProjectIdForUpdate(100L))
                .thenReturn(Optional.of(existing));

        assertThat(service.markIndexingHeartbeat(testProject)).isFalse();
        assertThat(existing.getUpdatedAt()).isEqualTo(previousActivity);
        verify(ragIndexStatusRepository, never()).save(any());
    }

    @Test
    void testMarkIndexingHeartbeat_WithoutStatusIsIgnored() {
        when(ragIndexStatusRepository.findByProjectIdForUpdate(100L))
                .thenReturn(Optional.empty());

        assertThat(service.markIndexingHeartbeat(testProject)).isFalse();
        verify(ragIndexStatusRepository, never()).save(any());
    }

    @Test
    void testConstructor() {
        RagIndexTrackingService newService = new RagIndexTrackingService(ragIndexStatusRepository);
        assertThat(newService).isNotNull();
    }

    // ── markUpdatingStarted ──────────────────────────────────────────────────

    @Test
    void testMarkUpdatingStarted_Success() {
        RagIndexStatus existing = new RagIndexStatus();
        existing.setProject(testProject);
        existing.setStatus(RagIndexingStatus.INDEXED);
        existing.setIndexedBranch("main");
        existing.setIndexedCommitHash("abc123");

        when(ragIndexStatusRepository.findByProjectIdForUpdate(100L)).thenReturn(Optional.of(existing));
        when(ragIndexStatusRepository.save(any(RagIndexStatus.class))).thenAnswer(i -> i.getArgument(0));

        RagIndexStatus result = service.markUpdatingStarted(testProject, "main", "def456", 93L);

        assertThat(result.getStatus()).isEqualTo(RagIndexingStatus.UPDATING);
        assertThat(result.getIndexedBranch()).isEqualTo("main");
        assertThat(result.getIndexedCommitHash()).isEqualTo("abc123");
        assertThat(result.getErrorMessage()).isNull();
        assertThat(result.getActiveJobId()).isEqualTo(93L);
    }

    @Test
    void testMarkUpdatingStarted_ThrowsWhenNotFound() {
        when(ragIndexStatusRepository.findByProjectIdForUpdate(100L)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.markUpdatingStarted(testProject, "main", "def456", 93L))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("Cannot update non-indexed project");
    }

    // ── markUpdatingCompleted ────────────────────────────────────────────────

    @Test
    void testMarkUpdatingCompleted_Success() {
        RagIndexStatus existing = new RagIndexStatus();
        existing.setProject(testProject);
        existing.setStatus(RagIndexingStatus.UPDATING);
        existing.setActiveJobId(93L);

        when(ragIndexStatusRepository.findByProjectIdForUpdate(100L)).thenReturn(Optional.of(existing));
        when(ragIndexStatusRepository.save(any(RagIndexStatus.class))).thenAnswer(i -> i.getArgument(0));

        RagIndexStatus result = service.markUpdatingCompleted(
                testProject, "main", "ghi789", null, null, null, 93L);

        assertThat(result.getStatus()).isEqualTo(RagIndexingStatus.INDEXED);
        assertThat(result.getIndexedBranch()).isEqualTo("main");
        assertThat(result.getIndexedCommitHash()).isEqualTo("ghi789");
        assertThat(result.getActiveJobId()).isNull();
        assertThat(result.getLastIndexedAt()).isNotNull();
        assertThat(result.getErrorMessage()).isNull();
    }

    @Test
    void testMarkUpdatingCompleted_ThrowsWhenNotFound() {
        when(ragIndexStatusRepository.findByProjectIdForUpdate(100L)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.markUpdatingCompleted(
                testProject, "main", "ghi789", null, null, null, 93L))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("RAG index status not found");
    }

    @Test
    void testMarkUpdatingCompleted_NonBaseBranchPreservesProjectCheckpoint() {
        RagIndexStatus existing = new RagIndexStatus();
        existing.setProject(testProject);
        existing.setStatus(RagIndexingStatus.UPDATING);
        existing.setIndexedBranch("main");
        existing.setIndexedCommitHash("main-commit");

        when(ragIndexStatusRepository.findByProjectIdForUpdate(100L))
                .thenReturn(Optional.of(existing));
        when(ragIndexStatusRepository.save(any(RagIndexStatus.class)))
                .thenAnswer(i -> i.getArgument(0));

        RagIndexStatus result = service.markUpdatingCompleted(
                testProject, "feature", "feature-commit", 1, 0, null, false);

        assertThat(result.getStatus()).isEqualTo(RagIndexingStatus.INDEXED);
        assertThat(result.getIndexedBranch()).isEqualTo("main");
        assertThat(result.getIndexedCommitHash()).isEqualTo("main-commit");
    }

    @Test
    void preparePublishedGenerationUsesItsActualActivationTime() {
        OffsetDateTime previousTimestamp = OffsetDateTime.parse(
                "2026-08-10T12:00:00Z");
        OffsetDateTime generationActivatedAt = OffsetDateTime.parse(
                "2026-08-17T15:27:17Z");
        RagIndexStatus existing = new RagIndexStatus();
        existing.setProject(testProject);
        existing.setStatus(RagIndexingStatus.INDEXED);
        existing.setLastIndexedAt(previousTimestamp);
        when(ragIndexStatusRepository.findByProjectIdForUpdate(100L))
                .thenReturn(Optional.of(existing));

        service.preparePublishedGenerationForUpdate(
                testProject,
                "master",
                "cf74934b6c7e",
                4277,
                39323,
                generationActivatedAt);

        assertThat(existing.getIndexedBranch()).isEqualTo("master");
        assertThat(existing.getIndexedCommitHash()).isEqualTo("cf74934b6c7e");
        assertThat(existing.getLastIndexedAt()).isEqualTo(generationActivatedAt);
        verify(ragIndexStatusRepository).save(existing);
    }

    // ── markIncrementalUpdateFailed ──────────────────────────────────────────

    @Test
    void testMarkIncrementalUpdateFailed_Success() {
        RagIndexStatus existing = new RagIndexStatus();
        existing.setProject(testProject);
        existing.setStatus(RagIndexingStatus.UPDATING);
        existing.setIndexedBranch("main");
        existing.setIndexedCommitHash("abc123");

        existing.setActiveJobId(93L);
        when(ragIndexStatusRepository.findByProjectIdForUpdate(100L)).thenReturn(Optional.of(existing));
        when(ragIndexStatusRepository.save(any(RagIndexStatus.class))).thenAnswer(i -> i.getArgument(0));

        RagIndexStatus result = service.markIncrementalUpdateFailed(
                testProject, "timeout error", 93L);

        assertThat(result.getStatus()).isEqualTo(RagIndexingStatus.INDEXED);
        assertThat(result.getIndexedBranch()).isEqualTo("main");
        assertThat(result.getIndexedCommitHash()).isEqualTo("abc123");
        assertThat(result.getErrorMessage()).contains("Incremental update failed: timeout error");
        verify(ragIndexStatusRepository).save(any());
    }

    @Test
    void testMarkIncrementalUpdateFailed_ThrowsWhenNotFound() {
        when(ragIndexStatusRepository.findByProjectIdForUpdate(100L)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.markIncrementalUpdateFailed(testProject, "error", 93L))
                .isInstanceOf(IllegalStateException.class);
    }

    @Test
    void staleCompletionCannotClearANewerJobOwner() {
        RagIndexStatus existing = new RagIndexStatus();
        existing.setProject(testProject);
        existing.setStatus(RagIndexingStatus.INDEXING);
        existing.setActiveJobId(92L);
        existing.setIndexedCommitHash("newer-commit");
        when(ragIndexStatusRepository.findByProjectIdForUpdate(100L))
                .thenReturn(Optional.of(existing));

        RagIndexStatus result = service.markIndexingCompleted(
                testProject, "main", "older-commit", 25, 50, 91L);

        assertThat(result.getStatus()).isEqualTo(RagIndexingStatus.INDEXING);
        assertThat(result.getActiveJobId()).isEqualTo(92L);
        assertThat(result.getIndexedCommitHash()).isEqualTo("newer-commit");
        verify(ragIndexStatusRepository, never()).save(any());
    }

    @Test
    void staleFailureCannotTerminalizeANewerJobOwner() {
        RagIndexStatus existing = new RagIndexStatus();
        existing.setProject(testProject);
        existing.setStatus(RagIndexingStatus.UPDATING);
        existing.setActiveJobId(92L);
        when(ragIndexStatusRepository.findByProjectIdForUpdate(100L))
                .thenReturn(Optional.of(existing));

        RagIndexStatus result = service.markIncrementalUpdateFailed(
                testProject, "older producer failed", 91L);

        assertThat(result.getStatus()).isEqualTo(RagIndexingStatus.UPDATING);
        assertThat(result.getActiveJobId()).isEqualTo(92L);
        assertThat(result.getErrorMessage()).isNull();
        verify(ragIndexStatusRepository, never()).save(any());
    }

    // ── canStartIndexing ─────────────────────────────────────────────────────

    @Test
    void succeededOperationRecoveryRequiresItsExactStatusOwner() {
        RagIndexStatus ownerless = new RagIndexStatus();
        ownerless.setProject(testProject);
        ownerless.setStatus(RagIndexingStatus.UPDATING);
        ownerless.setIndexedCommitHash("newer-checkpoint");
        when(ragIndexStatusRepository.findByProjectIdForUpdate(100L))
                .thenReturn(Optional.of(ownerless));

        boolean reconciled = service.reconcilePublishedGeneration(
                testProject, "main", "old-checkpoint", 10, 20, 91L);

        assertThat(reconciled).isFalse();
        assertThat(ownerless.getIndexedCommitHash()).isEqualTo("newer-checkpoint");
        assertThat(ownerless.getStatus()).isEqualTo(RagIndexingStatus.UPDATING);
        verify(ragIndexStatusRepository, never()).save(any());
    }

    @Test
    void sameRevisionLockOwnedReconciliationCanCreateMissingStatus() {
        when(ragIndexStatusRepository.findByProjectIdForUpdate(100L))
                .thenReturn(Optional.empty());
        when(ragIndexStatusRepository.save(any(RagIndexStatus.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        boolean reconciled = service.reconcilePublishedGeneration(
                testProject, "main", "published-checkpoint", 10, 20);

        assertThat(reconciled).isTrue();
        ArgumentCaptor<RagIndexStatus> saved = ArgumentCaptor.forClass(
                RagIndexStatus.class);
        verify(ragIndexStatusRepository).save(saved.capture());
        assertThat(saved.getValue().getIndexedCommitHash())
                .isEqualTo("published-checkpoint");
        assertThat(saved.getValue().getStatus()).isEqualTo(RagIndexingStatus.INDEXED);
    }

    @Test
    void testCanStartIndexing_NoStatus() {
        when(ragIndexStatusRepository.findByProjectId(100L)).thenReturn(Optional.empty());

        assertThat(service.canStartIndexing(testProject)).isTrue();
    }

    @Test
    void testCanStartIndexing_StatusIndexed() {
        RagIndexStatus status = new RagIndexStatus();
        status.setStatus(RagIndexingStatus.INDEXED);
        when(ragIndexStatusRepository.findByProjectId(100L)).thenReturn(Optional.of(status));

        assertThat(service.canStartIndexing(testProject)).isTrue();
    }

    @Test
    void testCanStartIndexing_StatusFailed() {
        RagIndexStatus status = new RagIndexStatus();
        status.setStatus(RagIndexingStatus.FAILED);
        when(ragIndexStatusRepository.findByProjectId(100L)).thenReturn(Optional.of(status));

        assertThat(service.canStartIndexing(testProject)).isTrue();
    }

    @Test
    void testCanStartIndexing_StatusIndexing() {
        RagIndexStatus status = new RagIndexStatus();
        status.setStatus(RagIndexingStatus.INDEXING);
        when(ragIndexStatusRepository.findByProjectId(100L)).thenReturn(Optional.of(status));

        assertThat(service.canStartIndexing(testProject)).isFalse();
    }

    @Test
    void testCanStartIndexing_StatusUpdating() {
        RagIndexStatus status = new RagIndexStatus();
        status.setStatus(RagIndexingStatus.UPDATING);
        when(ragIndexStatusRepository.findByProjectId(100L)).thenReturn(Optional.of(status));

        assertThat(service.canStartIndexing(testProject)).isFalse();
    }
}
