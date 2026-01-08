package org.rostilos.codecrow.vcsclient.gitlab;

/**
 * Exception for GitLab API errors.
 */
public class GitLabException extends RuntimeException {
    
    private final int statusCode;
    private final String responseBody;
    
    public GitLabException(String operation, int statusCode, String responseBody) {
        super(String.format("GitLab %s failed: %d - %s", operation, statusCode, responseBody));
        this.statusCode = statusCode;
        this.responseBody = responseBody;
    }
    
    public GitLabException(String message) {
        super(message);
        this.statusCode = -1;
        this.responseBody = null;
    }
    
    public GitLabException(String message, Throwable cause) {
        super(message, cause);
        this.statusCode = -1;
        this.responseBody = null;
    }
    
    public int getStatusCode() {
        return statusCode;
    }
    
    public String getResponseBody() {
        return responseBody;
    }
}
