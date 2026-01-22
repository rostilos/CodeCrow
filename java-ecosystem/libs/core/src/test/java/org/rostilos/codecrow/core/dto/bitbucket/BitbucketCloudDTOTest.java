package org.rostilos.codecrow.core.dto.bitbucket;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.EVcsSetupStatus;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.config.cloud.BitbucketCloudConfig;

import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

@DisplayName("BitbucketCloudDTO Tests")
class BitbucketCloudDTOTest {

    @Test
    @DisplayName("Record should store all fields correctly")
    void recordShouldStoreAllFieldsCorrectly() {
        LocalDateTime now = LocalDateTime.now();
        BitbucketCloudDTO dto = new BitbucketCloudDTO(
                1L,
                "connection-name",
                "workspace-123",
                5,
                EVcsSetupStatus.CONNECTED,
                true,
                false,
                now
        );

        assertThat(dto.id()).isEqualTo(1L);
        assertThat(dto.connectionName()).isEqualTo("connection-name");
        assertThat(dto.workspaceId()).isEqualTo("workspace-123");
        assertThat(dto.repoCount()).isEqualTo(5);
        assertThat(dto.setupStatus()).isEqualTo(EVcsSetupStatus.CONNECTED);
        assertThat(dto.hasAuthKey()).isTrue();
        assertThat(dto.hasAuthSecret()).isFalse();
        assertThat(dto.updatedAt()).isEqualTo(now);
    }

    @Test
    @DisplayName("Records with same values should be equal")
    void recordsWithSameValuesShouldBeEqual() {
        LocalDateTime now = LocalDateTime.now();
        BitbucketCloudDTO dto1 = new BitbucketCloudDTO(
                1L, "name", "workspace", 3, EVcsSetupStatus.CONNECTED, true, true, now
        );
        BitbucketCloudDTO dto2 = new BitbucketCloudDTO(
                1L, "name", "workspace", 3, EVcsSetupStatus.CONNECTED, true, true, now
        );

        assertThat(dto1).isEqualTo(dto2);
        assertThat(dto1.hashCode()).isEqualTo(dto2.hashCode());
    }

    @Nested
    @DisplayName("fromGitConfiguration tests")
    class FromGitConfigurationTests {

        @Test
        @DisplayName("Should throw exception for non-Bitbucket connection")
        void shouldThrowExceptionForNonBitbucketConnection() {
            VcsConnection vcsConnection = mock(VcsConnection.class);
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.GITHUB);

            assertThatThrownBy(() -> BitbucketCloudDTO.fromGitConfiguration(vcsConnection))
                    .isInstanceOf(IllegalArgumentException.class)
                    .hasMessage("Expected Bitbucket connection");
        }

        @Test
        @DisplayName("Should create DTO from APP-type connection")
        void shouldCreateDtoFromAppTypeConnection() {
            LocalDateTime now = LocalDateTime.now();
            VcsConnection vcsConnection = mock(VcsConnection.class);
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
            when(vcsConnection.getConnectionType()).thenReturn(EVcsConnectionType.APP);
            when(vcsConnection.getId()).thenReturn(10L);
            when(vcsConnection.getConnectionName()).thenReturn("app-connection");
            when(vcsConnection.getExternalWorkspaceSlug()).thenReturn("ext-workspace");
            when(vcsConnection.getRepoCount()).thenReturn(7);
            when(vcsConnection.getSetupStatus()).thenReturn(EVcsSetupStatus.CONNECTED);
            when(vcsConnection.getAccessToken()).thenReturn("access-token");
            when(vcsConnection.getRefreshToken()).thenReturn("refresh-token");
            when(vcsConnection.getUpdatedAt()).thenReturn(now);

            BitbucketCloudDTO dto = BitbucketCloudDTO.fromGitConfiguration(vcsConnection);

            assertThat(dto.id()).isEqualTo(10L);
            assertThat(dto.connectionName()).isEqualTo("app-connection");
            assertThat(dto.workspaceId()).isEqualTo("ext-workspace");
            assertThat(dto.repoCount()).isEqualTo(7);
            assertThat(dto.setupStatus()).isEqualTo(EVcsSetupStatus.CONNECTED);
            assertThat(dto.hasAuthKey()).isTrue();
            assertThat(dto.hasAuthSecret()).isTrue();
            assertThat(dto.updatedAt()).isEqualTo(now);
        }

        @Test
        @DisplayName("Should handle null access token for APP connection")
        void shouldHandleNullAccessTokenForAppConnection() {
            VcsConnection vcsConnection = mock(VcsConnection.class);
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
            when(vcsConnection.getConnectionType()).thenReturn(EVcsConnectionType.APP);
            when(vcsConnection.getAccessToken()).thenReturn(null);
            when(vcsConnection.getRefreshToken()).thenReturn(null);

            BitbucketCloudDTO dto = BitbucketCloudDTO.fromGitConfiguration(vcsConnection);

            assertThat(dto.hasAuthKey()).isFalse();
            assertThat(dto.hasAuthSecret()).isFalse();
        }

        @Test
        @DisplayName("Should handle blank access token for APP connection")
        void shouldHandleBlankAccessTokenForAppConnection() {
            VcsConnection vcsConnection = mock(VcsConnection.class);
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
            when(vcsConnection.getConnectionType()).thenReturn(EVcsConnectionType.APP);
            when(vcsConnection.getAccessToken()).thenReturn("   ");
            when(vcsConnection.getRefreshToken()).thenReturn("");

            BitbucketCloudDTO dto = BitbucketCloudDTO.fromGitConfiguration(vcsConnection);

            assertThat(dto.hasAuthKey()).isFalse();
            assertThat(dto.hasAuthSecret()).isFalse();
        }

        @Test
        @DisplayName("Should create DTO from null configuration")
        void shouldCreateDtoFromNullConfiguration() {
            VcsConnection vcsConnection = mock(VcsConnection.class);
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
            when(vcsConnection.getConnectionType()).thenReturn(EVcsConnectionType.OAUTH_MANUAL);
            when(vcsConnection.getConfiguration()).thenReturn(null);
            when(vcsConnection.getAccessToken()).thenReturn("token");
            when(vcsConnection.getRefreshToken()).thenReturn("refresh");

            BitbucketCloudDTO dto = BitbucketCloudDTO.fromGitConfiguration(vcsConnection);

            assertThat(dto.hasAuthKey()).isTrue();
            assertThat(dto.hasAuthSecret()).isTrue();
        }

        @Test
        @DisplayName("Should create DTO from legacy OAuth connection with config")
        void shouldCreateDtoFromLegacyOAuthConnection() {
            LocalDateTime now = LocalDateTime.now();
            BitbucketCloudConfig config = new BitbucketCloudConfig("oauth-key", "oauth-token", "ws-123");
            
            VcsConnection vcsConnection = mock(VcsConnection.class);
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
            when(vcsConnection.getConnectionType()).thenReturn(EVcsConnectionType.OAUTH_MANUAL);
            when(vcsConnection.getConfiguration()).thenReturn(config);
            when(vcsConnection.getId()).thenReturn(20L);
            when(vcsConnection.getConnectionName()).thenReturn("legacy-connection");
            when(vcsConnection.getRepoCount()).thenReturn(12);
            when(vcsConnection.getSetupStatus()).thenReturn(EVcsSetupStatus.PENDING);
            when(vcsConnection.getUpdatedAt()).thenReturn(now);

            BitbucketCloudDTO dto = BitbucketCloudDTO.fromGitConfiguration(vcsConnection);

            assertThat(dto.id()).isEqualTo(20L);
            assertThat(dto.connectionName()).isEqualTo("legacy-connection");
            assertThat(dto.workspaceId()).isEqualTo("ws-123");
            assertThat(dto.repoCount()).isEqualTo(12);
            assertThat(dto.setupStatus()).isEqualTo(EVcsSetupStatus.PENDING);
            assertThat(dto.hasAuthKey()).isTrue();
            assertThat(dto.hasAuthSecret()).isTrue();
            assertThat(dto.updatedAt()).isEqualTo(now);
        }

        @Test
        @DisplayName("Should detect blank OAuth credentials in config")
        void shouldDetectBlankOAuthCredentialsInConfig() {
            BitbucketCloudConfig config = new BitbucketCloudConfig("", "   ", "ws-123");
            
            VcsConnection vcsConnection = mock(VcsConnection.class);
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);
            when(vcsConnection.getConnectionType()).thenReturn(EVcsConnectionType.OAUTH_MANUAL);
            when(vcsConnection.getConfiguration()).thenReturn(config);

            BitbucketCloudDTO dto = BitbucketCloudDTO.fromGitConfiguration(vcsConnection);

            assertThat(dto.hasAuthKey()).isFalse();
            assertThat(dto.hasAuthSecret()).isFalse();
        }
    }
}
