package org.rostilos.codecrow.core.dto.admin;

/**
 * Embedding / RAG pipeline configuration.
 * Polled by the Python rag-pipeline via /api/internal/settings/embedding.
 */
public record EmbeddingSettingsDTO(
        String provider,
        String ollamaBaseUrl,
        String ollamaModel,
        String openrouterApiKey,
        String openrouterModel
) {
    public static final String KEY_PROVIDER = "provider";
    public static final String KEY_OLLAMA_BASE_URL = "ollama-base-url";
    public static final String KEY_OLLAMA_MODEL = "ollama-model";
    public static final String KEY_OPENROUTER_API_KEY = "openrouter-api-key";
    public static final String KEY_OPENROUTER_MODEL = "openrouter-model";
}
