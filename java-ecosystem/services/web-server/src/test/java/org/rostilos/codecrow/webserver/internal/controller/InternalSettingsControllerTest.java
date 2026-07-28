package org.rostilos.codecrow.webserver.internal.controller;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.dto.admin.EmbeddingSettingsDTO;
import org.rostilos.codecrow.core.service.SiteSettingsProvider;
import org.springframework.http.ResponseEntity;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class InternalSettingsControllerTest {

    @Test
    void marksDefaultOnlySettingsAsNotExplicitlyConfigured() {
        SiteSettingsProvider settingsProvider = mock(SiteSettingsProvider.class);
        when(settingsProvider.getEmbeddingSettings()).thenReturn(new EmbeddingSettingsDTO(
                "ollama",
                "http://host.docker.internal:11434",
                "qwen3-embedding:0.6b",
                "",
                "qwen/qwen3-embedding-8b"
        ));
        when(settingsProvider.isEmbeddingConfigurationExplicitlySet()).thenReturn(false);

        ResponseEntity<Map<String, String>> response =
                new InternalSettingsController(settingsProvider).getEmbeddingConfig();

        assertThat(response.getBody())
                .containsEntry("EMBEDDING_SETTINGS_CONFIGURED", "false")
                .containsEntry("EMBEDDING_PROVIDER", "ollama");
    }
}
