package org.rostilos.codecrow.webserver.ai.provider;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.model.ai.LlmModel;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

import org.rostilos.codecrow.webserver.admin.service.ISiteSettingsProvider;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;

@Component
public class OpenAIModelFetcher implements LlmModelFetcher {

    private static final Logger logger = LoggerFactory.getLogger(OpenAIModelFetcher.class);
    private static final String OPENAI_MODELS_URL = "https://api.openai.com/v1/models";

    // Known OpenAI models that support tools/function calling and their context windows
    private static final Set<String> TOOL_CAPABLE_MODELS = Set.of(
            // GPT-5.2 family (latest)
            "gpt-5.2", "gpt-5.2-pro", "gpt-5.2-codex",
            // GPT-5.1 family
            "gpt-5.1", "gpt-5.1-codex", "gpt-5.1-codex-mini", "gpt-5.1-codex-max",
            // GPT-5 family
            "gpt-5", "gpt-5-pro", "gpt-5-codex", "gpt-5-mini", "gpt-5-nano",
            // GPT-4.1 family
            "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
            // GPT-4o family
            "gpt-4o", "gpt-4o-mini", "gpt-4o-2024-08-06", "gpt-4o-mini-2024-07-18",
            // GPT-4 family
            "gpt-4-turbo", "gpt-4-turbo-preview", "gpt-4-turbo-2024-04-09",
            // Reasoning models
            "o3", "o3-pro", "o3-mini", "o4-mini", "o1", "o1-mini", "o1-preview"
    );

    // Context windows for OpenAI models (OpenAI API doesn't return this)
    private static final java.util.Map<String, Integer> MODEL_CONTEXT_WINDOWS = java.util.Map.ofEntries(
            // GPT-5.2 family
            java.util.Map.entry("gpt-5.2", 256000),
            java.util.Map.entry("gpt-5.2-pro", 256000),
            java.util.Map.entry("gpt-5.2-codex", 256000),
            // GPT-5.1 family
            java.util.Map.entry("gpt-5.1", 256000),
            java.util.Map.entry("gpt-5.1-codex", 256000),
            java.util.Map.entry("gpt-5.1-codex-mini", 200000),
            java.util.Map.entry("gpt-5.1-codex-max", 256000),
            // GPT-5 family
            java.util.Map.entry("gpt-5", 200000),
            java.util.Map.entry("gpt-5-pro", 200000),
            java.util.Map.entry("gpt-5-codex", 200000),
            java.util.Map.entry("gpt-5-mini", 200000),
            java.util.Map.entry("gpt-5-nano", 128000),
            // GPT-4.1 family
            java.util.Map.entry("gpt-4.1", 1000000),
            java.util.Map.entry("gpt-4.1-mini", 1000000),
            java.util.Map.entry("gpt-4.1-nano", 1000000),
            // GPT-4o family
            java.util.Map.entry("gpt-4o", 128000),
            java.util.Map.entry("gpt-4o-mini", 128000),
            java.util.Map.entry("gpt-4o-2024-08-06", 128000),
            java.util.Map.entry("gpt-4o-mini-2024-07-18", 128000),
            // GPT-4 family
            java.util.Map.entry("gpt-4-turbo", 128000),
            java.util.Map.entry("gpt-4-turbo-preview", 128000),
            java.util.Map.entry("gpt-4-turbo-2024-04-09", 128000),
            // Reasoning models
            java.util.Map.entry("o3", 200000),
            java.util.Map.entry("o3-pro", 200000),
            java.util.Map.entry("o3-mini", 200000),
            java.util.Map.entry("o4-mini", 200000),
            java.util.Map.entry("o1", 200000),
            java.util.Map.entry("o1-mini", 128000),
            java.util.Map.entry("o1-preview", 128000)
    );

    private final RestTemplate restTemplate;
    private final ISiteSettingsProvider siteSettingsProvider;

    public OpenAIModelFetcher(RestTemplate restTemplate, ISiteSettingsProvider siteSettingsProvider) {
        this.restTemplate = restTemplate;
        this.siteSettingsProvider = siteSettingsProvider;
    }

    @Override
    public AIProviderKey getProviderKey() {
        return AIProviderKey.OPENAI;
    }

    @Override
    public boolean isConfigured() {
        String apiKey = siteSettingsProvider.getLlmSyncSettings().openaiApiKey();
        return apiKey != null && !apiKey.isBlank();
    }

    @Override
    public List<LlmModel> fetchModels(int minContextWindow) {
        List<LlmModel> models = new ArrayList<>();

        if (!isConfigured()) {
            logger.warn("OpenAI API key not configured, using static model list");
            return getStaticModelList(minContextWindow);
        }

        try {
            HttpHeaders headers = new HttpHeaders();
            headers.set("Authorization", "Bearer " + siteSettingsProvider.getLlmSyncSettings().openaiApiKey());
            headers.set("Accept", "application/json");

            HttpEntity<String> entity = new HttpEntity<>(headers);
            ResponseEntity<OpenAIModelsResponse> response = restTemplate.exchange(
                    OPENAI_MODELS_URL,
                    HttpMethod.GET,
                    entity,
                    OpenAIModelsResponse.class
            );

            if (response.getBody() != null && response.getBody().data != null) {
                OffsetDateTime now = OffsetDateTime.now();

                for (OpenAIModel model : response.getBody().data) {
                    // Filter for chat models that support tools
                    if (!isToolCapableModel(model.id)) {
                        continue;
                    }

                    int contextWindow = getContextWindow(model.id);
                    if (contextWindow < minContextWindow) {
                        continue;
                    }

                    LlmModel llmModel = new LlmModel();
                    llmModel.setProviderKey(AIProviderKey.OPENAI);
                    llmModel.setModelId(model.id);
                    llmModel.setDisplayName(formatDisplayName(model.id));
                    llmModel.setContextWindow(contextWindow);
                    llmModel.setSupportsTools(true);
                    llmModel.setLastSyncedAt(now);

                    models.add(llmModel);
                }
            }

            logger.info("Fetched {} models from OpenAI", models.size());

        } catch (Exception e) {
            logger.error("Failed to fetch models from OpenAI: {}", e.getMessage(), e);
            // Fall back to static list
            return getStaticModelList(minContextWindow);
        }

        return models;
    }

    private List<LlmModel> getStaticModelList(int minContextWindow) {
        List<LlmModel> models = new ArrayList<>();
        OffsetDateTime now = OffsetDateTime.now();

        for (String modelId : TOOL_CAPABLE_MODELS) {
            int contextWindow = getContextWindow(modelId);
            if (contextWindow < minContextWindow) {
                continue;
            }

            LlmModel llmModel = new LlmModel();
            llmModel.setProviderKey(AIProviderKey.OPENAI);
            llmModel.setModelId(modelId);
            llmModel.setDisplayName(formatDisplayName(modelId));
            llmModel.setContextWindow(contextWindow);
            llmModel.setSupportsTools(true);
            llmModel.setLastSyncedAt(now);

            models.add(llmModel);
        }

        return models;
    }

    private boolean isToolCapableModel(String modelId) {
        // Check exact match first
        if (TOOL_CAPABLE_MODELS.contains(modelId)) {
            return true;
        }
        // Check prefix match for versioned models
        for (String toolModel : TOOL_CAPABLE_MODELS) {
            if (modelId.startsWith(toolModel)) {
                return true;
            }
        }
        return false;
    }

    private int getContextWindow(String modelId) {
        // Check exact match first
        if (MODEL_CONTEXT_WINDOWS.containsKey(modelId)) {
            return MODEL_CONTEXT_WINDOWS.get(modelId);
        }
        // Check prefix match
        for (var entry : MODEL_CONTEXT_WINDOWS.entrySet()) {
            if (modelId.startsWith(entry.getKey())) {
                return entry.getValue();
            }
        }
        // Default for newer models
        return 128000;
    }

    private String formatDisplayName(String modelId) {
        return modelId.replace("-", " ")
                .replace("gpt ", "GPT-")
                .replace("turbo", "Turbo")
                .replace("preview", "Preview")
                .replace("mini", "Mini");
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    static class OpenAIModelsResponse {
        public List<OpenAIModel> data;
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    static class OpenAIModel {
        public String id;
        
        @JsonProperty("owned_by")
        public String ownedBy;
        
        public Long created;
    }
}
