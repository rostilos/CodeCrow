package org.rostilos.codecrow.vcsclient;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.Response;
import okhttp3.Request;

import java.io.IOException;
import java.time.Instant;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.Locale;

/** A provider cooldown is distinct from a missing ref, permission error or transient read failure. */
public final class VcsRateLimitException extends IOException {
    private static final ObjectMapper JSON = new ObjectMapper();
    private final int statusCode;
    private final String resource;
    private final Instant retryAt;
    private final String providerRequestId;
    private final boolean sharedAcrossResources;

    public VcsRateLimitException(int statusCode, String resource, Instant retryAt,
                                 String providerRequestId, boolean sharedAcrossResources) {
        super("VCS provider rate limit: HTTP " + statusCode + ", resource=" + resource
                + ", retryAt=" + retryAt
                + (providerRequestId == null ? "" : ", requestId=" + providerRequestId));
        this.statusCode = statusCode;
        this.resource = resource;
        this.retryAt = retryAt;
        this.providerRequestId = providerRequestId;
        this.sharedAcrossResources = sharedAcrossResources;
    }

    public int statusCode() { return statusCode; }
    public String resource() { return resource; }
    public Instant retryAt() { return retryAt; }
    public String providerRequestId() { return providerRequestId; }
    public boolean sharedAcrossResources() { return sharedAcrossResources; }

    public static VcsRateLimitException fromResponse(Response response, String body, Instant now) {
        String message = body == null ? "" : body.toLowerCase(Locale.ROOT);
        boolean graphql = response.isSuccessful() && response.request().url().encodedPath().endsWith("/graphql");
        boolean graphqlLimited = false;
        if (graphql && response.isSuccessful()) {
            // Inspect errors, never source text in a successful GraphQL data payload.
            try {
                JsonNode root = JSON.readTree(body == null ? "{}" : body);
                JsonNode errors = root == null ? JSON.createArrayNode() : root.path("errors");
                for (JsonNode error : errors) {
                    graphqlLimited |= "RATE_LIMITED".equals(error.path("type").asText())
                            || mentionsLimit(error.path("message").asText());
                }
            } catch (IOException ignored) {
                return null;
            }
        }
        boolean exhausted = "0".equals(response.header("X-RateLimit-Remaining"))
                || "0".equals(response.header("RateLimit-Remaining"));
        if (response.code() != 429 && !graphqlLimited
                && !(response.code() == 403 && (exhausted || mentionsLimit(message)))) {
            return null;
        }
        boolean secondary = message.contains("secondary rate limit") || message.contains("abuse detection");
        String resource = response.header("X-RateLimit-Resource", resourceFor(response.request()));
        Instant retryAt = retryAt(response, now);
        String requestId = response.header("X-GitHub-Request-Id",
                response.header("X-Request-Id", response.header("X-Request-UUID")));
        return new VcsRateLimitException(response.code(), resource, retryAt, requestId,
                secondary || (!exhausted && response.header("Retry-After") != null));
    }

    public static void check(Response response, String body) throws VcsRateLimitException {
        VcsRateLimitException failure = fromResponse(response, body, Instant.now());
        if (failure != null) throw failure;
    }

    public static void check(okhttp3.OkHttpClient client, Response response, String body) throws VcsRateLimitException {
        VcsRateLimitException failure = fromResponse(response, body, Instant.now());
        if (failure == null) return;
        for (okhttp3.Interceptor interceptor : client.networkInterceptors()) {
            if (interceptor instanceof VcsRateLimitInterceptor observer) observer.remember(response.request(), failure);
        }
        throw failure;
    }

    static String resourceFor(Request request) {
        String path = request.url().encodedPath();
        if (path.endsWith("/graphql")) return "graphql";
        if (path.startsWith("/search/code")) return "code_search";
        if (path.startsWith("/search/")) return "search";
        return path.startsWith("/2.0/") || path.contains("/api/v4/") ? "api" : "core";
    }

    public static Instant retryAt(Response response, Instant now) {
        Instant until = null;
        String retryAfter = response.header("Retry-After");
        if (retryAfter != null) {
            try {
                until = now.plusSeconds(Math.max(0, Long.parseLong(retryAfter.trim())));
            } catch (RuntimeException malformedSeconds) {
                try {
                    until = ZonedDateTime.parse(retryAfter, DateTimeFormatter.RFC_1123_DATE_TIME).toInstant();
                } catch (RuntimeException malformedDate) {
                    // Missing/invalid delay falls back to the provider reset or a conservative cooldown.
                }
            }
        }
        String reset = response.header("X-RateLimit-Reset", response.header("RateLimit-Reset"));
        if (reset != null && ("0".equals(response.header("X-RateLimit-Remaining"))
                || "0".equals(response.header("RateLimit-Remaining")))) {
            try {
                Instant resetAt = Instant.ofEpochSecond(Long.parseLong(reset));
                if (until == null || resetAt.isAfter(until)) until = resetAt;
            } catch (RuntimeException malformedReset) {
                // Keep a valid Retry-After value when reset metadata is malformed.
            }
        }
        return until != null && until.isAfter(now) ? until : now.plusSeconds(60);
    }

    public static VcsRateLimitException find(Throwable failure) {
        for (Throwable current = failure; current != null; current = current.getCause()) {
            if (current instanceof VcsRateLimitException limited) return limited;
        }
        return null;
    }

    public static void rethrowIfRateLimited(Throwable failure) throws VcsRateLimitException {
        VcsRateLimitException limited = find(failure);
        if (limited != null) throw limited;
    }

    private static boolean mentionsLimit(String text) {
        String lower = text.toLowerCase(Locale.ROOT);
        return lower.contains("rate limit") || lower.contains("rate-limit")
                || lower.contains("too many requests") || lower.contains("abuse detection");
    }
}
