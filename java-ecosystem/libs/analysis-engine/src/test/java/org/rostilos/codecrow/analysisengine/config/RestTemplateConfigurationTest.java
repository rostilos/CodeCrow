package org.rostilos.codecrow.analysisengine.config;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.boot.web.client.RestTemplateBuilder;
import org.springframework.web.client.RestTemplate;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("RestTemplateConfiguration")
class RestTemplateConfigurationTest {

    @Test
    @DisplayName("should create aiRestTemplate bean")
    void shouldCreateAiRestTemplateBean() {
        RestTemplateConfiguration config = new RestTemplateConfiguration();
        RestTemplateBuilder builder = new RestTemplateBuilder();

        RestTemplate restTemplate = config.aiRestTemplate(builder);

        assertThat(restTemplate).isNotNull();
    }

    @Test
    @DisplayName("aiRestTemplate should be usable for HTTP requests")
    void aiRestTemplateShouldBeUsable() {
        RestTemplateConfiguration config = new RestTemplateConfiguration();
        RestTemplateBuilder builder = new RestTemplateBuilder();

        RestTemplate restTemplate = config.aiRestTemplate(builder);

        assertThat(restTemplate.getRequestFactory()).isNotNull();
    }
}
