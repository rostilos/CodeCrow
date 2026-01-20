package org.rostilos.codecrow.core.model.vcs;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.vcs.config.VcsConnectionConfig;
import org.rostilos.codecrow.core.model.workspace.Workspace;

import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

@DisplayName("VcsConnection")
class VcsConnectionTest {

    private VcsConnection connection;

    @BeforeEach
    void setUp() {
        connection = new VcsConnection();
    }

    @Nested
    @DisplayName("Basic Getters/Setters")
    class BasicGettersSetters {

        @Test
        @DisplayName("should set and get id")
        void shouldSetAndGetId() {
            connection.setId(1L);
            assertThat(connection.getId()).isEqualTo(1L);
        }

        @Test
        @DisplayName("should set and get workspace")
        void shouldSetAndGetWorkspace() {
            Workspace workspace = new Workspace();
            connection.setWorkspace(workspace);
            assertThat(connection.getWorkspace()).isSameAs(workspace);
        }

        @Test
        @DisplayName("should set and get connectionName")
        void shouldSetAndGetConnectionName() {
            connection.setConnectionName("GitHub - MyOrg");
            assertThat(connection.getConnectionName()).isEqualTo("GitHub - MyOrg");
        }

        @Test
        @DisplayName("should set and get setupStatus")
        void shouldSetAndGetSetupStatus() {
            connection.setSetupStatus(EVcsSetupStatus.CONNECTED);
            assertThat(connection.getSetupStatus()).isEqualTo(EVcsSetupStatus.CONNECTED);
        }

        @Test
        @DisplayName("should set and get providerType")
        void shouldSetAndGetProviderType() {
            connection.setProviderType(EVcsProvider.GITHUB);
            assertThat(connection.getProviderType()).isEqualTo(EVcsProvider.GITHUB);
        }

        @Test
        @DisplayName("should set and get connectionType")
        void shouldSetAndGetConnectionType() {
            connection.setConnectionType(EVcsConnectionType.APP);
            assertThat(connection.getConnectionType()).isEqualTo(EVcsConnectionType.APP);
        }

        @Test
        @DisplayName("should set and get externalWorkspaceId")
        void shouldSetAndGetExternalWorkspaceId() {
            connection.setExternalWorkspaceId("org-123");
            assertThat(connection.getExternalWorkspaceId()).isEqualTo("org-123");
        }

        @Test
        @DisplayName("should set and get externalWorkspaceSlug")
        void shouldSetAndGetExternalWorkspaceSlug() {
            connection.setExternalWorkspaceSlug("my-org");
            assertThat(connection.getExternalWorkspaceSlug()).isEqualTo("my-org");
        }

        @Test
        @DisplayName("should set and get installationId")
        void shouldSetAndGetInstallationId() {
            connection.setInstallationId("inst-456");
            assertThat(connection.getInstallationId()).isEqualTo("inst-456");
        }

        @Test
        @DisplayName("should set and get repositoryPath")
        void shouldSetAndGetRepositoryPath() {
            connection.setRepositoryPath("owner/repo");
            assertThat(connection.getRepositoryPath()).isEqualTo("owner/repo");
        }

        @Test
        @DisplayName("should set and get accessToken")
        void shouldSetAndGetAccessToken() {
            connection.setAccessToken("token-abc");
            assertThat(connection.getAccessToken()).isEqualTo("token-abc");
        }

        @Test
        @DisplayName("should set and get refreshToken")
        void shouldSetAndGetRefreshToken() {
            connection.setRefreshToken("refresh-xyz");
            assertThat(connection.getRefreshToken()).isEqualTo("refresh-xyz");
        }

        @Test
        @DisplayName("should set and get tokenExpiresAt")
        void shouldSetAndGetTokenExpiresAt() {
            LocalDateTime expires = LocalDateTime.now().plusHours(1);
            connection.setTokenExpiresAt(expires);
            assertThat(connection.getTokenExpiresAt()).isEqualTo(expires);
        }

        @Test
        @DisplayName("should set and get scopes")
        void shouldSetAndGetScopes() {
            connection.setScopes("repo,user");
            assertThat(connection.getScopes()).isEqualTo("repo,user");
        }

        @Test
        @DisplayName("should set and get repoCount")
        void shouldSetAndGetRepoCount() {
            connection.setRepoCount(42);
            assertThat(connection.getRepoCount()).isEqualTo(42);
        }

        @Test
        @DisplayName("should set and get configuration")
        void shouldSetAndGetConfiguration() {
            // VcsConnectionConfig is a sealed interface, use a concrete implementation
            org.rostilos.codecrow.core.model.vcs.config.github.GitHubConfig config = 
                new org.rostilos.codecrow.core.model.vcs.config.github.GitHubConfig("token", "org", null);
            connection.setConfiguration(config);
            assertThat(connection.getConfiguration()).isSameAs(config);
        }
    }

    @Nested
    @DisplayName("hasOAuthTokens()")
    class HasOAuthTokensTests {

        @Test
        @DisplayName("should return false when accessToken is null")
        void shouldReturnFalseWhenNull() {
            connection.setAccessToken(null);
            assertThat(connection.hasOAuthTokens()).isFalse();
        }

        @Test
        @DisplayName("should return false when accessToken is blank")
        void shouldReturnFalseWhenBlank() {
            connection.setAccessToken("  ");
            assertThat(connection.hasOAuthTokens()).isFalse();
        }

        @Test
        @DisplayName("should return false when accessToken is empty")
        void shouldReturnFalseWhenEmpty() {
            connection.setAccessToken("");
            assertThat(connection.hasOAuthTokens()).isFalse();
        }

        @Test
        @DisplayName("should return true when accessToken is present")
        void shouldReturnTrueWhenPresent() {
            connection.setAccessToken("valid-token");
            assertThat(connection.hasOAuthTokens()).isTrue();
        }
    }

    @Nested
    @DisplayName("isTokenExpired()")
    class IsTokenExpiredTests {

        @Test
        @DisplayName("should return false when tokenExpiresAt is null")
        void shouldReturnFalseWhenNull() {
            connection.setTokenExpiresAt(null);
            assertThat(connection.isTokenExpired()).isFalse();
        }

        @Test
        @DisplayName("should return false when token is not expired")
        void shouldReturnFalseWhenNotExpired() {
            connection.setTokenExpiresAt(LocalDateTime.now().plusHours(1));
            assertThat(connection.isTokenExpired()).isFalse();
        }

        @Test
        @DisplayName("should return true when token is expired")
        void shouldReturnTrueWhenExpired() {
            connection.setTokenExpiresAt(LocalDateTime.now().minusHours(1));
            assertThat(connection.isTokenExpired()).isTrue();
        }
    }

    @Nested
    @DisplayName("Provider Types")
    class ProviderTypeTests {

        @Test
        @DisplayName("should support GITHUB provider")
        void shouldSupportGitHub() {
            connection.setProviderType(EVcsProvider.GITHUB);
            assertThat(connection.getProviderType()).isEqualTo(EVcsProvider.GITHUB);
        }

        @Test
        @DisplayName("should support GITLAB provider")
        void shouldSupportGitLab() {
            connection.setProviderType(EVcsProvider.GITLAB);
            assertThat(connection.getProviderType()).isEqualTo(EVcsProvider.GITLAB);
        }

        @Test
        @DisplayName("should support BITBUCKET_CLOUD provider")
        void shouldSupportBitbucketCloud() {
            connection.setProviderType(EVcsProvider.BITBUCKET_CLOUD);
            assertThat(connection.getProviderType()).isEqualTo(EVcsProvider.BITBUCKET_CLOUD);
        }

        @Test
        @DisplayName("should support BITBUCKET_SERVER provider")
        void shouldSupportBitbucketServer() {
            connection.setProviderType(EVcsProvider.BITBUCKET_SERVER);
            assertThat(connection.getProviderType()).isEqualTo(EVcsProvider.BITBUCKET_SERVER);
        }
    }

    @Nested
    @DisplayName("Connection Types")
    class ConnectionTypeTests {

        @Test
        @DisplayName("should support APP connection type")
        void shouldSupportApp() {
            connection.setConnectionType(EVcsConnectionType.APP);
            assertThat(connection.getConnectionType()).isEqualTo(EVcsConnectionType.APP);
        }

        @Test
        @DisplayName("should support OAUTH_MANUAL connection type")
        void shouldSupportOAuthManual() {
            connection.setConnectionType(EVcsConnectionType.OAUTH_MANUAL);
            assertThat(connection.getConnectionType()).isEqualTo(EVcsConnectionType.OAUTH_MANUAL);
        }

        @Test
        @DisplayName("should support REPOSITORY_TOKEN connection type")
        void shouldSupportRepositoryToken() {
            connection.setConnectionType(EVcsConnectionType.REPOSITORY_TOKEN);
            assertThat(connection.getConnectionType()).isEqualTo(EVcsConnectionType.REPOSITORY_TOKEN);
        }
    }

    @Nested
    @DisplayName("Setup Status")
    class SetupStatusTests {

        @Test
        @DisplayName("should support CONNECTED status")
        void shouldSupportConnected() {
            connection.setSetupStatus(EVcsSetupStatus.CONNECTED);
            assertThat(connection.getSetupStatus()).isEqualTo(EVcsSetupStatus.CONNECTED);
        }

        @Test
        @DisplayName("should support PENDING status")
        void shouldSupportPending() {
            connection.setSetupStatus(EVcsSetupStatus.PENDING);
            assertThat(connection.getSetupStatus()).isEqualTo(EVcsSetupStatus.PENDING);
        }

        @Test
        @DisplayName("should support ERROR status")
        void shouldSupportError() {
            connection.setSetupStatus(EVcsSetupStatus.ERROR);
            assertThat(connection.getSetupStatus()).isEqualTo(EVcsSetupStatus.ERROR);
        }

        @Test
        @DisplayName("should support DISABLED status")
        void shouldSupportDisabled() {
            connection.setSetupStatus(EVcsSetupStatus.DISABLED);
            assertThat(connection.getSetupStatus()).isEqualTo(EVcsSetupStatus.DISABLED);
        }
    }

    @Nested
    @DisplayName("Timestamps")
    class TimestampTests {

        @Test
        @DisplayName("should have null createdAt by default")
        void shouldHaveNullCreatedAtByDefault() {
            assertThat(connection.getCreatedAt()).isNull();
        }

        @Test
        @DisplayName("should have null updatedAt by default")
        void shouldHaveNullUpdatedAtByDefault() {
            assertThat(connection.getUpdatedAt()).isNull();
        }
    }
}
