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
public class AnthropicModelFetcher implements LlmModelFetcher {

    private static final Logger logger = LoggerFactory.getLogger(AnthropicModelFetcher.class);

    // Anthropic models with tool support and their context windows
    // Anthropic doesn't have a public models list API, so we maintain a static list
    private static final List<AnthropicModelInfo> ANTHROPIC_MODELS = List.of(
            // Claude 4.5 family (latest)
            new AnthropicModelInfo("claude-opus-4-5-20260115", "Claude Opus 4.5", 200000, true),
            new AnthropicModelInfo("claude-sonnet-4-5-20260110", "Claude Sonnet 4.5", 200000, true),
            
            // Claude 4 family
            new AnthropicModelInfo("claude-sonnet-4-20250514", "Claude Sonnet 4", 200000, true),
            new AnthropicModelInfo("claude-opus-4-20250514", "Claude Opus 4", 200000, true),
            
            // Claude 3.5 family
            new AnthropicModelInfo("claude-3-5-sonnet-20241022", "Claude 3.5 Sonnet (Oct 2024)", 200000, true),
            new AnthropicModelInfo("claude-3-5-haiku-20241022", "Claude 3.5 Haiku (Oct 2024)", 200000, true),
            
            // Claude 3 family
            new AnthropicModelInfo("claude-3-opus-20240229", "Claude 3 Opus", 200000, true),
            new AnthropicModelInfo("claude-3-sonnet-20240229", "Claude 3 Sonnet", 200000, true),
            new AnthropicModelInfo("claude-3-haiku-20240307", "Claude 3 Haiku", 200000, true),
            
            // Latest aliases
            new AnthropicModelInfo("claude-opus-4-5-latest", "Claude Opus 4.5 (Latest)", 200000, true),
            new AnthropicModelInfo("claude-sonnet-4-5-latest", "Claude Sonnet 4.5 (Latest)", 200000, true),
            new AnthropicModelInfo("claude-sonnet-4-latest", "Claude Sonnet 4 (Latest)", 200000, true),
            new AnthropicModelInfo("claude-3-5-sonnet-latest", "Claude 3.5 Sonnet (Latest)", 200000, true)
    );

    private final RestTemplate restTemplate;
    private final ISiteSettingsProvider siteSettingsProvider;

    public AnthropicModelFetcher(RestTemplate restTemplate, ISiteSettingsProvider siteSettingsProvider) {
        this.restTemplate = restTemplate;
        this.siteSettingsProvider = siteSettingsProvider;
    }

    @Override
    public AIProviderKey getProviderKey() {
        return AIProviderKey.ANTHROPIC;
    }

    @Override
    public boolean isConfigured() {
        // We use a static list, so always configured
        return true;
    }

    @Override
    public List<LlmModel> fetchModels(int minContextWindow) {
        List<LlmModel> models = new ArrayList<>();
        OffsetDateTime now = OffsetDateTime.now();

        // If API key is configured, we could try to validate models, but Anthropic
        // doesn't have a public models list endpoint yet, so we use static list

        for (AnthropicModelInfo modelInfo : ANTHROPIC_MODELS) {
            if (modelInfo.contextWindow < minContextWindow) {
                continue;
            }

            if (!modelInfo.supportsTools) {
                continue;
            }

            LlmModel llmModel = new LlmModel();
            llmModel.setProviderKey(AIProviderKey.ANTHROPIC);
            llmModel.setModelId(modelInfo.modelId);
            llmModel.setDisplayName(modelInfo.displayName);
            llmModel.setContextWindow(modelInfo.contextWindow);
            llmModel.setSupportsTools(true);
            llmModel.setLastSyncedAt(now);

            models.add(llmModel);
        }

        logger.info("Loaded {} Anthropic models from static list", models.size());
        return models;
    }

    private record AnthropicModelInfo(
            String modelId,
            String displayName,
            int contextWindow,
            boolean supportsTools
    ) {}
}
