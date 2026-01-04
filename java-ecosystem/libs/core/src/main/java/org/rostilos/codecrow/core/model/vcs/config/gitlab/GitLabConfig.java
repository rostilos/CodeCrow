package org.rostilos.codecrow.core.model.vcs.config.gitlab;

import com.fasterxml.jackson.annotation.JsonTypeName;
import org.rostilos.codecrow.core.model.vcs.config.VcsConnectionConfig;

import java.util.List;

/**
 * GitLab connection configuration.
 * Supports both GitLab.com and self-hosted GitLab instances.
 */
@JsonTypeName("gitlab")
public record GitLabConfig(
        String accessToken,
        String groupId,
        List<String> allowedRepos,
        String baseUrl  // For self-hosted GitLab instances (e.g., "https://gitlab.mycompany.com")
) implements VcsConnectionConfig {
    
    /**
     * Constructor for backward compatibility (without baseUrl).
     */
    public GitLabConfig(String accessToken, String groupId, List<String> allowedRepos) {
        this(accessToken, groupId, allowedRepos, null);
    }
    
    /**
     * Returns the effective base URL (defaults to gitlab.com if not specified).
     */
    public String effectiveBaseUrl() {
        return (baseUrl != null && !baseUrl.isBlank()) ? baseUrl : "https://gitlab.com";
    }
}
