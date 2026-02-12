package org.rostilos.codecrow.core.dto.admin;

/**
 * GitHub App credentials for self-hosted instances.
 * Used by VcsClientProvider for GitHub APP connections.
 */
public record GitHubSettingsDTO(
        String appId,
        String privateKeyPath
) {
    public static final String KEY_APP_ID = "app-id";
    public static final String KEY_PRIVATE_KEY_PATH = "private-key-path";
}
