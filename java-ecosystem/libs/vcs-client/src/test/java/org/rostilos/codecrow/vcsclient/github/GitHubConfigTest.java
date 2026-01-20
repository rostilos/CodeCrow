package org.rostilos.codecrow.vcsclient.github;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("GitHubConfig")
class GitHubConfigTest {

    @Test
    @DisplayName("should have correct API base URL")
    void shouldHaveCorrectApiBaseUrl() {
        assertThat(GitHubConfig.API_BASE).isEqualTo("https://api.github.com");
    }

    @Test
    @DisplayName("should have correct OAuth authorize URL")
    void shouldHaveCorrectOAuthAuthorizeUrl() {
        assertThat(GitHubConfig.OAUTH_AUTHORIZE_URL).isEqualTo("https://github.com/login/oauth/authorize");
    }

    @Test
    @DisplayName("should have correct OAuth token URL")
    void shouldHaveCorrectOAuthTokenUrl() {
        assertThat(GitHubConfig.OAUTH_TOKEN_URL).isEqualTo("https://github.com/login/oauth/access_token");
    }

    @Test
    @DisplayName("should have correct app installations URL")
    void shouldHaveCorrectAppInstallationsUrl() {
        assertThat(GitHubConfig.APP_INSTALLATIONS_URL).isEqualTo("https://api.github.com/app/installations");
    }

    @Test
    @DisplayName("should have correct default page size")
    void shouldHaveCorrectDefaultPageSize() {
        assertThat(GitHubConfig.DEFAULT_PAGE_SIZE).isEqualTo(30);
    }

    @Test
    @DisplayName("all URLs should be HTTPS")
    void allUrlsShouldBeHttps() {
        assertThat(GitHubConfig.API_BASE).startsWith("https://");
        assertThat(GitHubConfig.OAUTH_AUTHORIZE_URL).startsWith("https://");
        assertThat(GitHubConfig.OAUTH_TOKEN_URL).startsWith("https://");
        assertThat(GitHubConfig.APP_INSTALLATIONS_URL).startsWith("https://");
    }
}
