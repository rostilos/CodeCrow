package org.rostilos.codecrow.testsupport.legacy;

import org.junit.platform.launcher.LauncherSession;
import org.junit.platform.launcher.LauncherSessionListener;
import org.rostilos.codecrow.testsupport.offline.ExternalCallLedger;
import org.rostilos.codecrow.testsupport.offline.OfflineNetworkBoundary;
import org.rostilos.codecrow.testsupport.offline.UnexpectedExternalCall;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.util.HashMap;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.function.BiFunction;
import java.util.function.Function;
import java.util.function.Supplier;

/** Installs the guarded boundary before JUnit performs test discovery. */
public final class LegacyContainerItLauncherSessionListener
        implements LauncherSessionListener {

    private final Supplier<Optional<LegacyContainerItContract.Activation>> activationSupplier;
    private final Runnable visibilityCheck;
    private final Function<LegacyContainerItContract.Activation, Lifecycle> lifecycleFactory;
    private boolean opened;
    private boolean closed;
    private Lifecycle lifecycle;

    public LegacyContainerItLauncherSessionListener() {
        this(
                LegacyContainerItLauncherSessionListener::readActivation,
                () -> LegacyContainerVisibility.assertHidden(
                        System.getenv(),
                        LegacyContainerVisibility.capture()
                ),
                LegacyContainerItLauncherSessionListener::createLifecycle
        );
    }

    LegacyContainerItLauncherSessionListener(
            Supplier<Optional<LegacyContainerItContract.Activation>> activationSupplier,
            Runnable visibilityCheck,
            Function<LegacyContainerItContract.Activation, Lifecycle> lifecycleFactory
    ) {
        this.activationSupplier = activationSupplier;
        this.visibilityCheck = visibilityCheck;
        this.lifecycleFactory = lifecycleFactory;
    }

    @Override
    public synchronized void launcherSessionOpened(LauncherSession session) {
        if (opened) {
            throw new IllegalStateException("guarded launcher session is already opened");
        }
        if (closed) {
            throw new IllegalStateException("guarded launcher session is already closed");
        }
        opened = true;
        Optional<LegacyContainerItContract.Activation> activation = activationSupplier.get();
        if (activation.isEmpty()) {
            return;
        }

        visibilityCheck.run();
        Lifecycle candidate = lifecycleFactory.apply(activation.orElseThrow());
        try {
            candidate.open();
            lifecycle = candidate;
        } catch (RuntimeException | Error failure) {
            throw failure;
        } catch (Exception failure) {
            throw new IllegalStateException("cannot open guarded legacy IT runtime", failure);
        }
    }

    @Override
    public synchronized void launcherSessionClosed(LauncherSession session) {
        if (closed) {
            return;
        }
        closed = true;
        Lifecycle current = lifecycle;
        lifecycle = null;
        if (current == null) {
            return;
        }
        try {
            current.closeForProcessExit();
        } catch (RuntimeException | Error failure) {
            throw failure;
        } catch (Exception failure) {
            throw new IllegalStateException("cannot close guarded legacy IT runtime", failure);
        }
    }

    private static Optional<LegacyContainerItContract.Activation> readActivation() {
        Map<String, String> properties = new HashMap<>();
        System.getProperties().forEach((key, value) -> properties.put(
                String.valueOf(key), String.valueOf(value)
        ));
        return LegacyContainerItContract.activation(System.getenv(), properties);
    }

    private static Lifecycle createLifecycle(
            LegacyContainerItContract.Activation activation
    ) {
        LegacyContainerModuleVisibility.assertExact(activation);
        return createLifecycle(activation, new RealLifecycleAssembly());
    }

    static Lifecycle createLifecycle(
            LegacyContainerItContract.Activation activation,
            LifecycleAssembly assembly
    ) {
        Objects.requireNonNull(activation, "activation");
        Objects.requireNonNull(assembly, "assembly");
        InstalledBoundary installed = Objects.requireNonNull(
                assembly.install(),
                "installed boundary"
        );
        try {
            return Objects.requireNonNull(
                    assembly.assemble(activation, installed),
                    "assembled lifecycle"
            );
        } catch (RuntimeException | Error failure) {
            try {
                installed.close();
            } catch (RuntimeException | Error cleanupFailure) {
                failure.addSuppressed(cleanupFailure);
            }
            throw failure;
        }
    }

    interface Lifecycle {

        void open() throws Exception;

        void closeForProcessExit() throws Exception;
    }

    interface InstalledBoundary {

        void close();
    }

    interface LifecycleAssembly {

        InstalledBoundary install();

        Lifecycle assemble(
                LegacyContainerItContract.Activation activation,
                InstalledBoundary installed
        );
    }

    private static final class RealLifecycleAssembly implements LifecycleAssembly {

        private final BiFunction<
                OfflineNetworkBoundary,
                ExternalCallLedger,
                InstalledBoundary
        > installedFactory;

        private RealLifecycleAssembly() {
            this(RealInstalledBoundary::new);
        }

        private RealLifecycleAssembly(BiFunction<
                OfflineNetworkBoundary,
                ExternalCallLedger,
                InstalledBoundary
        > installedFactory) {
            this.installedFactory = Objects.requireNonNull(installedFactory, "installedFactory");
        }

        @Override
        public InstalledBoundary install() {
            ExternalCallLedger ledger = new ExternalCallLedger();
            OfflineNetworkBoundary boundary = OfflineNetworkBoundary.install(ledger);
            try {
                return installedFactory.apply(boundary, ledger);
            } catch (RuntimeException | Error failure) {
                try {
                    boundary.close();
                } catch (RuntimeException | Error cleanupFailure) {
                    failure.addSuppressed(cleanupFailure);
                }
                throw failure;
            }
        }

        @Override
        public Lifecycle assemble(
                LegacyContainerItContract.Activation activation,
                InstalledBoundary installed
        ) {
            RealInstalledBoundary real = (RealInstalledBoundary) installed;
            LegacyBoundaryAdapter adapter = new LegacyBoundaryAdapter(
                    real.boundary(),
                    real.ledger()
            );
            LegacyContainerLedgerExporter exporter =
                    new LegacyContainerLedgerExporter(real.ledger());
            return new LegacyContainerItRuntime(activation, adapter, exporter::export);
        }
    }

    private record RealInstalledBoundary(
            OfflineNetworkBoundary boundary,
            ExternalCallLedger ledger
    ) implements InstalledBoundary {

        private RealInstalledBoundary {
            Objects.requireNonNull(boundary, "boundary");
            Objects.requireNonNull(ledger, "ledger");
        }

        @Override
        public void close() {
            boundary.close();
        }
    }

    private static final class LegacyBoundaryAdapter
            implements LegacyContainerItRuntime.Boundary {

        private static final String PROCESS_PROOF = "codecrow-denied-process-proof";

        private final OfflineNetworkBoundary boundary;
        private final ExternalCallLedger ledger;
        private boolean aborted;
        private boolean sealed;

        private LegacyBoundaryAdapter(
                OfflineNetworkBoundary boundary,
                ExternalCallLedger ledger
        ) {
            this.boundary = boundary;
            this.ledger = ledger;
        }

        @Override
        public AutoCloseable allowLoopback(String host, int port) {
            if (sealed || aborted) {
                throw new IllegalStateException("guarded boundary no longer accepts leases");
            }
            return boundary.allowLoopback(host, port);
        }

        @Override
        public void proveDeniedControls() {
            proveNetworkDenied();
            proveProcessDenied();
        }

        @Override
        public void assertClean() {
            ledger.assertZeroLiveCalls();
            ledger.assertNoUnacknowledgedBlockedCalls();
        }

        @Override
        public void sealForProcessExit() {
            sealed = true;
            proveDeniedControls();
            assertClean();
            // Deliberately do not close OfflineNetworkBoundary: the deny guard must
            // remain installed until the test fork exits.
        }

        @Override
        public void abortOpen() {
            aborted = true;
            boundary.close();
        }

        private void proveNetworkDenied() {
            try (Socket socket = new Socket()) {
                socket.connect(new InetSocketAddress("127.0.0.1", 1));
                throw new IllegalStateException("network denial proof unexpectedly connected");
            } catch (UnexpectedExternalCall blocked) {
                ledger.acknowledgeBlocked(
                        blocked.call(),
                        "network",
                        "connect",
                        "PRE_SOCKET",
                        "127.0.0.1:1"
                );
            } catch (IOException unguardedFailure) {
                throw new IllegalStateException(
                        "network denial proof reached the socket implementation",
                        unguardedFailure
                );
            }
        }

        private void proveProcessDenied() {
            try {
                new ProcessBuilder(PROCESS_PROOF).start();
                throw new IllegalStateException("process denial proof unexpectedly started");
            } catch (UnexpectedExternalCall blocked) {
                ledger.acknowledgeBlocked(
                        blocked.call(),
                        "process",
                        "exec",
                        "PRE_EXEC",
                        PROCESS_PROOF
                );
            } catch (IOException unguardedFailure) {
                throw new IllegalStateException(
                        "process denial proof reached the operating system",
                        unguardedFailure
                );
            }
        }
    }
}
