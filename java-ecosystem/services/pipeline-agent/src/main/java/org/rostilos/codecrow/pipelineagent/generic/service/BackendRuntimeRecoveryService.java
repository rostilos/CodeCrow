package org.rostilos.codecrow.pipelineagent.generic.service;

import java.util.ArrayList;
import java.util.List;
import java.util.Set;

import org.rostilos.codecrow.core.model.analysis.RagIndexStatus;
import org.rostilos.codecrow.core.model.analysis.RagIndexingStatus;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobStatus;
import org.rostilos.codecrow.core.model.reconcile.ReconcileTask;
import org.rostilos.codecrow.core.model.reconcile.ReconcileTaskStatus;
import org.rostilos.codecrow.core.persistence.repository.analysis.AnalysisLockRepository;
import org.rostilos.codecrow.core.persistence.repository.analysis.RagIndexStatusRepository;
import org.rostilos.codecrow.core.persistence.repository.job.JobRepository;
import org.rostilos.codecrow.core.persistence.repository.reconcile.ReconcileTaskRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.SmartInitializingSingleton;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Cancels work owned by the previous backend runtime before this instance
 * starts accepting work. Production runs one pipeline instance, so active
 * locks, jobs, and queued reconciliation tasks at process startup are
 * necessarily orphaned.
 */
@Service
public class BackendRuntimeRecoveryService implements SmartInitializingSingleton {
    static final String RESTART_REASON = "Cancelled because the backend runtime restarted";

    private static final Logger log = LoggerFactory.getLogger(BackendRuntimeRecoveryService.class);
    private static final Set<JobStatus> ACTIVE_JOB_STATUSES = Set.of(
            JobStatus.PENDING,
            JobStatus.QUEUED,
            JobStatus.RUNNING,
            JobStatus.WAITING);

    private final RagIndexStatusRepository ragIndexStatusRepository;
    private final AnalysisLockRepository analysisLockRepository;
    private final JobRepository jobRepository;
    private final ReconcileTaskRepository reconcileTaskRepository;

    public BackendRuntimeRecoveryService(
            RagIndexStatusRepository ragIndexStatusRepository,
            AnalysisLockRepository analysisLockRepository,
            JobRepository jobRepository,
            ReconcileTaskRepository reconcileTaskRepository) {
        this.ragIndexStatusRepository = ragIndexStatusRepository;
        this.analysisLockRepository = analysisLockRepository;
        this.jobRepository = jobRepository;
        this.reconcileTaskRepository = reconcileTaskRepository;
    }

    @Override
    @Transactional
    public void afterSingletonsInstantiated() {
        List<RagIndexStatus> fullIndexes =
                ragIndexStatusRepository.findByStatus(RagIndexingStatus.INDEXING);
        List<RagIndexStatus> incrementalUpdates =
                ragIndexStatusRepository.findByStatus(RagIndexingStatus.UPDATING);

        fullIndexes.forEach(BackendRuntimeRecoveryService::invalidateInterruptedIndex);
        incrementalUpdates.forEach(status -> {
            // Incremental mutation is not atomic. Do not claim that the old
            // commit is still indexed after an interrupted update.
            invalidateInterruptedIndex(status);
            status.incrementFailedIncrementalCount();
        });
        ragIndexStatusRepository.saveAll(fullIndexes);
        ragIndexStatusRepository.saveAll(incrementalUpdates);

        long releasedLocks = analysisLockRepository.count();
        analysisLockRepository.deleteAllInBatch();

        List<Job> interruptedJobs = jobRepository.findByStatusIn(ACTIVE_JOB_STATUSES);
        interruptedJobs.forEach(job -> {
            job.cancel();
            job.setErrorMessage(RESTART_REASON);
        });
        jobRepository.saveAll(interruptedJobs);

        List<ReconcileTask> interruptedReconcileTasks = new ArrayList<>(reconcileTaskRepository
                .findByStatusOrderByCreatedAtAsc(ReconcileTaskStatus.PENDING));
        interruptedReconcileTasks.addAll(reconcileTaskRepository
                .findByStatusOrderByCreatedAtAsc(ReconcileTaskStatus.IN_PROGRESS));
        interruptedReconcileTasks.forEach(task -> task.markFailed(RESTART_REASON));
        reconcileTaskRepository.saveAll(interruptedReconcileTasks);

        if (!fullIndexes.isEmpty() || !incrementalUpdates.isEmpty()
                || releasedLocks > 0 || !interruptedJobs.isEmpty()
                || !interruptedReconcileTasks.isEmpty()) {
            log.warn(
                    "Recovered interrupted backend runtime state: fullIndexes={}, "
                            + "incrementalUpdates={}, locks={}, jobs={}, reconcileTasks={}",
                    fullIndexes.size(), incrementalUpdates.size(),
                    releasedLocks, interruptedJobs.size(), interruptedReconcileTasks.size());
        }
    }

    private static void invalidateInterruptedIndex(RagIndexStatus status) {
        status.setStatus(RagIndexingStatus.FAILED);
        status.setIndexedCommitHash(null);
        status.setLastIndexedAt(null);
        status.setTotalFilesIndexed(null);
        status.setChunkCount(null);
        status.setErrorMessage(RESTART_REASON);
    }
}
