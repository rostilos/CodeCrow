package org.rostilos.codecrow.core.model.vcs.config.github;

import org.junit.jupiter.api.Test;

import java.util.Arrays;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class GitHubConfigTest {

    @Test
    void testFullConstructor() {
        List<String> allowedRepos = Arrays.asList("repo1", "repo2");
        GitHubConfig config = new GitHubConfig("github-token", "org-123", allowedRepos);
        
        assertThat(config.accessToken()).isEqualTo("github-token");
        assertThat(config.organizationId()).isEqualTo("org-123");
        assertThat(config.allowedRepos()).isEqualTo(allowedRepos);
    }

    @Test
    void testWithNullAllowedRepos() {
        GitHubConfig config = new GitHubConfig("token", "org-id", null);
        
        assertThat(config.accessToken()).isEqualTo("token");
        assertThat(config.organizationId()).isEqualTo("org-id");
        assertThat(config.allowedRepos()).isNull();
    }

    @Test
    void testRecordEquality() {
        List<String> allowedRepos = Arrays.asList("repo1", "repo2");
        GitHubConfig config1 = new GitHubConfig("token", "org-id", allowedRepos);
        GitHubConfig config2 = new GitHubConfig("token", "org-id", allowedRepos);
        
        assertThat(config1).isEqualTo(config2);
        assertThat(config1.hashCode()).isEqualTo(config2.hashCode());
    }

    @Test
    void testRecordInequality() {
        GitHubConfig config1 = new GitHubConfig("token1", "org-id", null);
        GitHubConfig config2 = new GitHubConfig("token2", "org-id", null);
        
        assertThat(config1).isNotEqualTo(config2);
    }

    @Test
    void testWithEmptyAllowedRepos() {
        List<String> emptyList = Arrays.asList();
        GitHubConfig config = new GitHubConfig("token", "org-id", emptyList);
        
        assertThat(config.allowedRepos()).isEmpty();
    }

    @Test
    void testWithMultipleAllowedRepos() {
        List<String> allowedRepos = Arrays.asList("repo1", "repo2", "repo3", "repo4");
        GitHubConfig config = new GitHubConfig("token", "org-id", allowedRepos);
        
        assertThat(config.allowedRepos()).hasSize(4);
        assertThat(config.allowedRepos()).containsExactly("repo1", "repo2", "repo3", "repo4");
    }

    @Test
    void testToString() {
        GitHubConfig config = new GitHubConfig("token", "org-id", Arrays.asList("repo1"));
        String str = config.toString();
        
        assertThat(str).contains("GitHubConfig");
        assertThat(str).contains("token");
        assertThat(str).contains("org-id");
    }
}
