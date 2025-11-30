package org.rostilos.codecrow.vcsclient.model;

import java.util.List;

/**
 * Represents a webhook configuration in a VCS provider.
 */
public record VcsWebhook(
    /**
     * Webhook ID.
     */
    String id,
    
    /**
     * Target URL for webhook delivery.
     */
    String url,
    
    /**
     * Whether the webhook is active.
     */
    boolean active,
    
    /**
     * List of events this webhook subscribes to.
     */
    List<String> events,
    
    /**
     * Description/name of the webhook.
     */
    String description
) {
    /**
     * Check if this webhook matches a target URL (case-insensitive).
     */
    public boolean matchesUrl(String targetUrl) {
        if (url == null || targetUrl == null) return false;
        return url.equalsIgnoreCase(targetUrl);
    }
}
