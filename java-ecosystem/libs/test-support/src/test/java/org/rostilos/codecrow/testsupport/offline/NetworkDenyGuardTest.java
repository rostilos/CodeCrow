package org.rostilos.codecrow.testsupport.offline;

import org.junit.jupiter.api.Test;

import java.net.InetAddress;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class NetworkDenyGuardTest {

    @Test
    void concurrentDenialsExposeTheExactRecordedCallForIdentityAcknowledgement() throws Exception {
        ExternalCallLedger ledger = new ExternalCallLedger();
        NetworkDenyGuard guard = new NetworkDenyGuard(ledger);
        int invocationCount = 8;
        ExecutorService executor = Executors.newFixedThreadPool(invocationCount);
        CountDownLatch ready = new CountDownLatch(invocationCount);
        CountDownLatch start = new CountDownLatch(1);
        List<Future<UnexpectedExternalCall>> futures = new ArrayList<>();

        try {
            for (int invocation = 0; invocation < invocationCount; invocation++) {
                futures.add(executor.submit(() -> {
                    ready.countDown();
                    start.await();
                    return assertThrows(
                            UnexpectedExternalCall.class,
                            () -> guard.assertAllowed(
                                    "network", "connect", "parallel.invalid", 443
                            )
                    );
                }));
            }
            ready.await();
            start.countDown();

            List<UnexpectedExternalCall> failures = new ArrayList<>();
            for (Future<UnexpectedExternalCall> future : futures) {
                failures.add(future.get());
            }

            List<ExternalCall> entries = ledger.entries();
            assertThat(entries).hasSize(invocationCount);
            assertThat(failures).allSatisfy(failure -> {
                ExternalCall exactCall = failure.call();
                assertThat(entries.stream().anyMatch(entry -> entry == exactCall)).isTrue();
                ledger.acknowledgeBlocked(
                        exactCall,
                        "network",
                        "connect",
                        "PRE_DNS",
                        "parallel.invalid:443"
                );
            });
            ledger.assertNoUnacknowledgedBlockedCalls();
        } finally {
            executor.shutdownNow();
        }
    }

    @Test
    void blocksAnUnregisteredTargetBeforeDnsOrSocketUse() {
        ExternalCallLedger ledger = new ExternalCallLedger();
        NetworkDenyGuard guard = new NetworkDenyGuard(ledger);
        AtomicInteger resolverCalls = new AtomicInteger();
        AtomicInteger connectorCalls = new AtomicInteger();

        assertThatThrownBy(() -> guard.connect(
                "llm",
                "chat",
                "unregistered.invalid",
                443,
                host -> {
                    resolverCalls.incrementAndGet();
                    return new InetAddress[]{InetAddress.getLoopbackAddress()};
                },
                (addresses, port) -> {
                    connectorCalls.incrementAndGet();
                    return "connected";
                }
        ))
                .isInstanceOf(UnexpectedExternalCall.class)
                .hasMessageContaining("unregistered.invalid:443");

        assertThat(resolverCalls).hasValue(0);
        assertThat(connectorCalls).hasValue(0);
        assertThat(ledger.entries()).singleElement().satisfies(entry -> {
            assertThat(entry.boundary()).isEqualTo("llm");
            assertThat(entry.live()).isFalse();
            assertThat(entry.operation()).isEqualTo("chat");
            assertThat(entry.outcome()).isEqualTo("blocked");
            assertThat(entry.phase()).isEqualTo("PRE_DNS");
            assertThat(entry.sequence()).isEqualTo(1);
            assertThat(entry.simulated()).isFalse();
            assertThat(entry.target()).isEqualTo("unregistered.invalid:443");
        });
    }

    @Test
    void allowsOnlyTheExactLeasedLoopbackHostAndPort() throws Exception {
        ExternalCallLedger ledger = new ExternalCallLedger();
        NetworkDenyGuard guard = new NetworkDenyGuard(ledger);
        AtomicInteger resolverCalls = new AtomicInteger();
        AtomicInteger connectorCalls = new AtomicInteger();

        try (NetworkDenyGuard.NetworkLease ignored = guard.allowLoopback("127.0.0.1", 5432)) {
            String result = guard.connect(
                    "postgres",
                    "query",
                    "127.0.0.1",
                    5432,
                    host -> {
                        resolverCalls.incrementAndGet();
                        return new InetAddress[]{InetAddress.getLoopbackAddress()};
                    },
                    (addresses, port) -> {
                        connectorCalls.incrementAndGet();
                        assertThat(addresses).hasSize(1);
                        assertThat(port).isEqualTo(5432);
                        return "test-owned";
                    }
            );

            assertThat(result).isEqualTo("test-owned");
            assertThatThrownBy(() -> guard.assertAllowed("postgres", "query", "127.0.0.1", 5433))
                    .isInstanceOf(UnexpectedExternalCall.class);
        }

        assertThat(resolverCalls).hasValue(1);
        assertThat(connectorCalls).hasValue(1);
        assertThatThrownBy(() -> guard.assertAllowed("postgres", "query", "127.0.0.1", 5432))
                .isInstanceOf(UnexpectedExternalCall.class);
    }

    @Test
    void nestedLeasesDoNotPrematurelyRemoveAnEndpoint() {
        ExternalCallLedger ledger = new ExternalCallLedger();
        NetworkDenyGuard guard = new NetworkDenyGuard(ledger);
        NetworkDenyGuard.NetworkLease first = guard.allowLoopback("::1", 6379);
        NetworkDenyGuard.NetworkLease second = guard.allowLoopback("::1", 6379);

        first.close();
        first.close();
        guard.assertAllowed("redis", "get", "::1", 6379);
        second.close();

        assertThatThrownBy(() -> guard.assertAllowed("redis", "get", "::1", 6379))
                .isInstanceOf(UnexpectedExternalCall.class);
    }

    @Test
    void closeReportsSortedReferenceCountsRevokesLeasesAndRejectsRegistration() {
        NetworkDenyGuard guard = new NetworkDenyGuard(new ExternalCallLedger());
        NetworkDenyGuard.NetworkLease ipv4First = guard.allowLoopback("127.0.0.1", 5432);
        NetworkDenyGuard.NetworkLease ipv4Second = guard.allowLoopback("127.0.0.1", 5432);
        NetworkDenyGuard.NetworkLease ipv6 = guard.allowLoopback("::1", 6379);

        assertThatThrownBy(guard::close)
                .isInstanceOf(AssertionError.class)
                .hasMessage(
                        "leaked loopback endpoint lease(s): "
                                + "127.0.0.1:5432 (count=2), [::1]:6379 (count=1)"
                );

        assertThatThrownBy(() -> guard.allowLoopback("127.0.0.1", 5432))
                .isInstanceOf(IllegalStateException.class)
                .hasMessage("network deny guard is closed");
        assertThatThrownBy(() -> guard.assertAllowed("postgres", "query", "127.0.0.1", 5432))
                .isInstanceOf(UnexpectedExternalCall.class);
        assertThatThrownBy(() -> guard.assertAllowed("redis", "get", "::1", 6379))
                .isInstanceOf(UnexpectedExternalCall.class);

        ipv4First.close();
        ipv4Second.close();
        ipv6.close();
        ipv6.close();
        guard.close();
    }

    @Test
    void refusesRemoteOrInvalidEndpointLeases() {
        NetworkDenyGuard guard = new NetworkDenyGuard(new ExternalCallLedger());

        assertThatThrownBy(() -> guard.allowLoopback("example.com", 443))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("literal loopback");
        assertThatThrownBy(() -> guard.allowLoopback("127.0.0.1", 0))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> guard.allowLoopback("127.0.0.1", 65536))
                .isInstanceOf(IllegalArgumentException.class);
    }
}
