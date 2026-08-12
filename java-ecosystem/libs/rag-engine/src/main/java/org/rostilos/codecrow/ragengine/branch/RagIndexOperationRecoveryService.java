package org.rostilos.codecrow.ragengine.branch;

import org.rostilos.codecrow.analysisapi.rag.RagOperationsService;
import org.rostilos.codecrow.analysisengine.service.AnalysisLockService;
import org.rostilos.codecrow.core.model.analysis.RagIndexingStatus;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.rag.RagIndexOperation;
import org.rostilos.codecrow.core.persistence.repository.project.ProjectRepository;
import org.rostilos.codecrow.core.service.JobService;
import org.rostilos.codecrow.ragengine.service.RagBranchIndexRegistryService;
import org.rostilos.codecrow.ragengine.service.RagIndexTrackingService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.time.OffsetDateTime;

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
        for (var operation : registryService.findRecoverableOperations(cutoff)) {
            String diagnostic = "Exact RAG generation producer stopped heartbeating for "
                    + staleAfterMinutes + " minutes; the previous active generation was preserved";
            try {
                if (!registryService.failIfAbandoned(operation.getId(), cutoff, diagnostic)) {
                    continue;
                }
                log.warn("Failed abandoned RAG generation operation {} for branch {}",
                        operation.getId(), operation.getBranchName());
            } catch (Exception failure) {
                log.error("Could not terminalize abandoned RAG generation operation {}",
                        operation.getId(), failure);
                continue;
            }

            recoverProjections(operation, diagnostic);
        }

        for (var operation : registryService.findFailedOperationsWithActiveProjections()) {
            String diagnostic = operation.getErrorMessage() != null
                    && !operation.getErrorMessage().isBlank()
                    ? operation.getErrorMessage()
                    : "RAG generation failed before its durable projections were terminalized";
            recoverProjections(operation, diagnostic);
        }
    }

    private void recoverProjections(
            RagIndexOperation operation,
            String diagnostic) {
        failDurableJob(operation.getJobId(), diagnostic);
        terminalizePrimaryStatus(operation, diagnostic);
        releaseAbandonedLock(operation);
    }

    private void failDurableJob(Long jobId, String diagnostic) {
        if (jobId == null) {
            return;
        }
        try {
            Job job = jobService.findById(jobId).orElse(null);
            if (job != null && !job.isTerminal()) {
                jobService.failJob(job, diagnostic);
            }
        } catch (Exception failure) {
            log.error("Could not fail durable RAG job {} after producer abandonment",
                    jobId, failure);
        }
    }

    private void terminalizePrimaryStatus(RagIndexOperation operation, String diagnostic) {
        Long projectId = operation.getProject().getId();
        String branchName = operation.getBranchName();
        try {
            var project = projectRepository.findByIdWithFullDetails(projectId)
                    .orElse(null);
            if (project == null
                    || !branchName.equals(ragOperationsService.getBaseBranch(project))) {
                return;
            }
            var status = trackingService.getIndexStatus(project).orElse(null);
            if (status == null) {
                return;
            }
            if (status.getActiveJobId() != null
                    && !status.getActiveJobId().equals(operation.getJobId())) {
                log.info(
                        "Preserving RAG status owned by newer job {} while recovering abandoned job {}: "
                                + "project={}, branch={}",
                        status.getActiveJobId(), operation.getJobId(), projectId, branchName);
                return;
            }
            if (status.getStatus() == RagIndexingStatus.INDEXING) {
                trackingService.markIndexingFailed(project, diagnostic, operation.getJobId());
            } else if (status.getStatus() == RagIndexingStatus.UPDATING) {
                trackingService.markIncrementalUpdateFailed(
                        project, diagnostic, operation.getJobId());
            }
        } catch (Exception failure) {
            log.error(
                    "Could not terminalize RAG project status after producer abandonment: "
                            + "project={}, branch={}",
                    projectId, branchName, failure);
        }
    }

    private void releaseAbandonedLock(RagIndexOperation operation) {
        Long projectId = operation.getProject().getId();
        String branchName = operation.getBranchName();
        String lockKey = operation.getAnalysisLockKey();
        if (lockKey == null || lockKey.isBlank()) {
            log.warn(
                    "Cannot release abandoned RAG lock without its exact owner key; "
                            + "leaving it to expire: project={}, branch={}, commit={}",
                    projectId, branchName, operation.getToRevision());
            return;
        }
        try {
            lockService.releaseLock(lockKey);
            log.warn(
                    "Released abandoned RAG indexing lock for project={}, branch={}, commit={}",
                    projectId, branchName, operation.getToRevision());
        } catch (Exception failure) {
            log.error(
                    "Could not release abandoned RAG indexing lock: project={}, branch={}",
                    projectId, branchName, failure);
        }
    }
}
