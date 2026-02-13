package org.rostilos.codecrow.core.dto.admin;

/**
 * Base URL settings for public-facing URLs (webhook callbacks, frontend links, etc.).
 */
public record BaseUrlSettingsDTO(
        String baseUrl,
        String frontendUrl,
        String webhookBaseUrl
) {
    public static final String KEY_BASE_URL = "base-url";
    public static final String KEY_FRONTEND_URL = "frontend-url";
    public static final String KEY_WEBHOOK_BASE_URL = "webhook-base-url";
}
