package org.rostilos.codecrow.core.dto.admin;

/**
 * Bitbucket OAuth Consumer credentials for self-hosted instances.
 * Used by VcsClientProvider for Bitbucket APP/OAuth connections.
 */
public record BitbucketSettingsDTO(
        String clientId,
        String clientSecret
) {
    /**
     * Property keys stored in site_settings table.
     */
    public static final String KEY_CLIENT_ID = "client-id";
    public static final String KEY_CLIENT_SECRET = "client-secret";
}
