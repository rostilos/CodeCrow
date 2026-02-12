package org.rostilos.codecrow.core.dto.admin;

/**
 * Google OAuth2 social-login credentials.
 */
public record GoogleOAuthSettingsDTO(
        String clientId
) {
    public static final String KEY_CLIENT_ID = "client-id";
}
