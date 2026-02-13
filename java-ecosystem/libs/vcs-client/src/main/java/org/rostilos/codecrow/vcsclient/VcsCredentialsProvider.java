package org.rostilos.codecrow.vcsclient;

/**
 * Abstraction for providing VCS provider credentials to {@link VcsClientProvider}.
 * <p>
 * In community/self-hosted mode, the implementation reads from the site_settings DB table.
 * In cloud/SaaS mode, the implementation reads from {@code @Value} application properties.
 * <p>
 * This interface lives in the vcs-client lib so VcsClientProvider can depend on it
 * without creating a circular dependency with web-server.
 */
public interface VcsCredentialsProvider {

    // ── Bitbucket ──
    String getBitbucketAppClientId();
    String getBitbucketAppClientSecret();

    // ── GitHub ──
    String getGitHubAppId();
    String getGitHubAppPrivateKeyPath();
    String getGitHubAppWebhookSecret();
    String getGitHubAppSlug();

    // ── GitLab ──
    String getGitLabOAuthClientId();
    String getGitLabOAuthClientSecret();
    String getGitLabBaseUrl();
}
