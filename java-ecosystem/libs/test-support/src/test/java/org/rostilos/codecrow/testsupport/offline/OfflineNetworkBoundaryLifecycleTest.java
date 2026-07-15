package org.rostilos.codecrow.testsupport.offline;

import org.junit.jupiter.api.Test;

import java.util.concurrent.atomic.AtomicBoolean;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class OfflineNetworkBoundaryLifecycleTest {

    @Test
    @SuppressWarnings("removal")
    void leakedLeaseFailsCloseAfterTheOwnedSecurityManagerIsRestored() {
        OfflineNetworkBoundary boundary = OfflineNetworkBoundary.install(
                new ExternalCallLedger()
        );
        NetworkDenyGuard.NetworkLease leaked = boundary.allowLoopback("127.0.0.1", 15432);

        assertThatThrownBy(boundary::close)
                .isInstanceOf(AssertionError.class)
                .hasMessage(
                        "leaked loopback endpoint lease(s): 127.0.0.1:15432 (count=1)"
                );

        assertThat(System.getSecurityManager()).isNull();
        leaked.close();
        boundary.close();
    }

    @Test
    void resourceCloseAggregatesLeakAndCleanupFailuresWithoutMaskingEither() {
        NetworkDenyGuard leakingGuard = new NetworkDenyGuard(new ExternalCallLedger());
        NetworkDenyGuard.NetworkLease leaked = leakingGuard.allowLoopback("::1", 6379);
        IllegalStateException cleanupFailure = new IllegalStateException(
                "scripted boundary cleanup failure"
        );
        AtomicBoolean cleanupAttempted = new AtomicBoolean();

        assertThatThrownBy(() -> OfflineNetworkBoundary.closeResources(
                leakingGuard,
                () -> {
                    cleanupAttempted.set(true);
                    throw cleanupFailure;
                }
        )).isInstanceOf(AssertionError.class)
                .hasMessage(
                        "leaked loopback endpoint lease(s): [::1]:6379 (count=1)"
                )
                .satisfies(failure -> assertThat(failure.getSuppressed())
                        .containsExactly(cleanupFailure));
        assertThat(cleanupAttempted).isTrue();
        leaked.close();

        NetworkDenyGuard cleanGuard = new NetworkDenyGuard(new ExternalCallLedger());
        IllegalArgumentException cleanupOnly = new IllegalArgumentException(
                "scripted cleanup-only failure"
        );
        assertThatThrownBy(() -> OfflineNetworkBoundary.closeResources(
                cleanGuard,
                () -> {
                    throw cleanupOnly;
                }
        )).isSameAs(cleanupOnly);
    }

    @Test
    @SuppressWarnings("removal")
    void refusesNestedInstallationAndRestoresTheProcessBoundaryIdempotently() {
        assertThat(System.getSecurityManager()).isNull();
        OfflineNetworkBoundary boundary = OfflineNetworkBoundary.install(new ExternalCallLedger());

        try {
            assertThat(System.getSecurityManager()).isNotNull();
            assertThatThrownBy(() -> OfflineNetworkBoundary.install(new ExternalCallLedger()))
                    .isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("already installed");
        } finally {
            boundary.close();
            boundary.close();
        }

        assertThat(System.getSecurityManager()).isNull();
    }

    @Test
    @SuppressWarnings("removal")
    void deniesUntrustedRemovalAndReplacementWhileControlledCloseStillRestores() {
        ExternalCallLedger ledger = new ExternalCallLedger();
        OfflineNetworkBoundary boundary = OfflineNetworkBoundary.install(ledger);
        SecurityManager installed = System.getSecurityManager();
        SecurityManager replacement = new SecurityManager() {
            @Override
            public void checkPermission(java.security.Permission permission) {
                // Deliberately permissive test replacement.
            }
        };

        try {
            assertThatThrownBy(() -> System.setSecurityManager(null))
                    .isInstanceOf(SecurityException.class)
                    .hasMessageContaining("denies untrusted");
            assertThatThrownBy(() -> System.setSecurityManager(replacement))
                    .isInstanceOf(SecurityException.class)
                    .hasMessageContaining("denies untrusted");
            assertThat(System.getSecurityManager()).isSameAs(installed);
            OfflineNetworkBoundary.assertOwnsSecurityManager(installed, installed);
            assertThatThrownBy(() -> OfflineNetworkBoundary.assertOwnsSecurityManager(
                    installed, replacement
            )).isInstanceOf(IllegalStateException.class)
                    .hasMessageContaining("no longer owns");
            assertThat(ledger.entries()).isEmpty();
        } finally {
            boundary.close();
        }

        assertThat(System.getSecurityManager()).isNull();
    }
}
