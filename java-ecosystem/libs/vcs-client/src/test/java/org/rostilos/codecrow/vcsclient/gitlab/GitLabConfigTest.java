package org.rostilos.codecrow.vcsclient.gitlab;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("GitLabConfig")
class GitLabConfigTest {

    @Test
    @DisplayName("should have correct API base URL")
    void shouldHaveCorrectApiBaseUrl() {
        assertThat(GitLabConfig.API_BASE).isEqualTo("https://gitlab.com/api/v4");
    }

    @Test
    @DisplayName("should have correct default page size")
    void shouldHaveCorrectDefaultPageSize() {
        assertThat(GitLabConfig.DEFAULT_PAGE_SIZE).isEqualTo(20);
    }

    @Test
    @DisplayName("API base URL should be HTTPS")
    void apiBaseUrlShouldBeHttps() {
        assertThat(GitLabConfig.API_BASE).startsWith("https://");
    }

    @Test
    @DisplayName("API base URL should target gitlab.com")
    void apiBaseUrlShouldTargetGitLab() {
        assertThat(GitLabConfig.API_BASE).contains("gitlab.com");
    }

    @Test
    @DisplayName("API base URL should use v4 API version")
    void apiBaseUrlShouldUseV4ApiVersion() {
        assertThat(GitLabConfig.API_BASE).endsWith("/v4");
    }
}
