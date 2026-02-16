package org.rostilos.codecrow.webserver.internal.controller;

import org.rostilos.codecrow.core.dto.admin.EmbeddingSettingsDTO;
import org.rostilos.codecrow.core.service.SiteSettingsProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.HashMap;
import java.util.Map;

/**
 * Internal API for service-to-service communication.
 * Used by Python RAG pipeline to fetch embedding configuration.
 *
 * Security:
 * 1. Network-level: Only accessible from internal Docker network (not exposed to public)
 * 2. Auth: X-Internal-Secret header validated by InternalApiSecurityFilter
 */
@RestController
@RequestMapping("/api/internal/settings")
public class InternalSettingsController {

    private static final Logger log = LoggerFactory.getLogger(InternalSettingsController.class);

    private final SiteSettingsProvider siteSettingsProvider;

    public InternalSettingsController(SiteSettingsProvider siteSettingsProvider) {
        this.siteSettingsProvider = siteSettingsProvider;
    }

    /**
     * Returns embedding configuration for the RAG pipeline.
     * Python service polls this endpoint to get current embedding provider settings.
     *
     * Response format matches the env vars the Python service expects:
     * {
     *   "EMBEDDING_PROVIDER": "ollama",
     *   "OLLAMA_BASE_URL": "http://ollama:11434",
     *   "OLLAMA_EMBEDDING_MODEL": "nomic-embed-text",
     *   "OPENROUTER_API_KEY": "sk-...",
     *   "OPENROUTER_MODEL": "..."
     * }
     */
    @GetMapping("/embedding")
    public ResponseEntity<Map<String, String>> getEmbeddingConfig() {
        try {
            EmbeddingSettingsDTO embedding = siteSettingsProvider.getEmbeddingSettings();

            Map<String, String> config = new HashMap<>();
            config.put("EMBEDDING_PROVIDER", embedding.provider() != null ? embedding.provider() : "");
            config.put("OLLAMA_BASE_URL", embedding.ollamaBaseUrl() != null ? embedding.ollamaBaseUrl() : "");
            config.put("OLLAMA_EMBEDDING_MODEL", embedding.ollamaModel() != null ? embedding.ollamaModel() : "");
            config.put("OPENROUTER_API_KEY", embedding.openrouterApiKey() != null ? embedding.openrouterApiKey() : "");
            config.put("OPENROUTER_MODEL", embedding.openrouterModel() != null ? embedding.openrouterModel() : "");

            return ResponseEntity.ok(config);
        } catch (Exception e) {
            log.error("Failed to retrieve embedding configuration", e);
            return ResponseEntity.internalServerError().build();
        }
    }
}
