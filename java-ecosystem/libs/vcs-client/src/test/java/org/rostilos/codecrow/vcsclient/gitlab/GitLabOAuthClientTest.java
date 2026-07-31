package org.rostilos.codecrow.vcsclient.gitlab;

import okhttp3.OkHttpClient;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.Test;

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
            LocalDateTime beforeRequest = LocalDateTime.now();

            GitLabOAuthTokens exchanged = client.exchangeAuthorizationCode(
                    instanceBase,
                    "client-id",
                    "client-secret",
                    "authorization-code",
                    "https://codecrow.example/callback");
            GitLabOAuthTokens refreshed = client.refreshToken(
                    instanceBase,
                    "client-id",
                    "client-secret",
                    "refresh-one",
                    "https://codecrow.example/callback");
            client.revokeToken(
                    instanceBase,
                    "client-id",
                    "client-secret",
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
                    .contains("code=authorization-code");
            assertThat(refreshRequest.getPath()).isEqualTo("/nested/gitlab/oauth/token");
            assertThat(refreshRequest.getBody().readUtf8())
                    .contains("grant_type=refresh_token")
                    .contains("refresh_token=refresh-one");
            assertThat(revokeRequest.getPath()).isEqualTo("/nested/gitlab/oauth/revoke");
            assertThat(revokeRequest.getBody().readUtf8())
                    .contains("token=access-two");
        }
    }

    @Test
    void authorizationUrlUsesNormalizedInstanceRoot() {
        String url = GitLabOAuthClient.authorizationUrl(
                "https://gitlab.example/root/api/v4/",
                "client id",
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

    private static MockResponse jsonResponse(String body) {
        return new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(body);
    }
}
