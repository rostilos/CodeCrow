package org.rostilos.codecrow.pipelineagent.generic.service;

import java.time.OffsetDateTime;
import java.util.List;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.analysis.RagIndexStatus;
import org.rostilos.codecrow.core.model.analysis.RagIndexingStatus;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobStatus;
import org.rostilos.codecrow.core.model.job.JobType;
import org.rostilos.codecrow.core.model.reconcile.ReconcileTask;
import org.rostilos.codecrow.core.model.reconcile.ReconcileTaskStatus;
import org.rostilos.codecrow.core.persistence.repository.analysis.AnalysisLockRepository;
import org.rostilos.codecrow.core.persistence.repository.analysis.RagIndexStatusRepository;
import org.rostilos.codecrow.core.persistence.repository.job.JobRepository;
import org.rostilos.codecrow.core.persistence.repository.reconcile.ReconcileTaskRepository;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyCollection;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class BackendRuntimeRecoveryServiceTest {
    @Mock private RagIndexStatusRepository statusRepository;
    @Mock private AnalysisLockRepository lockRepository;
    @Mock private JobRepository jobRepository;
    @Mock private ReconcileTaskRepository reconcileTaskRepository;

    @Test
    void cancelsOrphanedRuntimeStateAndInvalidatesPartialIndexes() {
        RagIndexStatus fullIndex = status(RagIndexingStatus.INDEXING, null);
        RagIndexStatus incremental = status(
                RagIndexingStatus.UPDATING, OffsetDateTime.now().minusDays(1));
        incremental.setIndexedCommitHash("a".repeat(40));
        incremental.setTotalFilesIndexed(12);
        incremental.setChunkCount(34);
        Job initialJob = job(JobType.RAG_INITIAL_INDEX, JobStatus.RUNNING);
        Job incrementalJob = job(JobType.RAG_INCREMENTAL_INDEX, JobStatus.QUEUED);
        Job reviewJob = job(JobType.PR_ANALYSIS, JobStatus.WAITING);
        ReconcileTask pendingReconcile = reconcileTask(ReconcileTaskStatus.PENDING);
        ReconcileTask runningReconcile = reconcileTask(ReconcileTaskStatus.IN_PROGRESS);
        when(statusRepository.findByStatus(RagIndexingStatus.INDEXING))
                .thenReturn(List.of(fullIndex));
        when(statusRepository.findByStatus(RagIndexingStatus.UPDATING))
                .thenReturn(List.of(incremental));
        when(lockRepository.count()).thenReturn(3L);
        when(jobRepository.findByStatusIn(anyCollection()))
                .thenReturn(List.of(initialJob, incrementalJob, reviewJob));
        when(reconcileTaskRepository.findByStatusOrderByCreatedAtAsc(ReconcileTaskStatus.PENDING))
                .thenReturn(List.of(pendingReconcile));
        when(reconcileTaskRepository.findByStatusOrderByCreatedAtAsc(ReconcileTaskStatus.IN_PROGRESS))
                .thenReturn(List.of(runningReconcile));

        new BackendRuntimeRecoveryService(
                statusRepository, lockRepository, jobRepository, reconcileTaskRepository)
                .afterSingletonsInstantiated();

        assertThat(fullIndex.getStatus()).isEqualTo(RagIndexingStatus.FAILED);
        assertThat(incremental.getStatus()).isEqualTo(RagIndexingStatus.FAILED);
        assertThat(incremental.getIndexedCommitHash()).isNull();
        assertThat(incremental.getLastIndexedAt()).isNull();
        assertThat(incremental.getTotalFilesIndexed()).isNull();
        assertThat(incremental.getChunkCount()).isNull();
        assertThat(incremental.getFailedIncrementalCount()).isEqualTo(1);
        assertThat(List.of(initialJob, incrementalJob, reviewJob)).allSatisfy(job -> {
            assertThat(job.getStatus()).isEqualTo(JobStatus.CANCELLED);
            assertThat(job.getCompletedAt()).isNotNull();
            assertThat(job.getErrorMessage())
                    .isEqualTo(BackendRuntimeRecoveryService.RESTART_REASON);
        });
        assertThat(List.of(pendingReconcile, runningReconcile)).allSatisfy(task -> {
            assertThat(task.getStatus()).isEqualTo(ReconcileTaskStatus.FAILED);
            assertThat(task.getCompletedAt()).isNotNull();
            assertThat(task.getErrorMessage())
                    .isEqualTo(BackendRuntimeRecoveryService.RESTART_REASON);
        });
        verify(statusRepository).saveAll(List.of(fullIndex));
        verify(statusRepository).saveAll(List.of(incremental));
        verify(lockRepository).deleteAllInBatch();
        verify(jobRepository).saveAll(List.of(initialJob, incrementalJob, reviewJob));
        verify(reconcileTaskRepository).saveAll(List.of(pendingReconcile, runningReconcile));
    }

    @Test
    void succeedsWhenThereIsNoInterruptedState() {
        when(statusRepository.findByStatus(RagIndexingStatus.INDEXING))
                .thenReturn(List.of());
        when(statusRepository.findByStatus(RagIndexingStatus.UPDATING))
                .thenReturn(List.of());
        when(jobRepository.findByStatusIn(anyCollection())).thenReturn(List.of());
        when(reconcileTaskRepository.findByStatusOrderByCreatedAtAsc(ReconcileTaskStatus.PENDING))
                .thenReturn(List.of());
        when(reconcileTaskRepository.findByStatusOrderByCreatedAtAsc(ReconcileTaskStatus.IN_PROGRESS))
                .thenReturn(List.of());

        new BackendRuntimeRecoveryService(
                statusRepository, lockRepository, jobRepository, reconcileTaskRepository)
                .afterSingletonsInstantiated();

        verify(lockRepository).deleteAllInBatch();
        verify(jobRepository).saveAll(List.of());
        verify(reconcileTaskRepository).saveAll(List.of());
    }

    private static RagIndexStatus status(
            RagIndexingStatus state,
            OffsetDateTime lastIndexedAt) {
        RagIndexStatus status = new RagIndexStatus();
        status.setStatus(state);
        status.setLastIndexedAt(lastIndexedAt);
        return status;
    }

    private static Job job(JobType type, JobStatus status) {
        Job job = new Job();
        job.setJobType(type);
        job.setStatus(status);
        return job;
    }

    private static ReconcileTask reconcileTask(ReconcileTaskStatus status) {
        ReconcileTask task = new ReconcileTask();
        task.setStatus(status);
        return task;
    }
}
