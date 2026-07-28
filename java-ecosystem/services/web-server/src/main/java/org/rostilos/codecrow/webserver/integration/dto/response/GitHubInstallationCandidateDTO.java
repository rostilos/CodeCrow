package org.rostilos.codecrow.webserver.integration.dto.response;

import org.rostilos.codecrow.vcsclient.github.GitHubAppAuthService;

/**
 * An already-installed GitHub App installation that the verified requester may
 * explicitly select for a pending CodeCrow connection.
 */
public record GitHubInstallationCandidateDTO(
        long installationId,
        String accountLogin,
        String accountType,
        String accountAvatarUrl
) {
    public static GitHubInstallationCandidateDTO from(
            GitHubAppAuthService.InstallationInfo installation) {
        return new GitHubInstallationCandidateDTO(
                installation.installationId(),
                installation.accountLogin(),
                installation.accountType(),
                installation.accountAvatarUrl());
    }
}
