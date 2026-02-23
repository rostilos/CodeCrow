package org.rostilos.codecrow.pipelineagent.generic.service;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Service to deduplicate webhook events based on commit hash.
 * 
 * When a PR is merged in Bitbucket, it sends both:
 * - pullrequest:fulfilled (with merge commit)
 * - repo:push (with same merge commit)
 * 
 * Both events would trigger analysis for the same commit, causing duplicate processing.
 * This service tracks recently analyzed commits and skips duplicates within a time window.
 */
@Service
public class WebhookDeduplicationService {
    
    private static final Logger log = LoggerFactory.getLogger(WebhookDeduplicationService.class);
    
    /**
     * Time window in seconds to consider events as duplicates.
     */
    private static final long DEDUP_WINDOW_SECONDS = 30;
    
    /**
     * Counter to throttle cleanup operations (run every N requests instead of every request).
     */
    private final java.util.concurrent.atomic.AtomicInteger cleanupCounter = new java.util.concurrent.atomic.AtomicInteger(0);
    private static final int CLEANUP_INTERVAL = 10;
    
    /**
     * Cache of recently analyzed commits.
     * Key: "projectId:commitHash"
     * Value: timestamp when the analysis was triggered
     */
    private final Map<String, Instant> recentCommitAnalyses = new ConcurrentHashMap<>();
    
    /**
     * Check if a branch analysis job should be suppressed because a competing event for the
     * same project+branch was already accepted within the dedup window.
     * <p>
     * Used at the controller level (before job creation) to prevent duplicate jobs when
     * Bitbucket fires both {@code pullrequest:fulfilled} and {@code repo:push} for the same
     * merge. The two events carry <em>different</em> commit hashes (PR source commit vs merge
     * commit), so commit-level dedup does not catch them — branch-level dedup does.
     *
     * @param projectId  The project ID
     * @param branchName The branch being analyzed
     * @param eventType  The webhook event type (for logging)
     * @return true if another event for this branch was already accepted and the job should be
     *         suppressed; false if processing should continue
     */
    public boolean isDuplicateBranchEvent(Long projectId, String branchName, String eventType) {
        if (branchName == null || branchName.isBlank()) {
            return false;
        }

        String key = "branch:" + projectId + ":" + branchName;
        Instant now = Instant.now();

        Instant existingTimestamp = recentCommitAnalyses.putIfAbsent(key, now);

        if (existingTimestamp != null) {
            long secondsSince = now.getEpochSecond() - existingTimestamp.getEpochSecond();
            if (secondsSince < DEDUP_WINDOW_SECONDS) {
                log.info("Suppressing duplicate branch event before job creation: project={}, branch={}, event={}, "
                        + "previous event {}s ago (within {}s window)",
                        projectId, branchName, eventType, secondsSince, DEDUP_WINDOW_SECONDS);
                return true;
            }
            recentCommitAnalyses.put(key, now);
        }

        if (cleanupCounter.incrementAndGet() % CLEANUP_INTERVAL == 0) {
            cleanupOldEntries(now);
        }

        return false;
    }

    /**
     * Cache of PR numbers associated with merge commits.
     * Populated by pullrequest:fulfilled events so that a subsequent repo:push
     * for the same commit can pick up the PR number (cross-event enrichment).
     * Key: "projectId:commitHash"
     * Value: PR number
     */
    private final Map<String, Long> mergePrNumbers = new ConcurrentHashMap<>();
    
    /**
     * Check if a commit analysis should be skipped as a duplicate.
     * If not a duplicate, records this commit for future deduplication.
     * 
     * @param projectId The project ID
     * @param commitHash The commit being analyzed
     * @param eventType The webhook event type (for logging)
     * @return true if this is a duplicate and should be skipped, false if it should proceed
     */
    public boolean isDuplicateCommitAnalysis(Long projectId, String commitHash, String eventType) {
        if (commitHash == null || commitHash.isBlank()) {
            return false;
        }
        
        String key = projectId + ":" + commitHash;
        Instant now = Instant.now();
        
        // Use putIfAbsent for atomic check-and-set to prevent race conditions
        // where multiple threads could pass the duplicate check simultaneously
        Instant existingTimestamp = recentCommitAnalyses.putIfAbsent(key, now);
        
        if (existingTimestamp != null) {
            long secondsSinceLastAnalysis = now.getEpochSecond() - existingTimestamp.getEpochSecond();
            
            if (secondsSinceLastAnalysis < DEDUP_WINDOW_SECONDS) {
                log.info("Skipping duplicate commit analysis: project={}, commit={}, event={}, " +
                        "lastAnalysis={}s ago (within {}s window)", 
                        projectId, commitHash, eventType, secondsSinceLastAnalysis, DEDUP_WINDOW_SECONDS);
                return true;
            }
            
            // Entry exists but is stale, update it
            recentCommitAnalyses.put(key, now);
        }
        
        // Cleanup old entries periodically (not on every request)
        if (cleanupCounter.incrementAndGet() % CLEANUP_INTERVAL == 0) {
            cleanupOldEntries(now);
        }
        
        return false;
    }
    
    /**
     * Remove entries older than the dedup window to prevent memory growth.
     */
    private void cleanupOldEntries(Instant now) {
        recentCommitAnalyses.entrySet().removeIf(entry -> {
            long age = now.getEpochSecond() - entry.getValue().getEpochSecond();
            return age > DEDUP_WINDOW_SECONDS * 2;
        });
        // Also clean up stale PR number entries (use a wider window since
        // repo:push may arrive slightly later than the dedup window)
        mergePrNumbers.entrySet().removeIf(entry -> !recentCommitAnalyses.containsKey(entry.getKey()));
    }
    
    /**
     * Record a PR number associated with a merge commit.
     * Called when pullrequest:fulfilled arrives — stores the PR number so that
     * a subsequent repo:push for the same commit can retrieve it.
     *
     * @param projectId  The project ID
     * @param commitHash The merge commit hash
     * @param prNumber   The PR number from the fulfilled event
     */
    public void recordMergePrNumber(Long projectId, String commitHash, Long prNumber) {
        if (commitHash == null || commitHash.isBlank() || prNumber == null) {
            return;
        }
        String key = projectId + ":" + commitHash;
        mergePrNumbers.put(key, prNumber);
        log.debug("Recorded merge PR number: project={}, commit={}, PR={}", projectId, commitHash, prNumber);
    }
    
    /**
     * Retrieve a previously recorded PR number for a commit.
     * Used by repo:push handlers to enrich the request with PR context
     * when pullrequest:fulfilled was already received.
     *
     * @param projectId  The project ID
     * @param commitHash The commit hash to look up
     * @return The PR number if found, null otherwise
     */
    public Long getRecordedMergePrNumber(Long projectId, String commitHash) {
        if (commitHash == null || commitHash.isBlank()) {
            return null;
        }
        return mergePrNumbers.get(projectId + ":" + commitHash);
    }
}
