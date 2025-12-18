package org.rostilos.codecrow.webserver.exception.comment;

/**
 * Base exception for all comment command related errors.
 */
public class CommentCommandException extends RuntimeException {
    
    private final String errorCode;
    private final boolean retryable;
    
    public CommentCommandException(String message) {
        this(message, null, "COMMENT_CMD_ERROR", false);
    }
    
    public CommentCommandException(String message, String errorCode) {
        this(message, null, errorCode, false);
    }
    
    public CommentCommandException(String message, Throwable cause) {
        this(message, cause, "COMMENT_CMD_ERROR", false);
    }
    
    public CommentCommandException(String message, Throwable cause, String errorCode, boolean retryable) {
        super(message, cause);
        this.errorCode = errorCode;
        this.retryable = retryable;
    }
    
    public String getErrorCode() {
        return errorCode;
    }
    
    public boolean isRetryable() {
        return retryable;
    }
}
