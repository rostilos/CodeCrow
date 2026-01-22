package org.rostilos.codecrow.vcsclient.utils;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.config.cloud.BitbucketCloudConfig;
import org.rostilos.codecrow.core.model.vcs.config.github.GitHubConfig;
import org.rostilos.codecrow.core.model.vcs.config.gitlab.GitLabConfig;
import org.rostilos.codecrow.security.oauth.TokenEncryptionService;

import java.security.GeneralSecurityException;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class VcsConnectionCredentialsExtractorTest {

    @Mock
    private TokenEncryptionService tokenEncryptionService;

    @Mock
    private VcsConnection vcsConnection;

    private VcsConnectionCredentialsExtractor extractor;

    @BeforeEach
    void setUp() {
        extractor = new VcsConnectionCredentialsExtractor(tokenEncryptionService);
    }

    @Test
    void testExtractCredentials_NullConnection_ThrowsException() {
        assertThatThrownBy(() -> extractor.extractCredentials(null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("VcsConnection cannot be null");
    }

    @Test
    void testExtractCredentials_NullProvider_ThrowsException() {
        when(vcsConnection.getProviderType()).thenReturn(null);
        when(vcsConnection.getConnectionType()).thenReturn(EVcsConnectionType.APP);

        assertThatThrownBy(() -> extractor.extractCredentials(vcsConnection))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("provider type cannot be null");
    }

    @Test
    void testExtractCredentials_NullConnectionType_ThrowsException() {
        when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.GITHUB);
        when(vcsConnection.getConnectionType()).thenReturn(null);

        assertThatThrownBy(() -> extractor.extractCredentials(vcsConnection))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("type cannot be null");
    }

    @Test
    void testExtractCredentials_OAuthManual_BitbucketCloud() throws GeneralSecurityException {
        BitbucketCloudConfig config = new BitbucketCloudConfig("encrypted-key", "encrypted-token", "workspace-id");
        when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
        when(vcsConnection.getConnectionType()).thenReturn(EVcsConnectionType.OAUTH_MANUAL);
        when(vcsConnection.getConfiguration()).thenReturn(config);
        when(tokenEncryptionService.decrypt("encrypted-key")).thenReturn("decrypted-key");
        when(tokenEncryptionService.decrypt("encrypted-token")).thenReturn("decrypted-token");

        VcsConnectionCredentialsExtractor.VcsConnectionCredentials credentials = extractor.extractCredentials(vcsConnection);

        assertThat(credentials.oAuthClient()).isEqualTo("decrypted-key");
        assertThat(credentials.oAuthSecret()).isEqualTo("decrypted-token");
        assertThat(credentials.vcsProviderString()).isEqualTo("bitbucket_cloud");
        assertThat(credentials.provider()).isEqualTo(EVcsProvider.BITBUCKET_CLOUD);
    }

    @Test
    void testExtractCredentials_App_UsesStoredAccessToken() throws GeneralSecurityException {
        when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
        when(vcsConnection.getConnectionType()).thenReturn(EVcsConnectionType.APP);
        when(vcsConnection.getAccessToken()).thenReturn("encrypted-access-token");
        when(tokenEncryptionService.decrypt("encrypted-access-token")).thenReturn("decrypted-access-token");

        VcsConnectionCredentialsExtractor.VcsConnectionCredentials credentials = extractor.extractCredentials(vcsConnection);

        assertThat(credentials.accessToken()).isEqualTo("decrypted-access-token");
        assertThat(credentials.vcsProviderString()).isEqualTo("bitbucket_cloud");
    }

    @Test
    void testExtractCredentials_GitHubApp_UsesStoredAccessToken() throws GeneralSecurityException {
        when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.GITHUB);
        when(vcsConnection.getConnectionType()).thenReturn(EVcsConnectionType.GITHUB_APP);
        when(vcsConnection.getAccessToken()).thenReturn("encrypted-token");
        when(tokenEncryptionService.decrypt("encrypted-token")).thenReturn("decrypted-token");

        VcsConnectionCredentialsExtractor.VcsConnectionCredentials credentials = extractor.extractCredentials(vcsConnection);

        assertThat(credentials.accessToken()).isEqualTo("decrypted-token");
        assertThat(credentials.vcsProviderString()).isEqualTo("github");
    }

    @Test
    void testExtractCredentials_PersonalToken_GitHubConfig() throws GeneralSecurityException {
        GitHubConfig config = new GitHubConfig("my-token", "org", List.of("repo1", "repo2"));
        when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.GITHUB);
        when(vcsConnection.getConnectionType()).thenReturn(EVcsConnectionType.PERSONAL_TOKEN);
        when(vcsConnection.getConfiguration()).thenReturn(config);

        VcsConnectionCredentialsExtractor.VcsConnectionCredentials credentials = extractor.extractCredentials(vcsConnection);

        assertThat(credentials.accessToken()).isEqualTo("my-token");
        assertThat(credentials.vcsProviderString()).isEqualTo("github");
    }

    @Test
    void testExtractCredentials_PersonalToken_GitLabConfig() throws GeneralSecurityException {
        GitLabConfig config = new GitLabConfig("gitlab-token", "my-group", List.of(), "https://gitlab.com");
        when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.GITLAB);
        when(vcsConnection.getConnectionType()).thenReturn(EVcsConnectionType.PERSONAL_TOKEN);
        when(vcsConnection.getConfiguration()).thenReturn(config);

        VcsConnectionCredentialsExtractor.VcsConnectionCredentials credentials = extractor.extractCredentials(vcsConnection);

        assertThat(credentials.accessToken()).isEqualTo("gitlab-token");
        assertThat(credentials.vcsProviderString()).isEqualTo("gitlab");
    }

    @Test
    void testGetVcsProviderString_GitHub() {
        assertThat(VcsConnectionCredentialsExtractor.getVcsProviderString(EVcsProvider.GITHUB)).isEqualTo("github");
    }

    @Test
    void testGetVcsProviderString_GitLab() {
        assertThat(VcsConnectionCredentialsExtractor.getVcsProviderString(EVcsProvider.GITLAB)).isEqualTo("gitlab");
    }

    @Test
    void testGetVcsProviderString_BitbucketCloud() {
        assertThat(VcsConnectionCredentialsExtractor.getVcsProviderString(EVcsProvider.BITBUCKET_CLOUD)).isEqualTo("bitbucket_cloud");
    }

    @Test
    void testGetVcsProviderString_BitbucketServer() {
        assertThat(VcsConnectionCredentialsExtractor.getVcsProviderString(EVcsProvider.BITBUCKET_SERVER)).isEqualTo("bitbucket_server");
    }

    @Test
    void testGetVcsProviderString_Null_ReturnsDefault() {
        assertThat(VcsConnectionCredentialsExtractor.getVcsProviderString(null)).isEqualTo("bitbucket_cloud");
    }

    @Test
    void testHasOAuthCredentials_WithBothCredentials_ReturnsTrue() {
        VcsConnectionCredentialsExtractor.VcsConnectionCredentials credentials =
                new VcsConnectionCredentialsExtractor.VcsConnectionCredentials("client", "secret", null, null, null, null);

        assertThat(VcsConnectionCredentialsExtractor.hasOAuthCredentials(credentials)).isTrue();
        assertThat(credentials.hasOAuthCredentials()).isTrue();
    }

    @Test
    void testHasOAuthCredentials_WithBlankClient_ReturnsFalse() {
        VcsConnectionCredentialsExtractor.VcsConnectionCredentials credentials =
                new VcsConnectionCredentialsExtractor.VcsConnectionCredentials("", "secret", null, null, null, null);

        assertThat(VcsConnectionCredentialsExtractor.hasOAuthCredentials(credentials)).isFalse();
    }

    @Test
    void testHasOAuthCredentials_WithNullSecret_ReturnsFalse() {
        VcsConnectionCredentialsExtractor.VcsConnectionCredentials credentials =
                new VcsConnectionCredentialsExtractor.VcsConnectionCredentials("client", null, null, null, null, null);

        assertThat(VcsConnectionCredentialsExtractor.hasOAuthCredentials(credentials)).isFalse();
    }

    @Test
    void testHasAccessToken_WithToken_ReturnsTrue() {
        VcsConnectionCredentialsExtractor.VcsConnectionCredentials credentials =
                new VcsConnectionCredentialsExtractor.VcsConnectionCredentials(null, null, "token", null, null, null);

        assertThat(VcsConnectionCredentialsExtractor.hasAccessToken(credentials)).isTrue();
        assertThat(credentials.hasAccessToken()).isTrue();
    }

    @Test
    void testHasAccessToken_WithBlankToken_ReturnsFalse() {
        VcsConnectionCredentialsExtractor.VcsConnectionCredentials credentials =
                new VcsConnectionCredentialsExtractor.VcsConnectionCredentials(null, null, "  ", null, null, null);

        assertThat(VcsConnectionCredentialsExtractor.hasAccessToken(credentials)).isFalse();
    }

    @Test
    void testHasValidCredentials_WithOAuth_ReturnsTrue() {
        VcsConnectionCredentialsExtractor.VcsConnectionCredentials credentials =
                new VcsConnectionCredentialsExtractor.VcsConnectionCredentials("client", "secret", null, null, null, null);

        assertThat(VcsConnectionCredentialsExtractor.hasValidCredentials(credentials)).isTrue();
        assertThat(credentials.hasValidCredentials()).isTrue();
    }

    @Test
    void testHasValidCredentials_WithAccessToken_ReturnsTrue() {
        VcsConnectionCredentialsExtractor.VcsConnectionCredentials credentials =
                new VcsConnectionCredentialsExtractor.VcsConnectionCredentials(null, null, "token", null, null, null);

        assertThat(VcsConnectionCredentialsExtractor.hasValidCredentials(credentials)).isTrue();
    }

    @Test
    void testHasValidCredentials_WithNoCredentials_ReturnsFalse() {
        VcsConnectionCredentialsExtractor.VcsConnectionCredentials credentials =
                new VcsConnectionCredentialsExtractor.VcsConnectionCredentials(null, null, null, null, null, null);

        assertThat(VcsConnectionCredentialsExtractor.hasValidCredentials(credentials)).isFalse();
    }

    @Test
    void testVcsConnectionCredentials_BackwardsCompatibleConstructor() {
        VcsConnectionCredentialsExtractor.VcsConnectionCredentials credentials =
                new VcsConnectionCredentialsExtractor.VcsConnectionCredentials("client", "secret", "token");

        assertThat(credentials.oAuthClient()).isEqualTo("client");
        assertThat(credentials.oAuthSecret()).isEqualTo("secret");
        assertThat(credentials.accessToken()).isEqualTo("token");
        assertThat(credentials.vcsProviderString()).isNull();
        assertThat(credentials.provider()).isNull();
        assertThat(credentials.connectionType()).isNull();
    }
}
