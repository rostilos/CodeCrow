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

@Component
public class OpenRouterModelFetcher implements LlmModelFetcher {

    private static final Logger logger = LoggerFactory.getLogger(OpenRouterModelFetcher.class);
    private static final String OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models";

    private final RestTemplate restTemplate;
    private final SiteSettingsProvider siteSettingsProvider;

    public OpenRouterModelFetcher(RestTemplate restTemplate, SiteSettingsProvider siteSettingsProvider) {
        this.restTemplate = restTemplate;
        this.siteSettingsProvider = siteSettingsProvider;
    }

    @Override
    public AIProviderKey getProviderKey() {
        return AIProviderKey.OPENROUTER;
    }

    @Override
    public boolean isConfigured() {
        // OpenRouter allows listing models without API key
        return true;
    }

    @Override
    public List<LlmModel> fetchModels(int minContextWindow) {
        List<LlmModel> models = new ArrayList<>();

        try {
            HttpHeaders headers = new HttpHeaders();
            headers.set("Accept", "application/json");
            String apiKey = siteSettingsProvider.getLlmSyncSettings().openrouterApiKey();
            if (apiKey != null && !apiKey.isBlank()) {
                headers.set("Authorization", "Bearer " + apiKey);
            }

            HttpEntity<String> entity = new HttpEntity<>(headers);
            ResponseEntity<OpenRouterModelsResponse> response = restTemplate.exchange(
                    OPENROUTER_MODELS_URL,
                    HttpMethod.GET,
                    entity,
                    OpenRouterModelsResponse.class
            );

            if (response.getBody() != null && response.getBody().data != null) {
                OffsetDateTime now = OffsetDateTime.now();

                for (OpenRouterModel model : response.getBody().data) {
                    // Filter by context window
                    int contextLength = model.contextLength != null ? model.contextLength : 0;
                    if (contextLength < minContextWindow) {
                        continue;
                    }

                    // Check for tool support - OpenRouter includes this in supported_parameters
                    boolean supportsTools = hasToolSupport(model);
                    if (!supportsTools) {
                        continue;
                    }

                    LlmModel llmModel = new LlmModel();
                    llmModel.setProviderKey(AIProviderKey.OPENROUTER);
                    llmModel.setModelId(model.id);
                    llmModel.setDisplayName(model.name != null ? model.name : model.id);
                    llmModel.setContextWindow(contextLength);
                    llmModel.setSupportsTools(true);
                    llmModel.setLastSyncedAt(now);

                    // Set pricing if available (convert per-token to per-million)
                    if (model.pricing != null) {
                        String inputPrice = convertToPerMillion(model.pricing.prompt);
                        String outputPrice = convertToPerMillion(model.pricing.completion);
                        llmModel.setInputPricePerMillion(inputPrice);
                        llmModel.setOutputPricePerMillion(outputPrice);
                        
                        logger.debug("Model {} pricing: input={} (raw: {}), output={} (raw: {})", 
                                model.id, inputPrice, model.pricing.prompt, outputPrice, model.pricing.completion);
                    } else {
                        logger.debug("Model {} has no pricing info", model.id);
                    }

                    models.add(llmModel);
                }
            }

            logger.info("Fetched {} models from OpenRouter (filtered from total)", models.size());

        } catch (Exception e) {
            logger.error("Failed to fetch models from OpenRouter: {}", e.getMessage(), e);
        }

        return models;
    }

    private boolean hasToolSupport(OpenRouterModel model) {
        if (model.supportedParameters != null) {
            return model.supportedParameters.contains("tools") || 
                   model.supportedParameters.contains("tool_choice") ||
                   model.supportedParameters.contains("functions");
        }
        // Default to checking architecture or known tool-capable models
        if (model.architecture != null && model.architecture.modality != null) {
            String modality = model.architecture.modality.toLowerCase();
            // Models with text->text that are from major providers usually support tools
            return modality.contains("text");
        }
        return false;
    }

    /**
     * Convert price per token to price per million tokens.
     * OpenRouter returns prices as strings like "0.000001" (per token).
     */
    private String convertToPerMillion(String pricePerToken) {
        if (pricePerToken == null || pricePerToken.isBlank() || "0".equals(pricePerToken)) {
            return "0";
        }
        try {
            java.math.BigDecimal perToken = new java.math.BigDecimal(pricePerToken);
            java.math.BigDecimal perMillion = perToken.multiply(java.math.BigDecimal.valueOf(1_000_000));
            // Format to reasonable precision
            return perMillion.stripTrailingZeros().toPlainString();
        } catch (NumberFormatException e) {
            logger.warn("Failed to parse price: {}", pricePerToken);
            return null;
        }
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    static class OpenRouterModelsResponse {
        public List<OpenRouterModel> data;
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    static class OpenRouterModel {
        public String id;
        public String name;
        
        @JsonProperty("context_length")
        public Integer contextLength;
        
        @JsonProperty("supported_parameters")
        public List<String> supportedParameters;

        public OpenRouterArchitecture architecture;
        
        public OpenRouterPricing pricing;
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    static class OpenRouterArchitecture {
        public String modality;
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    static class OpenRouterPricing {
        public String prompt;      // Cost per input token
        public String completion;  // Cost per output token
    }
}
