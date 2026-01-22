package org.rostilos.codecrow.webserver.ai.controller;

import static org.springframework.http.MediaType.APPLICATION_JSON_VALUE;

import org.rostilos.codecrow.core.model.ai.AIProviderKey;
import org.rostilos.codecrow.webserver.ai.dto.response.LlmModelListResponse;
import org.rostilos.codecrow.webserver.ai.service.LlmModelService;
import org.rostilos.codecrow.webserver.ai.service.LlmModelSyncService;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

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
                        "OPENROUTER", llmModelService.hasModelsForProvider(AIProviderKey.OPENROUTER)
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
}
