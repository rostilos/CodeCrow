package org.rostilos.codecrow.webserver.ai.provider;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.service.SiteSettingsProvider;
import org.springframework.http.MediaType;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestTemplate;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.*;
import static org.springframework.test.web.client.response.MockRestResponseCreators.*;

class OpenRouterModelFetcherTest {
    private final RestTemplate rest = new RestTemplate();
    private final MockRestServiceServer server = MockRestServiceServer.bindTo(rest).build();
    private final SiteSettingsProvider settings = mock(SiteSettingsProvider.class, RETURNS_DEEP_STUBS);
    private final OpenRouterModelFetcher fetcher = new OpenRouterModelFetcher(rest, settings);

    private void models() {
        server.expect(requestTo("https://openrouter.ai/api/v1/models")).andRespond(withSuccess("""
            {"data":[{"id":"test/model:free","context_length":100000,"supported_parameters":["tools"]},
                     {"id":"excluded/model","context_length":1000,"supported_parameters":["tools"]}]}
            """, MediaType.APPLICATION_JSON));
    }

    @Test
    void fetchesOnlyThisModelsProvidersUsingRoutingTagsAndDeduplicates() {
        models();
        server.expect(requestTo("https://openrouter.ai/api/v1/models/test/model:free/endpoints"))
                .andRespond(withSuccess("""
                    {"data":{"endpoints":[{"tag":"google-vertex/us-east5","provider_name":"Google Vertex"},
                        {"tag":"google-vertex/us-east5","provider_name":"Google Vertex"},
                        {"tag":"google-ai-studio","provider_name":"Google AI Studio"}]}}
                    """, MediaType.APPLICATION_JSON));
        var result = fetcher.fetchModels(50000);
        assertThat(result).hasSize(1);
        assertThat(result.get(0).getProviders()).extracting("slug")
                .containsExactly("google-vertex/us-east5", "google-ai-studio");
        assertThat(result.get(0).getProvidersSyncedAt()).isNotNull();
        server.verify();
    }

    @Test
    void failedEndpointRefreshStillReturnsModelWithoutReplacingProviderSnapshot() {
        models();
        server.expect(requestTo("https://openrouter.ai/api/v1/models/test/model:free/endpoints"))
                .andRespond(withServerError());
        var result = fetcher.fetchModels(50000);
        assertThat(result).hasSize(1);
        assertThat(result.get(0).getProviders()).isNull();
        assertThat(result.get(0).getProvidersSyncedAt()).isNull();
        server.verify();
    }

    @Test
    void successfulEmptyEndpointsClearsRemovedProviders() {
        models();
        server.expect(requestTo("https://openrouter.ai/api/v1/models/test/model:free/endpoints"))
                .andRespond(withSuccess("{\"data\":{\"endpoints\":[]}}", MediaType.APPLICATION_JSON));
        var model = fetcher.fetchModels(50000).get(0);
        assertThat(model.getProviders()).isEmpty();
        assertThat(model.getProvidersSyncedAt()).isNotNull();
        server.verify();
    }
}
