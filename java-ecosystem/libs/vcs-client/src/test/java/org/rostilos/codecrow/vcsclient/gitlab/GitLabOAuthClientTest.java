package org.rostilos.codecrow.vcsclient.gitlab;

import okhttp3.OkHttpClient;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.core.dto.admin.GitLabSettingsDTO;

import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;

class GitLabOAuthClientTest {

    @Test
    void allOAuthOperationsUseTheConfiguredInstanceRoot() throws Exception {
        try (MockWebServer gitLab = new MockWebServer()) {
            gitLab.enqueue(jsonResponse("""
                    {
                      "access_token": "access-one",
                      "refresh_token": "refresh-one",
                      "expires_in": 3600,
                      "scope": "api read_user"
                    }
                    """));
            gitLab.enqueue(jsonResponse("""
                    {
                      "access_token": "access-two",
                      "refresh_token": "refresh-two",
                      "expires_in": 1800
                    }
                    """));
            gitLab.enqueue(new MockResponse().setResponseCode(200));
            gitLab.start();

            String instanceBase = gitLab.url("/nested/gitlab/api/v4").toString();
            GitLabOAuthClient client = GitLabClientFactory.createOAuthClient(
                    new OkHttpClient());
            GitLabOAuthProvider provider = GitLabOAuthProvider.from(
                    new GitLabSettingsDTO(
                            "client-id",
                            "client-secret",
                            instanceBase));
            LocalDateTime beforeRequest = LocalDateTime.now();

            GitLabOAuthTokens exchanged = client.exchangeAuthorizationCode(
                    provider,
                    "authorization-code",
                    "https://codecrow.example/callback");
            GitLabOAuthTokens refreshed = client.refreshToken(
                    provider,
                    "refresh-one",
                    "https://codecrow.example/callback");
            client.revokeToken(
                    provider,
                    "access-two");

            assertThat(exchanged.accessToken()).isEqualTo("access-one");
            assertThat(exchanged.refreshToken()).isEqualTo("refresh-one");
            assertThat(exchanged.scopes()).isEqualTo("api read_user");
            assertThat(exchanged.expiresAt()).isAfterOrEqualTo(beforeRequest.plusSeconds(3599));
            assertThat(refreshed.accessToken()).isEqualTo("access-two");

            RecordedRequest exchangeRequest = gitLab.takeRequest();
            RecordedRequest refreshRequest = gitLab.takeRequest();
            RecordedRequest revokeRequest = gitLab.takeRequest();
            assertThat(exchangeRequest.getPath()).isEqualTo("/nested/gitlab/oauth/token");
            assertThat(exchangeRequest.getBody().readUtf8())
                    .contains("grant_type=authorization_code")
                    .contains("code=authorization-code")
                    .contains("client_secret=client-secret");
            assertThat(refreshRequest.getPath()).isEqualTo("/nested/gitlab/oauth/token");
            assertThat(refreshRequest.getBody().readUtf8())
                    .contains("grant_type=refresh_token")
                    .contains("refresh_token=refresh-one")
                    .contains("client_secret=client-secret");
            assertThat(revokeRequest.getPath()).isEqualTo("/nested/gitlab/oauth/revoke");
            assertThat(revokeRequest.getBody().readUtf8())
                    .contains("token=access-two")
                    .contains("client_secret=client-secret");
        }
    }

    @Test
    void authorizationUrlUsesNormalizedInstanceRoot() {
        String url = GitLabOAuthClient.authorizationUrl(
                GitLabOAuthProvider.from(new GitLabSettingsDTO(
                        "client id",
                        "client secret",
                        "https://gitlab.example/root/api/v4/")),
                "https://codecrow.example/callback",
                "state value",
                "api read_user");

        assertThat(url).isEqualTo(
                "https://gitlab.example/root/oauth/authorize"
                        + "?client_id=client+id"
                        + "&redirect_uri=https%3A%2F%2Fcodecrow.example%2Fcallback"
                        + "&response_type=code"
                        + "&scope=api+read_user"
                        + "&state=state+value");
    }

    @Test
    void tokenRequestDoesNotFollowRedirects() throws Exception {
        try (MockWebServer issuer = new MockWebServer();
             MockWebServer redirectedHost = new MockWebServer()) {
            issuer.start();
            redirectedHost.start();
            issuer.enqueue(new MockResponse()
                    .setResponseCode(307)
                    .setHeader("Location", redirectedHost.url("/oauth/token")));

            GitLabOAuthClient client = new GitLabOAuthClient(new OkHttpClient());
            GitLabOAuthProvider provider = GitLabOAuthProvider.from(
                    new GitLabSettingsDTO(
                            "client-id",
                            "client-secret",
                            issuer.url("/").toString()));

            org.assertj.core.api.Assertions.assertThatThrownBy(() ->
                    client.exchangeAuthorizationCode(
                            provider,
                            "authorization-code",
                            "https://codecrow.example/callback"))
                    .isInstanceOf(java.io.IOException.class)
                    .hasMessageContaining("307");

            assertThat(issuer.getRequestCount()).isEqualTo(1);
            assertThat(redirectedHost.getRequestCount()).isZero();
        }
    }

    private static MockResponse jsonResponse(String body) {
        return new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(body);
    }
}
