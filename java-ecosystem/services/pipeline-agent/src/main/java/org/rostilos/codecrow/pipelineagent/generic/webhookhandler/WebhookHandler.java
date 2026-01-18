package org.rostilos.codecrow.pipelineagent.generic.webhookhandler;

import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.pipelineagent.generic.dto.webhook.WebhookPayload;
import org.springframework.http.ResponseEntity;

import java.util.Map;
import java.util.function.Consumer;

/**
 * Interface for handling webhooks from VCS providers.
 * Implementations handle provider-specific webhook processing.
 */
public interface WebhookHandler {
    
    /**
     * Get the VCS provider this handler supports.
     */
    EVcsProvider getProvider();
    
    /**
     * Check if this handler supports the given event type.
     * 
     * @param eventType The webhook event type
     * @return true if this handler can process the event
     */
    boolean supportsEvent(String eventType);
    
    /**
     * Process a webhook payload.
     * 
     * @param payload The parsed webhook payload
     * @param project The project this webhook is for
     * @param eventConsumer Consumer for streaming progress events
     * @return Processing result
     */
    WebhookResult handle(WebhookPayload payload, Project project, Consumer<Map<String, Object>> eventConsumer);
    
    /**
     * Result of webhook processing.
     */
    record WebhookResult(
        boolean success,
        String status,
        String message,
        Map<String, Object> data
    ) {
        public static WebhookResult success(String message) {
            return new WebhookResult(true, "processed", message, Map.of());
        }
        
        public static WebhookResult success(String message, Map<String, Object> data) {
            return new WebhookResult(true, "processed", message, data);
        }
        
        public static WebhookResult ignored(String message) {
            return new WebhookResult(true, "ignored", message, Map.of());
        }
        
        public static WebhookResult error(String message) {
            return new WebhookResult(false, "error", message, Map.of());
        }
        
        public static WebhookResult queued(String message) {
            return new WebhookResult(true, "queued", message, Map.of());
        }
        
        public ResponseEntity<Map<String, Object>> toResponseEntity() {
            Map<String, Object> body = new java.util.HashMap<>(data);
            body.put("status", status);
            body.put("message", message);
            
            if (success) {
                return ResponseEntity.ok(body);
            } else {
                return ResponseEntity.internalServerError().body(body);
            }
        }
    }
}
