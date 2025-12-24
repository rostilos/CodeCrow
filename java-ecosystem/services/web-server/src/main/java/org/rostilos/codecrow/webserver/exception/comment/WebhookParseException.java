package org.rostilos.codecrow.webserver.exception.comment;

/**
 * Exception thrown when the webhook payload cannot be parsed.
 */
public class WebhookParseException extends CommentCommandException {
    
    private final String provider;
    private final String eventType;
    private static final String PARSE_FAILED_CODE = "WEBHOOK_PARSE_FAILED";
    
    public WebhookParseException(String provider, String message) {
        super(message, null, PARSE_FAILED_CODE, false);
        this.provider = provider;
        this.eventType = null;
    }
    
    public WebhookParseException(String provider, String eventType, String message) {
        super(message, null, PARSE_FAILED_CODE, false);
        this.provider = provider;
        this.eventType = eventType;
    }
    
    public WebhookParseException(String provider, String message, Throwable cause) {
        super(message, cause, PARSE_FAILED_CODE, false);
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
