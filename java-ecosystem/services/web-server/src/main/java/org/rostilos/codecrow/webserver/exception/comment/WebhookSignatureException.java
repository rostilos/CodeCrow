package org.rostilos.codecrow.webserver.exception.comment;

/**
 * Exception thrown when webhook signature validation fails.
 */
public class WebhookSignatureException extends CommentCommandException {
    
    private final String provider;
    
    public WebhookSignatureException(String provider) {
        super("Webhook signature validation failed for provider: " + provider, 
              null, "WEBHOOK_SIGNATURE_INVALID", false);
        this.provider = provider;
    }
    
    public WebhookSignatureException(String provider, String message) {
        super(message, null, "WEBHOOK_SIGNATURE_INVALID", false);
        this.provider = provider;
    }
    
    public WebhookSignatureException(String provider, Throwable cause) {
        super("Webhook signature validation failed for provider: " + provider, 
              cause, "WEBHOOK_SIGNATURE_INVALID", false);
        this.provider = provider;
    }
    
    public String getProvider() {
        return provider;
    }
}
