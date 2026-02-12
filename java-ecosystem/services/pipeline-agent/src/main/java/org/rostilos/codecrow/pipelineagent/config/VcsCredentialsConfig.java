package org.rostilos.codecrow.pipelineagent.config;

import org.rostilos.codecrow.vcsclient.VcsCredentialsProvider;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * Simple {@code @Value}-based implementation of {@link VcsCredentialsProvider}
 * for the pipeline-agent service.
 * <p>
 * The pipeline-agent reads VCS credentials directly from the shared
 * {@code application.properties} (mounted from {@code java-shared/}).
 * Unlike the web-server, it does not have the admin-settings DB infrastructure.
 */
@Component
public class VcsCredentialsConfig implements VcsCredentialsProvider {

    @Value("${codecrow.bitbucket.app.client-id:}")
    private String bitbucketAppClientId;

    @Value("${codecrow.bitbucket.app.client-secret:}")
    private String bitbucketAppClientSecret;

    @Value("${codecrow.github.app.id:}")
    private String githubAppId;

    @Value("${codecrow.github.app.private-key-path:}")
    private String githubAppPrivateKeyPath;

    @Value("${codecrow.gitlab.oauth.client-id:}")
    private String gitlabOAuthClientId;

    @Value("${codecrow.gitlab.oauth.client-secret:}")
    private String gitlabOAuthClientSecret;

    @Value("${codecrow.gitlab.base-url:https://gitlab.com}")
    private String gitlabBaseUrl;

    @Override
    public String getBitbucketAppClientId() {
        return bitbucketAppClientId;
    }

    @Override
    public String getBitbucketAppClientSecret() {
        return bitbucketAppClientSecret;
    }

    @Override
    public String getGitHubAppId() {
        return githubAppId;
    }

    @Override
    public String getGitHubAppPrivateKeyPath() {
        return githubAppPrivateKeyPath;
    }

    @Override
    public String getGitLabOAuthClientId() {
        return gitlabOAuthClientId;
    }

    @Override
    public String getGitLabOAuthClientSecret() {
        return gitlabOAuthClientSecret;
    }

    @Override
    public String getGitLabBaseUrl() {
        return gitlabBaseUrl;
    }
}
