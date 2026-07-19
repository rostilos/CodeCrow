package org.rostilos.codecrow.webserver.ai.scheduler;

import org.rostilos.codecrow.webserver.ai.service.LlmModelSyncService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

@Service
public class LlmModelSyncScheduler {

    private static final Logger logger = LoggerFactory.getLogger(LlmModelSyncScheduler.class);

    private final LlmModelSyncService syncService;

    public LlmModelSyncScheduler(LlmModelSyncService syncService) {
        this.syncService = syncService;
    }

    /**
     * Run model sync daily at midnight (00:00).
     */
    @Scheduled(cron = "${llm.sync.cron:0 0 0 * * *}")
    public void scheduledSync() {
        logger.info("Starting scheduled LLM model sync");
        try {
            var result = syncService.syncAllProviders();
            logger.info("Scheduled sync completed: {} models synced, {} cleaned up", 
                    result.totalSynced(), result.cleanedUp());
            
            if (result.hasErrors()) {
                logger.warn("Sync completed with errors: {}", result.errors());
            }
        } catch (Exception e) {
            logger.error("Scheduled LLM model sync failed", e);
        }
    }

    /**
     * Run initial sync on application startup.
     */
    @EventListener(ApplicationReadyEvent.class)
    public void onApplicationReady() {
        logger.info("Running initial LLM model sync on startup");
        try {
            var result = syncService.syncAllProviders();
            logger.info("Initial sync completed: {} models synced", result.totalSynced());
        } catch (Exception e) {
            logger.error("Initial LLM model sync failed", e);
        }
    }
}
