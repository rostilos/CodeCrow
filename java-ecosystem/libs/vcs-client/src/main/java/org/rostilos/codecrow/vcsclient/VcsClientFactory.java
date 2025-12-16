package org.rostilos.codecrow.vcsclient;

import okhttp3.OkHttpClient;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.BitbucketCloudClient;
import org.rostilos.codecrow.vcsclient.github.GitHubClient;

/**
 * Factory for creating VcsClient instances based on provider and connection configuration.
 */
public class VcsClientFactory {
    
    private final HttpAuthorizedClientFactory httpClientFactory;
    
    public VcsClientFactory(HttpAuthorizedClientFactory httpClientFactory) {
        this.httpClientFactory = httpClientFactory;
    }
    
    /**
     * Create a VcsClient for the given connection.
     * 
     * @param connection the VCS connection
     * @param accessToken decrypted access token
     * @param refreshToken decrypted refresh token (may be null)
     * @return appropriate VcsClient implementation
     */
    public VcsClient createClient(VcsConnection connection, String accessToken, String refreshToken) {
        EVcsProvider provider = connection.getProviderType();
        
        return switch (provider) {
            case BITBUCKET_CLOUD -> createBitbucketCloudClient(connection, accessToken, refreshToken);
            case BITBUCKET_SERVER -> throw new UnsupportedOperationException("Bitbucket Server not yet implemented");
            case GITHUB -> createGitHubClient(accessToken);
            case GITLAB -> throw new UnsupportedOperationException("GitLab not yet implemented");
        };
    }
    
    public VcsClient createClient(EVcsProvider provider, String accessToken, String refreshToken) {
        return switch (provider) {
            case BITBUCKET_CLOUD -> createBitbucketCloudClientFromTokens(accessToken, refreshToken);
            case BITBUCKET_SERVER -> throw new UnsupportedOperationException("Bitbucket Server not yet implemented");
            case GITHUB -> createGitHubClient(accessToken);
            case GITLAB -> throw new UnsupportedOperationException("GitLab not yet implemented");
        };
    }
    
    private BitbucketCloudClient createBitbucketCloudClient(VcsConnection connection, String accessToken, String refreshToken) {
        OkHttpClient httpClient = httpClientFactory.createClientWithBearerToken(accessToken);
        return new BitbucketCloudClient(httpClient, connection.getExternalWorkspaceSlug());
    }
    
    private BitbucketCloudClient createBitbucketCloudClientFromTokens(String accessToken, String refreshToken) {
        OkHttpClient httpClient = httpClientFactory.createClientWithBearerToken(accessToken);
        return new BitbucketCloudClient(httpClient);
    }

    private GitHubClient createGitHubClient(String accessToken) {
        OkHttpClient httpClient = httpClientFactory.createClientWithBearerToken(accessToken);
        return new GitHubClient(httpClient);
    }
    
    public VcsClient createClientWithOAuth(EVcsProvider provider, String oAuthKey, String oAuthSecret) {
        if (provider != EVcsProvider.BITBUCKET_CLOUD) {
            throw new UnsupportedOperationException("OAuth key/secret only supported for Bitbucket Cloud");
        }
        
        OkHttpClient httpClient = httpClientFactory.createClient(oAuthKey, oAuthSecret, provider.getId());
        return new BitbucketCloudClient(httpClient);
    }
}
