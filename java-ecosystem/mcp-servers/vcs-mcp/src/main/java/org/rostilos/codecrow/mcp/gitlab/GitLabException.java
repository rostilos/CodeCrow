package org.rostilos.codecrow.mcp.gitlab;

/**
 * Exception for GitLab MCP operations.
 */
public class GitLabException extends RuntimeException {
    
    public GitLabException(String message) {
        super(message);
    }
    
    public GitLabException(String message, Throwable cause) {
        super(message, cause);
    }
}
