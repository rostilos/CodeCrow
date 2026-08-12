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
    private static final long WAIT_STATUS_INTERVAL_NANOS = TimeUnit.SECONDS.toNanos(60);

    private final JobRepository jobRepository;

    @Value("${analysis.branch.pr-wait.timeout.minutes:60}")
    private long waitTimeoutMinutes;

    @Value("${analysis.branch.pr-wait.poll-interval.millis:1000}")
    private long pollIntervalMillis;

    public BranchAnalysisGateService(JobRepository jobRepository) {
        this.jobRepository = jobRepository;
    }

    /**
     * Apply the dependency barrier for a durably accepted analysis job.
     * Jobs only wait for older work on the same target branch, which preserves
     * repository/RAG ordering without serializing independent PR reviews.
     */
    public GateResult awaitDependencies(
            Long projectId,
            Job job,
            Consumer<Map<String, Object>> consumer) {
        if (job == null || job.getId() == null) {
            return GateResult.READY;
        }
        if (job.getJobType() == JobType.BRANCH_ANALYSIS) {
            return awaitTurn(
                    projectId,
                    job.getBranchName(),
                    job.getId(),
                    job.getPrNumber(),
                    consumer);
        }
        if (job.getJobType() == JobType.PR_ANALYSIS) {
            awaitOlderBranchAnalyses(
                    projectId,
                    job.getBranchName(),
                    job.getId(),
                    consumer);
        }
        return GateResult.READY;
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
        long nextStatusAt = startedAt;

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

            long now = System.nanoTime();
            if (now >= nextStatusAt) {
                emitWait(consumer, branchName, sourcePrNumber, waitedNanos);
                nextStatusAt = now + WAIT_STATUS_INTERVAL_NANOS;
            }
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

    public void awaitOlderBranchAnalyses(
            Long projectId,
            String branchName,
            Long currentPrJobId,
            Consumer<Map<String, Object>> consumer) {
        if (branchName == null || branchName.isBlank() || currentPrJobId == null) {
            return;
        }

        long timeoutNanos = TimeUnit.MINUTES.toNanos(Math.max(1, waitTimeoutMinutes));
        long startedAt = System.nanoTime();
        long nextStatusAt = startedAt;
        while (jobRepository.existsActiveBranchAnalysisJobBefore(
                projectId, branchName, currentPrJobId)) {
            long waitedNanos = System.nanoTime() - startedAt;
            if (waitedNanos >= timeoutNanos) {
                log.warn("Timed out waiting for target branch update: project={}, branch={}, prJob={}, waited={}m",
                        projectId, branchName, currentPrJobId,
                        TimeUnit.NANOSECONDS.toMinutes(waitedNanos));
                throw new AnalysisLockedException(
                        AnalysisLockType.BRANCH_ANALYSIS.name(), branchName, projectId);
            }

            long now = System.nanoTime();
            if (now >= nextStatusAt) {
                emitBranchWait(consumer, branchName, waitedNanos);
                nextStatusAt = now + WAIT_STATUS_INTERVAL_NANOS;
            }
            if (!pause()) {
                throw new AnalysisLockedException(
                        AnalysisLockType.BRANCH_ANALYSIS.name(), branchName, projectId);
            }
        }
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
                    ? "Branch analysis for " + branchName
                            + " is waiting for earlier PR analyses targeting that branch"
                    : "Post-merge branch analysis for " + branchName
                            + " is waiting for PR #" + sourcePrNumber
                            + " analysis to finish");
            event.put("waitingJobType", JobType.BRANCH_ANALYSIS.name());
            event.put("blockingJobType", JobType.PR_ANALYSIS.name());
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

    private void emitBranchWait(
            Consumer<Map<String, Object>> consumer,
            String branchName,
            long waitedNanos) {
        if (consumer == null) {
            return;
        }
        try {
            consumer.accept(Map.of(
                    "type", "branch_analysis_wait",
                    "state", "waiting_for_target_branch",
                    "message", "PR analysis targeting " + branchName
                            + " is waiting for the earlier branch update and its RAG publication",
                    "waitingJobType", JobType.PR_ANALYSIS.name(),
                    "blockingJobType", JobType.BRANCH_ANALYSIS.name(),
                    "branchName", branchName,
                    "waitedSeconds", TimeUnit.NANOSECONDS.toSeconds(waitedNanos)));
        } catch (Exception e) {
            log.debug("Could not emit branch barrier status: {}", e.getMessage());
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
