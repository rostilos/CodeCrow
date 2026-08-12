package org.rostilos.codecrow.pipelineagent.generic.service;

import org.rostilos.codecrow.core.model.branch.BranchHealthStatus;
import org.rostilos.codecrow.core.model.codeanalysis.AnalysisType;
import org.rostilos.codecrow.core.persistence.repository.branch.BranchRepository;
import org.rostilos.codecrow.analysisengine.dto.request.processor.BranchProcessRequest;
import org.rostilos.codecrow.analysisengine.processor.analysis.BranchAnalysisProcessor;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

import java.time.OffsetDateTime;
import java.time.temporal.ChronoUnit;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;

/**
 * Scheduled service that retries analysis for STALE branches.
 * 
 * A branch is marked STALE when analysis fails (e.g., VCS API timeout, AI provider error).
 * Without this scheduler, STALE branches remain stuck until a new push event arrives.
 * This service periodically picks up STALE branches and retries analysis with exponential
 * backoff based on the number of consecutive failures.
 * 
 * Backoff strategy: consecutiveFailures * BASE_BACKOFF_MINUTES
 *   - 1 failure  → retry after 10 min
 *   - 2 failures → retry after 20 min
 *   - 3 failures → retry after 30 min
 *   - ...
 *   - MAX_CONSECUTIVE_FAILURES (10) → left STALE until the next push
 * 
 * Batch size is limited to prevent overwhelming the system.
 */
@Service
public class BranchHealthScheduler {
    
    private static final Logger log = LoggerFactory.getLogger(BranchHealthScheduler.class);
    
    /** Base backoff in minutes per consecutive failure. */
    private static final long BASE_BACKOFF_MINUTES = 10;
    
    /** Maximum consecutive failures before scheduled retries stop. */
    private static final int MAX_CONSECUTIVE_FAILURES = 10;
    
    /** Maximum branches to retry per scheduler run to avoid overloading. */
    private static final int BATCH_SIZE = 5;

    /** Scheduled retries have no request stream, but downstream stages may emit. */
    private static final Consumer<Map<String, Object>> NO_OP_OBSERVER = ignored -> { };
    
    private final BranchRepository branchRepository;
    private final BranchAnalysisProcessor branchAnalysisProcessor;
    
    public BranchHealthScheduler(
            BranchRepository branchRepository,
            BranchAnalysisProcessor branchAnalysisProcessor
    ) {
        this.branchRepository = branchRepository;
        this.branchAnalysisProcessor = branchAnalysisProcessor;
    }
    
    /**
     * Periodically retries analysis for branches in STALE status.
     * Runs every 10 minutes. Each branch is subject to individual backoff
     * based on its consecutiveFailures count.
     */
    @Scheduled(fixedDelayString = "${branch.health.retry.interval.ms:600000}")
    public void retryStaleBranches() {
        // The repository materializes scalar data in its own short read
        // transaction. Never hold a scheduler transaction/connection while the
        // processor performs VCS, RAG, or AI calls.
        List<BranchRepository.StaleRetryCandidate> staleBranches =
                branchRepository.findStaleRetryCandidates(BranchHealthStatus.STALE);
        
        if (staleBranches.isEmpty()) {
            return;
        }
        
        log.info("Found {} STALE branches for retry evaluation", staleBranches.size());
        
        OffsetDateTime now = OffsetDateTime.now();
        int retried = 0;
        
        for (BranchRepository.StaleRetryCandidate branch : staleBranches) {
            if (retried >= BATCH_SIZE) {
                log.info("Batch size limit reached ({}), remaining branches will be retried next cycle", BATCH_SIZE);
                break;
            }
            
            // Stop retrying after too many consecutive failures.
            // The branch stays STALE but backoff is so large it effectively won't retry
            // until a new push event resets the failure counter.
            if (branch.getConsecutiveFailures() >= MAX_CONSECUTIVE_FAILURES) {
                log.debug("Branch {} (project={}, branch='{}') remains above max retries ({}) — "
                                + "waiting for the next push event",
                        branch.getBranchId(), branch.getProjectId(), branch.getBranchName(),
                        branch.getConsecutiveFailures());
                continue;
            }
            
            // Exponential backoff: wait consecutiveFailures * BASE_BACKOFF_MINUTES
            if (!isEligibleForRetry(branch, now)) {
                continue;
            }
            
            // Skip if branch analysis is disabled for this project
            if (!branch.getBranchAnalysisEnabled()) {
                log.debug("Skipping STALE branch {} — branch analysis disabled for project {}",
                        branch.getBranchName(), branch.getProjectId());
                continue;
            }
            
            // Skip if no commit hash to retry with
            if (branch.getCommitHash() == null || branch.getCommitHash().isBlank()) {
                log.debug("Skipping STALE branch {} — no commit hash available", branch.getBranchId());
                continue;
            }
            
            retryBranchAnalysis(branch);
            retried++;
        }
        
        if (retried > 0) {
            log.info("Retried {} STALE branches", retried);
        }
    }
    
    /**
     * Check if a branch is eligible for retry based on backoff.
     * A branch must wait (consecutiveFailures * BASE_BACKOFF_MINUTES) since its last health check.
     */
    private boolean isEligibleForRetry(
            BranchRepository.StaleRetryCandidate branch,
            OffsetDateTime now) {
        OffsetDateTime lastCheck = branch.getLastHealthCheckAt();
        if (lastCheck == null) {
            // Never checked — eligible
            return true;
        }
        
        long requiredWaitMinutes = (long) branch.getConsecutiveFailures() * BASE_BACKOFF_MINUTES;
        long minutesSinceLastCheck = ChronoUnit.MINUTES.between(lastCheck, now);
        
        if (minutesSinceLastCheck < requiredWaitMinutes) {
            log.debug("Branch {} backoff not elapsed: waited {}m of required {}m (failures={})",
                    branch.getBranchId(), minutesSinceLastCheck, requiredWaitMinutes,
                    branch.getConsecutiveFailures());
            return false;
        }
        
        return true;
    }
    
    /**
     * Retry branch analysis for a single STALE branch.
     */
    private void retryBranchAnalysis(BranchRepository.StaleRetryCandidate branch) {
        try {
            log.info("Retrying STALE branch: id={}, project={}, branch='{}', commit={}, failures={}",
                    branch.getBranchId(), branch.getProjectId(), branch.getBranchName(),
                    branch.getCommitHash(), branch.getConsecutiveFailures());
            
            BranchProcessRequest request = new BranchProcessRequest();
            request.projectId = branch.getProjectId();
            request.targetBranchName = branch.getBranchName();
            request.commitHash = branch.getCommitHash();
            request.analysisType = AnalysisType.BRANCH_ANALYSIS;
            // No sourcePrNumber — this is a scheduled retry, not triggered by a PR merge
            
            branchAnalysisProcessor.process(request, NO_OP_OBSERVER);
            
            log.info("STALE branch retry completed successfully: branch={}, project={}",
                    branch.getBranchName(), branch.getProjectId());
            
        } catch (Exception e) {
            // The processor itself handles markStale() on failure,
            // so we just log the error and move on to the next branch.
            log.error("STALE branch retry failed: branch={}, project={}, error={}",
                    branch.getBranchName(), branch.getProjectId(), e.getMessage());
        }
    }
}
