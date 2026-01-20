package org.rostilos.codecrow.core.model.vcs.config.gitlab;

import org.junit.jupiter.api.Test;

import java.util.Arrays;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class GitLabConfigTest {

    @Test
    void testFullConstructor() {
        List<String> allowedRepos = Arrays.asList("repo1", "repo2");
        GitLabConfig config = new GitLabConfig("token", "group-id", allowedRepos, "https://gitlab.custom.com");
        
        assertThat(config.accessToken()).isEqualTo("token");
        assertThat(config.groupId()).isEqualTo("group-id");
        assertThat(config.allowedRepos()).isEqualTo(allowedRepos);
        assertThat(config.baseUrl()).isEqualTo("https://gitlab.custom.com");
    }

    @Test
    void testConstructorWithoutBaseUrl() {
        List<String> allowedRepos = Arrays.asList("repo1");
        GitLabConfig config = new GitLabConfig("token", "group-id", allowedRepos);
        
        assertThat(config.accessToken()).isEqualTo("token");
        assertThat(config.groupId()).isEqualTo("group-id");
        assertThat(config.allowedRepos()).isEqualTo(allowedRepos);
        assertThat(config.baseUrl()).isNull();
    }

    @Test
    void testEffectiveBaseUrl_WithCustomUrl() {
        GitLabConfig config = new GitLabConfig("token", "group-id", null, "https://gitlab.mycompany.com");
        assertThat(config.effectiveBaseUrl()).isEqualTo("https://gitlab.mycompany.com");
    }

    @Test
    void testEffectiveBaseUrl_WithNullBaseUrl() {
        GitLabConfig config = new GitLabConfig("token", "group-id", null, null);
        assertThat(config.effectiveBaseUrl()).isEqualTo("https://gitlab.com");
    }

    @Test
    void testEffectiveBaseUrl_WithBlankBaseUrl() {
        GitLabConfig config = new GitLabConfig("token", "group-id", null, "  ");
        assertThat(config.effectiveBaseUrl()).isEqualTo("https://gitlab.com");
    }

    @Test
    void testEffectiveBaseUrl_WithEmptyBaseUrl() {
        GitLabConfig config = new GitLabConfig("token", "group-id", null, "");
        assertThat(config.effectiveBaseUrl()).isEqualTo("https://gitlab.com");
    }

    @Test
    void testRecordEquality() {
        List<String> allowedRepos = Arrays.asList("repo1", "repo2");
        GitLabConfig config1 = new GitLabConfig("token", "group-id", allowedRepos, "https://gitlab.com");
        GitLabConfig config2 = new GitLabConfig("token", "group-id", allowedRepos, "https://gitlab.com");
        
        assertThat(config1).isEqualTo(config2);
        assertThat(config1.hashCode()).isEqualTo(config2.hashCode());
    }

    @Test
    void testRecordInequality() {
        GitLabConfig config1 = new GitLabConfig("token1", "group-id", null, null);
        GitLabConfig config2 = new GitLabConfig("token2", "group-id", null, null);
        
        assertThat(config1).isNotEqualTo(config2);
    }

    @Test
    void testWithNullAllowedRepos() {
        GitLabConfig config = new GitLabConfig("token", "group-id", null, "https://gitlab.com");
        assertThat(config.allowedRepos()).isNull();
    }

    @Test
    void testSelfHostedInstance() {
        List<String> allowedRepos = Arrays.asList("project1", "project2");
        GitLabConfig config = new GitLabConfig("self-hosted-token", "my-group", allowedRepos, "https://gitlab.internal.company");
        
        assertThat(config.effectiveBaseUrl()).isEqualTo("https://gitlab.internal.company");
        assertThat(config.groupId()).isEqualTo("my-group");
    }
}
