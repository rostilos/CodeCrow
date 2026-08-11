package org.rostilos.codecrow.ragengine.branch;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.analysis.RagIndexStatus;
import org.rostilos.codecrow.core.model.analysis.RagIndexingStatus;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.rag.RagIndexOperation;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.service.JobService;
import org.rostilos.codecrow.ragengine.service.RagBranchIndexRegistryService;
import org.rostilos.codecrow.ragengine.service.RagIndexTrackingService;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class RagIndexOperationRecoveryServiceTest {

    private RagBranchIndexRegistryService registry;
    private ProjectRepository projects;
    private JobService jobs;
    private RagIndexTrackingService tracking;
    private RagOperationsService ragOperations;
    private AnalysisLockService locks;
    private RagIndexOperationRecoveryService recovery;
    private RagIndexOperation operation;
    private Project project;

    @BeforeEach
    void setUp() {
        registry = mock(RagBranchIndexRegistryService.class);
        projects = mock(ProjectRepository.class);
        jobs = mock(JobService.class);
        tracking = mock(RagIndexTrackingService.class);
        ragOperations = mock(RagOperationsService.class);
        locks = mock(AnalysisLockService.class);
        recovery = new RagIndexOperationRecoveryService(
                registry, projects, jobs, tracking, ragOperations, locks, 30);

        project = mock(Project.class);
        when(project.getId()).thenReturn(42L);
        operation = mock(RagIndexOperation.class);
        when(operation.getId()).thenReturn(81L);
        when(operation.getProject()).thenReturn(project);
        when(operation.getBranchName()).thenReturn("main");
        when(operation.getToRevision()).thenReturn("commit-a");
        when(operation.getJobId()).thenReturn(91L);
        when(registry.findFailedOperationsWithActiveProjections())
                .thenReturn(List.of());
    }

    @Test
    void abandonedOperationTerminalizesJobPrimaryStatusAndLock() {
        Job job = new Job();
        RagIndexStatus status = new RagIndexStatus();
        status.setStatus(RagIndexingStatus.INDEXING);
        when(registry.findRecoverableOperations(any(OffsetDateTime.class)))
                .thenReturn(List.of(operation));
        when(registry.fail(eq(81L), anyString())).thenReturn(true);
        when(jobs.findById(91L)).thenReturn(Optional.of(job));
        when(projects.findByIdWithFullDetails(42L)).thenReturn(Optional.of(project));
        when(ragOperations.getBaseBranch(project)).thenReturn("main");
        when(tracking.getIndexStatus(project)).thenReturn(Optional.of(status));
        when(locks.releaseMatchingLock(
                42L, "main", AnalysisLockType.RAG_INDEXING, "commit-a"))
                .thenReturn(true);

        recovery.failAbandonedOperations();

        verify(registry).fail(eq(81L), argThat(message ->
                message.contains("stopped heartbeating")
                        && message.contains("previous active generation was preserved")));
        verify(jobs).failJob(eq(job), contains("stopped heartbeating"));
        verify(tracking).markIndexingFailed(eq(project), contains("stopped heartbeating"));
        verify(locks).releaseMatchingLock(
                42L, "main", AnalysisLockType.RAG_INDEXING, "commit-a");
    }

    @Test
    void abandonedIncrementalOperationRestoresTheUsableIndexedStatus() {
        RagIndexStatus status = new RagIndexStatus();
        status.setStatus(RagIndexingStatus.UPDATING);
        when(registry.findRecoverableOperations(any(OffsetDateTime.class)))
                .thenReturn(List.of(operation));
        when(registry.fail(eq(81L), anyString())).thenReturn(true);
        when(jobs.findById(91L)).thenReturn(Optional.empty());
        when(projects.findByIdWithFullDetails(42L)).thenReturn(Optional.of(project));
        when(ragOperations.getBaseBranch(project)).thenReturn("main");
        when(tracking.getIndexStatus(project)).thenReturn(Optional.of(status));

        recovery.failAbandonedOperations();

        verify(tracking).markIncrementalUpdateFailed(
                eq(project), contains("stopped heartbeating"));
        verify(tracking, never()).markIndexingFailed(any(), anyString());
    }

    @Test
    void newerLiveOperationOwnsStatusAndLockButOldJobIsStillFailed() {
        Job job = new Job();
        when(registry.findRecoverableOperations(any(OffsetDateTime.class)))
                .thenReturn(List.of(operation));
        when(registry.fail(eq(81L), anyString())).thenReturn(true);
        when(registry.hasLiveOperation(42L, "main")).thenReturn(true);
        when(jobs.findById(91L)).thenReturn(Optional.of(job));

        recovery.failAbandonedOperations();

        verify(jobs).failJob(eq(job), contains("stopped heartbeating"));
        verifyNoInteractions(projects, tracking, ragOperations, locks);
    }

    @Test
    void alreadyFailedOperationRepairsOlderProjectionDrift() {
        Job job = new Job();
        RagIndexStatus status = new RagIndexStatus();
        status.setStatus(RagIndexingStatus.INDEXING);
        when(operation.getErrorMessage()).thenReturn("producer was abandoned");
        when(registry.findRecoverableOperations(any(OffsetDateTime.class)))
                .thenReturn(List.of());
        when(registry.findFailedOperationsWithActiveProjections())
                .thenReturn(List.of(operation));
        when(jobs.findById(91L)).thenReturn(Optional.of(job));
        when(projects.findByIdWithFullDetails(42L)).thenReturn(Optional.of(project));
        when(ragOperations.getBaseBranch(project)).thenReturn("main");
        when(tracking.getIndexStatus(project)).thenReturn(Optional.of(status));

        recovery.failAbandonedOperations();

        verify(registry, never()).fail(anyLong(), anyString());
        verify(jobs).failJob(job, "producer was abandoned");
        verify(tracking).markIndexingFailed(project, "producer was abandoned");
        verify(locks).releaseMatchingLock(
                42L, "main", AnalysisLockType.RAG_INDEXING, "commit-a");
    }
}
