package org.rostilos.codecrow.core.dto.admin;

/**
 * GitHub App credentials for self-hosted instances.
 * Used by VcsClientProvider for GitHub APP connections.
 * Also holds legacy OAuth client-id / client-secret used for the OAuth code flow.
 */
public record GitHubSettingsDTO(
        String appId,
        String privateKeyPath,
        String webhookSecret,
        String slug,
        String oauthClientId,
        String oauthClientSecret
) {
    public static final String KEY_APP_ID = "app-id";
    public static final String KEY_PRIVATE_KEY_PATH = "private-key-path";
    public static final String KEY_WEBHOOK_SECRET = "webhook-secret";
    public static final String KEY_SLUG = "slug";
    public static final String KEY_OAUTH_CLIENT_ID = "oauth-client-id";
    public static final String KEY_OAUTH_CLIENT_SECRET = "oauth-client-secret";
}
