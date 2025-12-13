package org.rostilos.codecrow.vcsclient.github;

import org.rostilos.codecrow.vcsclient.VcsClientException;

public class GitHubException extends VcsClientException {
    
    private final int statusCode;
    private final String responseBody;
    
    public GitHubException(String message) {
        super(message);
        this.statusCode = -1;
        this.responseBody = null;
    }
    
    public GitHubException(String message, Throwable cause) {
        super(message, cause);
        this.statusCode = -1;
        this.responseBody = null;
    }
    
    public GitHubException(String operation, int statusCode, String responseBody) {
        super(String.format("GitHub API error during %s: HTTP %d - %s", operation, statusCode, responseBody));
        this.statusCode = statusCode;
        this.responseBody = responseBody;
    }
    
    public int getStatusCode() {
        return statusCode;
    }
    
    public String getResponseBody() {
        return responseBody;
    }
    
    public boolean isNotFound() {
        return statusCode == 404;
    }
    
    public boolean isUnauthorized() {
        return statusCode == 401;
    }
    
    public boolean isForbidden() {
        return statusCode == 403;
    }
    
    public boolean isRateLimited() {
        return statusCode == 403 && responseBody != null && responseBody.contains("rate limit");
    }
}
