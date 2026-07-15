package org.rostilos.codecrow.testsupport.legacy;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.rostilos.codecrow.testsupport.containers.SharedPostgresContainer;
import org.rostilos.codecrow.testsupport.containers.SharedRedisContainer;
import org.rostilos.codecrow.testsupport.initializer.FullContainerInitializer;
import org.rostilos.codecrow.testsupport.initializer.RedisContainerInitializer;
import org.springframework.context.support.GenericApplicationContext;

import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class LegacyContainerItRuntimeTest {

    @TempDir
    Path ledgerDirectory;

    @AfterEach
    void clearStaticSession() {
        LegacyContainerItSession.resetForTesting();
    }

    @Test
    void duplicateApplicationRegistrationsShareUntilTheirLastOwnerReleases() throws Exception {
        RecordingBoundary boundary = new RecordingBoundary();
        List<String> events = boundary.events;
        LegacyContainerItRuntime runtime = new LegacyContainerItRuntime(
                activation(LegacyContainerItContract.Lane.PIPELINE),
                boundary,
                destination -> events.add("export:" + destination.getFileName())
        );

        runtime.open();
        AutoCloseable first = LegacyContainerItSession.registerApplicationLoopback(28741);
        AutoCloseable duplicate = LegacyContainerItSession.registerApplicationLoopback(28741);

        first.close();
        assertThat(events).doesNotContain("close:127.0.0.1:28741");
        duplicate.close();
        duplicate.close();
        runtime.closeForProcessExit();

        assertThat(events).containsExactly(
                "lease:127.0.0.1:15432",
                "prove-denials",
                "lease:127.0.0.1:28741",
                "close:127.0.0.1:28741",
                "close:127.0.0.1:15432",
                "assert-clean",
                "seal-for-exit",
                "export:legacy-container-it-pipeline-p007_runtime_01234567.json"
        );
        assertThatThrownBy(() -> LegacyContainerItSession.registerApplicationLoopback(28742))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("not active");
    }

    @Test
    void endpointFacadesRequireThePublishedActiveRuntimeAndCorrectLane() throws Exception {
        assertThatThrownBy(SharedPostgresContainer::getInstance)
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("session is not active");
        assertThatThrownBy(SharedRedisContainer::getInstance)
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("session is not active");

        LegacyContainerItContract.Activation pipelineActivation =
                activation(LegacyContainerItContract.Lane.PIPELINE);
        LegacyContainerItRuntime runtime = new LegacyContainerItRuntime(
                pipelineActivation,
                new RecordingBoundary(),
                ignored -> { }
        );
        runtime.open();

        assertThat(SharedPostgresContainer.getInstance())
                .isSameAs(pipelineActivation.postgres().orElseThrow());
        assertThat(LegacyContainerItSession.requirePostgresEndpoint())
                .isSameAs(pipelineActivation.postgres().orElseThrow());
        assertThatThrownBy(SharedRedisContainer::getInstance)
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("does not expose Redis");

        runtime.closeForProcessExit();
        assertThatThrownBy(SharedPostgresContainer::getInstance)
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("session is not active");
    }

    @Test
    void sessionPublicationRejectsNullAndDuplicateRuntimesWithoutReplacingTheOwner()
            throws Exception {
        assertThatThrownBy(() -> LegacyContainerItSession.activate(null))
                .isInstanceOf(NullPointerException.class)
                .hasMessageContaining("runtime");

        LegacyContainerItRuntime owner = new LegacyContainerItRuntime(
                activation(LegacyContainerItContract.Lane.PIPELINE),
                new RecordingBoundary(),
                ignored -> { }
        );
        RecordingBoundary rejectedBoundary = new RecordingBoundary();
        LegacyContainerItRuntime rejected = new LegacyContainerItRuntime(
                activation(LegacyContainerItContract.Lane.PIPELINE),
                rejectedBoundary,
                ignored -> { }
        );
        owner.open();

        assertThatThrownBy(rejected::open)
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("already active");
        LegacyContainerItSession.deactivate(rejected);
        assertThat(LegacyContainerItSession.requirePostgresEndpoint())
                .isSameAs(owner.requirePostgresEndpoint());
        assertThat(rejectedBoundary.events).containsExactly(
                "lease:127.0.0.1:15432",
                "prove-denials",
                "close:127.0.0.1:15432",
                "abort-open"
        );

        owner.closeForProcessExit();
    }

    @Test
    void queueLaneRefusesApplicationPortsAndInvalidPortsFailBeforeLeasing() throws Exception {
        RecordingBoundary boundary = new RecordingBoundary();
        LegacyContainerItRuntime runtime = new LegacyContainerItRuntime(
                activation(LegacyContainerItContract.Lane.QUEUE),
                boundary,
                ignored -> { }
        );
        runtime.open();

        assertThat(SharedRedisContainer.getInstance())
                .isSameAs(runtime.requireRedisEndpoint());
        assertThat(SharedRedisContainer.getHost()).isEqualTo("127.0.0.1");
        assertThat(SharedRedisContainer.getPort()).isEqualTo(16379);
        assertThat(SharedRedisContainer.springProperties()).containsExactly(
                "spring.redis.host=127.0.0.1",
                "spring.redis.port=16379"
        );
        GenericApplicationContext context = new GenericApplicationContext();
        new RedisContainerInitializer().initialize(context);
        assertThat(context.getEnvironment().getProperty("spring.redis.host"))
                .isEqualTo("127.0.0.1");
        assertThat(context.getEnvironment().getProperty("spring.redis.port"))
                .isEqualTo("16379");
        assertThatThrownBy(() -> new FullContainerInitializer().initialize(context))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("not a valid guarded lane");
        context.close();
        assertThatThrownBy(SharedPostgresContainer::getInstance)
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("does not expose PostgreSQL");

        assertThatThrownBy(() -> LegacyContainerItSession.registerApplicationLoopback(20000))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("queue lane");
        assertThatThrownBy(() -> LegacyContainerItSession.registerApplicationLoopback(0))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("port");
        assertThatThrownBy(() -> LegacyContainerItSession.registerApplicationLoopback(65536))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("port");

        runtime.closeForProcessExit();
        assertThat(boundary.events).doesNotContain("lease:127.0.0.1:20000");
    }

    @Test
    void openFailureRestoresTheBoundaryAndNeverPublishesTheSession() {
        RecordingBoundary boundary = new RecordingBoundary();
        boundary.proofFailure = new IllegalStateException("proof failed");
        LegacyContainerItRuntime runtime = new LegacyContainerItRuntime(
                activation(LegacyContainerItContract.Lane.WEB),
                boundary,
                ignored -> { }
        );

        assertThatThrownBy(runtime::open).isSameAs(boundary.proofFailure);
        assertThat(boundary.events).containsExactly(
                "lease:127.0.0.1:15432",
                "prove-denials",
                "close:127.0.0.1:15432",
                "abort-open"
        );
        assertThatThrownBy(() -> LegacyContainerItSession.registerApplicationLoopback(28111))
                .isInstanceOf(IllegalStateException.class);
    }

    @Test
    void closeAttemptsEveryPhaseAndPreservesTheFirstFailure() throws Exception {
        RecordingBoundary boundary = new RecordingBoundary();
        boundary.cleanFailure = new AssertionError("ledger dirty");
        LegacyContainerItRuntime runtime = new LegacyContainerItRuntime(
                activation(LegacyContainerItContract.Lane.WEB),
                boundary,
                ignored -> {
                    throw new IllegalStateException("export failed");
                }
        );
        runtime.open();
        LegacyContainerItSession.registerApplicationLoopback(28112);

        assertThatThrownBy(runtime::closeForProcessExit)
                .isSameAs(boundary.cleanFailure)
                .satisfies(failure -> assertThat(failure.getSuppressed()))
                .satisfies(failure -> assertThat(failure.getSuppressed())
                        .anySatisfy(suppressed -> assertThat(suppressed)
                                .hasMessage("export failed")));
        assertThat(boundary.events).containsSubsequence(
                "close:127.0.0.1:28112",
                "close:127.0.0.1:15432",
                "assert-clean",
                "seal-for-exit"
        );
    }

    @Test
    void initialLeaseFailureStillAbortsTheInstalledBoundary() {
        RecordingBoundary boundary = new RecordingBoundary();
        boundary.leaseFailure = new IllegalStateException("lease failed");
        LegacyContainerItRuntime runtime = new LegacyContainerItRuntime(
                activation(LegacyContainerItContract.Lane.PIPELINE),
                boundary,
                ignored -> { }
        );

        assertThatThrownBy(runtime::open).isSameAs(boundary.leaseFailure);
        assertThat(boundary.events).containsExactly(
                "lease:127.0.0.1:15432",
                "abort-open"
        );
    }

    @Test
    void nullLeaseIsRejectedBeforeSessionPublication() {
        RecordingBoundary boundary = new RecordingBoundary();
        boundary.returnNullLease = true;
        LegacyContainerItRuntime runtime = new LegacyContainerItRuntime(
                activation(LegacyContainerItContract.Lane.QUEUE),
                boundary,
                ignored -> { }
        );

        assertThatThrownBy(runtime::open)
                .isInstanceOf(NullPointerException.class)
                .hasMessageContaining("service lease");
        assertThat(boundary.events).containsExactly(
                "lease:127.0.0.1:16379",
                "abort-open"
        );
    }

    @Test
    void nullApplicationLeaseDoesNotLeaveAFalseRegistration() throws Exception {
        RecordingBoundary boundary = new RecordingBoundary();
        LegacyContainerItRuntime runtime = new LegacyContainerItRuntime(
                activation(LegacyContainerItContract.Lane.PIPELINE),
                boundary,
                ignored -> { }
        );
        runtime.open();
        boundary.returnNullLease = true;

        assertThatThrownBy(() ->
                LegacyContainerItSession.registerApplicationLoopback(28747))
                .isInstanceOf(NullPointerException.class)
                .hasMessageContaining("application lease");
        boundary.returnNullLease = false;
        AutoCloseable lease =
                LegacyContainerItSession.registerApplicationLoopback(28747);
        lease.close();
        runtime.closeForProcessExit();

        assertThat(boundary.events).filteredOn("lease:127.0.0.1:28747"::equals)
                .hasSize(2);
    }

    @Test
    void abortAndLeaseCloseFailuresAreSuppressedBehindTheOpenFailure() {
        RecordingBoundary boundary = new RecordingBoundary();
        boundary.proofFailure = new IllegalStateException("proof failed");
        RuntimeException leaseCloseFailure =
                new IllegalStateException("lease close failed");
        boundary.leaseCloseFailures.add(leaseCloseFailure);
        boundary.abortFailure = new IllegalStateException("abort failed");
        LegacyContainerItRuntime runtime = new LegacyContainerItRuntime(
                activation(LegacyContainerItContract.Lane.WEB),
                boundary,
                ignored -> { }
        );

        assertThatThrownBy(runtime::open)
                .isSameAs(boundary.proofFailure)
                .satisfies(failure -> assertThat(failure.getSuppressed())
                        .containsExactly(leaseCloseFailure, boundary.abortFailure));
        assertThat(boundary.events).containsExactly(
                "lease:127.0.0.1:15432",
                "prove-denials",
                "close:127.0.0.1:15432",
                "abort-open"
        );
    }

    @Test
    void runtimeCannotCloseBeforeOpenOrReopenAfterClose() throws Exception {
        RecordingBoundary boundary = new RecordingBoundary();
        LegacyContainerItRuntime runtime = new LegacyContainerItRuntime(
                activation(LegacyContainerItContract.Lane.QUEUE),
                boundary,
                ignored -> { }
        );

        assertThatThrownBy(runtime::closeForProcessExit)
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("not opened");
        runtime.open();
        runtime.closeForProcessExit();
        assertThatThrownBy(runtime::open)
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("reopened");
        runtime.closeForProcessExit();
    }

    @Test
    void aReleasedPortCanBeRegisteredAgainAndEachHandleIsIdempotent() throws Exception {
        RecordingBoundary boundary = new RecordingBoundary();
        LegacyContainerItRuntime runtime = new LegacyContainerItRuntime(
                activation(LegacyContainerItContract.Lane.WEB),
                boundary,
                ignored -> { }
        );
        runtime.open();

        AutoCloseable first = LegacyContainerItSession.registerApplicationLoopback(28744);
        first.close();
        first.close();
        AutoCloseable replacement =
                LegacyContainerItSession.registerApplicationLoopback(28744);
        replacement.close();
        runtime.closeForProcessExit();

        assertThat(boundary.events).filteredOn("lease:127.0.0.1:28744"::equals)
                .hasSize(2);
        assertThat(boundary.events).filteredOn("close:127.0.0.1:28744"::equals)
                .hasSize(2);
    }

    @Test
    void explicitReleaseFailureCanBeRetriedByTheOwner() throws Exception {
        RecordingBoundary boundary = new RecordingBoundary();
        LegacyContainerItRuntime runtime = new LegacyContainerItRuntime(
                activation(LegacyContainerItContract.Lane.PIPELINE),
                boundary,
                ignored -> { }
        );
        runtime.open();
        AutoCloseable lease =
                LegacyContainerItSession.registerApplicationLoopback(28745);
        RuntimeException releaseFailure =
                new IllegalStateException("application release failed");
        boundary.leaseCloseFailures.add(releaseFailure);

        assertThatThrownBy(lease::close).isSameAs(releaseFailure);
        lease.close();
        runtime.closeForProcessExit();

        assertThat(boundary.events).filteredOn("close:127.0.0.1:28745"::equals)
                .hasSize(2);
    }

    @Test
    void processCloseRetainsFailedLeasesForRetryAndPreservesFailureOrder() throws Exception {
        RecordingBoundary boundary = new RecordingBoundary();
        LegacyContainerItRuntime runtime = new LegacyContainerItRuntime(
                activation(LegacyContainerItContract.Lane.WEB),
                boundary,
                ignored -> boundary.events.add("export")
        );
        runtime.open();
        AutoCloseable leaked =
                LegacyContainerItSession.registerApplicationLoopback(28746);
        RuntimeException applicationFailure =
                new IllegalStateException("application close failed");
        RuntimeException serviceFailure =
                new IllegalStateException("service close failed");
        boundary.leaseCloseFailures.add(applicationFailure);
        boundary.leaseCloseFailures.add(serviceFailure);

        assertThatThrownBy(runtime::closeForProcessExit)
                .isSameAs(applicationFailure)
                .satisfies(failure -> assertThat(failure.getSuppressed())
                        .containsExactly(serviceFailure));
        assertThatThrownBy(SharedPostgresContainer::getInstance)
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("session is not active");
        assertThat(boundary.events).containsSubsequence(
                "close:127.0.0.1:28746",
                "close:127.0.0.1:15432",
                "assert-clean",
                "seal-for-exit",
                "export"
        );

        runtime.closeForProcessExit();
        leaked.close();
        assertThat(boundary.events).filteredOn("close:127.0.0.1:28746"::equals)
                .hasSize(2);
        assertThat(boundary.events).filteredOn("close:127.0.0.1:15432"::equals)
                .hasSize(2);
    }

    @Test
    void failedApplicationLeaseCanBeRetriedWithoutFalseDeduplication() throws Exception {
        RecordingBoundary boundary = new RecordingBoundary();
        LegacyContainerItRuntime runtime = new LegacyContainerItRuntime(
                activation(LegacyContainerItContract.Lane.PIPELINE),
                boundary,
                ignored -> { }
        );
        runtime.open();
        boundary.leaseFailure = new IllegalStateException("application lease failed");

        assertThatThrownBy(() -> LegacyContainerItSession.registerApplicationLoopback(28743))
                .isSameAs(boundary.leaseFailure);
        boundary.leaseFailure = null;
        AutoCloseable lease =
                LegacyContainerItSession.registerApplicationLoopback(28743);
        lease.close();
        runtime.closeForProcessExit();

        assertThat(boundary.events).filteredOn("lease:127.0.0.1:28743"::equals)
                .hasSize(2);
    }

    @Test
    void inactiveEndpointSealFailureAndClosedRetryFailureAreExplicit() throws Exception {
        RecordingBoundary inactiveBoundary = new RecordingBoundary();
        LegacyContainerItRuntime inactive = new LegacyContainerItRuntime(
                activation(LegacyContainerItContract.Lane.PIPELINE),
                inactiveBoundary,
                ignored -> { }
        );
        assertThatThrownBy(inactive::requirePostgresEndpoint)
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("not active");

        RecordingBoundary sealBoundary = new RecordingBoundary();
        sealBoundary.sealFailure = new IllegalStateException("seal failed");
        LegacyContainerItRuntime sealRuntime = new LegacyContainerItRuntime(
                activation(LegacyContainerItContract.Lane.QUEUE),
                sealBoundary,
                ignored -> { }
        );
        sealRuntime.open();
        assertThatThrownBy(sealRuntime::closeForProcessExit)
                .isSameAs(sealBoundary.sealFailure);

        RecordingBoundary retryBoundary = new RecordingBoundary();
        RuntimeException first = new IllegalStateException("first close failed");
        RuntimeException second = new IllegalStateException("retry close failed");
        retryBoundary.leaseCloseFailures.add(first);
        retryBoundary.leaseCloseFailures.add(second);
        LegacyContainerItRuntime retryRuntime = new LegacyContainerItRuntime(
                activation(LegacyContainerItContract.Lane.WEB),
                retryBoundary,
                ignored -> { }
        );
        retryRuntime.open();
        assertThatThrownBy(retryRuntime::closeForProcessExit).isSameAs(first);
        assertThatThrownBy(retryRuntime::closeForProcessExit).isSameAs(second);
    }

    @Test
    void throwableCombinationAndConversionPreserveIdentity() throws Throwable {
        Throwable same = new Throwable("same");
        assertThat(invokeRuntimeStatic(
                "combine",
                new Class<?>[]{Throwable.class, Throwable.class},
                same,
                same
        )).isSameAs(same);

        Exception checked = new Exception("checked");
        assertThat(invokeRuntimeStatic(
                "rethrowable", new Class<?>[]{Throwable.class}, checked
        )).isSameAs(checked);
        Object wrapped = invokeRuntimeStatic(
                "rethrowable", new Class<?>[]{Throwable.class}, new Throwable("other")
        );
        assertThat(wrapped).isInstanceOf(IllegalStateException.class);
        assertThat(((Throwable) wrapped).getCause()).hasMessage("other");
    }

    private static Object invokeRuntimeStatic(
            String name,
            Class<?>[] parameterTypes,
            Object... arguments
    ) throws Throwable {
        Method method = LegacyContainerItRuntime.class.getDeclaredMethod(name, parameterTypes);
        method.setAccessible(true);
        try {
            return method.invoke(null, arguments);
        } catch (InvocationTargetException failure) {
            throw failure.getCause();
        }
    }

    private LegacyContainerItContract.Activation activation(
            LegacyContainerItContract.Lane lane
    ) {
        LegacyContainerEndpoints.PostgresEndpoint postgres = lane == LegacyContainerItContract.Lane.QUEUE
                ? null
                : new LegacyContainerEndpoints.PostgresEndpoint(
                        "127.0.0.1",
                        15432,
                        "p007_acceptance",
                        "offline_fixture",
                        "offline_fixture_only"
                );
        LegacyContainerEndpoints.RedisEndpoint redis = lane == LegacyContainerItContract.Lane.QUEUE
                ? new LegacyContainerEndpoints.RedisEndpoint("127.0.0.1", 16379)
                : null;
        return LegacyContainerItContract.Activation.forTesting(
                "p007_runtime_01234567",
                lane,
                ledgerDirectory,
                postgres,
                redis
        );
    }

    private static final class RecordingBoundary implements LegacyContainerItRuntime.Boundary {

        private final List<String> events = new ArrayList<>();
        private RuntimeException proofFailure;
        private RuntimeException leaseFailure;
        private final List<RuntimeException> leaseCloseFailures = new ArrayList<>();
        private RuntimeException abortFailure;
        private boolean returnNullLease;
        private AssertionError cleanFailure;
        private RuntimeException sealFailure;

        @Override
        public AutoCloseable allowLoopback(String host, int port) {
            String endpoint = host + ":" + port;
            events.add("lease:" + endpoint);
            if (leaseFailure != null) {
                throw leaseFailure;
            }
            if (returnNullLease) {
                return null;
            }
            return () -> {
                events.add("close:" + endpoint);
                if (!leaseCloseFailures.isEmpty()) {
                    throw leaseCloseFailures.remove(0);
                }
            };
        }

        @Override
        public void proveDeniedControls() {
            events.add("prove-denials");
            if (proofFailure != null) {
                throw proofFailure;
            }
        }

        @Override
        public void assertClean() {
            events.add("assert-clean");
            if (cleanFailure != null) {
                throw cleanFailure;
            }
        }

        @Override
        public void sealForProcessExit() {
            events.add("seal-for-exit");
            if (sealFailure != null) {
                throw sealFailure;
            }
        }

        @Override
        public void abortOpen() {
            events.add("abort-open");
            if (abortFailure != null) {
                throw abortFailure;
            }
        }
    }
}
