package org.rostilos.codecrow.webserver.controller.integration;

import com.fasterxml.jackson.databind.JsonNode;
import org.rostilos.codecrow.webserver.service.integration.ForgeWebhookService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * Controller for Atlassian Forge app lifecycle events.
 * 
 * This endpoint receives installation and uninstallation events from Forge.
 * The appSystemToken is extracted from the x-forge-oauth-system header
 * and stored encrypted for subsequent API calls.
 * 
 * Mapped to: /api/forge/lifecycle
 * 
 * @see <a href="https://developer.atlassian.com/platform/forge/remote/calling-product-apis/">Forge Remote APIs</a>
 */
@RestController
@RequestMapping("/api/forge")
public class ForgeWebhookController {

    private static final Logger log = LoggerFactory.getLogger(ForgeWebhookController.class);

    private final ForgeWebhookService forgeWebhookService;

    public ForgeWebhookController(ForgeWebhookService forgeWebhookService) {
        this.forgeWebhookService = forgeWebhookService;
    }

    /**
     * Handle Forge lifecycle events.
     * Events: avi:forge:installed:app, avi:forge:uninstalled:app
     * 
     * Headers:
     * - Authorization: Bearer <FIT token> (Forge Invocation Token)
     * - x-forge-oauth-system: <appSystemToken> (OAuth token for Bitbucket API)
     */
    @PostMapping(value = "/lifecycle", consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<?> handleLifecycleEvent(
            @RequestBody JsonNode payload,
            @RequestHeader(value = "Authorization", required = false) String authorization,
            @RequestHeader(value = "x-forge-oauth-system", required = false) String appSystemToken
    ) {
        log.info("Received Forge lifecycle event");
        log.debug("Headers - Authorization present: {}, appSystemToken present: {}", 
                authorization != null, appSystemToken != null);
        log.debug("Payload: {}", payload);

        try {
            // Extract FIT token from Authorization header
            String fitToken = extractFitToken(authorization);
            
            // Determine event type
            String eventType = extractEventType(payload);
            log.info("Forge event type: {}", eventType);

            switch (eventType) {
                case "avi:forge:installed:app" -> {
                    forgeWebhookService.handleAppInstalled(payload, appSystemToken, fitToken);
                    return ResponseEntity.ok(Map.of(
                            "status", "success",
                            "message", "App installed successfully"
                    ));
                }
                case "avi:forge:uninstalled:app" -> {
                    forgeWebhookService.handleAppUninstalled(payload, fitToken);
                    return ResponseEntity.ok(Map.of(
                            "status", "success",
                            "message", "App uninstalled successfully"
                    ));
                }
                default -> {
                    log.info("Unknown lifecycle event type: {}", eventType);
                    return ResponseEntity.ok(Map.of(
                            "status", "ignored",
                            "reason", "Unknown event type: " + eventType
                    ));
                }
            }
        } catch (Exception e) {
            log.error("Error processing Forge lifecycle event", e);
            return ResponseEntity.internalServerError().body(Map.of(
                    "error", "processing_failed",
                    "message", e.getMessage()
            ));
        }
    }

    /**
     * Health check endpoint for Forge.
     */
    @GetMapping("/health")
    public ResponseEntity<?> health() {
        return ResponseEntity.ok(Map.of("status", "healthy"));
    }

    private String extractFitToken(String authorization) {
        if (authorization != null && authorization.startsWith("Bearer ")) {
            return authorization.substring(7);
        }
        return null;
    }

    private String extractEventType(JsonNode payload) {
        // Try different locations where Forge might put the event type
        if (payload.has("eventType")) {
            return payload.get("eventType").asText();
        }
        if (payload.has("event")) {
            return payload.get("event").asText();
        }
        if (payload.has("type")) {
            return payload.get("type").asText();
        }
        // For trigger events, check the trigger key
        if (payload.has("trigger") && payload.get("trigger").has("key")) {
            String key = payload.get("trigger").get("key").asText();
            // Map trigger key to event type
            if (key.contains("installed")) {
                return "avi:forge:installed:app";
            }
            if (key.contains("uninstalled")) {
                return "avi:forge:uninstalled:app";
            }
        }
        return "unknown";
    }
}
