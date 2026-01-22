package org.rostilos.codecrow.core.dto.gitlab;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.vcs.EVcsConnectionType;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.EVcsSetupStatus;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.config.gitlab.GitLabConfig;

import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

@DisplayName("GitLabDTO Tests")
class GitLabDTOTest {

    @Test
    @DisplayName("Record should store all fields correctly")
    void recordShouldStoreAllFieldsCorrectly() {
        LocalDateTime now = LocalDateTime.now();
        GitLabDTO dto = new GitLabDTO(
                1L,
                "gitlab-connection",
                "group-123",
                10,
                EVcsSetupStatus.CONNECTED,
                true,
                now,
                EVcsConnectionType.PERSONAL_TOKEN,
                "/path/to/repo"
        );

        assertThat(dto.id()).isEqualTo(1L);
        assertThat(dto.connectionName()).isEqualTo("gitlab-connection");
        assertThat(dto.groupId()).isEqualTo("group-123");
        assertThat(dto.repoCount()).isEqualTo(10);
        assertThat(dto.setupStatus()).isEqualTo(EVcsSetupStatus.CONNECTED);
        assertThat(dto.hasAccessToken()).isTrue();
        assertThat(dto.updatedAt()).isEqualTo(now);
        assertThat(dto.connectionType()).isEqualTo(EVcsConnectionType.PERSONAL_TOKEN);
        assertThat(dto.repositoryPath()).isEqualTo("/path/to/repo");
    }

    @Test
    @DisplayName("Records with same values should be equal")
    void recordsWithSameValuesShouldBeEqual() {
        LocalDateTime now = LocalDateTime.now();
        GitLabDTO dto1 = new GitLabDTO(
                1L, "name", "group", 5, EVcsSetupStatus.CONNECTED, true, now, EVcsConnectionType.APP, "/path"
        );
        GitLabDTO dto2 = new GitLabDTO(
                1L, "name", "group", 5, EVcsSetupStatus.CONNECTED, true, now, EVcsConnectionType.APP, "/path"
        );

        assertThat(dto1).isEqualTo(dto2);
        assertThat(dto1.hashCode()).isEqualTo(dto2.hashCode());
    }

    @Nested
    @DisplayName("fromVcsConnection tests")
    class FromVcsConnectionTests {

        @Test
        @DisplayName("Should throw exception for non-GitLab connection")
        void shouldThrowExceptionForNonGitLabConnection() {
            VcsConnection vcsConnection = mock(VcsConnection.class);
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);

            assertThatThrownBy(() -> GitLabDTO.fromVcsConnection(vcsConnection))
                    .isInstanceOf(IllegalArgumentException.class)
                    .hasMessage("Expected GitLab connection");
        }

        @Test
        @DisplayName("Should create DTO from APP-type connection")
        void shouldCreateDtoFromAppTypeConnection() {
            LocalDateTime now = LocalDateTime.now();
            VcsConnection vcsConnection = mock(VcsConnection.class);
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.GITLAB);
            when(vcsConnection.getConnectionType()).thenReturn(EVcsConnectionType.APP);
            when(vcsConnection.getId()).thenReturn(10L);
            when(vcsConnection.getConnectionName()).thenReturn("gitlab-app");
            when(vcsConnection.getExternalWorkspaceSlug()).thenReturn("ext-group");
            when(vcsConnection.getRepoCount()).thenReturn(15);
            when(vcsConnection.getSetupStatus()).thenReturn(EVcsSetupStatus.CONNECTED);
            when(vcsConnection.getAccessToken()).thenReturn("access-token");
            when(vcsConnection.getUpdatedAt()).thenReturn(now);
            when(vcsConnection.getRepositoryPath()).thenReturn("/repos/project");

            GitLabDTO dto = GitLabDTO.fromVcsConnection(vcsConnection);

            assertThat(dto.id()).isEqualTo(10L);
            assertThat(dto.connectionName()).isEqualTo("gitlab-app");
            assertThat(dto.groupId()).isEqualTo("ext-group");
            assertThat(dto.repoCount()).isEqualTo(15);
            assertThat(dto.setupStatus()).isEqualTo(EVcsSetupStatus.CONNECTED);
            assertThat(dto.hasAccessToken()).isTrue();
            assertThat(dto.updatedAt()).isEqualTo(now);
            assertThat(dto.connectionType()).isEqualTo(EVcsConnectionType.APP);
            assertThat(dto.repositoryPath()).isEqualTo("/repos/project");
        }

        @Test
        @DisplayName("Should handle null access token for APP connection")
        void shouldHandleNullAccessTokenForAppConnection() {
            VcsConnection vcsConnection = mock(VcsConnection.class);
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.GITLAB);
            when(vcsConnection.getConnectionType()).thenReturn(EVcsConnectionType.APP);
            when(vcsConnection.getAccessToken()).thenReturn(null);

            GitLabDTO dto = GitLabDTO.fromVcsConnection(vcsConnection);

            assertThat(dto.hasAccessToken()).isFalse();
        }

        @Test
        @DisplayName("Should handle blank access token for APP connection")
        void shouldHandleBlankAccessTokenForAppConnection() {
            VcsConnection vcsConnection = mock(VcsConnection.class);
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.GITLAB);
            when(vcsConnection.getConnectionType()).thenReturn(EVcsConnectionType.APP);
            when(vcsConnection.getAccessToken()).thenReturn("   ");

            GitLabDTO dto = GitLabDTO.fromVcsConnection(vcsConnection);

            assertThat(dto.hasAccessToken()).isFalse();
        }

        @Test
        @DisplayName("Should create DTO from null configuration")
        void shouldCreateDtoFromNullConfiguration() {
            VcsConnection vcsConnection = mock(VcsConnection.class);
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.GITLAB);
            when(vcsConnection.getConnectionType()).thenReturn(EVcsConnectionType.PERSONAL_TOKEN);
            when(vcsConnection.getConfiguration()).thenReturn(null);
            when(vcsConnection.getAccessToken()).thenReturn("token");

            GitLabDTO dto = GitLabDTO.fromVcsConnection(vcsConnection);

            assertThat(dto.hasAccessToken()).isTrue();
        }

        @Test
        @DisplayName("Should create DTO from manual connection with config")
        void shouldCreateDtoFromManualConnectionWithConfig() {
            LocalDateTime now = LocalDateTime.now();
            GitLabConfig config = new GitLabConfig("glpat-token123", "my-group", null);
            
            VcsConnection vcsConnection = mock(VcsConnection.class);
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.GITLAB);
            when(vcsConnection.getConnectionType()).thenReturn(EVcsConnectionType.PERSONAL_TOKEN);
            when(vcsConnection.getConfiguration()).thenReturn(config);
            when(vcsConnection.getId()).thenReturn(20L);
            when(vcsConnection.getConnectionName()).thenReturn("manual-gitlab");
            when(vcsConnection.getRepoCount()).thenReturn(8);
            when(vcsConnection.getSetupStatus()).thenReturn(EVcsSetupStatus.PENDING);
            when(vcsConnection.getUpdatedAt()).thenReturn(now);
            when(vcsConnection.getRepositoryPath()).thenReturn("/manual/path");

            GitLabDTO dto = GitLabDTO.fromVcsConnection(vcsConnection);

            assertThat(dto.id()).isEqualTo(20L);
            assertThat(dto.connectionName()).isEqualTo("manual-gitlab");
            assertThat(dto.groupId()).isEqualTo("my-group");
            assertThat(dto.repoCount()).isEqualTo(8);
            assertThat(dto.setupStatus()).isEqualTo(EVcsSetupStatus.PENDING);
            assertThat(dto.hasAccessToken()).isTrue();
            assertThat(dto.updatedAt()).isEqualTo(now);
            assertThat(dto.connectionType()).isEqualTo(EVcsConnectionType.PERSONAL_TOKEN);
            assertThat(dto.repositoryPath()).isEqualTo("/manual/path");
        }

        @Test
        @DisplayName("Should detect null access token in config")
        void shouldDetectNullAccessTokenInConfig() {
            GitLabConfig config = new GitLabConfig(null, "group", null);
            
            VcsConnection vcsConnection = mock(VcsConnection.class);
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.GITLAB);
            when(vcsConnection.getConnectionType()).thenReturn(EVcsConnectionType.PERSONAL_TOKEN);
            when(vcsConnection.getConfiguration()).thenReturn(config);

            GitLabDTO dto = GitLabDTO.fromVcsConnection(vcsConnection);

            assertThat(dto.hasAccessToken()).isFalse();
        }

        @Test
        @DisplayName("Should detect blank access token in config")
        void shouldDetectBlankAccessTokenInConfig() {
            GitLabConfig config = new GitLabConfig("   ", "group", null);
            
            VcsConnection vcsConnection = mock(VcsConnection.class);
            when(vcsConnection.getProviderType()).thenReturn(EVcsProvider.GITLAB);
            when(vcsConnection.getConnectionType()).thenReturn(EVcsConnectionType.PERSONAL_TOKEN);
            when(vcsConnection.getConfiguration()).thenReturn(config);

            GitLabDTO dto = GitLabDTO.fromVcsConnection(vcsConnection);

            assertThat(dto.hasAccessToken()).isFalse();
        }
    }
}
