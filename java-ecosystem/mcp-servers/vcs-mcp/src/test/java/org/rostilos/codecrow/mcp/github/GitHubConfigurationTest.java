package org.rostilos.codecrow.mcp.github;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("GitHubConfiguration")
class GitHubConfigurationTest {

    @Test
    @DisplayName("should create with all parameters")
    void shouldCreateWithAllParameters() {
        String accessToken = "ghp_test123";
        String owner = "codecrow-org";
        String repo = "codecrow-repo";
        String prNumber = "42";
        
        GitHubConfiguration config = new GitHubConfiguration(accessToken, owner, repo, prNumber);
        
        assertThat(config.getAccessToken()).isEqualTo(accessToken);
        assertThat(config.getOwner()).isEqualTo(owner);
        assertThat(config.getRepo()).isEqualTo(repo);
        assertThat(config.getPrNumber()).isEqualTo(prNumber);
    }

    @Test
    @DisplayName("should allow null values")
    void shouldAllowNullValues() {
        GitHubConfiguration config = new GitHubConfiguration(null, null, null, null);
        
        assertThat(config.getAccessToken()).isNull();
        assertThat(config.getOwner()).isNull();
        assertThat(config.getRepo()).isNull();
        assertThat(config.getPrNumber()).isNull();
    }

    @Test
    @DisplayName("getAccessToken should return access token")
    void getAccessTokenShouldReturnAccessToken() {
        GitHubConfiguration config = new GitHubConfiguration("my-token", "o", "r", "1");
        
        assertThat(config.getAccessToken()).isEqualTo("my-token");
    }

    @Test
    @DisplayName("getOwner should return owner")
    void getOwnerShouldReturnOwner() {
        GitHubConfiguration config = new GitHubConfiguration("t", "test-owner", "r", "1");
        
        assertThat(config.getOwner()).isEqualTo("test-owner");
    }

    @Test
    @DisplayName("getRepo should return repo")
    void getRepoShouldReturnRepo() {
        GitHubConfiguration config = new GitHubConfiguration("t", "o", "test-repo", "1");
        
        assertThat(config.getRepo()).isEqualTo("test-repo");
    }

    @Test
    @DisplayName("getPrNumber should return PR number")
    void getPrNumberShouldReturnPrNumber() {
        GitHubConfiguration config = new GitHubConfiguration("t", "o", "r", "123");
        
        assertThat(config.getPrNumber()).isEqualTo("123");
    }
}
