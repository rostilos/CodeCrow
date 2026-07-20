package org.rostilos.codecrow.analysisengine.service.branch;

import org.rostilos.codecrow.analysisengine.exception.AnalysisLockedException;
import org.rostilos.codecrow.core.model.analysis.AnalysisLockType;
import org.rostilos.codecrow.core.model.job.Job;
import org.rostilos.codecrow.core.model.job.JobType;
import org.rostilos.codecrow.core.persistence.repository.job.JobRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.util.Map;
import java.util.Optional;
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
     * Wait for the relevant target-branch PR job. When the merge PR number is
     * known, only its newest analysis attempt can block reconciliation. When it
     * is unknown (for example, a provider push webhook won the merge-event
     * race), the fallback considers all PR work that existed when the branch
     * job was accepted. New PR jobs cannot extend an existing barrier.
     *
     * <p>When {@code currentBranchJobId} is supplied, an older branch job also
     * yields permanently to any viable newer branch job.</p>
     */
    public GateResult awaitTurn(
            Long projectId,
            String branchName,
            Long currentBranchJobId,
            Long sourcePrNumber,
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

            if (!hasBlockingPrAnalysis(
                    projectId, branchName, currentBranchJobId, sourcePrNumber)) {
                return GateResult.READY;
            }

            long waitedNanos = System.nanoTime() - startedAt;
            if (waitedNanos >= timeoutNanos) {
                log.warn("Timed out waiting for PR analysis: project={}, branch={}, pr={}, waited={}m",
                        projectId, branchName, sourcePrNumber,
                        TimeUnit.NANOSECONDS.toMinutes(waitedNanos));
                throw new AnalysisLockedException(
                        AnalysisLockType.PR_ANALYSIS.name(), branchName, projectId);
            }

            emitWait(consumer, branchName, sourcePrNumber, waitedNanos);
            if (!pause()) {
                throw new AnalysisLockedException(
                        AnalysisLockType.PR_ANALYSIS.name(), branchName, projectId);
            }
        }
    }

    /**
     * Backward-compatible broad barrier for callers without PR context.
     */
    public GateResult awaitTurn(
            Long projectId,
            String branchName,
            Long currentBranchJobId,
            Consumer<Map<String, Object>> consumer) {
        return awaitTurn(projectId, branchName, currentBranchJobId, null, consumer);
    }

    public void awaitPrAnalyses(
            Long projectId,
            String branchName,
            Consumer<Map<String, Object>> consumer) {
        awaitTurn(projectId, branchName, null, null, consumer);
    }

    public void awaitPrAnalysis(
            Long projectId,
            String branchName,
            Long sourcePrNumber,
            Consumer<Map<String, Object>> consumer) {
        awaitTurn(projectId, branchName, null, sourcePrNumber, consumer);
    }

    private boolean hasBlockingPrAnalysis(
            Long projectId,
            String branchName,
            Long currentBranchJobId,
            Long sourcePrNumber) {
        if (sourcePrNumber != null) {
            Optional<Job> latestAttempt = currentBranchJobId == null
                    ? jobRepository.findFirstByProjectIdAndBranchNameAndJobTypeAndPrNumberOrderByIdDesc(
                            projectId, branchName, JobType.PR_ANALYSIS, sourcePrNumber)
                    : jobRepository.findFirstByProjectIdAndBranchNameAndJobTypeAndPrNumberAndIdLessThanOrderByIdDesc(
                            projectId, branchName, JobType.PR_ANALYSIS,
                            sourcePrNumber, currentBranchJobId);
            return latestAttempt.map(job -> !job.isTerminal()).orElse(false);
        }

        if (currentBranchJobId != null) {
            return jobRepository.existsActivePrAnalysisJobBefore(
                    projectId, branchName, currentBranchJobId);
        }
        return jobRepository.existsActivePrAnalysisJob(projectId, branchName);
    }

    private void emitWait(
            Consumer<Map<String, Object>> consumer,
            String branchName,
            Long sourcePrNumber,
            long waitedNanos) {
        if (consumer == null) {
            return;
        }
        try {
            Map<String, Object> event = new java.util.HashMap<>();
            event.put("type", "pr_analysis_wait");
            event.put("state", "waiting_for_pr_analysis");
            event.put("message", sourcePrNumber == null
                    ? "Waiting for earlier PR analyses targeting " + branchName + " to finish"
                    : "Waiting for PR #" + sourcePrNumber + " analysis targeting "
                            + branchName + " to finish");
            event.put("branchName", branchName);
            event.put("waitedSeconds", TimeUnit.NANOSECONDS.toSeconds(waitedNanos));
            if (sourcePrNumber != null) {
                event.put("prNumber", sourcePrNumber);
            }
            consumer.accept(event);
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
