package org.rostilos.codecrow.webserver.ai.controller;

import static org.springframework.http.MediaType.APPLICATION_JSON_VALUE;

import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.core.util.SsrfSafeUrlValidator;
import org.rostilos.codecrow.webserver.ai.dto.response.LlmModelListResponse;
import org.rostilos.codecrow.webserver.ai.service.LlmModelService;
import org.rostilos.codecrow.webserver.ai.service.LlmModelSyncService;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.client.RestTemplate;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpMethod;

import java.util.List;
import java.util.Map;

@CrossOrigin(origins = "*", maxAge = 3600)
@RestController
@RequestMapping(path = "/api/llm-models", produces = APPLICATION_JSON_VALUE)
public class LlmModelController {

    private final LlmModelService llmModelService;
    private final LlmModelSyncService llmModelSyncService;

    public LlmModelController(
            LlmModelService llmModelService,
            LlmModelSyncService llmModelSyncService
    ) {
        this.llmModelService = llmModelService;
        this.llmModelSyncService = llmModelSyncService;
    }

    /**
     * Search LLM models with optional filtering and pagination.
     *
     * @param provider Optional provider filter (OPENAI, ANTHROPIC, GOOGLE, OPENROUTER)
     * @param search   Optional search query for model ID or display name
     * @param page     Page number (0-indexed), default 0
     * @param size     Page size, default 20
     */
    @GetMapping
    public ResponseEntity<LlmModelListResponse> searchModels(
            @RequestParam(required = false) AIProviderKey provider,
            @RequestParam(required = false) String search,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int size
    ) {
        // Limit page size to prevent abuse
        if (size > 100) {
            size = 100;
        }

        LlmModelListResponse response = llmModelService.searchModels(provider, search, page, size);
        return ResponseEntity.ok(response);
    }

    /**
     * Check if models are available in the database.
     */
    @GetMapping("/status")
    public ResponseEntity<Map<String, Object>> getStatus() {
        boolean hasModels = llmModelService.hasModels();
        
        return ResponseEntity.ok(Map.of(
                "hasModels", hasModels,
                "providers", Map.of(
                        "OPENAI", llmModelService.hasModelsForProvider(AIProviderKey.OPENAI),
                        "ANTHROPIC", llmModelService.hasModelsForProvider(AIProviderKey.ANTHROPIC),
                        "GOOGLE", llmModelService.hasModelsForProvider(AIProviderKey.GOOGLE),
                        "OPENROUTER", llmModelService.hasModelsForProvider(AIProviderKey.OPENROUTER),
                        "OPENAI_COMPATIBLE", false
                ),
                "minContextWindow", llmModelSyncService.getMinContextWindow()
        ));
    }

    /**
     * Manually trigger model sync (admin only).
     */
    @PostMapping("/sync")
    @PreAuthorize("hasRole('ADMIN')")
    public ResponseEntity<Map<String, Object>> triggerSync() {
        var result = llmModelSyncService.syncAllProviders();
        
        return ResponseEntity.ok(Map.of(
                "totalSynced", result.totalSynced(),
                "cleanedUp", result.cleanedUp(),
                "syncedByProvider", result.syncedCounts(),
                "errors", result.errors()
        ));
    }

    /**
     * Proxy endpoint to fetch models from an OpenAI-compatible endpoint.
     * This avoids CORS issues by routing through the Java backend.
     * The endpoint validates the URL for SSRF and fetches /v1/models.
     */
    @PostMapping("/fetch-custom")
    @PreAuthorize("isAuthenticated()")
    public ResponseEntity<?> fetchCustomModels(@RequestBody Map<String, String> request) {
        String baseUrl = request.get("baseUrl");
        String apiKey = request.get("apiKey");

        if (baseUrl == null || baseUrl.isBlank()) {
            return ResponseEntity.badRequest().body(Map.of("error", "baseUrl is required"));
        }

        // Validate URL for SSRF
        try {
            SsrfSafeUrlValidator.validate(baseUrl);
        } catch (IllegalArgumentException e) {
            return ResponseEntity.badRequest().body(Map.of("error", e.getMessage()));
        }

        // Build the /v1/models URL
        String modelsUrl = baseUrl.replaceAll("/+$", "") + "/v1/models";

        try {
            RestTemplate restTemplate = new RestTemplate();
            HttpHeaders headers = new HttpHeaders();
            headers.set("Accept", "application/json");
            if (apiKey != null && !apiKey.isBlank()) {
                headers.setBearerAuth(apiKey);
            }

            HttpEntity<Void> entity = new HttpEntity<>(headers);
            @SuppressWarnings("rawtypes")
            ResponseEntity<Map> response = restTemplate.exchange(
                    modelsUrl, HttpMethod.GET, entity, Map.class
            );

            // Extract model IDs from the OpenAI-style response
            @SuppressWarnings("rawtypes")
            Map body = response.getBody();
            if (body != null && body.containsKey("data")) {
                @SuppressWarnings("unchecked")
                List<Map<String, Object>> data = (List<Map<String, Object>>) body.get("data");
                List<String> modelIds = data.stream()
                        .map(m -> (String) m.get("id"))
                        .filter(id -> id != null)
                        .sorted()
                        .toList();
                return ResponseEntity.ok(Map.of("models", modelIds));
            }

            return ResponseEntity.ok(Map.of("models", List.of()));
        } catch (Exception e) {
            return ResponseEntity.status(502).body(Map.of(
                    "error", "Failed to fetch models from endpoint: " + e.getMessage()
            ));
        }
    }
}
