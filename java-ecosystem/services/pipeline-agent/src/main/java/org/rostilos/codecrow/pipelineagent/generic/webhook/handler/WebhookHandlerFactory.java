package org.rostilos.codecrow.pipelineagent.generic.webhook.handler;

import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.stream.Collectors;

/**
 * Factory for obtaining the appropriate WebhookHandler based on provider and event type.
 */
@Component
public class WebhookHandlerFactory {
    
    private static final Logger log = LoggerFactory.getLogger(WebhookHandlerFactory.class);
    
    private final Map<EVcsProvider, List<WebhookHandler>> handlersByProvider;
    
    public WebhookHandlerFactory(List<WebhookHandler> handlers) {
        this.handlersByProvider = handlers.stream()
                .collect(Collectors.groupingBy(WebhookHandler::getProvider));
        
        log.info("Registered {} webhook handlers for {} providers", 
                handlers.size(), handlersByProvider.size());
        
        handlersByProvider.forEach((provider, providerHandlers) -> 
            log.info("  {}: {} handlers", provider, providerHandlers.size()));
    }
    
    /**
     * Get a handler that supports the given provider and event type.
     * 
     * @param provider The VCS provider
     * @param eventType The webhook event type
     * @return Optional containing the handler, or empty if no handler supports this event
     */
    public Optional<WebhookHandler> getHandler(EVcsProvider provider, String eventType) {
        List<WebhookHandler> handlers = handlersByProvider.get(provider);
        
        if (handlers == null || handlers.isEmpty()) {
            log.debug("No handlers registered for provider: {}", provider);
            return Optional.empty();
        }
        
        return handlers.stream()
                .filter(h -> h.supportsEvent(eventType))
                .findFirst();
    }
    
    /**
     * Check if any handler exists for the given provider.
     */
    public boolean hasHandlerForProvider(EVcsProvider provider) {
        return handlersByProvider.containsKey(provider) && 
               !handlersByProvider.get(provider).isEmpty();
    }
    
    /**
     * Get all handlers for a provider.
     */
    public List<WebhookHandler> getHandlersForProvider(EVcsProvider provider) {
        return handlersByProvider.getOrDefault(provider, List.of());
    }
}
