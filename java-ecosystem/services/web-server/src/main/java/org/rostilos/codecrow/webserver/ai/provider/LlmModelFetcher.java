package org.rostilos.codecrow.webserver.ai.provider;

import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.ai.LlmModel;

import java.util.List;

/**
 * Interface for fetching LLM models from AI providers.
 * Implementations should filter models based on:
 * - Context window >= minimum threshold (default 50k)
 * - Supports tool/function calling
 */
public interface LlmModelFetcher {

    /**
     * Get the provider key this fetcher handles.
     */
    AIProviderKey getProviderKey();

    /**
     * Fetch models from the provider API.
     *
     * @param minContextWindow Minimum context window size in tokens
     * @return List of models matching the criteria
     */
    List<LlmModel> fetchModels(int minContextWindow);

    /**
     * Check if this fetcher is configured and can make API calls.
     * Some providers require API keys to list models.
     */
    boolean isConfigured();
}
