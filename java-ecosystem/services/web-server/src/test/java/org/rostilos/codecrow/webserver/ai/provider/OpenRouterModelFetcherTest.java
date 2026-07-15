package org.rostilos.codecrow.webserver.ai.provider;

import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.rostilos.codecrow.core.dto.admin.LlmSyncSettingsDTO;
import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.service.SiteSettingsProvider;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;
import org.springframework.web.client.RestTemplate;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class OpenRouterModelFetcherTest {

    private static final String MODELS_URL = "https://openrouter.ai/api/v1/models";

    @Test
    void exposesProviderIdentityAndRemainsConfiguredWithoutAnApiKey() {
        OpenRouterModelFetcher fetcher = new OpenRouterModelFetcher(
                mock(RestTemplate.class), mock(SiteSettingsProvider.class)
        );

        assertThat(fetcher.getProviderKey()).isEqualTo(AIProviderKey.OPENROUTER);
        assertThat(fetcher.isConfigured()).isTrue();
    }

    @Test
    void mapsOnlyToolCapableModelsAndNormalizesNamesContextAndPricing() {
        RestTemplate restTemplate = mock(RestTemplate.class);
        SiteSettingsProvider settings = settings("secret-key");
        OpenRouterModelFetcher.OpenRouterModelsResponse responseBody = response(
                model("missing-context", null, List.of("tools"), null, null),
                model("small", 49_999, List.of("tools"), null, null),
                model("tools", 50_000, List.of("tools"), null, null),
                model("choice", 60_000, List.of("tool_choice"), "Choice", pricing(null, " ")),
                model("functions", 70_000, List.of("functions"), "Functions", pricing("0", "0.0000025")),
                modelWithArchitecture("architecture", 80_000, "TEXT->TEXT", pricing("bad", "0.000001")),
                modelWithArchitecture("non-text", 90_000, "image", null),
                modelWithArchitecture("missing-modality", 90_000, null, null),
                model("missing-architecture", 90_000, null, null, null),
                model("unsupported-parameter", 90_000, List.of("temperature"), null, null)
        );
        when(restTemplate.exchange(
                eq(MODELS_URL), eq(HttpMethod.GET), any(HttpEntity.class),
                eq(OpenRouterModelFetcher.OpenRouterModelsResponse.class)
        )).thenReturn(ResponseEntity.ok(responseBody));

        OpenRouterModelFetcher fetcher = new OpenRouterModelFetcher(restTemplate, settings);
        var models = fetcher.fetchModels(50_000);

        assertThat(models).hasSize(4);
        assertThat(models).allSatisfy(model -> {
            assertThat(model.getProviderKey()).isEqualTo(AIProviderKey.OPENROUTER);
            assertThat(model.isSupportsTools()).isTrue();
            assertThat(model.getLastSyncedAt()).isNotNull();
        });
        assertThat(models.get(0).getModelId()).isEqualTo("tools");
        assertThat(models.get(0).getDisplayName()).isEqualTo("tools");
        assertThat(models.get(0).getContextWindow()).isEqualTo(50_000);
        assertThat(models.get(0).getInputPricePerMillion()).isNull();
        assertThat(models.get(1).getDisplayName()).isEqualTo("Choice");
        assertThat(models.get(1).getInputPricePerMillion()).isEqualTo("0");
        assertThat(models.get(1).getOutputPricePerMillion()).isEqualTo("0");
        assertThat(models.get(2).getInputPricePerMillion()).isEqualTo("0");
        assertThat(models.get(2).getOutputPricePerMillion()).isEqualTo("2.5");
        assertThat(models.get(3).getInputPricePerMillion()).isNull();
        assertThat(models.get(3).getOutputPricePerMillion()).isEqualTo("1");

        ArgumentCaptor<HttpEntity<String>> entity = ArgumentCaptor.forClass(HttpEntity.class);
        verify(restTemplate).exchange(
                eq(MODELS_URL), eq(HttpMethod.GET), entity.capture(),
                eq(OpenRouterModelFetcher.OpenRouterModelsResponse.class)
        );
        assertThat(entity.getValue().getHeaders().getFirst("Accept")).isEqualTo("application/json");
        assertThat(entity.getValue().getHeaders().getFirst("Authorization"))
                .isEqualTo("Bearer secret-key");
    }

    @Test
    void acceptsNullOrBlankKeysAndEmptyResponsesWithoutInventingModels() {
        for (String apiKey : new String[]{null, " "}) {
            RestTemplate restTemplate = mock(RestTemplate.class);
            when(restTemplate.exchange(
                    eq(MODELS_URL), eq(HttpMethod.GET), any(HttpEntity.class),
                    eq(OpenRouterModelFetcher.OpenRouterModelsResponse.class)
            )).thenReturn(ResponseEntity.ok(apiKey == null ? null : response()));

            OpenRouterModelFetcher fetcher = new OpenRouterModelFetcher(
                    restTemplate, settings(apiKey)
            );
            assertThat(fetcher.fetchModels(1)).isEmpty();

            ArgumentCaptor<HttpEntity<String>> entity = ArgumentCaptor.forClass(HttpEntity.class);
            verify(restTemplate).exchange(
                    eq(MODELS_URL), eq(HttpMethod.GET), entity.capture(),
                    eq(OpenRouterModelFetcher.OpenRouterModelsResponse.class)
            );
            assertThat(entity.getValue().getHeaders().containsKey("Authorization")).isFalse();
        }
    }

    @Test
    void providerFailureIsContainedAsAnEmptyResult() {
        RestTemplate restTemplate = mock(RestTemplate.class);
        when(restTemplate.exchange(
                eq(MODELS_URL), eq(HttpMethod.GET), any(HttpEntity.class),
                eq(OpenRouterModelFetcher.OpenRouterModelsResponse.class)
        )).thenThrow(new IllegalStateException("offline failure"));

        assertThat(new OpenRouterModelFetcher(restTemplate, settings(null)).fetchModels(1))
                .isEmpty();
    }

    private static SiteSettingsProvider settings(String openRouterApiKey) {
        SiteSettingsProvider settings = mock(SiteSettingsProvider.class);
        when(settings.getLlmSyncSettings()).thenReturn(
                new LlmSyncSettingsDTO(openRouterApiKey, null, null, null)
        );
        return settings;
    }

    private static OpenRouterModelFetcher.OpenRouterModelsResponse response(
            OpenRouterModelFetcher.OpenRouterModel... models
    ) {
        OpenRouterModelFetcher.OpenRouterModelsResponse response =
                new OpenRouterModelFetcher.OpenRouterModelsResponse();
        response.data = models.length == 0 ? null : List.of(models);
        return response;
    }

    private static OpenRouterModelFetcher.OpenRouterModel model(
            String id,
            Integer contextLength,
            List<String> supportedParameters,
            String name,
            OpenRouterModelFetcher.OpenRouterPricing pricing
    ) {
        OpenRouterModelFetcher.OpenRouterModel model = new OpenRouterModelFetcher.OpenRouterModel();
        model.id = id;
        model.name = name;
        model.contextLength = contextLength;
        model.supportedParameters = supportedParameters;
        model.pricing = pricing;
        return model;
    }

    private static OpenRouterModelFetcher.OpenRouterModel modelWithArchitecture(
            String id,
            int contextLength,
            String modality,
            OpenRouterModelFetcher.OpenRouterPricing pricing
    ) {
        OpenRouterModelFetcher.OpenRouterModel model = model(
                id, contextLength, null, null, pricing
        );
        model.architecture = new OpenRouterModelFetcher.OpenRouterArchitecture();
        model.architecture.modality = modality;
        return model;
    }

    private static OpenRouterModelFetcher.OpenRouterPricing pricing(
            String prompt, String completion
    ) {
        OpenRouterModelFetcher.OpenRouterPricing pricing =
                new OpenRouterModelFetcher.OpenRouterPricing();
        pricing.prompt = prompt;
        pricing.completion = completion;
        return pricing;
    }
}
