package org.rostilos.codecrow.vcsclient.gitlab;

/**
 * Configuration constants for GitLab API access.
 */
public final class GitLabConfig {
    
    public static final String API_BASE = "https://gitlab.com/api/v4";
    public static final int DEFAULT_PAGE_SIZE = 20;
    
    private GitLabConfig() {
        // Utility class
    }
}
