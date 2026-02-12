package org.rostilos.codecrow.core.dto.admin;

/**
 * GitLab OAuth credentials for self-hosted instances.
 * Used by VcsClientProvider for GitLab OAuth connections.
 */
public record GitLabSettingsDTO(
        String clientId,
        String clientSecret,
        String baseUrl
) {
    public static final String KEY_CLIENT_ID = "client-id";
    public static final String KEY_CLIENT_SECRET = "client-secret";
    public static final String KEY_BASE_URL = "base-url";
}
