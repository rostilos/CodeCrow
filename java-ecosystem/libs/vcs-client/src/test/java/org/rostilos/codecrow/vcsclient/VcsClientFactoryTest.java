package org.rostilos.codecrow.vcsclient;

import okhttp3.OkHttpClient;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.BitbucketCloudClient;
import org.rostilos.codecrow.vcsclient.github.GitHubClient;
import org.rostilos.codecrow.vcsclient.gitlab.GitLabClient;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.*;

class VcsClientFactoryTest {

    private HttpAuthorizedClientFactory mockHttpClientFactory;
    private VcsClientFactory factory;
    private OkHttpClient mockHttpClient;

    @BeforeEach
    void setUp() {
        mockHttpClientFactory = mock(HttpAuthorizedClientFactory.class);
        mockHttpClient = mock(OkHttpClient.class);
        factory = new VcsClientFactory(mockHttpClientFactory);
        
        when(mockHttpClientFactory.createClientWithBearerToken(anyString())).thenReturn(mockHttpClient);
        when(mockHttpClientFactory.createClient(anyString(), anyString(), anyString())).thenReturn(mockHttpClient);
    }

    @Test
    void testCreateClient_GitHub_ReturnsGitHubClient() {
        VcsConnection connection = new VcsConnection();
        connection.setProviderType(EVcsProvider.GITHUB);

        VcsClient result = factory.createClient(connection, "github-token", null);

        assertThat(result).isInstanceOf(GitHubClient.class);
        verify(mockHttpClientFactory).createClientWithBearerToken("github-token");
    }

    @Test
    void testCreateClient_GitLab_ReturnsGitLabClient() {
        VcsConnection connection = new VcsConnection();
        connection.setProviderType(EVcsProvider.GITLAB);

        VcsClient result = factory.createClient(connection, "gitlab-token", null);

        assertThat(result).isInstanceOf(GitLabClient.class);
        verify(mockHttpClientFactory).createClientWithBearerToken("gitlab-token");
    }

    @Test
    void testCreateClient_BitbucketCloud_ReturnsBitbucketClient() {
        VcsConnection connection = new VcsConnection();
        connection.setProviderType(EVcsProvider.BITBUCKET_CLOUD);
        connection.setExternalWorkspaceSlug("my-workspace");

        VcsClient result = factory.createClient(connection, "bb-token", "refresh-token");

        assertThat(result).isInstanceOf(BitbucketCloudClient.class);
        verify(mockHttpClientFactory).createClientWithBearerToken("bb-token");
    }

    @Test
    void testCreateClient_BitbucketServer_ThrowsException() {
        VcsConnection connection = new VcsConnection();
        connection.setProviderType(EVcsProvider.BITBUCKET_SERVER);

        assertThatThrownBy(() -> factory.createClient(connection, "token", null))
                .isInstanceOf(UnsupportedOperationException.class)
                .hasMessageContaining("Bitbucket Server not yet implemented");
    }

    @Test
    void testCreateClientByProvider_GitHub_ReturnsGitHubClient() {
        VcsClient result = factory.createClient(EVcsProvider.GITHUB, "token", null);

        assertThat(result).isInstanceOf(GitHubClient.class);
    }

    @Test
    void testCreateClientByProvider_GitLab_ReturnsGitLabClient() {
        VcsClient result = factory.createClient(EVcsProvider.GITLAB, "token", null);

        assertThat(result).isInstanceOf(GitLabClient.class);
    }

    @Test
    void testCreateClientByProvider_BitbucketCloud_ReturnsBitbucketClient() {
        VcsClient result = factory.createClient(EVcsProvider.BITBUCKET_CLOUD, "token", "refresh");

        assertThat(result).isInstanceOf(BitbucketCloudClient.class);
    }

    @Test
    void testCreateClientWithOAuth_BitbucketCloud_Success() {
        VcsClient result = factory.createClientWithOAuth(EVcsProvider.BITBUCKET_CLOUD, "key", "secret");

        assertThat(result).isInstanceOf(BitbucketCloudClient.class);
        verify(mockHttpClientFactory).createClient("key", "secret", EVcsProvider.BITBUCKET_CLOUD.getId());
    }

    @Test
    void testCreateClientWithOAuth_GitHub_ThrowsException() {
        assertThatThrownBy(() -> factory.createClientWithOAuth(EVcsProvider.GITHUB, "key", "secret"))
                .isInstanceOf(UnsupportedOperationException.class)
                .hasMessageContaining("OAuth key/secret only supported for Bitbucket Cloud");
    }

    @Test
    void testCreateClientWithOAuth_GitLab_ThrowsException() {
        assertThatThrownBy(() -> factory.createClientWithOAuth(EVcsProvider.GITLAB, "key", "secret"))
                .isInstanceOf(UnsupportedOperationException.class);
    }
}
