package org.rostilos.codecrow.ragengine.branch;

import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.core.model.analysis.RagIndexingStatus;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.persistence.repository.rag.RagIndexOperationRepository;
import org.rostilos.codecrow.core.service.JobService;
import org.rostilos.codecrow.ragengine.service.RagBranchIndexRegistryService;
import org.rostilos.codecrow.ragengine.service.RagIndexTrackingService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.time.OffsetDateTime;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Terminates registry operations whose producer disappeared before publishing
 * a generation. The prior active generation remains queryable; a later event
 * retries the same idempotency key from a fresh snapshot or checkpoint.
 */
@Service
public class RagIndexOperationRecoveryService {

    private static final Logger log = LoggerFactory.getLogger(
            RagIndexOperationRecoveryService.class);

    private final RagBranchIndexRegistryService registryService;
    private final ProjectRepository projectRepository;
    private final JobService jobService;
    private final RagIndexTrackingService trackingService;
    private final RagOperationsService ragOperationsService;
    private final AnalysisLockService lockService;
    private final long staleAfterMinutes;
    private final AtomicBoolean recoveryDegraded = new AtomicBoolean(false);

    public RagIndexOperationRecoveryService(
            RagBranchIndexRegistryService registryService,
            ProjectRepository projectRepository,
            JobService jobService,
            RagIndexTrackingService trackingService,
            RagOperationsService ragOperationsService,
            AnalysisLockService lockService,
            @Value("${codecrow.rag.generation.stale-after-minutes:30}")
            long staleAfterMinutes) {
        this.registryService = registryService;
        this.projectRepository = projectRepository;
        this.jobService = jobService;
        this.trackingService = trackingService;
        this.ragOperationsService = ragOperationsService;
        this.lockService = lockService;
        this.staleAfterMinutes = Math.max(5, staleAfterMinutes);
    }

    @Scheduled(
            fixedDelayString = "${codecrow.rag.generation.recovery-interval-ms:300000}",
            initialDelayString = "${codecrow.rag.generation.recovery-initial-delay-ms:60000}")
    public void failAbandonedOperations() {
        OffsetDateTime cutoff = OffsetDateTime.now().minusMinutes(staleAfterMinutes);
        boolean passDegraded = false;
        try {
            for (var operation : registryService.findRecoverableOperations(cutoff)) {
                String diagnostic = "Exact RAG generation producer stopped heartbeating for "
                        + staleAfterMinutes + " minutes; the previous active generation was preserved";
                try {
                    if (!registryService.failIfAbandoned(
                            operation.getOperationId(), cutoff, diagnostic)) {
                        continue;
                    }
                    log.warn("Failed abandoned RAG generation operation {} for branch {}",
                            operation.getOperationId(), operation.getBranchName());
                } catch (Exception failure) {
                    passDegraded = true;
                    reportDegraded(
                                    "Could not terminalize abandoned RAG generation operation "
                                    + operation.getOperationId(),
                            failure);
                    continue;
                }

                if (!recoverProjections(operation, diagnostic)) {
                    passDegraded = true;
                }
            }

            for (var operation : registryService.findFailedOperationsWithActiveProjections()) {
                String diagnostic = operation.getErrorMessage() != null
                        && !operation.getErrorMessage().isBlank()
                        ? operation.getErrorMessage()
                        : "RAG generation failed before its durable projections were terminalized";
                if (!recoverProjections(operation, diagnostic)) {
                    passDegraded = true;
                }
            }

            for (var operation : registryService.findSucceededOperationsWithActiveProjections()) {
                if (!recoverPublishedProjections(operation)) {
                    passDegraded = true;
                }
            }
        } catch (Exception selectionFailure) {
            passDegraded = true;
            reportDegraded(
                    "Could not scan exact RAG operation recovery state",
                    selectionFailure);
        }

        if (!passDegraded && recoveryDegraded.compareAndSet(true, false)) {
            log.info("Exact RAG operation recovery scan recovered");
        }
    }

    private boolean recoverPublishedProjections(
            RagIndexOperationRepository.SucceededOperationProjection operation) {
        boolean recovered = true;
        Long projectId = operation.getProjectId();
        String branchName = operation.getBranchName();
        Long jobId = operation.getJobId();
        try {
            var project = projectRepository.findByIdWithFullDetails(projectId)
                    .orElse(null);
            if (Boolean.TRUE.equals(operation.getActiveGeneration())
                    && project != null
                    && branchName.equals(ragOperationsService.getBaseBranch(project))) {
                trackingService.reconcilePublishedGeneration(
                        project,
                        branchName,
                        operation.getToRevision(),
                        operation.getFileCount(),
                        operation.getChunkCount(),
                        jobId);
            }
        } catch (Exception failure) {
            recovered = false;
            reportDegraded(
                    "Could not reconcile published RAG project status: project="
                            + projectId + ", branch=" + branchName,
                    failure);
        }

        if (jobId != null) {
            try {
                Job job = jobService.findById(jobId).orElse(null);
                if (job != null && !job.isTerminal()) {
                    jobService.completeJob(job);
                }
            } catch (Exception failure) {
                recovered = false;
                reportDegraded(
                        "Could not complete durable job " + jobId
                                + " for published RAG generation",
                        failure);
            }
        }

        return releasePublishedLock(operation) && recovered;
    }

    private boolean releasePublishedLock(
            RagIndexOperationRepository.SucceededOperationProjection operation) {
        String lockKey = operation.getAnalysisLockKey();
        if (lockKey == null || lockKey.isBlank()) {
            return true;
        }
        try {
            lockService.releaseLock(lockKey);
            log.info(
                    "Released completed RAG indexing lock during projection recovery: "
                            + "project={}, branch={}, commit={}",
                    operation.getProjectId(), operation.getBranchName(),
                    operation.getToRevision());
            return true;
        } catch (Exception failure) {
            reportDegraded(
                    "Could not release completed RAG indexing lock: project="
                            + operation.getProjectId() + ", branch="
                            + operation.getBranchName(),
                    failure);
            return false;
        }
    }

    private boolean recoverProjections(
            RagIndexOperationRepository.RecoveryOperationProjection operation,
            String diagnostic) {
        boolean jobRecovered = failDurableJob(operation.getJobId(), diagnostic);
        boolean statusRecovered = terminalizePrimaryStatus(operation, diagnostic);
        boolean lockRecovered = releaseAbandonedLock(operation);
        return jobRecovered && statusRecovered && lockRecovered;
    }

    private boolean failDurableJob(Long jobId, String diagnostic) {
        if (jobId == null) {
            return true;
        }
        try {
            Job job = jobService.findById(jobId).orElse(null);
            if (job != null && !job.isTerminal()) {
                jobService.failJob(job, diagnostic);
            }
            return true;
        } catch (Exception failure) {
            reportDegraded(
                    "Could not fail durable RAG job " + jobId
                            + " after producer abandonment",
                    failure);
            return false;
        }
    }

    private boolean terminalizePrimaryStatus(
            RagIndexOperationRepository.RecoveryOperationProjection operation,
            String diagnostic) {
        Long projectId = operation.getProjectId();
        String branchName = operation.getBranchName();
        Long jobId = operation.getJobId();
        try {
            var project = projectRepository.findByIdWithFullDetails(projectId)
                    .orElse(null);
            if (project == null
                    || !branchName.equals(ragOperationsService.getBaseBranch(project))) {
                return true;
            }
            var status = trackingService.getIndexStatus(project).orElse(null);
            if (status == null) {
                return true;
            }
            if (status.getActiveJobId() == null
                    || !status.getActiveJobId().equals(jobId)) {
                log.info(
                        "Preserving RAG status owned by job {} while recovering abandoned job {}: "
                                + "project={}, branch={}",
                        status.getActiveJobId(), jobId, projectId, branchName);
                return true;
            }
            if (status.getStatus() == RagIndexingStatus.INDEXING) {
                trackingService.markIndexingFailed(project, diagnostic, jobId);
            } else if (status.getStatus() == RagIndexingStatus.UPDATING) {
                trackingService.markIncrementalUpdateFailed(
                        project, diagnostic, jobId);
            }
            return true;
        } catch (Exception failure) {
            reportDegraded(
                    "Could not terminalize RAG project status after producer abandonment: "
                            + "project=" + projectId + ", branch=" + branchName,
                    failure);
            return false;
        }
    }

    private boolean releaseAbandonedLock(
            RagIndexOperationRepository.RecoveryOperationProjection operation) {
        Long projectId = operation.getProjectId();
        String branchName = operation.getBranchName();
        String lockKey = operation.getAnalysisLockKey();
        if (lockKey == null || lockKey.isBlank()) {
            log.info(
                    "Cannot release abandoned RAG lock without its exact owner key; "
                            + "leaving it to expire: project={}, branch={}, commit={}",
                    projectId, branchName, operation.getToRevision());
            return true;
        }
        try {
            lockService.releaseLock(lockKey);
            log.info(
                    "Released abandoned RAG indexing lock for project={}, branch={}, commit={}",
                    projectId, branchName, operation.getToRevision());
            return true;
        } catch (Exception failure) {
            reportDegraded(
                    "Could not release abandoned RAG indexing lock: project="
                            + projectId + ", branch=" + branchName,
                    failure);
            return false;
        }
    }

    private void reportDegraded(String operation, Exception failure) {
        String detail = failure.getMessage() != null
                ? failure.getMessage()
                : failure.getClass().getSimpleName();
        if (recoveryDegraded.compareAndSet(false, true)) {
            log.warn("Exact RAG operation recovery degraded: {}: {}", operation, detail);
        } else {
            log.debug("Exact RAG operation recovery remains degraded: {}: {}", operation, detail);
        }
    }
}
