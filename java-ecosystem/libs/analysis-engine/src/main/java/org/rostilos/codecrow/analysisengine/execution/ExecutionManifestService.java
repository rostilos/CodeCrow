package org.rostilos.codecrow.analysisengine.execution;

import java.util.List;
import java.util.Objects;

/**
 * Persists and re-verifies immutable execution identity before downstream
 * work is allowed to proceed.
 */
public final class ExecutionManifestService {
    private final ExecutionManifestPersistencePort persistencePort;

    public ExecutionManifestService(ExecutionManifestPersistencePort persistencePort) {
        this.persistencePort = Objects.requireNonNull(persistencePort, "persistencePort");
    }

    /**
     * Verifies the exact raw bytes, atomically creates or loads their durable
     * manifest, and accepts only an exact persisted replay.
     */
    public ImmutableExecutionManifest persistBeforeWork(
            ImmutableExecutionManifest manifest,
            byte[] rawDiff) {
        Objects.requireNonNull(manifest, "manifest");
        Objects.requireNonNull(rawDiff, "rawDiff");

        ArtifactManifestEntry diffEntry = expectedDiffEntry(manifest);
        return persistBeforeWork(
                manifest,
                List.of(new ExecutionArtifactPayload(diffEntry, rawDiff)));
    }

    /** Persists every manifest-bound input byte atomically before analysis. */
    public ImmutableExecutionManifest persistBeforeWork(
            ImmutableExecutionManifest manifest,
            List<ExecutionArtifactPayload> inputArtifacts) {
        Objects.requireNonNull(manifest, "manifest");
        List<ExecutionArtifactPayload> verified = List.copyOf(
                Objects.requireNonNull(inputArtifacts, "inputArtifacts"));
        requireExactInputArtifacts(manifest, verified);

        ExecutionManifestPersistencePort.PersistedExecution persisted =
                persistencePort.createOrLoad(manifest, verified);
        return requireExactPersistedState(persisted, manifest, verified);
    }

    /**
     * Loads and fully re-verifies durable state without relying on in-memory
     * state from a prior service instance.
     */
    public ImmutableExecutionManifest requireVerified(String executionId) {
        requireExecutionId(executionId);
        ExecutionManifestPersistencePort.PersistedExecution persisted = persistencePort
                .findByExecutionId(executionId)
                .orElseThrow(() -> new IllegalStateException(
                        "execution manifest is not durably persisted"));

        ImmutableExecutionManifest manifest = persisted.manifest();
        if (!executionId.equals(manifest.executionId())) {
            throw new IllegalStateException(
                    "persisted manifest belongs to another execution");
        }
        return requireExactPersistedState(
                persisted,
                manifest,
                persisted.inputArtifacts());
    }

    private static ImmutableExecutionManifest requireExactPersistedState(
            ExecutionManifestPersistencePort.PersistedExecution persisted,
            ImmutableExecutionManifest expectedManifest,
            List<ExecutionArtifactPayload> expectedArtifacts) {
        if (persisted == null) {
            throw new IllegalStateException("persistence returned no execution manifest");
        }
        if (!expectedManifest.equals(persisted.manifest())) {
            throw new IllegalStateException(
                    "persisted manifest conflicts with immutable execution coordinates");
        }
        try {
            requireExactInputArtifacts(expectedManifest, persisted.inputArtifacts());
        } catch (IllegalArgumentException error) {
            throw new IllegalStateException(
                    "persisted input artifacts conflict with immutable manifest", error);
        }
        if (!expectedArtifacts.equals(persisted.inputArtifacts())) {
            throw new IllegalStateException(
                    "persisted input artifact bytes conflict with immutable manifest");
        }
        return persisted.manifest();
    }

    private static void requireExactInputArtifacts(
            ImmutableExecutionManifest manifest,
            List<ExecutionArtifactPayload> payloads) {
        if (payloads.size() != manifest.inputArtifacts().size()) {
            throw new IllegalArgumentException(
                    "input artifact count does not match immutable manifest");
        }
        for (int index = 0; index < payloads.size(); index++) {
            ExecutionArtifactPayload payload = Objects.requireNonNull(
                    payloads.get(index), "input artifact");
            if (!manifest.inputArtifacts().get(index).equals(payload.entry())) {
                throw new IllegalArgumentException(
                        "input artifact entry does not match immutable manifest");
            }
        }
    }

    private static ArtifactManifestEntry expectedDiffEntry(
            ImmutableExecutionManifest manifest) {
        if (!ImmutableExecutionManifest.RAW_DIFF_ARTIFACT_KIND.equals(
                manifest.diffArtifactKind())) {
            throw new IllegalStateException("manifest does not describe a raw diff artifact");
        }
        return new ArtifactManifestEntry(
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

    private static void requireExecutionId(String executionId) {
        if (executionId == null || executionId.isBlank()) {
            throw new IllegalArgumentException("executionId must not be blank");
        }
    }
}
