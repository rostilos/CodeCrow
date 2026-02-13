package org.rostilos.codecrow.core.dto.admin;

/**
 * GitHub App credentials for self-hosted instances.
 * Used by VcsClientProvider for GitHub APP connections.
 */
public record GitHubSettingsDTO(
        String appId,
        String privateKeyPath,
        String webhookSecret,
        String slug
) {
    public static final String KEY_APP_ID = "app-id";
    public static final String KEY_PRIVATE_KEY_PATH = "private-key-path";
    public static final String KEY_WEBHOOK_SECRET = "webhook-secret";
    public static final String KEY_SLUG = "slug";
}
