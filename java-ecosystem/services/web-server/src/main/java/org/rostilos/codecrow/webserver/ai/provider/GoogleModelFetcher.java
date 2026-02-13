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

import org.rostilos.codecrow.core.service.SiteSettingsProvider;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;

@Component
public class GoogleModelFetcher implements LlmModelFetcher {

    private static final Logger logger = LoggerFactory.getLogger(GoogleModelFetcher.class);
    private static final String GOOGLE_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models";

    // Known Google models with tool support
    private static final Set<String> TOOL_CAPABLE_PREFIXES = Set.of(
            "gemini-1.5", "gemini-2.0", "gemini-2.5", "gemini-3", "gemini-pro"
    );

    // Static fallback list for when API is not available
    private static final List<GoogleModelInfo> STATIC_MODELS = List.of(
            // Gemini 3 family (latest)
            new GoogleModelInfo("gemini-3-pro", "Gemini 3 Pro", 2000000, true),
            new GoogleModelInfo("gemini-3-flash", "Gemini 3 Flash", 1000000, true),
            // Gemini 2.5 family
            new GoogleModelInfo("gemini-2.5-flash", "Gemini 2.5 Flash", 1000000, true),
            new GoogleModelInfo("gemini-2.5-pro", "Gemini 2.5 Pro", 1000000, true),
            // Gemini 2.0 family
            new GoogleModelInfo("gemini-2.0-flash", "Gemini 2.0 Flash", 1000000, true),
            new GoogleModelInfo("gemini-2.0-flash-lite", "Gemini 2.0 Flash Lite", 1000000, true),
            // Gemini 1.5 family
            new GoogleModelInfo("gemini-1.5-flash", "Gemini 1.5 Flash", 1000000, true),
            new GoogleModelInfo("gemini-1.5-pro", "Gemini 1.5 Pro", 2000000, true)
    );

    private final RestTemplate restTemplate;
    private final SiteSettingsProvider siteSettingsProvider;

    public GoogleModelFetcher(RestTemplate restTemplate, SiteSettingsProvider siteSettingsProvider) {
        this.restTemplate = restTemplate;
        this.siteSettingsProvider = siteSettingsProvider;
    }

    @Override
    public AIProviderKey getProviderKey() {
        return AIProviderKey.GOOGLE;
    }

    @Override
    public boolean isConfigured() {
        String apiKey = siteSettingsProvider.getLlmSyncSettings().googleApiKey();
        return apiKey != null && !apiKey.isBlank();
    }

    @Override
    public List<LlmModel> fetchModels(int minContextWindow) {
        List<LlmModel> models = new ArrayList<>();

        if (!isConfigured()) {
            logger.warn("Google API key not configured, using static model list");
            return getStaticModelList(minContextWindow);
        }

        try {
            String url = GOOGLE_MODELS_URL + "?key=" + siteSettingsProvider.getLlmSyncSettings().googleApiKey();

            HttpHeaders headers = new HttpHeaders();
            headers.set("Accept", "application/json");

            HttpEntity<String> entity = new HttpEntity<>(headers);
            ResponseEntity<GoogleModelsResponse> response = restTemplate.exchange(
                    url,
                    HttpMethod.GET,
                    entity,
                    GoogleModelsResponse.class
            );

            if (response.getBody() != null && response.getBody().models != null) {
                OffsetDateTime now = OffsetDateTime.now();

                for (GoogleModel model : response.getBody().models) {
                    // Filter for Gemini chat models
                    if (!isToolCapableModel(model.name)) {
                        continue;
                    }

                    // Get context window from input token limit
                    int contextWindow = model.inputTokenLimit != null ? model.inputTokenLimit : 0;
                    if (contextWindow < minContextWindow) {
                        continue;
                    }

                    // Check for function calling support
                    boolean supportsTools = hasToolSupport(model);
                    if (!supportsTools) {
                        continue;
                    }

                    // Extract model ID from full name (models/gemini-1.5-pro -> gemini-1.5-pro)
                    String modelId = model.name.replace("models/", "");

                    LlmModel llmModel = new LlmModel();
                    llmModel.setProviderKey(AIProviderKey.GOOGLE);
                    llmModel.setModelId(modelId);
                    llmModel.setDisplayName(model.displayName != null ? model.displayName : modelId);
                    llmModel.setContextWindow(contextWindow);
                    llmModel.setSupportsTools(true);
                    llmModel.setLastSyncedAt(now);

                    models.add(llmModel);
                }
            }

            logger.info("Fetched {} models from Google AI", models.size());

        } catch (Exception e) {
            logger.error("Failed to fetch models from Google AI: {}", e.getMessage(), e);
            return getStaticModelList(minContextWindow);
        }

        return models;
    }

    private List<LlmModel> getStaticModelList(int minContextWindow) {
        List<LlmModel> models = new ArrayList<>();
        OffsetDateTime now = OffsetDateTime.now();

        for (GoogleModelInfo modelInfo : STATIC_MODELS) {
            if (modelInfo.contextWindow < minContextWindow) {
                continue;
            }

            LlmModel llmModel = new LlmModel();
            llmModel.setProviderKey(AIProviderKey.GOOGLE);
            llmModel.setModelId(modelInfo.modelId);
            llmModel.setDisplayName(modelInfo.displayName);
            llmModel.setContextWindow(modelInfo.contextWindow);
            llmModel.setSupportsTools(true);
            llmModel.setLastSyncedAt(now);

            models.add(llmModel);
        }

        return models;
    }

    private boolean isToolCapableModel(String modelName) {
        String lowerName = modelName.toLowerCase();
        for (String prefix : TOOL_CAPABLE_PREFIXES) {
            if (lowerName.contains(prefix)) {
                return true;
            }
        }
        return false;
    }

    private boolean hasToolSupport(GoogleModel model) {
        // Check supported generation methods for function calling
        if (model.supportedGenerationMethods != null) {
            return model.supportedGenerationMethods.contains("generateContent");
        }
        // Gemini models generally support tools
        return model.name != null && model.name.contains("gemini");
    }

    private record GoogleModelInfo(
            String modelId,
            String displayName,
            int contextWindow,
            boolean supportsTools
    ) {}

    @JsonIgnoreProperties(ignoreUnknown = true)
    static class GoogleModelsResponse {
        public List<GoogleModel> models;
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    static class GoogleModel {
        public String name;
        
        @JsonProperty("displayName")
        public String displayName;
        
        @JsonProperty("inputTokenLimit")
        public Integer inputTokenLimit;
        
        @JsonProperty("outputTokenLimit")
        public Integer outputTokenLimit;
        
        @JsonProperty("supportedGenerationMethods")
        public List<String> supportedGenerationMethods;
    }
}
