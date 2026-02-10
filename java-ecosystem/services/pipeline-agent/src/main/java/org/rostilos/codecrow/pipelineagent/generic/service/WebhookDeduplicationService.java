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
    }
}
