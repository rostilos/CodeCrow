package org.rostilos.codecrow.testsupport.legacy;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Arrays;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;
import java.util.regex.Pattern;

/** Exact opt-in contract for the three externally provisioned legacy IT lanes. */
public final class LegacyContainerItContract {

    static final String PROTOCOL_ENV = "CODECROW_LEGACY_IT_PROTOCOL";
    static final String EXECUTOR_ENV = "CODECROW_LEGACY_IT_EXECUTOR";
    static final String RUN_ID_ENV = "CODECROW_LEGACY_IT_RUN_ID";
    static final String LANE_ENV = "CODECROW_LEGACY_IT_LANE";
    static final String TARGET_ARTIFACT_ENV = "CODECROW_LEGACY_IT_TARGET_ARTIFACT";
    static final String REDIS_HOST_ENV = "CODECROW_LEGACY_IT_REDIS_HOST";
    static final String REDIS_PORT_ENV = "CODECROW_LEGACY_IT_REDIS_PORT";
    static final String POSTGRES_HOST_ENV = "CODECROW_LEGACY_IT_POSTGRES_HOST";
    static final String POSTGRES_PORT_ENV = "CODECROW_LEGACY_IT_POSTGRES_PORT";
    static final String POSTGRES_DATABASE_ENV = "CODECROW_LEGACY_IT_POSTGRES_DATABASE";
    static final String POSTGRES_USERNAME_ENV = "CODECROW_LEGACY_IT_POSTGRES_USERNAME";
    static final String POSTGRES_PASSWORD_ENV = "CODECROW_LEGACY_IT_POSTGRES_PASSWORD";
    static final String LEDGER_DIRECTORY_ENV = "CODECROW_EXTERNAL_CALL_LEDGER_DIR";
    static final String PROVISIONING_RECEIPT_ENV = "CODECROW_LEGACY_IT_PROVISIONING_RECEIPT";
    static final String PROVISIONING_RECEIPT_SHA256_ENV =
            "CODECROW_LEGACY_IT_PROVISIONING_RECEIPT_SHA256";
    static final String TARGET_ARTIFACT_PROPERTY = "codecrow.legacy-it.target-artifact";

    private static final String GUARDED_PREFIX = "CODECROW_LEGACY_IT_";
    private static final String SELECTOR_PROPERTY = "it.test";
    private static final String GUARDED_PROPERTY_PREFIX = "codecrow.legacy-it.";
    private static final Pattern RUN_ID = Pattern.compile("^[a-z0-9][a-z0-9_-]{7,63}$");
    private static final Pattern SHA256 = Pattern.compile("^[0-9a-f]{64}$");
    private static final String IMAGE_MANIFEST_SHA256 =
            "a0c1f1063fadb33cc486760abeeb0edd2a1889c790ac69e9a1a12529cf3ae71c";
    private static final String GUARDED_POLICY_SHA256 =
            "c79a437923ecfbbedfd2f7a369dc7e71a5caa6f2d119595615ca152f4805cb59";
    private static final String POSTGRES_IMAGE =
            "postgres@sha256:e013e867e712fec275706a6c51c966f0bb0c93cfa8f51000f85a15f9865a28cb";
    private static final String REDIS_IMAGE =
            "redis@sha256:6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99";
    private static final Set<String> GUARDED_KEYS = Set.of(
            PROTOCOL_ENV,
            EXECUTOR_ENV,
            RUN_ID_ENV,
            LANE_ENV,
            TARGET_ARTIFACT_ENV,
            REDIS_HOST_ENV,
            REDIS_PORT_ENV,
            POSTGRES_HOST_ENV,
            POSTGRES_PORT_ENV,
            POSTGRES_DATABASE_ENV,
            POSTGRES_USERNAME_ENV,
            POSTGRES_PASSWORD_ENV,
            PROVISIONING_RECEIPT_ENV,
            PROVISIONING_RECEIPT_SHA256_ENV
    );
    private static final Set<String> POSTGRES_KEYS = Set.of(
            POSTGRES_HOST_ENV,
            POSTGRES_PORT_ENV,
            POSTGRES_DATABASE_ENV,
            POSTGRES_USERNAME_ENV,
            POSTGRES_PASSWORD_ENV
    );
    private static final Set<String> REDIS_KEYS = Set.of(REDIS_HOST_ENV, REDIS_PORT_ENV);

    private LegacyContainerItContract() {
    }

    public static Optional<Activation> activation(
            Map<String, String> environment,
            Map<String, String> properties
    ) {
        Objects.requireNonNull(environment, "environment");
        Objects.requireNonNull(properties, "properties");
        boolean requested = environment.keySet().stream()
                .anyMatch(key -> key.startsWith(GUARDED_PREFIX));
        if (!requested) {
            return Optional.empty();
        }

        rejectUnknownGuardedKeys(environment);
        rejectContainerRuntimeVisibility(environment);
        rejectUnknownGuardedProperties(properties);

        String protocol = required(environment, PROTOCOL_ENV);
        if (!"1".equals(protocol)) {
            throw new IllegalStateException("unsupported guarded legacy IT protocol");
        }
        if (!"maven-failsafe".equals(required(environment, EXECUTOR_ENV))) {
            throw new IllegalStateException(
                    "guarded legacy IT activation is restricted to Maven Failsafe"
            );
        }
        String runId = required(environment, RUN_ID_ENV);
        if (!RUN_ID.matcher(runId).matches()) {
            throw new IllegalStateException("invalid guarded legacy IT run id");
        }
        Lane lane = Lane.fromId(required(environment, LANE_ENV));
        String targetArtifact = required(environment, TARGET_ARTIFACT_ENV);
        if (!lane.targetArtifact().equals(targetArtifact)) {
            throw new IllegalStateException("environment target artifact does not match lane");
        }
        if (!targetArtifact.equals(required(properties, TARGET_ARTIFACT_PROPERTY))) {
            throw new IllegalStateException("JVM target artifact does not match guarded lane");
        }
        if (!lane.selectors().equals(required(properties, SELECTOR_PROPERTY))) {
            throw new IllegalStateException("JVM selector set does not match guarded lane");
        }

        Path ledgerDirectory = ledgerDirectory(required(environment, LEDGER_DIRECTORY_ENV));
        LegacyContainerEndpoints.PostgresEndpoint postgres = null;
        LegacyContainerEndpoints.RedisEndpoint redis = null;
        if (lane == Lane.QUEUE) {
            rejectPresent(environment, POSTGRES_KEYS, "queue lane forbids PostgreSQL variables");
            redis = new LegacyContainerEndpoints.RedisEndpoint(
                    required(environment, REDIS_HOST_ENV),
                    exactPort(environment, REDIS_PORT_ENV)
            );
        } else {
            rejectPresent(environment, REDIS_KEYS, "PostgreSQL lane forbids Redis variables");
            postgres = new LegacyContainerEndpoints.PostgresEndpoint(
                    required(environment, POSTGRES_HOST_ENV),
                    exactPort(environment, POSTGRES_PORT_ENV),
                    reviewedValue(
                            environment,
                            POSTGRES_DATABASE_ENV,
                            LegacyContainerEndpoints.POSTGRES_DATABASE
                    ),
                    reviewedValue(
                            environment,
                            POSTGRES_USERNAME_ENV,
                            LegacyContainerEndpoints.POSTGRES_USERNAME
                    ),
                    reviewedValue(
                            environment,
                            POSTGRES_PASSWORD_ENV,
                            LegacyContainerEndpoints.POSTGRES_PASSWORD
                    )
            );
        }
        verifyProvisioningReceipt(
                environment,
                protocol,
                runId,
                lane,
                targetArtifact,
                ledgerDirectory,
                lane == Lane.QUEUE ? redis.host() : postgres.host(),
                lane == Lane.QUEUE ? redis.port() : postgres.port()
        );

        Activation activation = new Activation(
                protocol,
                runId,
                lane,
                targetArtifact,
                ledgerPath(ledgerDirectory, lane, runId),
                postgres,
                redis
        );
        rejectExistingLedger(activation.ledgerPath());
        return Optional.of(activation);
    }

    private static void rejectUnknownGuardedKeys(Map<String, String> environment) {
        Set<String> unknown = new HashSet<>();
        for (String key : environment.keySet()) {
            if (key.startsWith(GUARDED_PREFIX) && !GUARDED_KEYS.contains(key)) {
                unknown.add(key);
            }
        }
        if (!unknown.isEmpty()) {
            throw new IllegalStateException("unknown guarded environment variable(s): "
                    + String.join(",", unknown.stream().sorted().toList()));
        }
    }

    private static void rejectContainerRuntimeVisibility(Map<String, String> environment) {
        environment.keySet().stream()
                .filter(key -> key.startsWith("DOCKER_") || key.startsWith("TESTCONTAINERS_"))
                .sorted()
                .findFirst()
                .ifPresent(key -> {
                    throw new IllegalStateException(
                            "guarded JVM exposes forbidden runtime variable " + key
                    );
                });
    }

    private static void rejectUnknownGuardedProperties(Map<String, String> properties) {
        properties.keySet().stream()
                .filter(key -> key.startsWith(GUARDED_PROPERTY_PREFIX))
                .filter(key -> !TARGET_ARTIFACT_PROPERTY.equals(key))
                .sorted()
                .findFirst()
                .ifPresent(key -> {
                    throw new IllegalStateException(
                            "unknown guarded system property: " + key
                    );
                });
    }

    private static void rejectPresent(
            Map<String, String> environment,
            Set<String> forbidden,
            String message
    ) {
        if (forbidden.stream().anyMatch(environment::containsKey)) {
            throw new IllegalStateException(message);
        }
    }

    private static String required(Map<String, String> values, String key) {
        String value = values.get(key);
        if (value == null || value.isBlank()) {
            throw new IllegalStateException("required contract value is missing: " + key);
        }
        return value;
    }

    private static int exactPort(Map<String, String> environment, String key) {
        String value = required(environment, key);
        String expected = REDIS_PORT_ENV.equals(key) ? "16379" : "15432";
        if (!expected.equals(value)) {
            throw new IllegalStateException(
                    "guarded endpoint port must be the canonical fixed value " + expected
            );
        }
        return Integer.parseInt(expected);
    }

    private static String reviewedValue(
            Map<String, String> environment,
            String key,
            String expected
    ) {
        String value = required(environment, key);
        if (!expected.equals(value)) {
            throw new IllegalStateException(
                    "PostgreSQL contract value does not match the reviewed fixture: " + key
            );
        }
        return value;
    }

    private static Path ledgerDirectory(String text) {
        Path candidate;
        try {
            candidate = Path.of(text);
        } catch (RuntimeException invalidPath) {
            throw new IllegalStateException("invalid external-call ledger directory");
        }
        return LegacyContainerSafePaths.requireTrustedDirectory(candidate);
    }

    private static Path ledgerPath(Path directory, Lane lane, String runId) {
        return directory.resolve(
                "legacy-container-it-" + lane.id() + "-" + runId + ".json"
        );
    }

    private static void rejectExistingLedger(Path ledgerPath) {
        if (Files.exists(ledgerPath, LinkOption.NOFOLLOW_LINKS)) {
            throw new IllegalStateException("guarded external-call ledger already exists");
        }
    }

    private static void verifyProvisioningReceipt(
            Map<String, String> environment,
            String protocol,
            String runId,
            Lane lane,
            String targetArtifact,
            Path ledgerDirectory,
            String serviceHost,
            int servicePort
    ) {
        Path receipt;
        try {
            receipt = Path.of(required(environment, PROVISIONING_RECEIPT_ENV))
                    .toAbsolutePath()
                    .normalize();
        } catch (RuntimeException invalidPath) {
            throw new IllegalStateException("invalid guarded provisioning receipt path");
        }
        if (!ledgerDirectory.resolve("provisioning.receipt").equals(receipt)
                || Files.isSymbolicLink(receipt)
                || !Files.isRegularFile(receipt, LinkOption.NOFOLLOW_LINKS)) {
            throw new IllegalStateException(
                    "guarded provisioning receipt must be the exact regular lane artifact"
            );
        }
        try {
            if (!Files.getPosixFilePermissions(receipt, LinkOption.NOFOLLOW_LINKS).equals(
                    PosixFilePermissions.fromString("r--------")
            )) {
                throw new IllegalStateException(
                        "guarded provisioning receipt must have private mode 0400"
                );
            }
            if (!Files.getOwner(receipt, LinkOption.NOFOLLOW_LINKS).equals(
                    Files.getOwner(Path.of("/proc/self"))
            )) {
                throw new IllegalStateException(
                        "guarded provisioning receipt must be owned by the guarded process"
                );
            }
            byte[] document = Files.readAllBytes(receipt);
            if (document.length == 0 || document.length > 4096) {
                throw new IllegalStateException("guarded provisioning receipt size is invalid");
            }
            if (document[document.length - 1] != '\n') {
                throw new IllegalStateException(
                        "guarded provisioning receipt must end with canonical LF"
                );
            }
            for (byte value : document) {
                int unsigned = Byte.toUnsignedInt(value);
                if (unsigned != '\n' && (unsigned < 0x20 || unsigned > 0x7e)) {
                    throw new IllegalStateException(
                            "guarded provisioning receipt must be canonical ASCII text"
                    );
                }
            }
            String expectedDigest = required(environment, PROVISIONING_RECEIPT_SHA256_ENV);
            if (!SHA256.matcher(expectedDigest).matches()
                    || !expectedDigest.equals(sha256(document))) {
                throw new IllegalStateException("guarded provisioning receipt digest mismatch");
            }
            String token = runId.replace('_', '-');
            String expectedImage = lane == Lane.QUEUE ? REDIS_IMAGE : POSTGRES_IMAGE;
            List<String> lines = new String(document, StandardCharsets.US_ASCII)
                    .lines()
                    .toList();
            if (lines.size() != 11
                    || !lines.get(0).equals("schemaVersion=" + protocol)
                    || !lines.get(1).equals("runId=" + runId)
                    || !lines.get(2).equals("lane=" + lane.id())
                    || !lines.get(3).equals("targetArtifact=" + targetArtifact)
                    || !lines.get(4).equals("namespace=codecrow-" + token + "-" + lane.id())
                    || !lines.get(5).equals("policySha256=" + GUARDED_POLICY_SHA256)
                    || !lines.get(6).equals("imageManifestSha256=" + IMAGE_MANIFEST_SHA256)
                    || !lines.get(7).equals("imageReference=" + expectedImage)
                    || !lines.get(8).matches("containerId=[0-9a-f]{64}")
                    || !lines.get(9).equals("serviceHost=" + serviceHost)
                    || !lines.get(10).equals("servicePort=" + servicePort)) {
                throw new IllegalStateException("guarded provisioning receipt contract mismatch");
            }
        } catch (UnsupportedOperationException unsupported) {
            throw new IllegalStateException(
                    "guarded provisioning receipt requires POSIX nofollow validation"
            );
        } catch (IOException failure) {
            throw new IllegalStateException("cannot verify guarded provisioning receipt");
        }
    }

    private static String sha256(byte[] content) {
        try {
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(content)
            );
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException("SHA-256 is unavailable", impossible);
        }
    }

    public enum Lane {
        QUEUE(
                "queue",
                "codecrow-queue",
                "org.rostilos.codecrow.queue.ConnectionFactoryIT,"
                        + "org.rostilos.codecrow.queue.QueueIsolationIT,"
                        + "org.rostilos.codecrow.queue.RedisQueueIT"
        ),
        PIPELINE(
                "pipeline",
                "codecrow-pipeline-agent",
                "org.rostilos.codecrow.pipelineagent.BranchResolverFlowIT,"
                        + "org.rostilos.codecrow.pipelineagent.HealthCheckControllerIT,"
                        + "org.rostilos.codecrow.pipelineagent.LineTrackingFlowIT,"
                        + "org.rostilos.codecrow.pipelineagent.PipelineActionControllerIT,"
                        + "org.rostilos.codecrow.pipelineagent.PipelineAgentSecurityIT,"
                        + "org.rostilos.codecrow.pipelineagent.ProviderWebhookControllerIT,"
                        + "org.rostilos.codecrow.pipelineagent.RagIndexingControllerIT"
        ),
        WEB(
                "web",
                "codecrow-web-server",
                "org.rostilos.codecrow.webserver.AuthControllerIT,"
                        + "org.rostilos.codecrow.webserver.HealthCheckControllerIT,"
                        + "org.rostilos.codecrow.webserver.InternalApiSecurityIT,"
                        + "org.rostilos.codecrow.webserver.LlmModelControllerIT,"
                        + "org.rostilos.codecrow.webserver.ProjectControllerIT,"
                        + "org.rostilos.codecrow.webserver.PublicSiteConfigControllerIT,"
                        + "org.rostilos.codecrow.webserver.QualityGateControllerIT,"
                        + "org.rostilos.codecrow.webserver.TaskManagementControllerIT,"
                        + "org.rostilos.codecrow.webserver.UserDataControllerIT,"
                        + "org.rostilos.codecrow.webserver.WorkspaceControllerIT"
        );

        private final String id;
        private final String targetArtifact;
        private final String selectors;

        Lane(String id, String targetArtifact, String selectors) {
            this.id = id;
            this.targetArtifact = targetArtifact;
            this.selectors = selectors;
        }

        public String id() {
            return id;
        }

        public String targetArtifact() {
            return targetArtifact;
        }

        public String selectors() {
            return selectors;
        }

        private static Lane fromId(String id) {
            return Arrays.stream(values())
                    .filter(lane -> lane.id.equals(id))
                    .findFirst()
                    .orElseThrow(() -> new IllegalStateException(
                            "unknown guarded legacy IT lane"
                    ));
        }
    }

    public record Activation(
            String protocol,
            String runId,
            Lane lane,
            String targetArtifact,
            Path ledgerPath,
            LegacyContainerEndpoints.PostgresEndpoint postgresEndpoint,
            LegacyContainerEndpoints.RedisEndpoint redisEndpoint
    ) {

        public Activation {
            Objects.requireNonNull(protocol, "protocol");
            Objects.requireNonNull(runId, "runId");
            Objects.requireNonNull(lane, "lane");
            Objects.requireNonNull(targetArtifact, "targetArtifact");
            Objects.requireNonNull(ledgerPath, "ledgerPath");
            if ((postgresEndpoint == null) == (redisEndpoint == null)) {
                throw new IllegalStateException("guarded lane must expose exactly one service");
            }
            if (lane == Lane.QUEUE && redisEndpoint == null) {
                throw new IllegalStateException("queue lane requires Redis");
            }
            if (lane != Lane.QUEUE && postgresEndpoint == null) {
                throw new IllegalStateException("PostgreSQL lane requires PostgreSQL");
            }
        }

        public Optional<LegacyContainerEndpoints.PostgresEndpoint> postgres() {
            return Optional.ofNullable(postgresEndpoint);
        }

        public Optional<LegacyContainerEndpoints.RedisEndpoint> redis() {
            return Optional.ofNullable(redisEndpoint);
        }

        @Override
        public String toString() {
            return "Activation[protocol=" + protocol
                    + ", runId=" + runId
                    + ", lane=" + lane
                    + ", targetArtifact=" + targetArtifact
                    + ", ledgerPath=" + ledgerPath
                    + ", postgres=" + (postgresEndpoint == null ? "absent" : "<redacted>")
                    + ", redis=" + (redisEndpoint == null ? "absent" : redisEndpoint)
                    + "]";
        }

        static Activation forTesting(
                String runId,
                Lane lane,
                Path ledgerDirectory,
                LegacyContainerEndpoints.PostgresEndpoint postgres,
                LegacyContainerEndpoints.RedisEndpoint redis
        ) {
            return new Activation(
                    "1",
                    runId,
                    lane,
                    lane.targetArtifact,
                    LegacyContainerItContract.ledgerPath(ledgerDirectory, lane, runId),
                    postgres,
                    redis
            );
        }
    }
}
