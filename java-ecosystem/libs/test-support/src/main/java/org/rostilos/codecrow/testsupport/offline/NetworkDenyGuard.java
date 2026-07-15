package org.rostilos.codecrow.testsupport.offline;

import java.io.IOException;
import java.net.InetAddress;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Denies outbound network and process calls by default. Network authorization
 * is limited to exact literal-loopback host/port leases and occurs before the
 * supplied resolver runs; child-process execution remains denied.
 */
public final class NetworkDenyGuard implements AutoCloseable {

    private final ExternalCallLedger ledger;
    private final Object leaseLock = new Object();
    private final Map<Endpoint, Integer> leases = new HashMap<>();
    private boolean closed;

    public NetworkDenyGuard(ExternalCallLedger ledger) {
        this.ledger = Objects.requireNonNull(ledger, "ledger");
    }

    public NetworkLease allowLoopback(String host, int port) {
        if (!"127.0.0.1".equals(host) && !"::1".equals(host)) {
            throw new IllegalArgumentException("only an exact literal loopback host may be leased");
        }
        Endpoint endpoint = new Endpoint(host, port);
        synchronized (leaseLock) {
            if (closed) {
                throw new IllegalStateException("network deny guard is closed");
            }
            leases.merge(endpoint, 1, Integer::sum);
        }
        return new NetworkLease(this, endpoint);
    }

    public void assertAllowed(String boundary, String operation, String host, int port) {
        Endpoint endpoint = new Endpoint(host, port);
        assertEndpointAllowed(boundary, operation, endpoint, "PRE_DNS");
    }

    void assertSystemAllowed(String host, int port) {
        if (port == -1) {
            boolean registeredHost;
            synchronized (leaseLock) {
                registeredHost = leases.keySet().stream()
                        .anyMatch(endpoint -> endpoint.host().equals(host));
            }
            if (!registeredHost) {
                throw denied("network", "resolve", host, "PRE_DNS");
            }
            return;
        }
        assertEndpointAllowed(
                "network",
                "connect",
                new Endpoint(host, port),
                "PRE_SOCKET"
        );
    }

    UnexpectedExternalCall deniedSystemExec(String command) {
        return denied("process", "exec", command, "PRE_EXEC");
    }

    private void assertEndpointAllowed(
            String boundary,
            String operation,
            Endpoint endpoint,
            String phase
    ) {
        boolean registered;
        synchronized (leaseLock) {
            registered = leases.containsKey(endpoint);
        }
        if (!registered) {
            throw denied(boundary, operation, endpoint.target(), phase);
        }
    }

    private UnexpectedExternalCall denied(
            String boundary,
            String operation,
            String target,
            String phase
    ) {
        ExternalCall call = ledger.record(
                boundary, false, operation, "blocked", phase, false, target
        );
        return new UnexpectedExternalCall(call);
    }

    public <T> T connect(
            String boundary,
            String operation,
            String host,
            int port,
            AddressResolver resolver,
            SocketConnector<T> connector
    ) throws IOException {
        assertAllowed(boundary, operation, host, port);
        InetAddress[] addresses = resolver.resolve(host);
        return connector.connect(addresses, port);
    }

    private void release(Endpoint endpoint) {
        synchronized (leaseLock) {
            leases.computeIfPresent(endpoint, (ignored, count) -> count == 1 ? null : count - 1);
        }
    }

    @Override
    public void close() {
        List<String> leakedEndpoints;
        synchronized (leaseLock) {
            if (closed) {
                return;
            }
            closed = true;
            leakedEndpoints = leases.entrySet().stream()
                    .sorted(Map.Entry.comparingByKey(
                            Comparator.comparing(Endpoint::host)
                                    .thenComparingInt(Endpoint::port)
                    ))
                    .map(entry -> entry.getKey().diagnosticTarget()
                            + " (count=" + entry.getValue() + ")")
                    .toList();
            leases.clear();
        }
        if (!leakedEndpoints.isEmpty()) {
            throw new AssertionError(
                    "leaked loopback endpoint lease(s): " + String.join(", ", leakedEndpoints)
            );
        }
    }

    @FunctionalInterface
    public interface AddressResolver {
        InetAddress[] resolve(String host) throws IOException;
    }

    @FunctionalInterface
    public interface SocketConnector<T> {
        T connect(InetAddress[] addresses, int port) throws IOException;
    }

    public static final class NetworkLease implements AutoCloseable {

        private final NetworkDenyGuard guard;
        private final Endpoint endpoint;
        private final AtomicBoolean closed = new AtomicBoolean();

        private NetworkLease(NetworkDenyGuard guard, Endpoint endpoint) {
            this.guard = guard;
            this.endpoint = endpoint;
        }

        @Override
        public void close() {
            if (closed.compareAndSet(false, true)) {
                guard.release(endpoint);
            }
        }
    }

    private record Endpoint(String host, int port) {
        private Endpoint {
            Objects.requireNonNull(host, "host");
            if (port < 1) {
                throw new IllegalArgumentException("port must be at least 1");
            }
            if (port > 65535) {
                throw new IllegalArgumentException("port must be at most 65535");
            }
        }

        private String target() {
            return host + ":" + port;
        }

        private String diagnosticTarget() {
            return (host.contains(":") ? "[" + host + "]" : host) + ":" + port;
        }
    }
}
