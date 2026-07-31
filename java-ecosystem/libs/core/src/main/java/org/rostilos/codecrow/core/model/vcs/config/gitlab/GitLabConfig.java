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
    public static final String DEFAULT_BASE_URL = "https://gitlab.com";
    
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
        return normalizeBaseUrl(baseUrl);
    }

    /**
     * Normalize a persisted or process-provided GitLab instance root.
     */
    public static String normalizeBaseUrl(String baseUrl) {
        if (baseUrl == null || baseUrl.isBlank()) {
            return DEFAULT_BASE_URL;
        }

        String normalized = baseUrl.trim().replaceAll("/+$", "");
        if (normalized.endsWith("/api/v4")) {
            normalized = normalized.substring(0, normalized.length() - "/api/v4".length());
        }
        return normalized.isBlank() ? DEFAULT_BASE_URL : normalized;
    }
}
