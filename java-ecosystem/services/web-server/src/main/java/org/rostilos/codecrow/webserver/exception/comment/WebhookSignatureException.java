package org.rostilos.codecrow.webserver.exception.comment;

/**
 * Exception thrown when webhook signature validation fails.
 */
public class WebhookSignatureException extends CommentCommandException {
    
    private final String provider;
    private static final String SIGNATURE_INVALID_CODE = "WEBHOOK_SIGNATURE_INVALID";
    
    public WebhookSignatureException(String provider) {
        super("Webhook signature validation failed for provider: " + provider, 
              null, SIGNATURE_INVALID_CODE, false);
        this.provider = provider;
    }
    
    public WebhookSignatureException(String provider, String message) {
        super(message, null, SIGNATURE_INVALID_CODE, false);
        this.provider = provider;
    }
    
    public WebhookSignatureException(String provider, Throwable cause) {
        super("Webhook signature validation failed for provider: " + provider, 
              cause, SIGNATURE_INVALID_CODE, false);
        this.provider = provider;
    }
    
    public String getProvider() {
        return provider;
    }
}
