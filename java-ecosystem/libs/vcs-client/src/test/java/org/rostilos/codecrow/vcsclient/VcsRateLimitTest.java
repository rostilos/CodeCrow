package org.rostilos.codecrow.vcsclient;

import okhttp3.*;
import org.junit.jupiter.api.Test;
import org.rostilos.codecrow.vcsclient.bitbucket.cloud.BitbucketCloudClient;
import org.rostilos.codecrow.vcsclient.github.GitHubClient;
import org.rostilos.codecrow.vcsclient.gitlab.GitLabClient;

import java.io.IOException;
import java.time.Instant;
import java.util.List;
import java.util.stream.IntStream;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class VcsRateLimitTest {
    private static final Instant NOW = Instant.parse("2026-11-03T12:00:00Z");

    @Test
    void primary403PreservesResetAndRequestIdentityButPermission403IsNotThrottling() {
        try (Response response = response("https://api.github.com/repos/owner/repo/compare/a...b", 403,
                "{\"message\":\"API rate limit exceeded for installation ID 123\"}")
                .header("X-RateLimit-Remaining", "0").header("X-RateLimit-Reset", "1793709000")
                .header("X-RateLimit-Resource", "core").header("X-GitHub-Request-Id", "request-123").build()) {
            var failure = VcsRateLimitException.fromResponse(response, response.body().string(), NOW);
            assertThat(failure).isNotNull();
            assertThat(failure.retryAt()).isEqualTo(Instant.ofEpochSecond(1793709000));
            assertThat(failure.providerRequestId()).isEqualTo("request-123");
            assertThat(failure.sharedAcrossResources()).isFalse();
            assertThat(VcsRateLimitException.find(new IOException("wrapped", failure))).isSameAs(failure);
        } catch (IOException e) {
            throw new AssertionError(e);
        }
        try (Response forbidden = response("https://api.github.com/repos/owner/repo", 403, "").build()) {
            assertThat(VcsRateLimitException.fromResponse(forbidden, "Resource not accessible by integration", NOW)).isNull();
        }
    }

    @Test
    void parsesRetryAfterSecondsDatesAndMissingOrMalformedMetadata() {
        for (String value : List.of("120", "Tue, 3 Nov 2026 12:02:00 GMT")) {
            try (Response response = response("https://api.bitbucket.org/2.0/repositories/o/r", 429, "")
                    .header("Retry-After", value).build()) {
                assertThat(VcsRateLimitException.fromResponse(response, "", NOW).retryAt()).isEqualTo(NOW.plusSeconds(120));
            }
        }
        try (Response response = response("https://api.github.com/graphql", 403, "")
                .header("Retry-After", "invalid").build()) {
            var failure = VcsRateLimitException.fromResponse(response, "secondary rate limit exceeded", NOW);
            assertThat(failure.retryAt()).isEqualTo(NOW.plusSeconds(60));
            assertThat(failure.sharedAcrossResources()).isTrue();
        }
    }

    @Test
    void graphqlLimitDoesNotFallBackToFiftyRestRequests() throws Exception {
        for (int status : List.of(403, 429, 200)) {
            OkHttpClient http = mock(OkHttpClient.class);
            String body = status == 200
                    ? "{\"errors\":[{\"type\":\"RATE_LIMITED\",\"message\":\"API rate limit exceeded\"}]}"
                    : "{\"message\":\"API rate limit exceeded\"}";
            when(http.newCall(any())).thenAnswer(invocation -> {
                Request request = invocation.getArgument(0);
                Call call = mock(Call.class);
                when(call.execute()).thenReturn(response(request.url().toString(), status, body)
                        .header("Retry-After", "3600").build());
                return call;
            });
            List<String> paths = IntStream.range(0, 50).mapToObj(i -> "src/File" + i + ".java").toList();
            assertThatThrownBy(() -> new GitHubClient(http).getFileContents("o", "r", paths, "head", 1000))
                    .isInstanceOf(VcsRateLimitException.class);
            verify(http, times(1)).newCall(any());
        }
    }

    @Test
    void successfulGraphqlSourceMentioningRateLimitsIsNotAnError() throws Exception {
        String body = "{\"data\":{\"repository\":{\"file0\":{\"text\":\"API rate limit exceeded\",\"byteSize\":23}}}}";
        try (Response response = response("https://api.github.com/graphql", 200, body).build()) {
            assertThat(VcsRateLimitException.fromResponse(response, body, NOW)).isNull();
        }
    }

    @Test
    void bitbucketAndGitlabStopTheBatchAfterFirstThrottle() throws Exception {
        for (boolean bitbucket : List.of(true, false)) {
            OkHttpClient http = mock(OkHttpClient.class);
            when(http.newCall(any())).thenAnswer(invocation -> {
                Request request = invocation.getArgument(0);
                Call call = mock(Call.class);
                when(call.execute()).thenReturn(response(request.url().toString(), 429, "rate limit exceeded")
                        .header("Retry-After", "120").build());
                return call;
            });
            VcsClient client = bitbucket ? new BitbucketCloudClient(http) : new GitLabClient(http);
            assertThatThrownBy(() -> client.getFileContents("o", "r", List.of("a", "b", "c"), "head", 1000))
                    .isInstanceOf(VcsRateLimitException.class);
            verify(http, times(1)).newCall(any());
        }
    }

    private static Response.Builder response(String url, int status, String body) {
        return new Response.Builder().request(new Request.Builder().url(url).build())
                .protocol(Protocol.HTTP_1_1).code(status).message("fixture")
                .body(ResponseBody.create(body, MediaType.get("application/json")));
    }
}
