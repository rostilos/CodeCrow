package org.rostilos.codecrow.testsupport.legacy;

import java.util.Objects;
import java.util.concurrent.atomic.AtomicReference;

/** Process-wide registration point used by the two embedded-server IT bases. */
public final class LegacyContainerItSession {

    private static final AtomicReference<LegacyContainerItRuntime> ACTIVE =
            new AtomicReference<>();

    private LegacyContainerItSession() {
    }

    public static AutoCloseable registerApplicationLoopback(int port) {
        if (port < 1 || port > 65535) {
            throw new IllegalArgumentException("application loopback port is out of range");
        }
        return activeRuntime().registerApplicationLoopback(port);
    }

    public static LegacyContainerEndpoints.PostgresEndpoint requirePostgresEndpoint() {
        return activeRuntime().requirePostgresEndpoint();
    }

    public static LegacyContainerEndpoints.RedisEndpoint requireRedisEndpoint() {
        return activeRuntime().requireRedisEndpoint();
    }

    private static LegacyContainerItRuntime activeRuntime() {
        LegacyContainerItRuntime runtime = ACTIVE.get();
        if (runtime == null) {
            throw new IllegalStateException("guarded legacy IT session is not active");
        }
        return runtime;
    }

    static void activate(LegacyContainerItRuntime runtime) {
        Objects.requireNonNull(runtime, "runtime");
        if (!ACTIVE.compareAndSet(null, runtime)) {
            throw new IllegalStateException("a guarded legacy IT session is already active");
        }
    }

    static void deactivate(LegacyContainerItRuntime runtime) {
        ACTIVE.compareAndSet(runtime, null);
    }

    static void resetForTesting() {
        ACTIVE.set(null);
    }
}
