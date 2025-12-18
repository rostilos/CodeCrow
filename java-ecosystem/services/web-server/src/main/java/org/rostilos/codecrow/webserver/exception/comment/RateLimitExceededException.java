package org.rostilos.codecrow.webserver.exception.comment;

/**
 * Exception thrown when a command exceeds rate limits.
 */
public class RateLimitExceededException extends CommentCommandException {
    
    private final int retryAfterSeconds;
    private final String limitType;
    
    public RateLimitExceededException(String message, String limitType, int retryAfterSeconds) {
        super(message, null, "RATE_LIMIT_EXCEEDED", true);
        this.limitType = limitType;
        this.retryAfterSeconds = retryAfterSeconds;
    }
    
    public RateLimitExceededException(String limitType, int retryAfterSeconds) {
        this("Rate limit exceeded: " + limitType, limitType, retryAfterSeconds);
    }
    
    public int getRetryAfterSeconds() {
        return retryAfterSeconds;
    }
    
    public String getLimitType() {
        return limitType;
    }
    
    /**
     * Limit types for rate limiting.
     */
    public static class LimitType {
        public static final String PER_PR = "MAX_COMMANDS_PER_PR";
        public static final String PER_DAY = "MAX_COMMANDS_PER_DAY";
        public static final String COOLDOWN = "COOLDOWN_PERIOD";
        
        private LimitType() {}
    }
}
