package org.rostilos.codecrow.core.model.project;

import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.rostilos.codecrow.core.model.vcs.VcsConnection;

import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;

@SuppressWarnings("deprecation")
class ProjectVcsConnectionBindingTest {

    @Test
    void shouldCreateProjectVcsConnectionBinding() {
        ProjectVcsConnectionBinding binding = new ProjectVcsConnectionBinding();
        assertThat(binding).isNotNull();
    }

    @Test
    void shouldSetAndGetId() {
        ProjectVcsConnectionBinding binding = new ProjectVcsConnectionBinding();
        // ID is auto-generated, verify it's null for new entity
        assertThat(binding.getId()).isNull();
    }

    @Test
    void shouldSetAndGetProject() {
        ProjectVcsConnectionBinding binding = new ProjectVcsConnectionBinding();
        Project project = new Project();
        
        binding.setProject(project);
        
        assertThat(binding.getProject()).isEqualTo(project);
    }

    @Test
    void shouldSetAndGetVcsProvider() {
        ProjectVcsConnectionBinding binding = new ProjectVcsConnectionBinding();
        binding.setVcsProvider(EVcsProvider.BITBUCKET_CLOUD);
        
        assertThat(binding.getVcsProvider()).isEqualTo(EVcsProvider.BITBUCKET_CLOUD);
    }

    @Test
    void shouldSetAndGetVcsConnection() {
        ProjectVcsConnectionBinding binding = new ProjectVcsConnectionBinding();
        VcsConnection connection = new VcsConnection();
        
        binding.setVcsConnection(connection);
        
        assertThat(binding.getVcsConnection()).isEqualTo(connection);
    }

    @Test
    void shouldSetAndGetRepositoryUUID() {
        ProjectVcsConnectionBinding binding = new ProjectVcsConnectionBinding();
        UUID repoUuid = UUID.randomUUID();
        
        binding.setRepositoryUUID(repoUuid);
        
        assertThat(binding.getRepositoryUUID()).isEqualTo(repoUuid);
    }

    @Test
    void shouldSetAndGetWorkspace() {
        ProjectVcsConnectionBinding binding = new ProjectVcsConnectionBinding();
        binding.setWorkspace("my-workspace");
        
        assertThat(binding.getWorkspace()).isEqualTo("my-workspace");
        assertThat(binding.getRepoWorkspace()).isEqualTo("my-workspace");
    }

    @Test
    void shouldSetAndGetRepoSlug() {
        ProjectVcsConnectionBinding binding = new ProjectVcsConnectionBinding();
        binding.setRepoSlug("my-repo");
        
        assertThat(binding.getRepoSlug()).isEqualTo("my-repo");
    }

    @Test
    void shouldSetAndGetDisplayName() {
        ProjectVcsConnectionBinding binding = new ProjectVcsConnectionBinding();
        binding.setDisplayName("My Repository Display Name");
        
        assertThat(binding.getDisplayName()).isEqualTo("My Repository Display Name");
    }

    @Test
    void shouldImplementVcsRepoInfo() {
        ProjectVcsConnectionBinding binding = new ProjectVcsConnectionBinding();
        VcsConnection connection = new VcsConnection();
        
        binding.setWorkspace("acme-workspace");
        binding.setRepoSlug("backend-api");
        binding.setVcsConnection(connection);
        
        assertThat(binding.getRepoWorkspace()).isEqualTo("acme-workspace");
        assertThat(binding.getRepoSlug()).isEqualTo("backend-api");
        assertThat(binding.getVcsConnection()).isEqualTo(connection);
    }

    @Test
    void shouldBindProjectToVcsRepository() {
        ProjectVcsConnectionBinding binding = new ProjectVcsConnectionBinding();
        Project project = new Project();
        VcsConnection connection = new VcsConnection();
        UUID repoUuid = UUID.fromString("123e4567-e89b-12d3-a456-426614174000");
        
        binding.setProject(project);
        binding.setVcsProvider(EVcsProvider.GITHUB);
        binding.setVcsConnection(connection);
        binding.setRepositoryUUID(repoUuid);
        binding.setWorkspace("my-org");
        binding.setRepoSlug("awesome-project");
        binding.setDisplayName("Awesome Project");
        
        assertThat(binding.getProject()).isEqualTo(project);
        assertThat(binding.getVcsProvider()).isEqualTo(EVcsProvider.GITHUB);
        assertThat(binding.getVcsConnection()).isEqualTo(connection);
        assertThat(binding.getRepositoryUUID()).isEqualTo(repoUuid);
        assertThat(binding.getWorkspace()).isEqualTo("my-org");
        assertThat(binding.getRepoSlug()).isEqualTo("awesome-project");
        assertThat(binding.getDisplayName()).isEqualTo("Awesome Project");
    }
}
