package org.rostilos.codecrow.testsupport.offline;

import java.security.Permission;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Process-wide Java 17 external-operation boundary for offline tests. The
 * installed security manager permits ordinary JVM operations and delegates DNS,
 * socket, and process-execution checks to {@link NetworkDenyGuard}. This lets
 * unmodified clients be denied before their resolver, transport, or child
 * process performs an external operation.
 */
@SuppressWarnings("removal")
public final class OfflineNetworkBoundary implements AutoCloseable {

    private static final Object INSTALL_LOCK = new Object();

    private final NetworkDenyGuard guard;
    private final BoundarySecurityManager securityManager;
    private final AtomicBoolean closed = new AtomicBoolean();

    private OfflineNetworkBoundary(ExternalCallLedger ledger) {
        this.guard = new NetworkDenyGuard(ledger);
        this.securityManager = new BoundarySecurityManager(guard);
    }

    public static OfflineNetworkBoundary install(ExternalCallLedger ledger) {
        Objects.requireNonNull(ledger, "ledger");
        synchronized (INSTALL_LOCK) {
            if (System.getSecurityManager() != null) {
                throw new IllegalStateException("an offline network boundary is already installed");
            }
            OfflineNetworkBoundary boundary = new OfflineNetworkBoundary(ledger);
            System.setSecurityManager(boundary.securityManager);
            return boundary;
        }
    }

    public NetworkDenyGuard.NetworkLease allowLoopback(String host, int port) {
        return guard.allowLoopback(host, port);
    }

    @Override
    public void close() {
        synchronized (INSTALL_LOCK) {
            if (closed.compareAndSet(false, true)) {
                closeResources(guard, () -> {
                    assertOwnsSecurityManager(securityManager, System.getSecurityManager());
                    securityManager.uninstall();
                });
            }
        }
    }

    static void closeResources(NetworkDenyGuard guard, Runnable boundaryCleanup) {
        List<Throwable> failures = new ArrayList<>(2);
        try {
            guard.close();
        } catch (RuntimeException | Error failure) {
            failures.add(failure);
        }
        try {
            boundaryCleanup.run();
        } catch (RuntimeException | Error failure) {
            failures.add(failure);
        }
        if (failures.isEmpty()) {
            return;
        }
        Throwable firstFailure = failures.get(0);
        if (failures.size() == 2) {
            firstFailure.addSuppressed(failures.get(1));
        }
        if (firstFailure instanceof RuntimeException runtimeFailure) {
            throw runtimeFailure;
        }
        throw (Error) firstFailure;
    }

    static void assertOwnsSecurityManager(SecurityManager expected, SecurityManager actual) {
        if (actual != expected) {
            throw new IllegalStateException("offline network boundary no longer owns the security manager");
        }
    }

    private static final class BoundarySecurityManager extends SecurityManager {

        private final NetworkDenyGuard guard;
        private final ThreadLocal<Boolean> controlledUninstall =
                ThreadLocal.withInitial(() -> Boolean.FALSE);

        private BoundarySecurityManager(NetworkDenyGuard guard) {
            this.guard = guard;
        }

        @Override
        public void checkPermission(Permission permission) {
            assertSecurityManagerMutationAllowed(permission);
        }

        @Override
        public void checkPermission(Permission permission, Object context) {
            assertSecurityManagerMutationAllowed(permission);
        }

        @Override
        public void checkConnect(String host, int port) {
            guard.assertSystemAllowed(host, port);
        }

        @Override
        public void checkConnect(String host, int port, Object context) {
            guard.assertSystemAllowed(host, port);
        }

        @Override
        public void checkExec(String command) {
            throw guard.deniedSystemExec(command);
        }

        private void assertSecurityManagerMutationAllowed(Permission permission) {
            if (permission instanceof RuntimePermission runtimePermission
                    && "setSecurityManager".equals(runtimePermission.getName())
                    && !controlledUninstall.get()) {
                throw new SecurityException(
                        "offline boundary denies untrusted security-manager replacement"
                );
            }
        }

        private void uninstall() {
            controlledUninstall.set(Boolean.TRUE);
            try {
                System.setSecurityManager(null);
            } finally {
                controlledUninstall.remove();
            }
        }
    }
}
