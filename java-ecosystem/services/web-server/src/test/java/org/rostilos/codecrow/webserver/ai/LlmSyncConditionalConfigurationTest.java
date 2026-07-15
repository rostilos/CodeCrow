package org.rostilos.codecrow.webserver.ai;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.service.SiteSettingsProvider;
import org.rostilos.codecrow.webserver.ai.provider.OpenRouterModelFetcher;
import org.rostilos.codecrow.webserver.ai.scheduler.LlmModelSyncScheduler;
import org.rostilos.codecrow.webserver.ai.service.LlmModelSyncService;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Import;
import org.springframework.web.client.RestTemplate;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

class LlmSyncConditionalConfigurationTest {

    private final ApplicationContextRunner contextRunner = new ApplicationContextRunner()
            .withUserConfiguration(TestConfiguration.class)
            .withBean(RestTemplate.class, RestTemplate::new)
            .withBean(SiteSettingsProvider.class, () -> mock(SiteSettingsProvider.class))
            .withBean(LlmModelSyncService.class, () -> mock(LlmModelSyncService.class));

    @Test
    void syncComponentsRemainEnabledByDefault() {
        contextRunner.run(context -> {
            assertThat(context).hasSingleBean(OpenRouterModelFetcher.class);
            assertThat(context).hasSingleBean(LlmModelSyncScheduler.class);
        });
    }

    @Test
    void integrationPropertiesDisableLiveSyncComponents() {
        contextRunner
                .withPropertyValues(
                        "llm.sync.openrouter.enabled=false",
                        "llm.sync.scheduler.enabled=false"
                )
                .run(context -> {
                    assertThat(context).doesNotHaveBean(OpenRouterModelFetcher.class);
                    assertThat(context).doesNotHaveBean(LlmModelSyncScheduler.class);
                });
    }

    @Configuration(proxyBeanMethods = false)
    @Import({OpenRouterModelFetcher.class, LlmModelSyncScheduler.class})
    static class TestConfiguration {
    }
}
