package org.rostilos.codecrow.analysisengine.execution;

import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/** Contract for P1-01 durable execution-manifest persistence. */
class ExecutionManifestPersistenceContractTest {
    private static final String EXECUTION_ID = "pr:execution-0001";
    private static final String DIFF_ARTIFACT_ID = "diff:pull-request-82";
    private static final String ARTIFACT_SCHEMA_VERSION = "review-artifact-v1";
    private static final String PRODUCER = "java-vcs-acquisition";
    private static final String PRODUCER_VERSION = "analysis-engine-v1";
    private static final byte[] RAW_DIFF = ("diff --git a/a.java b/a.java\n"
            + "--- a/a.java\n"
            + "+++ b/a.java\n"
            + "@@ -1 +1 @@\n"
            + "-old\n"
            + "+new\n").getBytes(StandardCharsets.UTF_8);
    private static final String DIFF_DIGEST = sha256(RAW_DIFF);

    @Test
    void atomicallyPersistsTheManifestAndFullyBoundInitialDiffEntry() {
        InMemoryPort port = new InMemoryPort();
        ImmutableExecutionManifest manifest = manifest("b".repeat(40), "creation:00000001");
        ExecutionManifestService service = new ExecutionManifestService(port);

        assertThat(service.persistBeforeWork(manifest, RAW_DIFF.clone())).isEqualTo(manifest);

        assertThat(port.createOrLoadCalls).isOne();
        assertThat(port.lastManifest).isEqualTo(manifest);
        ExecutionArtifactPayload persistedPayload = port.lastArtifacts.get(0);
        ArtifactManifestEntry entry = persistedPayload.entry();
        assertThat(entry.executionId()).isEqualTo(EXECUTION_ID);
        assertThat(entry.artifactId()).isEqualTo(DIFF_ARTIFACT_ID);
        assertThat(entry.contentKey())
                .isEqualTo(ImmutableExecutionManifest.RAW_DIFF_CONTENT_KEY);
        assertThat(entry.snapshotSha()).isEqualTo(manifest.headSha());
        assertThat(entry.contentDigest()).isEqualTo(DIFF_DIGEST);
        assertThat(entry.byteLength()).isEqualTo(RAW_DIFF.length);
        assertThat(entry.kind()).isEqualTo(ArtifactManifestEntry.Kind.RAW_DIFF);
        assertThat(entry.artifactSchemaVersion()).isEqualTo(ARTIFACT_SCHEMA_VERSION);
        assertThat(entry.producer()).isEqualTo(PRODUCER);
        assertThat(entry.producerVersion()).isEqualTo(PRODUCER_VERSION);
        assertThat(persistedPayload.content()).isEqualTo(RAW_DIFF);
        assertThat(port.findByExecutionId(EXECUTION_ID)).contains(
                persisted(manifest, List.of(persistedPayload)));
    }

    @Test
    void verifiesRawBytesBeforeThePersistencePortCanBeCalled() {
        InMemoryPort port = new InMemoryPort();
        ExecutionManifestService service = new ExecutionManifestService(port);

        assertThatThrownBy(() -> service.persistBeforeWork(
                manifest("b".repeat(40), "creation:00000001"),
                "tampered diff".getBytes(StandardCharsets.UTF_8)))
                .isInstanceOf(IllegalArgumentException.class);

        assertThat(port.createOrLoadCalls).isZero();
        assertThat(port.findByExecutionId(EXECUTION_ID)).isEmpty();
    }

    @Test
    void acceptsAnExactReplayButFailsClosedWhenCreateOrLoadReturnsAConflict() {
        InMemoryPort port = new InMemoryPort();
        ImmutableExecutionManifest original = manifest("b".repeat(40), "creation:00000001");
        ImmutableExecutionManifest conflict = manifest("d".repeat(40), "creation:00000002");

        assertThat(new ExecutionManifestService(port)
                .persistBeforeWork(original, RAW_DIFF.clone())).isEqualTo(original);
        assertThat(new ExecutionManifestService(port)
                .persistBeforeWork(original, RAW_DIFF.clone())).isEqualTo(original);

        assertThatThrownBy(() -> new ExecutionManifestService(port)
                .persistBeforeWork(conflict, RAW_DIFF.clone()))
                .isInstanceOf(IllegalStateException.class);
        assertThat(port.createOrLoadCalls).isEqualTo(3);
        assertThat(port.findByExecutionId(EXECUTION_ID))
                .contains(persisted(original, List.of(expectedPayload(original))));
    }

    @Test
    void aFreshServiceInstanceCanRequireTheVerifiedPersistedState() {
        InMemoryPort port = new InMemoryPort();
        ImmutableExecutionManifest manifest = manifest("b".repeat(40), "creation:00000001");
        new ExecutionManifestService(port)
                .persistBeforeWork(manifest, RAW_DIFF.clone());

        ExecutionManifestService restartedService =
                new ExecutionManifestService(port);

        assertThat(restartedService.requireVerified(EXECUTION_ID)).isEqualTo(manifest);
        assertThatThrownBy(() -> restartedService.requireVerified("pr:missing-execution"))
                .isInstanceOf(IllegalStateException.class);
    }

    @Test
    void atomicallyPersistsAndReloadsEveryManifestBoundInputArtifact() {
        byte[] sourceContent = "class A {}\n".getBytes(StandardCharsets.UTF_8);
        String headSha = "b".repeat(40);
        ArtifactManifestEntry rawDiff = entry(
                EXECUTION_ID,
                DIFF_ARTIFACT_ID,
                ImmutableExecutionManifest.RAW_DIFF_CONTENT_KEY,
                headSha,
                DIFF_DIGEST,
                RAW_DIFF.length,
                ArtifactManifestEntry.Kind.RAW_DIFF,
                ARTIFACT_SCHEMA_VERSION,
                PRODUCER,
                PRODUCER_VERSION);
        ArtifactManifestEntry sourceFile = entry(
                EXECUTION_ID,
                "source:file-a",
                "src/main/java/A.java",
                headSha,
                sha256(sourceContent),
                sourceContent.length,
                ArtifactManifestEntry.Kind.SOURCE_FILE,
                ARTIFACT_SCHEMA_VERSION,
                PRODUCER,
                PRODUCER_VERSION);
        ImmutableExecutionManifest manifest = manifest(
                headSha,
                "creation:00000001",
                List.of(sourceFile, rawDiff));
        List<ExecutionArtifactPayload> inputArtifacts = List.of(
                payload(rawDiff, RAW_DIFF),
                payload(sourceFile, sourceContent));
        InMemoryPort port = new InMemoryPort();

        assertThat(new ExecutionManifestService(port)
                .persistBeforeWork(manifest, inputArtifacts))
                .isEqualTo(manifest);
        assertThat(port.lastArtifacts).containsExactlyElementsOf(inputArtifacts);
        assertThat(new ExecutionManifestService(port).requireVerified(EXECUTION_ID))
                .isEqualTo(manifest);
    }

    @Test
    void requireVerifiedRejectsMissingForeignOrTamperedArtifactBindings() {
        ImmutableExecutionManifest manifest = manifest("b".repeat(40), "creation:00000001");
        ArtifactManifestEntry exact = expectedEntry(manifest);
        byte[] sameLengthTamper = RAW_DIFF.clone();
        sameLengthTamper[sameLengthTamper.length - 1] ^= 1;
        byte[] longerContent = (new String(RAW_DIFF, StandardCharsets.UTF_8) + "x")
                .getBytes(StandardCharsets.UTF_8);
        List<Corruption> corruptions = List.of(
                new Corruption("missing diff entry", List.of()),
                new Corruption("wrong execution owner", List.of(payload(entry(
                        "pr:another-execution", exact.artifactId(), exact.contentKey(),
                        exact.snapshotSha(), exact.contentDigest(), exact.byteLength(),
                        exact.kind(), exact.artifactSchemaVersion(), exact.producer(),
                        exact.producerVersion()), RAW_DIFF))),
                new Corruption("wrong artifact owner", List.of(payload(entry(
                        exact.executionId(), "diff:another-artifact", exact.contentKey(),
                        exact.snapshotSha(), exact.contentDigest(), exact.byteLength(),
                        exact.kind(), exact.artifactSchemaVersion(), exact.producer(),
                        exact.producerVersion()), RAW_DIFF))),
                new Corruption("wrong content key", List.of(payload(entry(
                        exact.executionId(), exact.artifactId(), "another.diff",
                        exact.snapshotSha(), exact.contentDigest(), exact.byteLength(),
                        exact.kind(), exact.artifactSchemaVersion(), exact.producer(),
                        exact.producerVersion()), RAW_DIFF))),
                new Corruption("wrong snapshot", List.of(payload(entry(
                        exact.executionId(), exact.artifactId(), exact.contentKey(),
                        "d".repeat(40), exact.contentDigest(), exact.byteLength(),
                        exact.kind(), exact.artifactSchemaVersion(), exact.producer(),
                        exact.producerVersion()), RAW_DIFF))),
                new Corruption("tampered digest", List.of(payload(entry(
                        exact.executionId(), exact.artifactId(), exact.contentKey(),
                        exact.snapshotSha(), sha256(sameLengthTamper),
                        sameLengthTamper.length, exact.kind(), exact.artifactSchemaVersion(),
                        exact.producer(), exact.producerVersion()), sameLengthTamper))),
                new Corruption("tampered length", List.of(payload(entry(
                        exact.executionId(), exact.artifactId(), exact.contentKey(),
                        exact.snapshotSha(), sha256(longerContent), longerContent.length,
                        exact.kind(), exact.artifactSchemaVersion(), exact.producer(),
                        exact.producerVersion()), longerContent))),
                new Corruption("wrong kind", List.of(payload(entry(
                        exact.executionId(), exact.artifactId(), exact.contentKey(),
                        exact.snapshotSha(), exact.contentDigest(), exact.byteLength(),
                        ArtifactManifestEntry.Kind.REVIEW_OUTPUT, exact.artifactSchemaVersion(),
                        exact.producer(), exact.producerVersion()), RAW_DIFF))),
                new Corruption("wrong artifact schema", List.of(payload(entry(
                        exact.executionId(), exact.artifactId(), exact.contentKey(),
                        exact.snapshotSha(), exact.contentDigest(), exact.byteLength(),
                        exact.kind(), "review-artifact-v2", exact.producer(),
                        exact.producerVersion()), RAW_DIFF))),
                new Corruption("wrong producer", List.of(payload(entry(
                        exact.executionId(), exact.artifactId(), exact.contentKey(),
                        exact.snapshotSha(), exact.contentDigest(), exact.byteLength(),
                        exact.kind(), exact.artifactSchemaVersion(), "python-orchestrator",
                        exact.producerVersion()), RAW_DIFF))),
                new Corruption("wrong producer version", List.of(payload(entry(
                        exact.executionId(), exact.artifactId(), exact.contentKey(),
                        exact.snapshotSha(), exact.contentDigest(), exact.byteLength(),
                        exact.kind(), exact.artifactSchemaVersion(), exact.producer(),
                        "analysis-engine-v2"), RAW_DIFF))));

        for (Corruption corruption : corruptions) {
            InMemoryPort port = new InMemoryPort();
            port.seed(EXECUTION_ID, persisted(manifest, corruption.artifacts()));

            assertThatThrownBy(() -> new ExecutionManifestService(port)
                    .requireVerified(EXECUTION_ID))
                    .as(corruption.description())
                    .isInstanceOf(IllegalStateException.class);
        }
    }

    private static ImmutableExecutionManifest manifest(String headSha, String creationFence) {
        ArtifactManifestEntry rawDiff = entry(
                EXECUTION_ID,
                DIFF_ARTIFACT_ID,
                ImmutableExecutionManifest.RAW_DIFF_CONTENT_KEY,
                headSha,
                DIFF_DIGEST,
                RAW_DIFF.length,
                ArtifactManifestEntry.Kind.RAW_DIFF,
                ARTIFACT_SCHEMA_VERSION,
                PRODUCER,
                PRODUCER_VERSION);
        return manifest(headSha, creationFence, List.of(rawDiff));
    }

    private static ImmutableExecutionManifest manifest(
            String headSha,
            String creationFence,
            List<ArtifactManifestEntry> inputArtifacts) {
        return ImmutableExecutionManifest.create(
                1,
                EXECUTION_ID,
                41L,
                "github:codecrow/codecrow-public",
                82L,
                "a".repeat(40),
                headSha,
                "c".repeat(40),
                DIFF_ARTIFACT_ID,
                DIFF_DIGEST,
                RAW_DIFF.length,
                "raw-diff",
                PRODUCER,
                PRODUCER_VERSION,
                ARTIFACT_SCHEMA_VERSION,
                "candidate-review-v2",
                creationFence,
                Instant.parse("2026-07-15T12:00:00Z"),
                inputArtifacts);
    }

    private static ArtifactManifestEntry expectedEntry(ImmutableExecutionManifest manifest) {
        return entry(
                manifest.executionId(),
                manifest.diffArtifactId(),
                ImmutableExecutionManifest.RAW_DIFF_CONTENT_KEY,
                manifest.headSha(),
                manifest.diffDigest(),
                manifest.diffByteLength(),
                ArtifactManifestEntry.Kind.RAW_DIFF,
                manifest.artifactSchemaVersion(),
                manifest.diffArtifactProducer(),
                manifest.diffArtifactProducerVersion());
    }

    private static ArtifactManifestEntry entry(
            String executionId,
            String artifactId,
            String contentKey,
            String snapshotSha,
            String contentDigest,
            long byteLength,
            ArtifactManifestEntry.Kind kind,
            String artifactSchemaVersion,
            String producer,
            String producerVersion) {
        return new ArtifactManifestEntry(
                executionId,
                artifactId,
                contentKey,
                snapshotSha,
                contentDigest,
                byteLength,
                kind,
                artifactSchemaVersion,
                producer,
                producerVersion);
    }

    private static ExecutionManifestPersistencePort.PersistedExecution persisted(
            ImmutableExecutionManifest manifest,
            List<ExecutionArtifactPayload> inputArtifacts) {
        return new ExecutionManifestPersistencePort.PersistedExecution(
                manifest,
                inputArtifacts);
    }

    private static ExecutionArtifactPayload expectedPayload(
            ImmutableExecutionManifest manifest) {
        return payload(expectedEntry(manifest), RAW_DIFF);
    }

    private static ExecutionArtifactPayload payload(
            ArtifactManifestEntry entry,
            byte[] content) {
        return new ExecutionArtifactPayload(entry, content);
    }

    private static String sha256(byte[] value) {
        try {
            return java.util.HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(value));
        } catch (NoSuchAlgorithmException error) {
            throw new AssertionError("SHA-256 must be present in the test runtime", error);
        }
    }

    private record Corruption(
            String description,
            List<ExecutionArtifactPayload> artifacts) {
    }

    private static final class InMemoryPort implements ExecutionManifestPersistencePort {
        private final Map<String, PersistedExecution> executions = new HashMap<>();
        private int createOrLoadCalls;
        private ImmutableExecutionManifest lastManifest;
        private List<ExecutionArtifactPayload> lastArtifacts = List.of();

        @Override
        public synchronized PersistedExecution createOrLoad(
                ImmutableExecutionManifest manifest,
                List<ExecutionArtifactPayload> inputArtifacts) {
            createOrLoadCalls++;
            lastManifest = manifest;
            lastArtifacts = List.copyOf(inputArtifacts);
            return executions.computeIfAbsent(
                    manifest.executionId(),
                    ignored -> persisted(manifest, lastArtifacts));
        }

        @Override
        public synchronized Optional<PersistedExecution> findByExecutionId(String executionId) {
            return Optional.ofNullable(executions.get(executionId));
        }

        private synchronized void seed(String executionId, PersistedExecution persisted) {
            executions.put(executionId, persisted);
        }
    }
}
