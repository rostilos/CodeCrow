package org.rostilos.codecrow.core.util;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;
import org.rostilos.codecrow.core.model.vcs.VcsRepoBinding;
import org.rostilos.codecrow.core.model.vcs.VcsRepoInfo;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

@DisplayName("VcsBindingHelper")
class VcsBindingHelperTest {

    private Project mockProject;
    private VcsRepoInfo mockRepoInfo;
    private VcsConnection mockConnection;

    @BeforeEach
    void setUp() {
        mockProject = mock(Project.class);
        mockRepoInfo = mock(VcsRepoInfo.class);
        mockConnection = mock(VcsConnection.class);
    }

    @Nested
    @DisplayName("getEffectiveVcsRepoInfo()")
    class GetEffectiveVcsRepoInfo {

        @Test
        @DisplayName("should return null for null project")
        void shouldReturnNullForNullProject() {
            VcsRepoInfo result = VcsBindingHelper.getEffectiveVcsRepoInfo(null);

            assertThat(result).isNull();
        }

        @Test
        @DisplayName("should return repo info from project")
        void shouldReturnRepoInfoFromProject() {
            when(mockProject.getEffectiveVcsRepoInfo()).thenReturn(mockRepoInfo);

            VcsRepoInfo result = VcsBindingHelper.getEffectiveVcsRepoInfo(mockProject);

            assertThat(result).isEqualTo(mockRepoInfo);
            verify(mockProject).getEffectiveVcsRepoInfo();
        }

        @Test
        @DisplayName("should return null when project has no repo info")
        void shouldReturnNullWhenProjectHasNoRepoInfo() {
            when(mockProject.getEffectiveVcsRepoInfo()).thenReturn(null);

            VcsRepoInfo result = VcsBindingHelper.getEffectiveVcsRepoInfo(mockProject);

            assertThat(result).isNull();
        }
    }

    @Nested
    @DisplayName("getVcsConnection()")
    class GetVcsConnection {

        @Test
        @DisplayName("should return null for null project")
        void shouldReturnNullForNullProject() {
            VcsConnection result = VcsBindingHelper.getVcsConnection(null);

            assertThat(result).isNull();
        }

        @Test
        @DisplayName("should return connection from project")
        void shouldReturnConnectionFromProject() {
            when(mockProject.getEffectiveVcsConnection()).thenReturn(mockConnection);

            VcsConnection result = VcsBindingHelper.getVcsConnection(mockProject);

            assertThat(result).isEqualTo(mockConnection);
            verify(mockProject).getEffectiveVcsConnection();
        }

        @Test
        @DisplayName("should return null when project has no connection")
        void shouldReturnNullWhenProjectHasNoConnection() {
            when(mockProject.getEffectiveVcsConnection()).thenReturn(null);

            VcsConnection result = VcsBindingHelper.getVcsConnection(mockProject);

            assertThat(result).isNull();
        }
    }

    @Nested
    @DisplayName("getRepoWorkspace()")
    class GetRepoWorkspace {

        @Test
        @DisplayName("should return null for null project")
        void shouldReturnNullForNullProject() {
            String result = VcsBindingHelper.getRepoWorkspace(null);

            assertThat(result).isNull();
        }

        @Test
        @DisplayName("should return workspace from repo info")
        void shouldReturnWorkspaceFromRepoInfo() {
            when(mockProject.getEffectiveVcsRepoInfo()).thenReturn(mockRepoInfo);
            when(mockRepoInfo.getRepoWorkspace()).thenReturn("my-workspace");

            String result = VcsBindingHelper.getRepoWorkspace(mockProject);

            assertThat(result).isEqualTo("my-workspace");
        }

        @Test
        @DisplayName("should return null when no repo info")
        void shouldReturnNullWhenNoRepoInfo() {
            when(mockProject.getEffectiveVcsRepoInfo()).thenReturn(null);

            String result = VcsBindingHelper.getRepoWorkspace(mockProject);

            assertThat(result).isNull();
        }
    }

    @Nested
    @DisplayName("getRepoSlug()")
    class GetRepoSlug {

        @Test
        @DisplayName("should return null for null project")
        void shouldReturnNullForNullProject() {
            String result = VcsBindingHelper.getRepoSlug(null);

            assertThat(result).isNull();
        }

        @Test
        @DisplayName("should return slug from repo info")
        void shouldReturnSlugFromRepoInfo() {
            when(mockProject.getEffectiveVcsRepoInfo()).thenReturn(mockRepoInfo);
            when(mockRepoInfo.getRepoSlug()).thenReturn("my-repo");

            String result = VcsBindingHelper.getRepoSlug(mockProject);

            assertThat(result).isEqualTo("my-repo");
        }

        @Test
        @DisplayName("should return null when no repo info")
        void shouldReturnNullWhenNoRepoInfo() {
            when(mockProject.getEffectiveVcsRepoInfo()).thenReturn(null);

            String result = VcsBindingHelper.getRepoSlug(mockProject);

            assertThat(result).isNull();
        }
    }

    @Nested
    @DisplayName("getVcsProvider()")
    class GetVcsProvider {

        @Test
        @DisplayName("should return null for null project")
        void shouldReturnNullForNullProject() {
            EVcsProvider result = VcsBindingHelper.getVcsProvider(null);

            assertThat(result).isNull();
        }

        @Test
        @DisplayName("should return BITBUCKET_CLOUD provider")
        void shouldReturnBitbucketCloudProvider() {
            when(mockProject.getEffectiveVcsConnection()).thenReturn(mockConnection);
            when(mockConnection.getProviderType()).thenReturn(EVcsProvider.BITBUCKET_CLOUD);

            EVcsProvider result = VcsBindingHelper.getVcsProvider(mockProject);

            assertThat(result).isEqualTo(EVcsProvider.BITBUCKET_CLOUD);
        }

        @Test
        @DisplayName("should return GITHUB provider")
        void shouldReturnGithubProvider() {
            when(mockProject.getEffectiveVcsConnection()).thenReturn(mockConnection);
            when(mockConnection.getProviderType()).thenReturn(EVcsProvider.GITHUB);

            EVcsProvider result = VcsBindingHelper.getVcsProvider(mockProject);

            assertThat(result).isEqualTo(EVcsProvider.GITHUB);
        }

        @Test
        @DisplayName("should return GITLAB provider")
        void shouldReturnGitlabProvider() {
            when(mockProject.getEffectiveVcsConnection()).thenReturn(mockConnection);
            when(mockConnection.getProviderType()).thenReturn(EVcsProvider.GITLAB);

            EVcsProvider result = VcsBindingHelper.getVcsProvider(mockProject);

            assertThat(result).isEqualTo(EVcsProvider.GITLAB);
        }

        @Test
        @DisplayName("should return null when no connection")
        void shouldReturnNullWhenNoConnection() {
            when(mockProject.getEffectiveVcsConnection()).thenReturn(null);

            EVcsProvider result = VcsBindingHelper.getVcsProvider(mockProject);

            assertThat(result).isNull();
        }
    }

    @Nested
    @DisplayName("hasValidVcsBinding()")
    class HasValidVcsBinding {

        @Test
        @DisplayName("should return false for null project")
        void shouldReturnFalseForNullProject() {
            boolean result = VcsBindingHelper.hasValidVcsBinding(null);

            assertThat(result).isFalse();
        }

        @Test
        @DisplayName("should return true when connection exists")
        void shouldReturnTrueWhenConnectionExists() {
            when(mockProject.getEffectiveVcsConnection()).thenReturn(mockConnection);

            boolean result = VcsBindingHelper.hasValidVcsBinding(mockProject);

            assertThat(result).isTrue();
        }

        @Test
        @DisplayName("should return false when no connection")
        void shouldReturnFalseWhenNoConnection() {
            when(mockProject.getEffectiveVcsConnection()).thenReturn(null);

            boolean result = VcsBindingHelper.hasValidVcsBinding(mockProject);

            assertThat(result).isFalse();
        }
    }

    @Nested
    @DisplayName("getFullRepoPath()")
    class GetFullRepoPath {

        @Test
        @DisplayName("should return null for null project")
        void shouldReturnNullForNullProject() {
            String result = VcsBindingHelper.getFullRepoPath(null);

            assertThat(result).isNull();
        }

        @Test
        @DisplayName("should return full path when both workspace and slug exist")
        void shouldReturnFullPathWhenBothExist() {
            when(mockProject.getEffectiveVcsRepoInfo()).thenReturn(mockRepoInfo);
            when(mockRepoInfo.getRepoWorkspace()).thenReturn("my-workspace");
            when(mockRepoInfo.getRepoSlug()).thenReturn("my-repo");

            String result = VcsBindingHelper.getFullRepoPath(mockProject);

            assertThat(result).isEqualTo("my-workspace/my-repo");
        }

        @Test
        @DisplayName("should return null when workspace is null")
        void shouldReturnNullWhenWorkspaceIsNull() {
            when(mockProject.getEffectiveVcsRepoInfo()).thenReturn(mockRepoInfo);
            when(mockRepoInfo.getRepoWorkspace()).thenReturn(null);
            when(mockRepoInfo.getRepoSlug()).thenReturn("my-repo");

            String result = VcsBindingHelper.getFullRepoPath(mockProject);

            assertThat(result).isNull();
        }

        @Test
        @DisplayName("should return null when slug is null")
        void shouldReturnNullWhenSlugIsNull() {
            when(mockProject.getEffectiveVcsRepoInfo()).thenReturn(mockRepoInfo);
            when(mockRepoInfo.getRepoWorkspace()).thenReturn("my-workspace");
            when(mockRepoInfo.getRepoSlug()).thenReturn(null);

            String result = VcsBindingHelper.getFullRepoPath(mockProject);

            assertThat(result).isNull();
        }

        @Test
        @DisplayName("should return null when no repo info")
        void shouldReturnNullWhenNoRepoInfo() {
            when(mockProject.getEffectiveVcsRepoInfo()).thenReturn(null);

            String result = VcsBindingHelper.getFullRepoPath(mockProject);

            assertThat(result).isNull();
        }

        @Test
        @DisplayName("should handle nested workspace paths")
        void shouldHandleNestedWorkspacePaths() {
            when(mockProject.getEffectiveVcsRepoInfo()).thenReturn(mockRepoInfo);
            when(mockRepoInfo.getRepoWorkspace()).thenReturn("org/team");
            when(mockRepoInfo.getRepoSlug()).thenReturn("project");

            String result = VcsBindingHelper.getFullRepoPath(mockProject);

            assertThat(result).isEqualTo("org/team/project");
        }
    }
}
