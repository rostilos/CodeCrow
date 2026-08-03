package org.rostilos.codecrow.vcsclient.gitlab;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.FormBody;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;

import java.io.IOException;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.time.LocalDateTime;
import java.util.Objects;

/**
 * Owns GitLab OAuth endpoint construction and token operations.
 *
 * <p>This client is separate from {@link GitLabClient} because an authorized
 * API client cannot exist until the OAuth code has been exchanged. Both use
 * the same instance-root normalization.</p>
 */
public final class GitLabOAuthClient {

    private static final int DEFAULT_EXPIRES_IN_SECONDS = 7200;

    private final OkHttpClient httpClient;
    private final ObjectMapper objectMapper;

    public GitLabOAuthClient(OkHttpClient httpClient) {
        this(httpClient, new ObjectMapper());
    }

    GitLabOAuthClient(OkHttpClient httpClient, ObjectMapper objectMapper) {
        this.httpClient = Objects.requireNonNull(httpClient, "httpClient")
                .newBuilder()
                .followRedirects(false)
                .followSslRedirects(false)
                .build();
        this.objectMapper = Objects.requireNonNull(objectMapper, "objectMapper");
    }

    public static String authorizationUrl(
            GitLabOAuthProvider provider,
            String redirectUri,
            String state,
            String scopes
    ) {
        return provider.instanceBaseUrl() + "/oauth/authorize"
                + "?client_id=" + encode(provider.clientId())
                + "&redirect_uri=" + encode(redirectUri)
                + "&response_type=code"
                + "&scope=" + encode(scopes)
                + "&state=" + encode(state);
    }

    public GitLabOAuthTokens exchangeAuthorizationCode(
            GitLabOAuthProvider provider,
            String code,
            String redirectUri
    ) throws IOException {
        RequestBody body = new FormBody.Builder()
                .add("client_id", provider.clientId())
                .add("client_secret", provider.clientSecret())
                .add("code", code)
                .add("grant_type", "authorization_code")
                .add("redirect_uri", redirectUri)
                .build();
        return requestTokens(provider, body, "exchange GitLab authorization code");
    }

    public GitLabOAuthTokens refreshToken(
            GitLabOAuthProvider provider,
            String refreshToken,
            String redirectUri
    ) throws IOException {
        RequestBody body = new FormBody.Builder()
                .add("grant_type", "refresh_token")
                .add("refresh_token", refreshToken)
                .add("client_id", provider.clientId())
                .add("client_secret", provider.clientSecret())
                .add("redirect_uri", redirectUri)
                .build();
        return requestTokens(provider, body, "refresh GitLab token");
    }

    public void revokeToken(
            GitLabOAuthProvider provider,
            String accessToken
    ) throws IOException {
        RequestBody body = new FormBody.Builder()
                .add("client_id", provider.clientId())
                .add("client_secret", provider.clientSecret())
                .add("token", accessToken)
                .build();
        Request request = new Request.Builder()
                .url(provider.instanceBaseUrl() + "/oauth/revoke")
                .header("Accept", "application/json")
                .post(body)
                .build();

        try (Response response = httpClient.newCall(request).execute()) {
            if (!response.isSuccessful()) {
                String responseBody = response.body() == null ? "" : response.body().string();
                throw new IOException("Failed to revoke GitLab token: "
                        + response.code()
                        + (responseBody.isBlank() ? "" : " - " + responseBody));
            }
        }
    }

    private GitLabOAuthTokens requestTokens(
            GitLabOAuthProvider provider,
            RequestBody body,
            String operation
    ) throws IOException {
        Request request = new Request.Builder()
                .url(provider.instanceBaseUrl() + "/oauth/token")
                .header("Accept", "application/json")
                .post(body)
                .build();

        try (Response response = httpClient.newCall(request).execute()) {
            String responseBody = response.body() == null ? "" : response.body().string();
            if (!response.isSuccessful()) {
                throw new IOException("Failed to " + operation + ": "
                        + response.code()
                        + (responseBody.isBlank() ? "" : " - " + responseBody));
            }

            JsonNode json = objectMapper.readTree(responseBody);
            if (json.hasNonNull("error")) {
                String description = json.path("error_description").asText("");
                throw new IOException("GitLab OAuth error: "
                        + json.path("error").asText()
                        + (description.isBlank() ? "" : " - " + description));
            }

            String accessToken = json.path("access_token").asText("");
            if (accessToken.isBlank()) {
                throw new IOException("GitLab OAuth response did not contain an access token");
            }

            String refreshToken = optionalText(json, "refresh_token");
            int expiresIn = json.has("expires_in")
                    ? json.path("expires_in").asInt(DEFAULT_EXPIRES_IN_SECONDS)
                    : DEFAULT_EXPIRES_IN_SECONDS;
            String scopes = optionalText(json, "scope");
            if (scopes == null) {
                scopes = optionalText(json, "scopes");
            }

            return new GitLabOAuthTokens(
                    accessToken,
                    refreshToken,
                    LocalDateTime.now().plusSeconds(expiresIn),
                    scopes);
        }
    }

    private static String optionalText(JsonNode json, String field) {
        if (!json.hasNonNull(field)) {
            return null;
        }
        String value = json.path(field).asText("");
        return value.isBlank() ? null : value;
    }

    private static String encode(String value) {
        return URLEncoder.encode(Objects.requireNonNull(value), StandardCharsets.UTF_8);
    }
}
