package org.rostilos.codecrow.pipelineagent.generic.webhookhandler;

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
    private final List<WebhookHandler> multiProviderHandlers;
    
    public WebhookHandlerFactory(List<WebhookHandler> handlers) {
        // Separate handlers: those with specific provider vs multi-provider (null provider)
        this.multiProviderHandlers = handlers.stream()
                .filter(h -> h.getProvider() == null)
                .toList();
        
        this.handlersByProvider = handlers.stream()
                .filter(h -> h.getProvider() != null)
                .collect(Collectors.groupingBy(WebhookHandler::getProvider));
        
        log.info("Registered {} webhook handlers for {} providers, plus {} multi-provider handlers", 
                handlers.size() - multiProviderHandlers.size(), 
                handlersByProvider.size(),
                multiProviderHandlers.size());
        
        handlersByProvider.forEach((provider, providerHandlers) -> 
            log.info("  {}: {} handlers", provider, providerHandlers.size()));
    }
    
    /**
     * Get a handler that supports the given provider and event type.
     * First checks provider-specific handlers, then multi-provider handlers.
     * 
     * @param provider The VCS provider
     * @param eventType The webhook event type
     * @return Optional containing the handler, or empty if no handler supports this event
     */
    public Optional<WebhookHandler> getHandler(EVcsProvider provider, String eventType) {
        // First, try provider-specific handlers
        List<WebhookHandler> handlers = handlersByProvider.get(provider);
        
        if (handlers != null && !handlers.isEmpty()) {
            Optional<WebhookHandler> handler = handlers.stream()
                    .filter(h -> h.supportsEvent(eventType))
                    .findFirst();
            if (handler.isPresent()) {
                return handler;
            }
        }
        
        // Fall back to multi-provider handlers
        return multiProviderHandlers.stream()
                .filter(h -> h.supportsEvent(eventType))
                .findFirst();
    }
    
    /**
     * Check if any handler exists for the given provider.
     */
    public boolean hasHandlerForProvider(EVcsProvider provider) {
        return (handlersByProvider.containsKey(provider) && 
               !handlersByProvider.get(provider).isEmpty()) ||
               !multiProviderHandlers.isEmpty();
    }
    
    /**
     * Get all handlers for a provider (including multi-provider handlers).
     */
    public List<WebhookHandler> getHandlersForProvider(EVcsProvider provider) {
        List<WebhookHandler> result = new java.util.ArrayList<>(
            handlersByProvider.getOrDefault(provider, List.of())
        );
        result.addAll(multiProviderHandlers);
        return result;
    }
}
