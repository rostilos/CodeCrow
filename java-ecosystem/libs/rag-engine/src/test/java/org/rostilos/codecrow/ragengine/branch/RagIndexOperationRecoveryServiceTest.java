package org.rostilos.codecrow.ragengine.branch;

import ch.qos.logback.classic.Level;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.core.model.analysis.RagIndexStatus;
import org.rostilos.codecrow.core.model.analysis.RagIndexingStatus;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobStatus;
import org.rostilos.codecrow.core.model.job.JobType;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.persistence.repository.rag.RagIndexOperationRepository;
import org.rostilos.codecrow.core.service.JobService;
import org.rostilos.codecrow.ragengine.service.RagBranchIndexRegistryService;
import org.rostilos.codecrow.ragengine.service.RagIndexTrackingService;
import org.slf4j.LoggerFactory;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
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
    private RagIndexOperationRepository.RecoveryOperationProjection operation;
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
        operation = mock(
                RagIndexOperationRepository.RecoveryOperationProjection.class);
        when(operation.getOperationId()).thenReturn(81L);
        when(operation.getProjectId()).thenReturn(42L);
        when(operation.getBranchName()).thenReturn("main");
        when(operation.getToRevision()).thenReturn("commit-a");
        when(operation.getJobId()).thenReturn(91L);
        when(operation.getAnalysisLockKey()).thenReturn("rag-lock-owner-91");
        when(registry.findFailedOperationsWithActiveProjections())
                .thenReturn(List.of());
        when(registry.findSucceededOperationsWithActiveProjections())
                .thenReturn(List.of());
    }

    @Test
    void abandonedOperationTerminalizesJobPrimaryStatusAndLock() {
        Job job = new Job();
        job.setJobType(JobType.RAG_INCREMENTAL_INDEX);
        job.setStatus(JobStatus.RUNNING);
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

    @Test
    void failedOperationNeverGuessesOwnershipOfAnOwnerlessStatus() {
        RagIndexStatus status = new RagIndexStatus();
        status.setStatus(RagIndexingStatus.UPDATING);
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

    @Test
    void publishedInitialOperationCompletesJobRepairsPrimaryStatusAndReleasesLock() {
        RagIndexOperationRepository.SucceededOperationProjection published =
                mock(RagIndexOperationRepository.SucceededOperationProjection.class);
        when(published.getOperationId()).thenReturn(82L);
        when(published.getProjectId()).thenReturn(42L);
        when(published.getBranchName()).thenReturn("main");
        when(published.getToRevision()).thenReturn("commit-published");
        when(published.getJobId()).thenReturn(92L);
        when(published.getAnalysisLockKey()).thenReturn("published-lock-owner");
        when(published.getFileCount()).thenReturn(214);
        when(published.getChunkCount()).thenReturn(642);
        when(published.getActiveGeneration()).thenReturn(true);
        Job job = new Job();
        job.setJobType(JobType.RAG_INITIAL_INDEX);
        job.setStatus(JobStatus.RUNNING);
        when(registry.findRecoverableOperations(any())).thenReturn(List.of());
        when(registry.findSucceededOperationsWithActiveProjections())
                .thenReturn(List.of(published));
        when(projects.findByIdWithFullDetails(42L)).thenReturn(Optional.of(project));
        when(ragOperations.getBaseBranch(project)).thenReturn("main");
        when(jobs.findById(92L)).thenReturn(Optional.of(job));

        recovery.failAbandonedOperations();

        verify(tracking).reconcilePublishedGeneration(
                project, "main", "commit-published", 214, 642, 92L);
        verify(jobs).completeJob(job);
        verify(jobs, never()).failJob(any(), anyString());
        verify(locks).releaseLock("published-lock-owner");
    }

    @Test
    void publishedOperationWithOwnerlessStatusIsNotGuessedOrRegressed() {
        RagIndexOperationRepository.SucceededOperationProjection published =
                mock(RagIndexOperationRepository.SucceededOperationProjection.class);
        when(published.getProjectId()).thenReturn(42L);
        when(published.getBranchName()).thenReturn("main");
        when(published.getToRevision()).thenReturn("old-commit");
        when(published.getJobId()).thenReturn(92L);
        when(published.getActiveGeneration()).thenReturn(true);
        when(registry.findRecoverableOperations(any())).thenReturn(List.of());
        when(registry.findSucceededOperationsWithActiveProjections())
                .thenReturn(List.of(published));
        when(projects.findByIdWithFullDetails(42L)).thenReturn(Optional.of(project));
        when(ragOperations.getBaseBranch(project)).thenReturn("main");
        when(tracking.reconcilePublishedGeneration(
                project, "main", "old-commit", 0, 0, 92L))
                .thenReturn(false);

        recovery.failAbandonedOperations();

        verify(tracking).reconcilePublishedGeneration(
                project, "main", "old-commit", 0, 0, 92L);
        verifyNoMoreInteractions(tracking);
    }

    @Test
    void persistentProjectionFailuresEmitOneWarningThenDebugUntilRecovery() {
        when(registry.findRecoverableOperations(any())).thenReturn(List.of());
        when(registry.findFailedOperationsWithActiveProjections())
                .thenReturn(List.of(operation), List.of(operation), List.of());
        when(jobs.findById(91L)).thenThrow(new IllegalStateException("database down"));
        when(projects.findByIdWithFullDetails(42L))
                .thenThrow(new IllegalStateException("database down"));
        doThrow(new IllegalStateException("database down"))
                .when(locks).releaseLock("rag-lock-owner-91");
        Logger logger = (Logger) LoggerFactory.getLogger(
                RagIndexOperationRecoveryService.class);
        Level priorLevel = logger.getLevel();
        logger.setLevel(Level.DEBUG);
        ListAppender<ILoggingEvent> events = new ListAppender<>();
        events.start();
        logger.addAppender(events);
        try {
            recovery.failAbandonedOperations();
            recovery.failAbandonedOperations();

            reset(jobs, projects, locks);
            recovery.failAbandonedOperations();
        } finally {
            logger.detachAppender(events);
            logger.setLevel(priorLevel);
            events.stop();
        }

        List<ILoggingEvent> transitionEvents = events.list.stream()
                .filter(event -> event.getFormattedMessage()
                        .contains("Exact RAG operation recovery"))
                .toList();
        assertThat(transitionEvents.stream()
                .filter(event -> event.getLevel() == Level.WARN)).hasSize(1);
        assertThat(transitionEvents.stream()
                .filter(event -> event.getLevel() == Level.DEBUG)).isNotEmpty();
        assertThat(transitionEvents.stream()
                .filter(event -> event.getLevel() == Level.INFO
                        && event.getFormattedMessage().contains("scan recovered")))
                .hasSize(1);
        assertThat(events.list.stream()
                .filter(event -> event.getLevel() == Level.ERROR)).isEmpty();
    }
}
