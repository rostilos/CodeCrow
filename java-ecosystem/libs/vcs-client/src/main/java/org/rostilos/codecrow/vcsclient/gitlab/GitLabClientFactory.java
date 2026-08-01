package org.rostilos.codecrow.vcsclient.gitlab;

import okhttp3.OkHttpClient;
import okhttp3.Request;

import java.util.concurrent.TimeUnit;

/**
 * Creates authorized instances of the shared GitLab client.
 */
public final class GitLabClientFactory {

    private GitLabClientFactory() {
    }

    public static GitLabClient createWithAccessToken(
            String accessToken,
            String instanceBaseUrl
    ) {
        return new GitLabClient(
                createAuthorizedHttpClient(accessToken),
                instanceBaseUrl);
    }

    public static OkHttpClient createAuthorizedHttpClient(String accessToken) {
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
                            .header("Accept", "application/json")
                            .build();
                    return chain.proceed(authorized);
                })
                .build();
    }

    /**
     * Create the shared unauthenticated client used for GitLab OAuth flows.
     */
    public static GitLabOAuthClient createOAuthClient() {
        return createOAuthClient(new OkHttpClient.Builder()
                .connectTimeout(10, TimeUnit.SECONDS)
                .readTimeout(15, TimeUnit.SECONDS)
                .writeTimeout(10, TimeUnit.SECONDS)
                .build());
    }

    public static GitLabOAuthClient createOAuthClient(OkHttpClient httpClient) {
        return new GitLabOAuthClient(httpClient.newBuilder()
                .followRedirects(false)
                .followSslRedirects(false)
                .build());
    }
}
