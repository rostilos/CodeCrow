package org.rostilos.codecrow.core.dto.admin;

/**
 * GitHub App credentials for self-hosted instances.
 * Used by VcsClientProvider for GitHub APP connections.
 * Also holds legacy OAuth client-id / client-secret used for the OAuth code flow.
 *
 * <p>{@code privateKeyContent} stores the raw PEM text in the database so that
 * <em>every</em> service (web-server <b>and</b> pipeline-agent) can load the
 * private key without depending on a shared filesystem mount.
 * The field is encrypted at rest (see {@code SiteSettingsProvider.SECRET_KEYS}).
 */
public record GitHubSettingsDTO(
        String appId,
        String privateKeyPath,
        String privateKeyContent,
        String webhookSecret,
        String slug,
        String oauthClientId,
        String oauthClientSecret
) {
    public static final String KEY_APP_ID = "app-id";
    public static final String KEY_PRIVATE_KEY_PATH = "private-key-path";
    public static final String KEY_PRIVATE_KEY_CONTENT = "private-key-content";
    public static final String KEY_WEBHOOK_SECRET = "webhook-secret";
    public static final String KEY_SLUG = "slug";
    public static final String KEY_OAUTH_CLIENT_ID = "oauth-client-id";
    public static final String KEY_OAUTH_CLIENT_SECRET = "oauth-client-secret";
}
