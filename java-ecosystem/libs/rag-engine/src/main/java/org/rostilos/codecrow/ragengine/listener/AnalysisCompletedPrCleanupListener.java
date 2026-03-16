package org.rostilos.codecrow.ragengine.listener;

import org.rostilos.codecrow.events.analysis.AnalysisCompletedEvent;
import org.rostilos.codecrow.ragengine.client.RagPipelineClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.event.EventListener;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Component;

/**
 * DISABLED: PR-indexed RAG data now persists after analysis completes.
 *
 * PR data is cleaned up only when:
 * <ul>
 *   <li>The PR is closed/merged (via webhook handler {@code cleanupPrRagData()})</li>
 *   <li>A re-analysis runs (the PR index endpoint deletes old data before re-indexing)</li>
 * </ul>
 *
 * Keeping the class for reference but removing {@link Component} annotation
 * so Spring does not register it as a bean.
 *
 * Previously, this listener deleted PR data right after analysis completed,
 * which made the hybrid PR query mode useless for subsequent requests.
 */
// @Component  — intentionally disabled; see Javadoc above
public class AnalysisCompletedPrCleanupListener {

    private static final Logger log = LoggerFactory.getLogger(AnalysisCompletedPrCleanupListener.class);

    private static final int MAX_RETRIES = 3;
    private static final long INITIAL_BACKOFF_MS = 2_000;

    private final RagPipelineClient ragPipelineClient;

    public AnalysisCompletedPrCleanupListener(RagPipelineClient ragPipelineClient) {
        this.ragPipelineClient = ragPipelineClient;
    }

    @Async
    @EventListener
    public void onAnalysisCompleted(AnalysisCompletedEvent event) {
        Long prNumber = event.getPrNumber();
        String workspace = event.getProjectWorkspace();
        String namespace = event.getProjectNamespace();

        // Only clean up if this was a PR analysis (prNumber is set)
        if (prNumber == null || workspace == null || namespace == null) {
            return;
        }

        log.info("AnalysisCompletedEvent received: cleaning up PR #{} RAG data for {}/{}",
                prNumber, workspace, namespace);

        boolean success = false;
        for (int attempt = 1; attempt <= MAX_RETRIES; attempt++) {
            try {
                success = ragPipelineClient.deletePrFiles(workspace, namespace, prNumber.intValue());
                if (success) {
                    log.info("PR #{} RAG cleanup succeeded on attempt {}", prNumber, attempt);
                    return;
                }
                log.warn("PR #{} RAG cleanup returned false on attempt {}/{}", prNumber, attempt, MAX_RETRIES);
            } catch (Exception e) {
                log.warn("PR #{} RAG cleanup failed on attempt {}/{}: {}",
                        prNumber, attempt, MAX_RETRIES, e.getMessage());
            }

            if (attempt < MAX_RETRIES) {
                try {
                    long backoff = INITIAL_BACKOFF_MS * (1L << (attempt - 1)); // 2s, 4s
                    Thread.sleep(backoff);
                } catch (InterruptedException ie) {
                    Thread.currentThread().interrupt();
                    log.warn("PR #{} RAG cleanup interrupted, giving up", prNumber);
                    return;
                }
            }
        }

        log.error("PR #{} RAG cleanup exhausted all {} retries for {}/{}",
                prNumber, MAX_RETRIES, workspace, namespace);
    }
}
