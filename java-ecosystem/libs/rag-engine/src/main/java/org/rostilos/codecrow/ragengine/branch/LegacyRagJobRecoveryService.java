package org.rostilos.codecrow.ragengine.branch;

import org.rostilos.codecrow.core.persistence.repository.job.JobRepository;
import org.rostilos.codecrow.core.service.JobService;
import org.rostilos.codecrow.ragengine.service.RagIndexTrackingService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.time.OffsetDateTime;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Terminalizes legacy incremental RAG jobs whose database lease expired. The
 * RAG lock is deliberately not released here: the legacy remote mutation has
 * no fencing token, so its maintained lease must expire before a retry starts.
 */
@Service
public class LegacyRagJobRecoveryService {
    private static final Logger log = LoggerFactory.getLogger(
            LegacyRagJobRecoveryService.class);

    private final JobService jobService;
    private final RagIndexTrackingService trackingService;
    private final long staleAfterSeconds;
    private final int batchSize;
    private final AtomicBoolean recoveryDegraded = new AtomicBoolean(false);

    public LegacyRagJobRecoveryService(
            JobService jobService,
            RagIndexTrackingService trackingService,
            @Value("${codecrow.rag.legacy-job.lease-seconds:120}")
            long staleAfterSeconds,
            @Value("${codecrow.rag.legacy-job.recovery-batch-size:100}")
            int batchSize) {
        this.jobService = jobService;
        this.trackingService = trackingService;
        this.staleAfterSeconds = Math.max(30, staleAfterSeconds);
        this.batchSize = Math.max(1, batchSize);
    }

    @Scheduled(
            fixedDelayString = "${codecrow.rag.legacy-job.recovery-interval-ms:30000}",
            initialDelayString = "${codecrow.rag.legacy-job.recovery-initial-delay-ms:30000}")
    public void failAbandonedJobs() {
        OffsetDateTime cutoff = OffsetDateTime.now().minusSeconds(staleAfterSeconds);
        String diagnostic = "Legacy RAG update producer stopped heartbeating for "
                + staleAfterSeconds
                + " seconds; the last completed checkpoint was preserved";
        boolean passDegraded = false;

        try {
            for (var coordinates : jobService.findAbandonedLegacyRagJobs(
                    cutoff, batchSize)) {
                try {
                    if (!jobService.failAbandonedLegacyRagJob(
                            coordinates.getJobId(), cutoff, diagnostic)) {
                        continue;
                    }
                    log.warn(
                            "Failed abandoned legacy RAG job {} for project={}, branch={}, commit={}",
                            coordinates.getJobId(),
                            coordinates.getProjectId(),
                            coordinates.getBranchName(),
                            coordinates.getCommitHash());
                    recordDurableFailure(coordinates.getJobId(), diagnostic);
                    if (!repairProjectStatus(coordinates, diagnostic)) {
                        passDegraded = true;
                    }
                } catch (Exception failure) {
                    passDegraded = true;
                    reportDegraded(
                            "Could not terminalize abandoned legacy RAG job "
                                    + coordinates.getJobId(),
                            failure);
                }
            }

            // If a process stopped after the job CAS but before repairing its
            // project-level status, retry that projection independently.
            for (var coordinates : jobService.findFailedLegacyRagJobsWithActiveStatus(
                    batchSize)) {
                String priorDiagnostic = coordinates.getErrorMessage();
                if (!repairProjectStatus(
                        coordinates,
                        priorDiagnostic != null && !priorDiagnostic.isBlank()
                                ? priorDiagnostic
                                : diagnostic)) {
                    passDegraded = true;
                }
            }
        } catch (Exception selectionFailure) {
            passDegraded = true;
            reportDegraded(
                    "Could not scan legacy RAG job recovery state",
                    selectionFailure);
        }

        if (!passDegraded && recoveryDegraded.compareAndSet(true, false)) {
            log.info("Legacy RAG job recovery scan recovered");
        }
    }

    private void recordDurableFailure(long jobId, String diagnostic) {
        try {
            jobService.findById(jobId).ifPresent(job ->
                    jobService.recordExternallyFailedJob(
                            job, "rag_recovery", "Job failed: " + diagnostic));
        } catch (Exception failure) {
            // The guarded CAS already made the job terminal. Projection repair
            // is independent of an optional UI log/notification.
            log.debug("Could not append optional recovery diagnostics to legacy RAG job {}: {}",
                    jobId, failure.getMessage());
        }
    }

    private boolean repairProjectStatus(
            JobRepository.LegacyRagJobRecoveryCoordinates coordinates,
            String diagnostic) {
        try {
            // This is an incremental job, so retain the last usable checkpoint
            // even if an older producer happened to label the live state INDEXING.
            trackingService.recoverAbandonedIncrementalUpdate(
                    coordinates.getProjectId(), coordinates.getJobId(), diagnostic);
            return true;
        } catch (Exception failure) {
            reportDegraded(
                    "Could not repair RAG status after legacy producer abandonment: project="
                            + coordinates.getProjectId()
                            + ", job=" + coordinates.getJobId(),
                    failure);
            return false;
        }
    }

    private void reportDegraded(String operation, Exception failure) {
        String detail = failure.getMessage() != null
                ? failure.getMessage()
                : failure.getClass().getSimpleName();
        if (recoveryDegraded.compareAndSet(false, true)) {
            log.warn("Legacy RAG job recovery degraded: {}: {}", operation, detail);
        } else {
            log.debug("Legacy RAG job recovery remains degraded: {}: {}", operation, detail);
        }
    }
}
