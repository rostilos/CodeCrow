package org.rostilos.codecrow.vcsclient.gitlab;

import java.time.LocalDateTime;

/**
 * Tokens returned by GitLab's OAuth endpoint.
 */
public record GitLabOAuthTokens(
        String accessToken,
        String refreshToken,
        LocalDateTime expiresAt,
        String scopes
) {
}
