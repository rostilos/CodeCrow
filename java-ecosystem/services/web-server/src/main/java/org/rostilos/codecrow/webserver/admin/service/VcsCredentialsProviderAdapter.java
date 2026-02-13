package org.rostilos.codecrow.webserver.admin.service;

import org.rostilos.codecrow.core.dto.admin.BitbucketSettingsDTO;
import org.rostilos.codecrow.core.dto.admin.GitHubSettingsDTO;
import org.rostilos.codecrow.core.dto.admin.GitLabSettingsDTO;
import org.rostilos.codecrow.vcsclient.VcsCredentialsProvider;
import org.springframework.stereotype.Component;

/**
 * Bridges {@link VcsCredentialsProvider} (vcs-client lib) to {@link ISiteSettingsProvider} (web-server).
 * This adapter allows VcsClientProvider to read credentials without depending on web-server.
 */
@Component
public class VcsCredentialsProviderAdapter implements VcsCredentialsProvider {

    private final ISiteSettingsProvider settingsProvider;

    public VcsCredentialsProviderAdapter(ISiteSettingsProvider settingsProvider) {
        this.settingsProvider = settingsProvider;
    }

    @Override
    public String getBitbucketAppClientId() {
        BitbucketSettingsDTO s = settingsProvider.getBitbucketSettings();
        return s != null ? s.clientId() : "";
    }

    @Override
    public String getBitbucketAppClientSecret() {
        BitbucketSettingsDTO s = settingsProvider.getBitbucketSettings();
        return s != null ? s.clientSecret() : "";
    }

    @Override
    public String getGitHubAppId() {
        GitHubSettingsDTO s = settingsProvider.getGitHubSettings();
        return s != null ? s.appId() : "";
    }

    @Override
    public String getGitHubAppPrivateKeyPath() {
        GitHubSettingsDTO s = settingsProvider.getGitHubSettings();
        return s != null ? s.privateKeyPath() : "";
    }

    @Override
    public String getGitHubAppWebhookSecret() {
        GitHubSettingsDTO s = settingsProvider.getGitHubSettings();
        return s != null ? s.webhookSecret() : "";
    }

    @Override
    public String getGitHubAppSlug() {
        GitHubSettingsDTO s = settingsProvider.getGitHubSettings();
        return s != null ? s.slug() : "";
    }

    @Override
    public String getGitLabOAuthClientId() {
        GitLabSettingsDTO s = settingsProvider.getGitLabSettings();
        return s != null ? s.clientId() : "";
    }

    @Override
    public String getGitLabOAuthClientSecret() {
        GitLabSettingsDTO s = settingsProvider.getGitLabSettings();
        return s != null ? s.clientSecret() : "";
    }

    @Override
    public String getGitLabBaseUrl() {
        GitLabSettingsDTO s = settingsProvider.getGitLabSettings();
        return s != null ? s.baseUrl() : "https://gitlab.com";
    }
}
