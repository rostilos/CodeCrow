package org.rostilos.codecrow.testsupport.legacy;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/** Owns exact loopback leases and leaves the deny boundary sealed until JVM exit. */
final class LegacyContainerItRuntime
        implements LegacyContainerItLauncherSessionListener.Lifecycle {

    private final LegacyContainerItContract.Activation activation;
    private final Boundary boundary;
    private final LedgerExporter ledgerExporter;
    private final List<AutoCloseable> serviceLeases = new ArrayList<>();
    private final Map<Integer, ApplicationPortRegistration> applicationLeases =
            new LinkedHashMap<>();
    private State state = State.NEW;

    LegacyContainerItRuntime(
            LegacyContainerItContract.Activation activation,
            Boundary boundary,
            LedgerExporter ledgerExporter
    ) {
        this.activation = Objects.requireNonNull(activation, "activation");
        this.boundary = Objects.requireNonNull(boundary, "boundary");
        this.ledgerExporter = Objects.requireNonNull(ledgerExporter, "ledgerExporter");
    }

    @Override
    public synchronized void open() throws Exception {
        if (state != State.NEW) {
            throw new IllegalStateException("guarded legacy IT runtime cannot be reopened");
        }
        Throwable failure = null;
        try {
            serviceLeases.add(Objects.requireNonNull(
                    boundary.allowLoopback(serviceHost(), servicePort()),
                    "service lease"
            ));
            boundary.proveDeniedControls();
            state = State.ACTIVE;
            LegacyContainerItSession.activate(this);
            return;
        } catch (Throwable openFailure) {
            failure = openFailure;
        }

        state = State.CLOSED;
        failure = closeLeases(failure);
        try {
            boundary.abortOpen();
        } catch (Throwable abortFailure) {
            failure = combine(failure, abortFailure);
        }
        throw rethrowable(failure);
    }

    synchronized AutoCloseable registerApplicationLoopback(int port) {
        requireActive();
        if (activation.lane() == LegacyContainerItContract.Lane.QUEUE) {
            throw new IllegalStateException("queue lane has no application loopback lease");
        }

        ApplicationPortRegistration registration = applicationLeases.get(port);
        if (registration == null) {
            AutoCloseable boundaryLease = Objects.requireNonNull(
                    boundary.allowLoopback(LegacyContainerEndpoints.LOOPBACK, port),
                    "application lease"
            );
            registration = new ApplicationPortRegistration(port, boundaryLease);
            applicationLeases.put(port, registration);
        }
        registration.owners++;
        return new ApplicationLoopbackLease(this, registration);
    }

    synchronized LegacyContainerEndpoints.PostgresEndpoint requirePostgresEndpoint() {
        requireActive();
        return activation.postgres().orElseThrow(() -> new IllegalStateException(
                "active guarded lane does not expose PostgreSQL"
        ));
    }

    synchronized LegacyContainerEndpoints.RedisEndpoint requireRedisEndpoint() {
        requireActive();
        return activation.redis().orElseThrow(() -> new IllegalStateException(
                "active guarded lane does not expose Redis"
        ));
    }

    @Override
    public synchronized void closeForProcessExit() throws Exception {
        if (state == State.CLOSED) {
            Throwable retryFailure = closeLeases(null);
            if (retryFailure != null) {
                throw rethrowable(retryFailure);
            }
            return;
        }
        if (state != State.ACTIVE) {
            throw new IllegalStateException("guarded legacy IT runtime was not opened");
        }
        state = State.CLOSED;
        LegacyContainerItSession.deactivate(this);

        Throwable failure = closeLeases(null);
        try {
            boundary.assertClean();
        } catch (Throwable cleanFailure) {
            failure = combine(failure, cleanFailure);
        }
        try {
            boundary.sealForProcessExit();
        } catch (Throwable sealFailure) {
            failure = combine(failure, sealFailure);
        }
        try {
            ledgerExporter.export(activation.ledgerPath());
        } catch (Throwable exportFailure) {
            failure = combine(failure, exportFailure);
        }
        if (failure != null) {
            throw rethrowable(failure);
        }
    }

    private String serviceHost() {
        return activation.postgres()
                .map(LegacyContainerEndpoints.PostgresEndpoint::host)
                .orElseGet(() -> activation.redis().orElseThrow().host());
    }

    private int servicePort() {
        return activation.postgres()
                .map(LegacyContainerEndpoints.PostgresEndpoint::port)
                .orElseGet(() -> activation.redis().orElseThrow().port());
    }

    private void requireActive() {
        if (state != State.ACTIVE) {
            throw new IllegalStateException("guarded legacy IT runtime is not active");
        }
    }

    private synchronized void releaseApplicationLease(
            ApplicationLoopbackLease lease
    ) throws Exception {
        if (lease.closed) {
            return;
        }
        ApplicationPortRegistration registration = lease.registration;
        if (registration.closed) {
            lease.closed = true;
            return;
        }
        if (registration.owners > 1) {
            registration.owners--;
            lease.closed = true;
            return;
        }

        try {
            registration.boundaryLease.close();
        } catch (Throwable closeFailure) {
            throw rethrowable(closeFailure);
        }
        registration.closed = true;
        registration.owners = 0;
        applicationLeases.remove(registration.port, registration);
        lease.closed = true;
    }

    private Throwable closeLeases(Throwable failure) {
        List<ApplicationPortRegistration> registrations =
                new ArrayList<>(applicationLeases.values());
        for (int index = registrations.size() - 1; index >= 0; index--) {
            ApplicationPortRegistration registration = registrations.get(index);
            try {
                registration.boundaryLease.close();
                registration.closed = true;
                registration.owners = 0;
                applicationLeases.remove(registration.port, registration);
            } catch (Throwable closeFailure) {
                failure = combine(failure, closeFailure);
            }
        }

        for (int index = serviceLeases.size() - 1; index >= 0; index--) {
            try {
                serviceLeases.get(index).close();
                serviceLeases.remove(index);
            } catch (Throwable closeFailure) {
                failure = combine(failure, closeFailure);
            }
        }
        return failure;
    }

    private static Throwable combine(Throwable first, Throwable next) {
        if (first == null) {
            return next;
        }
        if (first != next) {
            first.addSuppressed(next);
        }
        return first;
    }

    private static Exception rethrowable(Throwable failure) {
        if (failure instanceof Error error) {
            throw error;
        }
        if (failure instanceof Exception exception) {
            return exception;
        }
        return new IllegalStateException("guarded legacy IT lifecycle failed", failure);
    }

    interface Boundary {

        AutoCloseable allowLoopback(String host, int port);

        void proveDeniedControls();

        void assertClean();

        void sealForProcessExit();

        void abortOpen();
    }

    @FunctionalInterface
    interface LedgerExporter {

        void export(Path destination) throws Exception;
    }

    private static final class ApplicationPortRegistration {

        private final int port;
        private final AutoCloseable boundaryLease;
        private int owners;
        private boolean closed;

        private ApplicationPortRegistration(int port, AutoCloseable boundaryLease) {
            this.port = port;
            this.boundaryLease = boundaryLease;
        }
    }

    private static final class ApplicationLoopbackLease implements AutoCloseable {

        private final LegacyContainerItRuntime runtime;
        private final ApplicationPortRegistration registration;
        private boolean closed;

        private ApplicationLoopbackLease(
                LegacyContainerItRuntime runtime,
                ApplicationPortRegistration registration
        ) {
            this.runtime = runtime;
            this.registration = registration;
        }

        @Override
        public void close() throws Exception {
            runtime.releaseApplicationLease(this);
        }
    }

    private enum State {
        NEW,
        ACTIVE,
        CLOSED
    }
}
