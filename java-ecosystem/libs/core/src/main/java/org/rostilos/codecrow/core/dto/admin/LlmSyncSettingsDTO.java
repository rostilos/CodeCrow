package org.rostilos.codecrow.core.dto.admin;

/**
 * LLM sync provider API keys for model listing/sync.
 * Used by the LlmModelFetcher implementations.
 */
public record LlmSyncSettingsDTO(
        String openrouterApiKey,
        String openaiApiKey,
        String anthropicApiKey,
        String googleApiKey
) {
    public static final String KEY_OPENROUTER_API_KEY = "openrouter-api-key";
    public static final String KEY_OPENAI_API_KEY = "openai-api-key";
    public static final String KEY_ANTHROPIC_API_KEY = "anthropic-api-key";
    public static final String KEY_GOOGLE_API_KEY = "google-api-key";
}
