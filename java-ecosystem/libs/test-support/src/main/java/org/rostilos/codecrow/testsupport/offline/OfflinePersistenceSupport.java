package org.rostilos.codecrow.testsupport.offline;

import org.testcontainers.containers.GenericContainer;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.images.ImagePullPolicy;
import org.testcontainers.utility.DockerImageName;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import java.util.UUID;

/**
 * Pinned, non-reusable persistence registrations for offline integration tests.
 * Construction does not start Docker; callers explicitly own lifecycle and use
 * the derived namespace for cleanup before and after each scenario.
 */
public final class OfflinePersistenceSupport {

    public static final String POSTGRES_IMAGE =
            "postgres@sha256:e013e867e712fec275706a6c51c966f0bb0c93cfa8f51000f85a15f9865a28cb";
    public static final String REDIS_IMAGE =
            "redis@sha256:6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99";
    public static final String QDRANT_IMAGE =
            "qdrant/qdrant@sha256:75eab8c4ba42096724fdcfde8b4de0b5713d529dde32f285a1f86fdcb2c9e50c";
    public static final ImagePullPolicy LOCAL_ONLY_PULL_POLICY = new LocalOnlyPullPolicy();

    private OfflinePersistenceSupport() {
    }

    public static Namespace namespace(String scenarioId) {
        String requiredScenarioId = Objects.requireNonNull(scenarioId, "scenarioId");
        if (requiredScenarioId.isBlank()) {
            throw new IllegalArgumentException("scenarioId must not be blank");
        }
        UUID digest = UUID.nameUUIDFromBytes(
                requiredScenarioId.getBytes(StandardCharsets.UTF_8)
        );
        String suffix = digest.toString().replace("-", "").substring(0, 16);
        return new Namespace(
                "p003_" + suffix,
                "fixture_" + suffix,
                "fixture:" + suffix + ":",
                "fixture_" + suffix
        );
    }

    public static Containers containers(Namespace namespace) {
        requireRyukDisabled(System.getenv("TESTCONTAINERS_RYUK_DISABLED"));
        Objects.requireNonNull(namespace, "namespace");
        PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>(
                pinnedImage(POSTGRES_IMAGE, "postgres")
        )
                .withImagePullPolicy(LOCAL_ONLY_PULL_POLICY)
                .withReuse(false)
                .withDatabaseName(namespace.postgresDatabase())
                .withUsername("offline_fixture")
                .withPassword("offline_fixture_only")
                .withLabel("codecrow.offline.namespace", namespace.postgresSchema());
        GenericContainer<?> redis = new GenericContainer<>(pinnedImage(REDIS_IMAGE, "redis"))
                .withImagePullPolicy(LOCAL_ONLY_PULL_POLICY)
                .withReuse(false)
                .withExposedPorts(6379)
                .withLabel("codecrow.offline.namespace", namespace.redisPrefix());
        GenericContainer<?> qdrant = new GenericContainer<>(
                DockerImageName.parse(QDRANT_IMAGE)
        )
                .withImagePullPolicy(LOCAL_ONLY_PULL_POLICY)
                .withReuse(false)
                .withExposedPorts(6333, 6334)
                .withLabel("codecrow.offline.namespace", namespace.qdrantCollection());
        return new Containers(postgres, redis, qdrant);
    }

    static void requireRyukDisabled(String configuredValue) {
        if (!"true".equals(configuredValue)) {
            throw new IllegalStateException(
                    "TESTCONTAINERS_RYUK_DISABLED must be exactly true for offline persistence"
            );
        }
    }

    public static void reset(
            Namespace namespace,
            PostgresReset postgres,
            RedisReset redis,
            QdrantReset qdrant
    ) throws Exception {
        runAll(
                () -> postgres.resetSchema(namespace.postgresSchema()),
                () -> redis.deletePrefix(namespace.redisPrefix()),
                () -> qdrant.deleteCollection(namespace.qdrantCollection())
        );
    }

    private static void runAll(CheckedAction... actions) throws Exception {
        List<Throwable> failures = new ArrayList<>();
        for (CheckedAction action : actions) {
            try {
                action.run();
            } catch (Throwable failure) {
                failures.add(failure);
            }
        }
        if (failures.isEmpty()) {
            return;
        }
        Throwable first = failures.get(0);
        failures.stream().skip(1).forEach(first::addSuppressed);
        throw propagate(first);
    }

    static void runAndCleanup(CheckedAction action, CheckedAction cleanup) throws Exception {
        Throwable primaryFailure = null;
        try {
            action.run();
        } catch (Throwable failure) {
            primaryFailure = failure;
        }
        try {
            cleanup.run();
        } catch (Throwable cleanupFailure) {
            if (primaryFailure == null) {
                primaryFailure = cleanupFailure;
            } else {
                primaryFailure.addSuppressed(cleanupFailure);
            }
        }
        if (primaryFailure == null) {
            return;
        }
        throw propagate(primaryFailure);
    }

    private static Error propagate(Throwable failure) throws Exception {
        if (failure instanceof Exception exception) {
            throw exception;
        }
        return (Error) failure;
    }

    private static DockerImageName pinnedImage(String reference, String compatibleImage) {
        return DockerImageName.parse(reference).asCompatibleSubstituteFor(compatibleImage);
    }

    private static final class LocalOnlyPullPolicy implements ImagePullPolicy {

        @Override
        public boolean shouldPull(DockerImageName imageName) {
            return false;
        }

        @Override
        public String toString() {
            return "CodeCrowLocalOnlyImagePullPolicy";
        }
    }

    public record Namespace(
            String postgresDatabase,
            String postgresSchema,
            String redisPrefix,
            String qdrantCollection
    ) {
    }

    public record Containers(
            PostgreSQLContainer<?> postgres,
            GenericContainer<?> redis,
            GenericContainer<?> qdrant
    ) implements AutoCloseable {

        public Containers {
            Objects.requireNonNull(postgres, "postgres");
            Objects.requireNonNull(redis, "redis");
            Objects.requireNonNull(qdrant, "qdrant");
        }

        @Override
        public void close() throws Exception {
            runAll(qdrant::stop, redis::stop, postgres::stop);
        }
    }

    @FunctionalInterface
    interface CheckedAction {
        void run() throws Exception;
    }

    @FunctionalInterface
    public interface PostgresReset {
        void resetSchema(String schema) throws Exception;
    }

    @FunctionalInterface
    public interface RedisReset {
        void deletePrefix(String prefix) throws Exception;
    }

    @FunctionalInterface
    public interface QdrantReset {
        void deleteCollection(String collection) throws Exception;
    }
}
