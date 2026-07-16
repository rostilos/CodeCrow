package org.rostilos.codecrow.analysisengine.service.branch;

import org.rostilos.codecrow.analysisengine.exception.AnalysisLockedException;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.persistence.repository.job.JobRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.util.Map;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.locks.LockSupport;
import java.util.function.Consumer;

/**
 * Target-branch barrier between PR analysis and branch reconciliation.
 *
 * <p>PR jobs are created transactionally before async dispatch, which closes
 * the gap where a merge webhook could arrive before the PR handler acquired its
 * analysis lock. The job remains active until the handler has released that
 * lock and persisted its analysis, so waiting for the job is stronger than
 * polling the source-branch lock alone.</p>
 */
@Service
public class BranchAnalysisGateService {

    private static final Logger log = LoggerFactory.getLogger(BranchAnalysisGateService.class);

    private final JobRepository jobRepository;

    @Value("${analysis.branch.pr-wait.timeout.minutes:60}")
    private long waitTimeoutMinutes;

    @Value("${analysis.branch.pr-wait.poll-interval.millis:1000}")
    private long pollIntervalMillis;

    public BranchAnalysisGateService(JobRepository jobRepository) {
        this.jobRepository = jobRepository;
    }

    /**
     * Wait for all target-branch PR jobs. When {@code currentBranchJobId} is
     * supplied, an older branch job yields permanently to any viable newer job.
     */
    public GateResult awaitTurn(
            Long projectId,
            String branchName,
            Long currentBranchJobId,
            Consumer<Map<String, Object>> consumer) {
        long timeoutNanos = TimeUnit.MINUTES.toNanos(Math.max(1, waitTimeoutMinutes));
        long startedAt = System.nanoTime();

        while (true) {
            if (currentBranchJobId != null && jobRepository.existsNewerBranchAnalysisJob(
                    projectId, branchName, currentBranchJobId)) {
                log.info("Branch analysis job {} superseded for project={}, branch={}",
                        currentBranchJobId, projectId, branchName);
                return GateResult.SUPERSEDED;
            }

            if (!jobRepository.existsActivePrAnalysisJob(projectId, branchName)) {
                return GateResult.READY;
            }

            long waitedNanos = System.nanoTime() - startedAt;
            if (waitedNanos >= timeoutNanos) {
                log.warn("Timed out waiting for PR analyses: project={}, branch={}, waited={}m",
                        projectId, branchName, TimeUnit.NANOSECONDS.toMinutes(waitedNanos));
                throw new AnalysisLockedException(
                        AnalysisLockType.PR_ANALYSIS.name(), branchName, projectId);
            }

            emitWait(consumer, branchName, waitedNanos);
            if (!pause()) {
                throw new AnalysisLockedException(
                        AnalysisLockType.PR_ANALYSIS.name(), branchName, projectId);
            }
        }
    }

    public void awaitPrAnalyses(
            Long projectId,
            String branchName,
            Consumer<Map<String, Object>> consumer) {
        awaitTurn(projectId, branchName, null, consumer);
    }

    private void emitWait(
            Consumer<Map<String, Object>> consumer,
            String branchName,
            long waitedNanos) {
        if (consumer == null) {
            return;
        }
        try {
            consumer.accept(Map.of(
                    "type", "pr_analysis_wait",
                    "state", "waiting_for_pr_analysis",
                    "message", "Waiting for PR analyses targeting " + branchName + " to finish",
                    "branchName", branchName,
                    "waitedSeconds", TimeUnit.NANOSECONDS.toSeconds(waitedNanos)));
        } catch (Exception e) {
            log.debug("Could not emit PR barrier status: {}", e.getMessage());
        }
    }

    private boolean pause() {
        if (pollIntervalMillis <= 0) {
            return true;
        }
        LockSupport.parkNanos(TimeUnit.MILLISECONDS.toNanos(pollIntervalMillis));
        return !Thread.currentThread().isInterrupted();
    }

    public enum GateResult {
        READY,
        SUPERSEDED
    }
}
