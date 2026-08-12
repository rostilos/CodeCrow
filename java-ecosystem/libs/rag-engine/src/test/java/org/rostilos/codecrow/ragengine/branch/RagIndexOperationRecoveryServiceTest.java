package org.rostilos.codecrow.ragengine.branch;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
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
        when(operation.getAnalysisLockKey()).thenReturn("rag-lock-owner-91");
        when(registry.findFailedOperationsWithActiveProjections())
                .thenReturn(List.of());
    }

    @Test
    void abandonedOperationTerminalizesJobPrimaryStatusAndLock() {
        Job job = new Job();
        RagIndexStatus status = new RagIndexStatus();
        status.setStatus(RagIndexingStatus.INDEXING);
        status.setActiveJobId(91L);
        when(registry.findRecoverableOperations(any(OffsetDateTime.class)))
                .thenReturn(List.of(operation));
        when(registry.failIfAbandoned(eq(81L), any(OffsetDateTime.class), anyString()))
                .thenReturn(true);
        when(jobs.findById(91L)).thenReturn(Optional.of(job));
        when(projects.findByIdWithFullDetails(42L)).thenReturn(Optional.of(project));
        when(ragOperations.getBaseBranch(project)).thenReturn("main");
        when(tracking.getIndexStatus(project)).thenReturn(Optional.of(status));
        recovery.failAbandonedOperations();

        verify(registry).failIfAbandoned(eq(81L), any(OffsetDateTime.class), argThat(message ->
                message.contains("stopped heartbeating")
                        && message.contains("previous active generation was preserved")));
        verify(jobs).failJob(eq(job), contains("stopped heartbeating"));
        verify(tracking).markIndexingFailed(
                eq(project), contains("stopped heartbeating"), eq(91L));
        verify(locks).releaseLock("rag-lock-owner-91");
    }

    @Test
    void abandonedIncrementalOperationRestoresTheUsableIndexedStatus() {
        RagIndexStatus status = new RagIndexStatus();
        status.setStatus(RagIndexingStatus.UPDATING);
        status.setActiveJobId(91L);
        when(registry.findRecoverableOperations(any(OffsetDateTime.class)))
                .thenReturn(List.of(operation));
        when(registry.failIfAbandoned(eq(81L), any(OffsetDateTime.class), anyString()))
                .thenReturn(true);
        when(jobs.findById(91L)).thenReturn(Optional.empty());
        when(projects.findByIdWithFullDetails(42L)).thenReturn(Optional.of(project));
        when(ragOperations.getBaseBranch(project)).thenReturn("main");
        when(tracking.getIndexStatus(project)).thenReturn(Optional.of(status));

        recovery.failAbandonedOperations();

        verify(tracking).markIncrementalUpdateFailed(
                eq(project), contains("stopped heartbeating"), eq(91L));
        verify(tracking, never()).markIndexingFailed(any(), anyString(), any());
    }

    @Test
    void newerLiveOperationPreservesStatusWhileOldExactLockIsReleased() {
        Job job = new Job();
        RagIndexStatus status = new RagIndexStatus();
        status.setStatus(RagIndexingStatus.INDEXING);
        status.setActiveJobId(92L);
        when(registry.findRecoverableOperations(any(OffsetDateTime.class)))
                .thenReturn(List.of(operation));
        when(registry.failIfAbandoned(eq(81L), any(OffsetDateTime.class), anyString()))
                .thenReturn(true);
        when(jobs.findById(91L)).thenReturn(Optional.of(job));
        when(projects.findByIdWithFullDetails(42L)).thenReturn(Optional.of(project));
        when(ragOperations.getBaseBranch(project)).thenReturn("main");
        when(tracking.getIndexStatus(project)).thenReturn(Optional.of(status));

        recovery.failAbandonedOperations();

        verify(jobs).failJob(eq(job), contains("stopped heartbeating"));
        verify(tracking, never()).markIndexingFailed(any(), anyString(), any());
        verify(locks).releaseLock("rag-lock-owner-91");
    }

    @Test
    void alreadyFailedOperationRepairsOlderProjectionDrift() {
        Job job = new Job();
        RagIndexStatus status = new RagIndexStatus();
        status.setStatus(RagIndexingStatus.INDEXING);
        status.setActiveJobId(91L);
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

        verify(registry, never()).failIfAbandoned(anyLong(), any(), anyString());
        verify(jobs).failJob(job, "producer was abandoned");
        verify(tracking).markIndexingFailed(project, "producer was abandoned", 91L);
        verify(locks).releaseLock("rag-lock-owner-91");
    }

    @Test
    void alreadyFailedOperationPreservesStatusOwnedByANewerJob() {
        RagIndexStatus status = new RagIndexStatus();
        status.setStatus(RagIndexingStatus.INDEXING);
        status.setActiveJobId(92L);
        when(registry.findRecoverableOperations(any(OffsetDateTime.class)))
                .thenReturn(List.of());
        when(registry.findFailedOperationsWithActiveProjections())
                .thenReturn(List.of(operation));
        when(projects.findByIdWithFullDetails(42L)).thenReturn(Optional.of(project));
        when(ragOperations.getBaseBranch(project)).thenReturn("main");
        when(tracking.getIndexStatus(project)).thenReturn(Optional.of(status));

        recovery.failAbandonedOperations();

        verify(tracking, never()).markIndexingFailed(any(), anyString(), any());
        verify(tracking, never()).markIncrementalUpdateFailed(any(), anyString(), any());
        verify(locks).releaseLock("rag-lock-owner-91");
    }
}
