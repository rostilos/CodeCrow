package org.rostilos.codecrow.vcsclient;

import okhttp3.*;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.core.StringRedisTemplate;

import java.io.IOException;
import java.time.Clock;
import java.time.Instant;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class VcsRateLimitsTest {
    @Test
    void actualHttpClientsBlockSharedCooldownWithoutSendingAnotherRequest() throws Exception {
        try (var server = new okhttp3.mockwebserver.MockWebServer()) {
            server.start();
            server.enqueue(new okhttp3.mockwebserver.MockResponse().setResponseCode(429)
                    .addHeader("Retry-After", "60").setBody("too many requests"));
            var limits = new VcsRateLimits();
            var first = new OkHttpClient.Builder().retryOnConnectionFailure(false)
                    .addNetworkInterceptor(new VcsRateLimitInterceptor(limits, "installation", "localhost", "1")).build();
            var second = first.newBuilder().build();
            var request = new Request.Builder().url(server.url("/repos/owner/repo/compare/a...b")).build();
            assertThatThrownBy(() -> first.newCall(request).execute()).isInstanceOf(VcsRateLimitException.class);
            assertThatThrownBy(() -> second.newCall(request).execute()).isInstanceOf(VcsRateLimitException.class);
            assertThat(server.getRequestCount()).isEqualTo(1);
            first.connectionPool().evictAll();
        }
    }

    @Test
    void cooldownExpiresAndPrimaryResourcesAndIdentitiesRemainIndependent() throws Exception {
        AtomicReference<Instant> now = new AtomicReference<>(Instant.parse("2026-11-03T12:00:00Z"));
        Clock clock = mock(Clock.class);
        when(clock.instant()).thenAnswer(invocation -> now.get());
        VcsRateLimits limits = new VcsRateLimits(null, clock);
        limits.remember("installation-a", new VcsRateLimitException(403, "core", now.get().plusSeconds(120), "req", false));
        assertThatThrownBy(() -> limits.check("installation-a", "core")).isInstanceOf(VcsRateLimitException.class);
        limits.check("installation-a", "graphql");
        limits.check("installation-b", "core");
        limits.remember("installation-a", new VcsRateLimitException(429, "core", now.get().plusSeconds(30), "req", false));
        now.set(now.get().plusSeconds(60));
        assertThatThrownBy(() -> limits.check("installation-a", "core")).isInstanceOf(VcsRateLimitException.class);
        now.set(now.get().plusSeconds(61));
        limits.check("installation-a", "core");
    }

    @Test
    void unavailableRedisKeepsKnownLocalCooldownButDoesNotRejectOtherWork() throws Exception {
        StringRedisTemplate redis = mock(StringRedisTemplate.class);
        AtomicReference<Instant> now = new AtomicReference<>(Instant.now());
        Clock clock = mock(Clock.class);
        when(clock.instant()).thenAnswer(invocation -> now.get());
        when(redis.opsForValue()).thenAnswer(invocation -> {
            now.set(now.get().plusSeconds(60));
            throw new IllegalStateException("redis unavailable after timeout");
        });
        VcsRateLimits limits = new VcsRateLimits(redis, clock);
        limits.check("a", "core");
        limits.remember("a", new VcsRateLimitException(429, "core", now.get().plusSeconds(120), "req", true));
        assertThatThrownBy(() -> limits.check("a", "graphql")).isInstanceOf(VcsRateLimitException.class);
        limits.check("b", "core");
        verify(redis, times(1)).opsForValue();
    }

    @Test
    void twoClientsShareCooldownAndNeverRetryWrites() throws Exception {
        VcsRateLimits limits = new VcsRateLimits();
        var first = new VcsRateLimitInterceptor(limits, "installation", "api.github.com", "1");
        var second = new VcsRateLimitInterceptor(limits, "installation", "api.github.com", "2");
        var request = new Request.Builder().url("https://api.github.com/repos/private-owner/private-repo/comments")
                .post(RequestBody.create("{}", MediaType.get("application/json"))).build();
        Interceptor.Chain chain = chain(request);
        when(chain.proceed(any())).thenReturn(new Response.Builder().request(request)
                .protocol(Protocol.HTTP_1_1).code(403).message("limited")
                .header("Retry-After", "120")
                .body(ResponseBody.create("{\"message\":\"secondary rate limit exceeded\"}", MediaType.get("application/json"))).build());
        assertThatThrownBy(() -> first.intercept(chain)).isInstanceOf(VcsRateLimitException.class);
        assertThatThrownBy(() -> second.intercept(chain)).isInstanceOf(VcsRateLimitException.class);
        verify(chain, times(1)).proceed(any());
        assertThat(VcsRateLimitInterceptor.endpoint(request)).isEqualTo("/repos/{owner}/{repo}/comments");
    }

    @Test
    void ambiguousWriteFailureIsNotReplayedAndCancellationDoesNotStartARequest() throws Exception {
        var observer = new VcsRateLimitInterceptor(new VcsRateLimits(), "a", "api.github.com", "1");
        Request request = new Request.Builder().url("https://api.github.com/repos/o/r/comments")
                .post(RequestBody.create("{}", MediaType.get("application/json"))).build();
        Interceptor.Chain chain = chain(request);
        IOException lostResponse = new IOException("lost response after write");
        when(chain.proceed(any())).thenThrow(lostResponse);
        assertThatThrownBy(() -> observer.intercept(chain)).isSameAs(lostResponse);
        verify(chain, times(1)).proceed(any());
        when(chain.call().isCanceled()).thenReturn(true);
        assertThatThrownBy(() -> observer.intercept(chain)).isInstanceOf(java.io.InterruptedIOException.class);
        verify(chain, times(1)).proceed(any());
    }

    private Interceptor.Chain chain(Request request) {
        Interceptor.Chain chain = mock(Interceptor.Chain.class);
        when(chain.request()).thenReturn(request);
        when(chain.call()).thenReturn(mock(Call.class));
        return chain;
    }
}
