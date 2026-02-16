package org.rostilos.codecrow.core.dto.admin;

/**
 * Bitbucket Connect App credentials for workspace-based integration (SaaS).
 * Separate from the standard Bitbucket OAuth Consumer credentials.
 */
public record BitbucketConnectSettingsDTO(
        String clientId,
        String clientSecret
) {
    /**
     * Property keys stored in site_settings table.
     */
    public static final String KEY_CLIENT_ID = "client-id";
    public static final String KEY_CLIENT_SECRET = "client-secret";
}
