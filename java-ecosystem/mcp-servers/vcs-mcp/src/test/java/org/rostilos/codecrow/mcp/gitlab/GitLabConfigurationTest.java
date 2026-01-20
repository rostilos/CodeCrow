package org.rostilos.codecrow.mcp.gitlab;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("GitLabConfiguration")
class GitLabConfigurationTest {

    @Test
    @DisplayName("should create with all parameters")
    void shouldCreateWithAllParameters() {
        String accessToken = "glpat-xxxx";
        String namespace = "codecrow-group";
        String project = "codecrow-project";
        String mrIid = "99";
        
        GitLabConfiguration config = new GitLabConfiguration(accessToken, namespace, project, mrIid);
        
        assertThat(config.getAccessToken()).isEqualTo(accessToken);
        assertThat(config.getNamespace()).isEqualTo(namespace);
        assertThat(config.getProject()).isEqualTo(project);
        assertThat(config.getMrIid()).isEqualTo(mrIid);
    }

    @Test
    @DisplayName("should allow null values")
    void shouldAllowNullValues() {
        GitLabConfiguration config = new GitLabConfiguration(null, null, null, null);
        
        assertThat(config.getAccessToken()).isNull();
        assertThat(config.getNamespace()).isNull();
        assertThat(config.getProject()).isNull();
        assertThat(config.getMrIid()).isNull();
    }

    @Test
    @DisplayName("getAccessToken should return access token")
    void getAccessTokenShouldReturnAccessToken() {
        GitLabConfiguration config = new GitLabConfiguration("my-token", "n", "p", "1");
        
        assertThat(config.getAccessToken()).isEqualTo("my-token");
    }

    @Test
    @DisplayName("getNamespace should return namespace")
    void getNamespaceShouldReturnNamespace() {
        GitLabConfiguration config = new GitLabConfiguration("t", "test-namespace", "p", "1");
        
        assertThat(config.getNamespace()).isEqualTo("test-namespace");
    }

    @Test
    @DisplayName("getProject should return project")
    void getProjectShouldReturnProject() {
        GitLabConfiguration config = new GitLabConfiguration("t", "n", "test-project", "1");
        
        assertThat(config.getProject()).isEqualTo("test-project");
    }

    @Test
    @DisplayName("getMrIid should return MR IID")
    void getMrIidShouldReturnMrIid() {
        GitLabConfiguration config = new GitLabConfiguration("t", "n", "p", "456");
        
        assertThat(config.getMrIid()).isEqualTo("456");
    }
}
