package org.rostilos.codecrow.webserver.ai.service;

import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.ai.LlmModel;
import org.rostilos.codecrow.core.persistence.repository.ai.LlmModelRepository;
import org.rostilos.codecrow.webserver.ai.provider.LlmModelFetcher;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

@Service
public class LlmModelSyncService {

    private static final Logger logger = LoggerFactory.getLogger(LlmModelSyncService.class);

    private final List<LlmModelFetcher> fetchers;
    private final LlmModelRepository llmModelRepository;

    @Value("${llm.sync.min-context-window:50000}")
    private int minContextWindow;

    public LlmModelSyncService(
            List<LlmModelFetcher> fetchers,
            LlmModelRepository llmModelRepository
    ) {
        this.fetchers = fetchers;
        this.llmModelRepository = llmModelRepository;
    }

    /**
     * Sync models from all configured providers.
     * This performs an upsert - new models are added, existing models are updated.
     */
    @Transactional
    public SyncResult syncAllProviders() {
        logger.info("Starting LLM model sync for all providers");
        
        Map<AIProviderKey, Integer> syncedCounts = new HashMap<>();
        Map<AIProviderKey, String> errors = new HashMap<>();
        OffsetDateTime syncStartTime = OffsetDateTime.now();

        for (LlmModelFetcher fetcher : fetchers) {
            AIProviderKey provider = fetcher.getProviderKey();
            
            try {
                if (!fetcher.isConfigured()) {
                    logger.warn("Fetcher for {} is not configured, skipping", provider);
                    continue;
                }

                logger.info("Syncing models from {}", provider);
                List<LlmModel> models = fetcher.fetchModels(minContextWindow);
                
                int upsertedCount = upsertModels(models);
                syncedCounts.put(provider, upsertedCount);
                
                logger.info("Synced {} models from {}", upsertedCount, provider);

            } catch (Exception e) {
                logger.error("Failed to sync models from {}: {}", provider, e.getMessage(), e);
                errors.put(provider, e.getMessage());
            }
        }

        // Clean up stale models (not seen in current sync)
        int cleanedUp = cleanupStaleModels(syncStartTime);
        
        logger.info("LLM model sync completed. Synced: {}, Cleaned up: {}, Errors: {}", 
                syncedCounts, cleanedUp, errors.size());

        return new SyncResult(syncedCounts, errors, cleanedUp);
    }

    /**
     * Sync models from a specific provider.
     */
    @Transactional
    public int syncProvider(AIProviderKey providerKey) {
        logger.info("Syncing models from {}", providerKey);

        LlmModelFetcher fetcher = fetchers.stream()
                .filter(f -> f.getProviderKey() == providerKey)
                .findFirst()
                .orElseThrow(() -> new IllegalArgumentException("No fetcher for provider: " + providerKey));

        if (!fetcher.isConfigured()) {
            throw new IllegalStateException("Fetcher for " + providerKey + " is not configured");
        }

        List<LlmModel> models = fetcher.fetchModels(minContextWindow);
        return upsertModels(models);
    }

    private int upsertModels(List<LlmModel> models) {
        int count = 0;

        for (LlmModel model : models) {
            var existing = llmModelRepository.findByProviderKeyAndModelId(
                    model.getProviderKey(), 
                    model.getModelId()
            );

            if (existing.isPresent()) {
                // Update existing
                LlmModel existingModel = existing.get();
                existingModel.setDisplayName(model.getDisplayName());
                existingModel.setContextWindow(model.getContextWindow());
                existingModel.setSupportsTools(model.isSupportsTools());
                existingModel.setInputPricePerMillion(model.getInputPricePerMillion());
                existingModel.setOutputPricePerMillion(model.getOutputPricePerMillion());
                existingModel.setLastSyncedAt(model.getLastSyncedAt());
                llmModelRepository.save(existingModel);
            } else {
                // Insert new
                llmModelRepository.save(model);
            }
            count++;
        }

        return count;
    }

    private int cleanupStaleModels(OffsetDateTime threshold) {
        int totalCleaned = 0;
        
        for (AIProviderKey provider : AIProviderKey.values()) {
            // Only clean up if we have any models for this provider synced after threshold
            // This prevents deleting all models if a provider sync completely fails
            if (llmModelRepository.existsByProviderKey(provider)) {
                int cleaned = llmModelRepository.deleteStaleModels(provider, threshold);
                totalCleaned += cleaned;
                if (cleaned > 0) {
                    logger.info("Cleaned up {} stale models from {}", cleaned, provider);
                }
            }
        }
        
        return totalCleaned;
    }

    /**
     * Get the current minimum context window setting.
     */
    public int getMinContextWindow() {
        return minContextWindow;
    }

    /**
     * Result of a sync operation.
     */
    public record SyncResult(
            Map<AIProviderKey, Integer> syncedCounts,
            Map<AIProviderKey, String> errors,
            int cleanedUp
    ) {
        public int totalSynced() {
            return syncedCounts.values().stream().mapToInt(Integer::intValue).sum();
        }

        public boolean hasErrors() {
            return !errors.isEmpty();
        }
    }
}
