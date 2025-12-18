package org.rostilos.codecrow.webserver.exception.comment;

/**
 * Exception thrown when the webhook payload cannot be parsed.
 */
public class WebhookParseException extends CommentCommandException {
    
    private final String provider;
    private final String eventType;
    
    public WebhookParseException(String provider, String message) {
        super(message, null, "WEBHOOK_PARSE_FAILED", false);
        this.provider = provider;
        this.eventType = null;
    }
    
    public WebhookParseException(String provider, String eventType, String message) {
        super(message, null, "WEBHOOK_PARSE_FAILED", false);
        this.provider = provider;
        this.eventType = eventType;
    }
    
    public WebhookParseException(String provider, String message, Throwable cause) {
        super(message, cause, "WEBHOOK_PARSE_FAILED", false);
        this.provider = provider;
        this.eventType = null;
    }
    
    public String getProvider() {
        return provider;
    }
    
    public String getEventType() {
        return eventType;
    }
}
