package org.rostilos.codecrow.webserver.publicshare;

/**
 * Generic public-share transport envelope. The content is always the provider's
 * sanitized public DTO. The optional path is emitted only after normal tenant
 * authorization succeeds for the authenticated principal.
 */
public record PublicSharePreviewResponse(
        String resourceType,
        Object content,
        String authorizedPath
) {
}
