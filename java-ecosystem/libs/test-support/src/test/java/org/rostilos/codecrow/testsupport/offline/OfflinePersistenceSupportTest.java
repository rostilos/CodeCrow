package org.rostilos.codecrow.testsupport.offline;

import org.junit.jupiter.api.Test;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.utility.DockerImageName;

import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class OfflinePersistenceSupportTest {

    @Test
    void derivesStableIsolatedNamespacesAndPinnedNonReusableContainers() {
        OfflinePersistenceSupport.Namespace first = OfflinePersistenceSupport.namespace("adapter case A");
        OfflinePersistenceSupport.Namespace repeated = OfflinePersistenceSupport.namespace("adapter case A");
        OfflinePersistenceSupport.Namespace different = OfflinePersistenceSupport.namespace("adapter case B");

        assertThatThrownBy(() -> OfflinePersistenceSupport.namespace(" \t"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("scenarioId");

        assertThat(first).isEqualTo(repeated).isNotEqualTo(different);
        assertThat(first.postgresDatabase()).matches("p003_[0-9a-f]{16}");
        assertThat(first.postgresSchema()).startsWith("fixture_");
        assertThat(first.redisPrefix()).endsWith(":");
        assertThat(first.qdrantCollection()).startsWith("fixture_");

        OfflinePersistenceSupport.Containers containers = OfflinePersistenceSupport.containers(first);
        assertThat(List.of(
                OfflinePersistenceSupport.POSTGRES_IMAGE,
                OfflinePersistenceSupport.REDIS_IMAGE,
                OfflinePersistenceSupport.QDRANT_IMAGE
        )).allSatisfy(image -> {
            assertThat(image).contains("@sha256:").doesNotContain(":latest");
            assertThat(OfflinePersistenceSupport.LOCAL_ONLY_PULL_POLICY.shouldPull(
                    DockerImageName.parse(image)
            )).isFalse();
        });
        assertThat(OfflinePersistenceSupport.LOCAL_ONLY_PULL_POLICY)
                .hasToString("CodeCrowLocalOnlyImagePullPolicy");
        assertThat(containers.postgres().getDatabaseName()).isEqualTo(first.postgresDatabase());
        assertThat(containers.postgres().isShouldBeReused()).isFalse();
        assertThat(containers.redis().isShouldBeReused()).isFalse();
        assertThat(containers.qdrant().isShouldBeReused()).isFalse();
        assertThat(containers.redis().getExposedPorts()).containsExactly(6379);
        assertThat(containers.qdrant().getExposedPorts()).containsExactly(6333, 6334);
    }

    @Test
    void resetInvokesEveryPersistenceBoundaryWithOnlyTheDerivedNamespace() throws Exception {
        OfflinePersistenceSupport.Namespace namespace = OfflinePersistenceSupport.namespace("reset case");
        List<String> resets = new ArrayList<>();

        OfflinePersistenceSupport.reset(
                namespace,
                schema -> resets.add("postgres:" + schema),
                prefix -> resets.add("redis:" + prefix),
                collection -> resets.add("qdrant:" + collection)
        );

        assertThat(resets).containsExactly(
                "postgres:" + namespace.postgresSchema(),
                "redis:" + namespace.redisPrefix(),
                "qdrant:" + namespace.qdrantCollection()
        );
    }

    @Test
    void resetAttemptsEveryBoundaryAndPreservesFailureOrder() {
        OfflinePersistenceSupport.Namespace namespace = OfflinePersistenceSupport.namespace("failing reset case");
        List<String> resets = new ArrayList<>();
        Exception postgresFailure = new Exception("postgres reset failed");
        Exception redisFailure = new Exception("redis reset failed");
        Exception qdrantFailure = new Exception("qdrant reset failed");

        assertThatThrownBy(() -> OfflinePersistenceSupport.reset(
                namespace,
                schema -> {
                    resets.add("postgres:" + schema);
                    throw postgresFailure;
                },
                prefix -> {
                    resets.add("redis:" + prefix);
                    throw redisFailure;
                },
                collection -> {
                    resets.add("qdrant:" + collection);
                    throw qdrantFailure;
                }
        )).isSameAs(postgresFailure)
                .satisfies(failure -> assertThat(failure.getSuppressed())
                        .containsExactly(redisFailure, qdrantFailure));

        assertThat(resets).containsExactly(
                "postgres:" + namespace.postgresSchema(),
                "redis:" + namespace.redisPrefix(),
                "qdrant:" + namespace.qdrantCollection()
        );
    }

    @Test
    void failsClosedWithoutRyukDisableAndClosesContainersInReverseOrder() throws Exception {
        assertThatThrownBy(() -> OfflinePersistenceSupport.requireRyukDisabled(null))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("exactly true");
        assertThatThrownBy(() -> OfflinePersistenceSupport.requireRyukDisabled("TRUE"))
                .isInstanceOf(IllegalStateException.class);
        OfflinePersistenceSupport.requireRyukDisabled("true");

        List<String> stopped = new ArrayList<>();
        PostgreSQLContainer<?> postgres = new RecordingPostgres(stopped);
        GenericContainer<?> redis = new RecordingContainer(
                OfflinePersistenceSupport.REDIS_IMAGE, "redis", stopped
        );
        GenericContainer<?> qdrant = new RecordingContainer(
                OfflinePersistenceSupport.QDRANT_IMAGE, "qdrant", stopped
        );

        new OfflinePersistenceSupport.Containers(postgres, redis, qdrant).close();

        assertThat(stopped).containsExactly("qdrant", "redis", "postgres");
    }

    @Test
    void closeAttemptsEveryContainerAndPreservesReverseOrderFailures() {
        List<String> stopped = new ArrayList<>();
        RuntimeException postgresFailure = new RuntimeException("postgres stop failed");
        RuntimeException redisFailure = new RuntimeException("redis stop failed");
        RuntimeException qdrantFailure = new RuntimeException("qdrant stop failed");
        PostgreSQLContainer<?> postgres = new RecordingPostgres(stopped, postgresFailure);
        GenericContainer<?> redis = new RecordingContainer(
                OfflinePersistenceSupport.REDIS_IMAGE, "redis", stopped, redisFailure
        );
        GenericContainer<?> qdrant = new RecordingContainer(
                OfflinePersistenceSupport.QDRANT_IMAGE, "qdrant", stopped, qdrantFailure
        );

        assertThatThrownBy(() -> new OfflinePersistenceSupport.Containers(
                postgres, redis, qdrant
        ).close()).isSameAs(qdrantFailure)
                .satisfies(failure -> assertThat(failure.getSuppressed())
                        .containsExactly(redisFailure, postgresFailure));

        assertThat(stopped).containsExactly("qdrant", "redis", "postgres");
    }

    @Test
    void closeStillAttemptsEveryContainerWhenTheFirstFailureIsAnError() {
        List<String> stopped = new ArrayList<>();
        AssertionError qdrantFailure = new AssertionError("qdrant stop assertion failed");
        RuntimeException redisFailure = new RuntimeException("redis stop failed");
        AssertionError postgresFailure = new AssertionError("postgres stop assertion failed");
        PostgreSQLContainer<?> postgres = new RecordingPostgres(stopped, postgresFailure);
        GenericContainer<?> redis = new RecordingContainer(
                OfflinePersistenceSupport.REDIS_IMAGE, "redis", stopped, redisFailure
        );
        GenericContainer<?> qdrant = new RecordingContainer(
                OfflinePersistenceSupport.QDRANT_IMAGE, "qdrant", stopped, qdrantFailure
        );

        assertThatThrownBy(() -> new OfflinePersistenceSupport.Containers(
                postgres, redis, qdrant
        ).close()).isSameAs(qdrantFailure)
                .satisfies(failure -> assertThat(failure.getSuppressed())
                        .containsExactly(redisFailure, postgresFailure));

        assertThat(stopped).containsExactly("qdrant", "redis", "postgres");
    }

    @Test
    void runAndCleanupPreservesPrimaryFailureAndSuppressesCleanupFailure() {
        List<String> actions = new ArrayList<>();
        Exception primaryFailure = new Exception("lifecycle failed");
        AssertionError cleanupFailure = new AssertionError("cleanup assertion failed");

        assertThatThrownBy(() -> OfflinePersistenceSupport.runAndCleanup(
                () -> {
                    actions.add("action");
                    throw primaryFailure;
                },
                () -> {
                    actions.add("cleanup");
                    throw cleanupFailure;
                }
        )).isSameAs(primaryFailure)
                .satisfies(failure -> assertThat(failure.getSuppressed())
                        .containsExactly(cleanupFailure));

        assertThat(actions).containsExactly("action", "cleanup");
    }

    @Test
    void runAndCleanupReturnsOnSuccessAndPropagatesCleanupOnlyFailure() throws Exception {
        List<String> actions = new ArrayList<>();
        OfflinePersistenceSupport.runAndCleanup(
                () -> actions.add("action"),
                () -> actions.add("cleanup")
        );
        assertThat(actions).containsExactly("action", "cleanup");

        AssertionError cleanupFailure = new AssertionError("cleanup-only failure");
        assertThatThrownBy(() -> OfflinePersistenceSupport.runAndCleanup(
                () -> actions.add("second action"),
                () -> {
                    actions.add("second cleanup");
                    throw cleanupFailure;
                }
        )).isSameAs(cleanupFailure);
        assertThat(actions).containsExactly(
                "action", "cleanup", "second action", "second cleanup"
        );
    }

    private static final class RecordingPostgres extends PostgreSQLContainer<RecordingPostgres> {

        private final List<String> stopped;
        private final Throwable failure;

        private RecordingPostgres(List<String> stopped) {
            this(stopped, null);
        }

        private RecordingPostgres(List<String> stopped, Throwable failure) {
            super(DockerImageName.parse(OfflinePersistenceSupport.POSTGRES_IMAGE)
                    .asCompatibleSubstituteFor("postgres"));
            this.stopped = stopped;
            this.failure = failure;
        }

        @Override
        public void stop() {
            stopped.add("postgres");
            rethrowUnchecked(failure);
        }
    }

    private static final class RecordingContainer extends GenericContainer<RecordingContainer> {

        private final String name;
        private final List<String> stopped;
        private final Throwable failure;

        private RecordingContainer(String image, String name, List<String> stopped) {
            this(image, name, stopped, null);
        }

        private RecordingContainer(
                String image,
                String name,
                List<String> stopped,
                Throwable failure
        ) {
            super(DockerImageName.parse(image));
            this.name = name;
            this.stopped = stopped;
            this.failure = failure;
        }

        @Override
        public void stop() {
            stopped.add(name);
            rethrowUnchecked(failure);
        }
    }

    private static void rethrowUnchecked(Throwable failure) {
        if (failure instanceof RuntimeException runtime) {
            throw runtime;
        }
        if (failure instanceof Error error) {
            throw error;
        }
    }
}
