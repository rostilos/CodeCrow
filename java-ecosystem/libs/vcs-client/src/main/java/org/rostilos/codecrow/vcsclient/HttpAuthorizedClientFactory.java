package org.rostilos.codecrow.vcsclient;

import okhttp3.*;
import org.rostilos.codecrow.core.model.vcs.EVcsProvider;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import java.util.stream.Collectors;

@Component
public class HttpAuthorizedClientFactory {
    private final Map<EVcsProvider, HttpAuthorizedClient> delegateMap;

    public HttpAuthorizedClientFactory(
            List<HttpAuthorizedClient> delegates
    ) {
        this.delegateMap = delegates.stream().collect(Collectors.toMap(HttpAuthorizedClient::getGitPlatform, d -> d));
    }

    public OkHttpClient createClient(
            String clientId,
            String clientSecret,
            String gitPlatformId
    ) {
        validateSettings(clientId, clientSecret);
        EVcsProvider gitProvider = EVcsProvider.fromId(gitPlatformId);
        HttpAuthorizedClient delegate = delegateMap.get(gitProvider);
        if (delegate == null) {
            throw new IllegalArgumentException("No factory for Git Provider: " + gitPlatformId);
        }

        return delegate.createClient(clientId, clientSecret);
    }

    /**
     * Create an OkHttpClient that uses a bearer token for authentication.
     * This is used for OAuth2 access tokens (e.g., from Bitbucket App installations).
     * 
     * @param accessToken the OAuth2 access token
     * @return configured OkHttpClient with bearer token authentication
     */
    public OkHttpClient createClientWithBearerToken(String accessToken) {
        if (accessToken == null || accessToken.isBlank()) {
            throw new IllegalArgumentException("Access token cannot be null or empty");
        }
        
        return new OkHttpClient.Builder()
                .connectTimeout(30, TimeUnit.SECONDS)
                .readTimeout(60, TimeUnit.SECONDS)
                .writeTimeout(60, TimeUnit.SECONDS)
                .addInterceptor(chain -> {
                    Request original = chain.request();
                    Request authorized = original.newBuilder()
                            .header("Authorization", "Bearer " + accessToken)
                            .build();
                    return chain.proceed(authorized);
                })
                .build();
    }

    /**
     * Create an OkHttpClient configured for GitHub API with bearer token authentication.
     * 
     * @param accessToken the GitHub personal access token or OAuth token
     * @return configured OkHttpClient for GitHub API
     */
    public OkHttpClient createGitHubClient(String accessToken) {
        if (accessToken == null || accessToken.isBlank()) {
            throw new IllegalArgumentException("Access token cannot be null or empty");
        }
        
        return new OkHttpClient.Builder()
                .connectTimeout(30, TimeUnit.SECONDS)
                .readTimeout(60, TimeUnit.SECONDS)
                .writeTimeout(60, TimeUnit.SECONDS)
                .addInterceptor(chain -> {
                    Request original = chain.request();
                    Request authorized = original.newBuilder()
                            .header("Authorization", "Bearer " + accessToken)
                            .header("Accept", "application/vnd.github+json")
                            .header("X-GitHub-Api-Version", "2022-11-28")
                            .build();
                    return chain.proceed(authorized);
                })
                .build();
    }

    private void validateSettings(String clientId, String clientSecret) {
        if (clientId.isEmpty()) {
            throw new IllegalArgumentException("No ClientId key has been set for Bitbucket connections");
        }
        if (clientSecret.isEmpty()) {
            throw new IllegalArgumentException("No ClientSecret has been set for Bitbucket connections");
        }
    }
}
