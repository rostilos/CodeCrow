package org.rostilos.codecrow.core.dto.github;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.EVcsSetupStatus;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.config.github.GitHubConfig;

import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

@DisplayName("GitHubDTO Tests")
class GitHubDTOTest {

    @Test
    @DisplayName("Record should store all fields correctly")
    void recordShouldStoreAllFieldsCorrectly() {
        LocalDateTime now = LocalDateTime.now();
        GitHubDTO dto = new GitHubDTO(
                1L,
                "github-connection",
                "my-org",
                20,
                EVcsSetupStatus.CONNECTED,
                true,
                now
        );

        assertThat(dto.id()).isEqualTo(1L);
        assertThat(dto.connectionName()).isEqualTo("github-connection");
        assertThat(dto.organizationId()).isEqualTo("my-org");
        assertThat(dto.repoCount()).isEqualTo(20);
        assertThat(dto.setupStatus()).isEqualTo(EVcsSetupStatus.CONNECTED);
        assertThat(dto.hasAccessToken()).isTrue();
        assertThat(dto.updatedAt()).isEqualTo(now);
    }

    @Test
    @DisplayName("Records with same values should be equal")
    void recordsWithSameValuesShouldBeEqual() {
        LocalDateTime now = LocalDateTime.now();
        GitHubDTO dto1 = new GitHubDTO(
                1L, "name", "org", 5, EVcsSetupStatus.CONNECTED, true, now
        );
        GitHubDTO dto2 = new GitHubDTO(
                1L, "name", "org", 5, EVcsSetupStatus.CONNECTED, true, now
        );

        assertThat(dto1).isEqualTo(dto2);
        assertThat(dto1.hashCode()).isEqualTo(dto2.hashCode());
    }

    @Test
    @DisplayName("Record should handle null values")
    void recordShouldHandleNullValues() {
        GitHubDTO dto = new GitHubDTO(null, null, null, 0, null, null, null);

        assertThat(dto.id()).isNull();
        assertThat(dto.connectionName()).isNull();
        assertThat(dto.organizationId()).isNull();
        assertThat(dto.setupStatus()).isNull();
        assertThat(dto.hasAccessToken()).isNull();
        assertThat(dto.updatedAt()).isNull();
    }

    @Nested
    @DisplayName("fromVcsConnection tests")
    class FromVcsConnectionTests {

        @Test
        @DisplayName("Should throw exception for non-GitHub connection")
        void shouldThrowExceptionForNonGitHubConnection() {
            VcsConnection vcsConnection = mock(VcsConnection.class);
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.GITLAB);

            assertThatThrownBy(() -> GitHubDTO.fromVcsConnection(vcsConnection))
                    .isInstanceOf(IllegalArgumentException.class)
                    .hasMessage("Expected GitHub connection");
        }

        @Test
        @DisplayName("Should throw exception for Bitbucket connection")
        void shouldThrowExceptionForBitbucketConnection() {
            VcsConnection vcsConnection = mock(VcsConnection.class);
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);

            assertThatThrownBy(() -> GitHubDTO.fromVcsConnection(vcsConnection))
                    .isInstanceOf(IllegalArgumentException.class)
                    .hasMessage("Expected GitHub connection");
        }

        @Test
        @DisplayName("Should create DTO from APP-type connection")
        void shouldCreateDtoFromAppTypeConnection() {
            LocalDateTime now = LocalDateTime.now();
            VcsConnection vcsConnection = mock(VcsConnection.class);
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.GITHUB);
            when(vcsConnection.getConnectionType()).thenReturn(EVcsConnectionType.APP);
            when(vcsConnection.getId()).thenReturn(10L);
            when(vcsConnection.getConnectionName()).thenReturn("github-app");
            when(vcsConnection.getExternalWorkspaceSlug()).thenReturn("ext-org");
            when(vcsConnection.getRepoCount()).thenReturn(25);
            when(vcsConnection.getSetupStatus()).thenReturn(EVcsSetupStatus.CONNECTED);
            when(vcsConnection.getAccessToken()).thenReturn("ghp_token123");
            when(vcsConnection.getUpdatedAt()).thenReturn(now);

            GitHubDTO dto = GitHubDTO.fromVcsConnection(vcsConnection);

            assertThat(dto.id()).isEqualTo(10L);
            assertThat(dto.connectionName()).isEqualTo("github-app");
            assertThat(dto.organizationId()).isEqualTo("ext-org");
            assertThat(dto.repoCount()).isEqualTo(25);
            assertThat(dto.setupStatus()).isEqualTo(EVcsSetupStatus.CONNECTED);
            assertThat(dto.hasAccessToken()).isTrue();
            assertThat(dto.updatedAt()).isEqualTo(now);
        }

        @Test
        @DisplayName("Should handle null access token for APP connection")
        void shouldHandleNullAccessTokenForAppConnection() {
            VcsConnection vcsConnection = mock(VcsConnection.class);
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.GITHUB);
            when(vcsConnection.getConnectionType()).thenReturn(EVcsConnectionType.APP);
            when(vcsConnection.getAccessToken()).thenReturn(null);

            GitHubDTO dto = GitHubDTO.fromVcsConnection(vcsConnection);

            assertThat(dto.hasAccessToken()).isFalse();
        }

        @Test
        @DisplayName("Should handle blank access token for APP connection")
        void shouldHandleBlankAccessTokenForAppConnection() {
            VcsConnection vcsConnection = mock(VcsConnection.class);
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.GITHUB);
            when(vcsConnection.getConnectionType()).thenReturn(EVcsConnectionType.APP);
            when(vcsConnection.getAccessToken()).thenReturn("   ");

            GitHubDTO dto = GitHubDTO.fromVcsConnection(vcsConnection);

            assertThat(dto.hasAccessToken()).isFalse();
        }

        @Test
        @DisplayName("Should handle empty access token for APP connection")
        void shouldHandleEmptyAccessTokenForAppConnection() {
            VcsConnection vcsConnection = mock(VcsConnection.class);
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.GITHUB);
            when(vcsConnection.getConnectionType()).thenReturn(EVcsConnectionType.APP);
            when(vcsConnection.getAccessToken()).thenReturn("");

            GitHubDTO dto = GitHubDTO.fromVcsConnection(vcsConnection);

            assertThat(dto.hasAccessToken()).isFalse();
        }

        @Test
        @DisplayName("Should create DTO from null configuration")
        void shouldCreateDtoFromNullConfiguration() {
            VcsConnection vcsConnection = mock(VcsConnection.class);
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.GITHUB);
            when(vcsConnection.getConnectionType()).thenReturn(EVcsConnectionType.OAUTH_APP);
            when(vcsConnection.getConfiguration()).thenReturn(null);
            when(vcsConnection.getAccessToken()).thenReturn("token");

            GitHubDTO dto = GitHubDTO.fromVcsConnection(vcsConnection);

            assertThat(dto.hasAccessToken()).isTrue();
        }

        @Test
        @DisplayName("Should create DTO from manual connection with config")
        void shouldCreateDtoFromManualConnectionWithConfig() {
            LocalDateTime now = LocalDateTime.now();
            GitHubConfig config = new GitHubConfig("ghp_validtoken123", "org-id", null);
            
            VcsConnection vcsConnection = mock(VcsConnection.class);
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.GITHUB);
            when(vcsConnection.getConnectionType()).thenReturn(EVcsConnectionType.OAUTH_APP);
            when(vcsConnection.getConfiguration()).thenReturn(config);
            when(vcsConnection.getId()).thenReturn(30L);
            when(vcsConnection.getConnectionName()).thenReturn("manual-github");
            when(vcsConnection.getRepoCount()).thenReturn(12);
            when(vcsConnection.getSetupStatus()).thenReturn(EVcsSetupStatus.PENDING);
            when(vcsConnection.getUpdatedAt()).thenReturn(now);

            GitHubDTO dto = GitHubDTO.fromVcsConnection(vcsConnection);

            assertThat(dto.id()).isEqualTo(30L);
            assertThat(dto.connectionName()).isEqualTo("manual-github");
            assertThat(dto.organizationId()).isEqualTo("org-id");
            assertThat(dto.repoCount()).isEqualTo(12);
            assertThat(dto.setupStatus()).isEqualTo(EVcsSetupStatus.PENDING);
            assertThat(dto.hasAccessToken()).isTrue();
            assertThat(dto.updatedAt()).isEqualTo(now);
        }

        @Test
        @DisplayName("Should detect null access token in config")
        void shouldDetectNullAccessTokenInConfig() {
            GitHubConfig config = new GitHubConfig(null, "org", null);
            
            VcsConnection vcsConnection = mock(VcsConnection.class);
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.GITHUB);
            when(vcsConnection.getConnectionType()).thenReturn(EVcsConnectionType.OAUTH_APP);
            when(vcsConnection.getConfiguration()).thenReturn(config);

            GitHubDTO dto = GitHubDTO.fromVcsConnection(vcsConnection);

            assertThat(dto.hasAccessToken()).isFalse();
        }

        @Test
        @DisplayName("Should detect blank access token in config")
        void shouldDetectBlankAccessTokenInConfig() {
            GitHubConfig config = new GitHubConfig("", "org", null);
            
            VcsConnection vcsConnection = mock(VcsConnection.class);
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.GITHUB);
            when(vcsConnection.getConnectionType()).thenReturn(EVcsConnectionType.OAUTH_APP);
            when(vcsConnection.getConfiguration()).thenReturn(config);

            GitHubDTO dto = GitHubDTO.fromVcsConnection(vcsConnection);

            assertThat(dto.hasAccessToken()).isFalse();
        }
    }
}
