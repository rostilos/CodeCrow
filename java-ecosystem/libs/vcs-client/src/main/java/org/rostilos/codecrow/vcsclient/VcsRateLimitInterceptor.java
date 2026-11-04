package org.rostilos.codecrow.vcsclient;

import okhttp3.Interceptor;
import okhttp3.Request;
import okhttp3.Response;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.slf4j.MDC;

import java.io.IOException;
import java.io.InterruptedIOException;
import java.time.Instant;

/** Network interceptor: observes each HTTP exchange and never retries an external operation. */
public final class VcsRateLimitInterceptor implements Interceptor {
    private static final Logger log = LoggerFactory.getLogger(VcsRateLimitInterceptor.class);
    private final VcsRateLimits limits;
    private final String identity;
    private final String apiHost;
    private final String connectionId;

    public VcsRateLimitInterceptor(VcsRateLimits limits, String identity, String apiHost, String connectionId) {
        this.limits = limits;
        this.identity = identity;
        this.apiHost = apiHost;
        this.connectionId = connectionId;
    }

    @Override
    public Response intercept(Chain chain) throws IOException {
        Request request = chain.request();
        if (!request.url().host().equals(apiHost)) return chain.proceed(request);
        if (Thread.currentThread().isInterrupted() || chain.call().isCanceled()) {
            throw new InterruptedIOException("VCS request cancelled");
        }
        String resource = VcsRateLimitException.resourceFor(request);
        try {
            limits.check(identity, resource);
        } catch (VcsRateLimitException coolingDown) {
            log.info("vcs_request_deferred quota={} connection={} stage={} project={} job={} resource={} retryAt={}",
                    identity, connectionId, stage(), MDC.get("projectId"), MDC.get("jobId"), resource, coolingDown.retryAt());
            throw coolingDown;
        }
        Response response;
        try {
            response = chain.proceed(request);
        } catch (IOException failure) {
            log.info("vcs_request transport=http attempt=1 quota={} connection={} stage={} project={} job={} method={} endpoint={} status=transport_error errorClass={}",
                    identity, connectionId, stage(), MDC.get("projectId"), MDC.get("jobId"), request.method(),
                    endpoint(request), failure.getClass().getSimpleName());
            throw failure;
        }
        log.info("vcs_request transport=http attempt=1 quota={} connection={} stage={} project={} job={} method={} endpoint={} status={} resource={} remaining={} reset={} retryAfter={} requestId={}",
                identity, connectionId, stage(), MDC.get("projectId"), MDC.get("jobId"), request.method(),
                endpoint(request), response.code(), response.header("X-RateLimit-Resource", resource),
                response.header("X-RateLimit-Remaining", response.header("RateLimit-Remaining")),
                response.header("X-RateLimit-Reset", response.header("RateLimit-Reset")), response.header("Retry-After"),
                response.header("X-GitHub-Request-Id", response.header("X-Request-Id", response.header("X-Request-UUID"))));
        VcsRateLimitException failure = null;
        if (response.code() == 403 || response.code() == 429 || "graphql".equals(resource)) {
            failure = VcsRateLimitException.fromResponse(response, response.peekBody(65_536).string(), Instant.now());
        }
        if (failure != null) {
            limits.remember(identity, failure);
            response.close();
            throw failure;
        }
        if ("0".equals(response.header("X-RateLimit-Remaining")) || "0".equals(response.header("RateLimit-Remaining"))) {
            limits.remember(identity, new VcsRateLimitException(response.code(),
                    response.header("X-RateLimit-Resource", resource),
                    VcsRateLimitException.retryAt(response, Instant.now()), null, false));
        }
        return response;
    }

    void remember(Request request, VcsRateLimitException failure) {
        if (request.url().host().equals(apiHost)) limits.remember(identity, failure);
    }

    private static String stage() {
        String stage = MDC.get("vcsStage");
        return stage == null ? "provider" : stage;
    }

    static String endpoint(Request request) {
        String path = request.url().encodedPath();
        if (path.endsWith("/graphql")) return "/graphql";
        // Never include repository names, file paths, commit IDs or query strings in request accounting.
        String[] segments = path.split("/");
        for (int i = 0; i < segments.length; i++) {
            if ((segments[i].equals("repos") || segments[i].equals("repositories")) && i + 3 < segments.length) {
                return "/" + segments[i] + "/{owner}/{repo}/" + segments[i + 3];
            }
            if (segments[i].equals("projects") && i + 2 < segments.length) return "/projects/{project}/" + segments[i + 2];
        }
        return "/provider-metadata";
    }
}
