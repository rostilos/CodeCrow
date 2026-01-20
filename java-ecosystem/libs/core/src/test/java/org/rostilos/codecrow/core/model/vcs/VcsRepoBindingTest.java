package org.rostilos.codecrow.core.model.vcs;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.project.Project;
import org.rostilos.codecrow.core.model.workspace.Workspace;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("VcsRepoBinding")
class VcsRepoBindingTest {

    private VcsRepoBinding binding;

    @BeforeEach
    void setUp() {
        binding = new VcsRepoBinding();
    }

    @Nested
    @DisplayName("Basic Getters/Setters")
    class BasicGettersSetters {

        @Test
        @DisplayName("should set and get id")
        void shouldSetAndGetId() {
            binding.setId(1L);
            assertThat(binding.getId()).isEqualTo(1L);
        }

        @Test
        @DisplayName("should set and get workspace")
        void shouldSetAndGetWorkspace() {
            Workspace workspace = new Workspace();
            binding.setWorkspace(workspace);
            assertThat(binding.getWorkspace()).isSameAs(workspace);
        }

        @Test
        @DisplayName("should set and get project")
        void shouldSetAndGetProject() {
            Project project = new Project();
            binding.setProject(project);
            assertThat(binding.getProject()).isSameAs(project);
        }

        @Test
        @DisplayName("should set and get vcsConnection")
        void shouldSetAndGetVcsConnection() {
            VcsConnection connection = new VcsConnection();
            binding.setVcsConnection(connection);
            assertThat(binding.getVcsConnection()).isSameAs(connection);
        }

        @Test
        @DisplayName("should set and get provider")
        void shouldSetAndGetProvider() {
            binding.setProvider(EVcsProvider.GITHUB);
            assertThat(binding.getProvider()).isEqualTo(EVcsProvider.GITHUB);
        }

        @Test
        @DisplayName("should set and get externalRepoId")
        void shouldSetAndGetExternalRepoId() {
            binding.setExternalRepoId("repo-uuid-123");
            assertThat(binding.getExternalRepoId()).isEqualTo("repo-uuid-123");
        }

        @Test
        @DisplayName("should set and get externalRepoSlug")
        void shouldSetAndGetExternalRepoSlug() {
            binding.setExternalRepoSlug("my-repo");
            assertThat(binding.getExternalRepoSlug()).isEqualTo("my-repo");
        }

        @Test
        @DisplayName("should set and get externalNamespace")
        void shouldSetAndGetExternalNamespace() {
            binding.setExternalNamespace("my-org");
            assertThat(binding.getExternalNamespace()).isEqualTo("my-org");
        }

        @Test
        @DisplayName("should set and get displayName")
        void shouldSetAndGetDisplayName() {
            binding.setDisplayName("My Repository");
            assertThat(binding.getDisplayName()).isEqualTo("My Repository");
        }

        @Test
        @DisplayName("should set and get defaultBranch")
        void shouldSetAndGetDefaultBranch() {
            binding.setDefaultBranch("main");
            assertThat(binding.getDefaultBranch()).isEqualTo("main");
        }

        @Test
        @DisplayName("should set and get webhooksConfigured")
        void shouldSetAndGetWebhooksConfigured() {
            binding.setWebhooksConfigured(true);
            assertThat(binding.isWebhooksConfigured()).isTrue();
        }

        @Test
        @DisplayName("should default webhooksConfigured to false")
        void shouldDefaultWebhooksConfiguredToFalse() {
            assertThat(binding.isWebhooksConfigured()).isFalse();
        }

        @Test
        @DisplayName("should set and get webhookId")
        void shouldSetAndGetWebhookId() {
            binding.setWebhookId("webhook-123");
            assertThat(binding.getWebhookId()).isEqualTo("webhook-123");
        }
    }

    @Nested
    @DisplayName("VcsRepoInfo Interface Methods")
    class VcsRepoInfoMethods {

        @Test
        @DisplayName("getRepoWorkspace should return externalNamespace")
        void getRepoWorkspaceShouldReturnExternalNamespace() {
            binding.setExternalNamespace("my-workspace");
            assertThat(binding.getRepoWorkspace()).isEqualTo("my-workspace");
        }

        @Test
        @DisplayName("getRepoSlug should return externalRepoSlug")
        void getRepoSlugShouldReturnExternalRepoSlug() {
            binding.setExternalRepoSlug("my-repo");
            assertThat(binding.getRepoSlug()).isEqualTo("my-repo");
        }
    }

    @Nested
    @DisplayName("getFullName()")
    class GetFullNameTests {

        @Test
        @DisplayName("should return namespace/slug when both set")
        void shouldReturnNamespaceSlugWhenBothSet() {
            binding.setExternalNamespace("my-org");
            binding.setExternalRepoSlug("my-repo");
            assertThat(binding.getFullName()).isEqualTo("my-org/my-repo");
        }

        @Test
        @DisplayName("should return displayName when namespace is null")
        void shouldReturnDisplayNameWhenNamespaceNull() {
            binding.setExternalNamespace(null);
            binding.setExternalRepoSlug("my-repo");
            binding.setDisplayName("Display Name");
            assertThat(binding.getFullName()).isEqualTo("Display Name");
        }

        @Test
        @DisplayName("should return displayName when slug is null")
        void shouldReturnDisplayNameWhenSlugNull() {
            binding.setExternalNamespace("my-org");
            binding.setExternalRepoSlug(null);
            binding.setDisplayName("Display Name");
            assertThat(binding.getFullName()).isEqualTo("Display Name");
        }

        @Test
        @DisplayName("should return null when all fields null")
        void shouldReturnNullWhenAllNull() {
            binding.setExternalNamespace(null);
            binding.setExternalRepoSlug(null);
            binding.setDisplayName(null);
            assertThat(binding.getFullName()).isNull();
        }
    }

    @Nested
    @DisplayName("Provider Types")
    class ProviderTypeTests {

        @Test
        @DisplayName("should support GITHUB provider")
        void shouldSupportGitHub() {
            binding.setProvider(EVcsProvider.GITHUB);
            assertThat(binding.getProvider()).isEqualTo(EVcsProvider.GITHUB);
        }

        @Test
        @DisplayName("should support GITLAB provider")
        void shouldSupportGitLab() {
            binding.setProvider(EVcsProvider.GITLAB);
            assertThat(binding.getProvider()).isEqualTo(EVcsProvider.GITLAB);
        }

        @Test
        @DisplayName("should support BITBUCKET_CLOUD provider")
        void shouldSupportBitbucketCloud() {
            binding.setProvider(EVcsProvider.BITBUCKET_CLOUD);
            assertThat(binding.getProvider()).isEqualTo(EVcsProvider.BITBUCKET_CLOUD);
        }

        @Test
        @DisplayName("should support BITBUCKET_SERVER provider")
        void shouldSupportBitbucketServer() {
            binding.setProvider(EVcsProvider.BITBUCKET_SERVER);
            assertThat(binding.getProvider()).isEqualTo(EVcsProvider.BITBUCKET_SERVER);
        }
    }

    @Nested
    @DisplayName("Timestamps")
    class TimestampTests {

        @Test
        @DisplayName("should have null createdAt by default")
        void shouldHaveNullCreatedAtByDefault() {
            assertThat(binding.getCreatedAt()).isNull();
        }

        @Test
        @DisplayName("should have null updatedAt by default")
        void shouldHaveNullUpdatedAtByDefault() {
            assertThat(binding.getUpdatedAt()).isNull();
        }
    }

    @Nested
    @DisplayName("Full Integration Scenario")
    class IntegrationScenarios {

        @Test
        @DisplayName("should properly configure complete binding")
        void shouldConfigureCompleteBinding() {
            Workspace workspace = new Workspace();
            Project project = new Project();
            VcsConnection connection = new VcsConnection();
            connection.setProviderType(EVcsProvider.GITHUB);

            binding.setWorkspace(workspace);
            binding.setProject(project);
            binding.setVcsConnection(connection);
            binding.setProvider(EVcsProvider.GITHUB);
            binding.setExternalRepoId("R_kgDOK12345");
            binding.setExternalRepoSlug("codecrow");
            binding.setExternalNamespace("rostilos");
            binding.setDisplayName("CodeCrow");
            binding.setDefaultBranch("main");
            binding.setWebhooksConfigured(true);
            binding.setWebhookId("hook-12345");

            assertThat(binding.getWorkspace()).isSameAs(workspace);
            assertThat(binding.getProject()).isSameAs(project);
            assertThat(binding.getVcsConnection()).isSameAs(connection);
            assertThat(binding.getProvider()).isEqualTo(EVcsProvider.GITHUB);
            assertThat(binding.getFullName()).isEqualTo("rostilos/codecrow");
            assertThat(binding.getRepoWorkspace()).isEqualTo("rostilos");
            assertThat(binding.getRepoSlug()).isEqualTo("codecrow");
        }
    }
}
