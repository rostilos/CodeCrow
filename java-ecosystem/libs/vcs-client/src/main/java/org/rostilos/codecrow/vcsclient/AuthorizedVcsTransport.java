package org.rostilos.codecrow.vcsclient;

import okhttp3.OkHttpClient;

import java.util.Objects;
import java.util.Optional;

/**
 * Provider-neutral authorized transport material.
 *
 * <p>The HTTP client is used for provider APIs. The optional access token is
 * retained only for provider implementations that also need an authenticated
 * non-HTTP-client transport, such as a Git smart-protocol checkout. Secret
 * material is deliberately excluded from {@link #toString()}.</p>
 */
public final class AuthorizedVcsTransport {
    private final OkHttpClient httpClient;
    private final String accessToken;

    private AuthorizedVcsTransport(OkHttpClient httpClient, String accessToken) {
        this.httpClient = Objects.requireNonNull(httpClient, "httpClient");
        this.accessToken = normalize(accessToken);
    }

    public static AuthorizedVcsTransport httpOnly(OkHttpClient httpClient) {
        return new AuthorizedVcsTransport(httpClient, null);
    }

    public static AuthorizedVcsTransport withAccessToken(OkHttpClient httpClient, String accessToken) {
        String normalizedToken = normalize(accessToken);
        if (normalizedToken == null) {
            throw new IllegalArgumentException("Access token cannot be null or blank");
        }
        return new AuthorizedVcsTransport(httpClient, normalizedToken);
    }

    public OkHttpClient httpClient() {
        return httpClient;
    }

    public Optional<String> accessToken() {
        return Optional.ofNullable(accessToken);
    }

    @Override
    public String toString() {
        return "AuthorizedVcsTransport{httpClient=" + httpClient.getClass().getSimpleName()
                + ", accessToken=" + (accessToken == null ? "absent" : "redacted") + '}';
    }

    private static String normalize(String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        return value;
    }
}
