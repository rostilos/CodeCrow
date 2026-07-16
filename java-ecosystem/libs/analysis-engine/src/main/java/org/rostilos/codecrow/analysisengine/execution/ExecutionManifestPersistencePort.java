package org.rostilos.codecrow.analysisengine.execution;

import java.util.Objects;
import java.util.Optional;
import java.util.List;

/**
 * Transaction boundary for durable immutable execution manifests.
 */
public interface ExecutionManifestPersistencePort {
    /**
     * Atomically creates the manifest and initial diff entry, or loads the
     * already persisted aggregate without overwriting it.
     */
    PersistedExecution createOrLoad(
            ImmutableExecutionManifest manifest,
            List<ExecutionArtifactPayload> inputArtifacts);

    /** Loads the manifest aggregate stored for an execution identifier. */
    Optional<PersistedExecution> findByExecutionId(String executionId);

    /**
     * Persistence-boundary representation. An empty diff entry deliberately
     * remains representable so the domain service can fail closed on partial
     * or corrupt storage.
     */
    record PersistedExecution(
            ImmutableExecutionManifest manifest,
            List<ExecutionArtifactPayload> inputArtifacts) {
        public PersistedExecution {
            Objects.requireNonNull(manifest, "manifest");
            inputArtifacts = List.copyOf(
                    Objects.requireNonNull(inputArtifacts, "inputArtifacts"));
        }
    }
}
