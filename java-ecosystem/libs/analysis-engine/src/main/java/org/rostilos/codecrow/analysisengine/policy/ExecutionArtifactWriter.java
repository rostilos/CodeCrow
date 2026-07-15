package org.rostilos.codecrow.analysisengine.policy;

import java.time.Clock;
import java.util.Objects;

/** Writes artifacts to a namespace derived from the frozen execution capability. */
public final class ExecutionArtifactWriter {
    private final ExecutionControlStore store;
    private final Clock clock;

    public ExecutionArtifactWriter(ExecutionControlStore store, Clock clock) {
        this.store = Objects.requireNonNull(store, "store");
        this.clock = Objects.requireNonNull(clock, "clock");
    }

    public ExecutionArtifact persist(
            PolicyExecution execution,
            String artifactId,
            String payloadJson) {
        Objects.requireNonNull(execution, "execution");
        ArtifactNamespace namespace = execution.mode() == ExecutionMode.SHADOW
                ? ArtifactNamespace.SHADOW
                : ArtifactNamespace.PRIMARY;
        ExecutionArtifact artifact = new ExecutionArtifact(
                execution.executionId(),
                namespace,
                artifactId,
                payloadJson,
                clock.instant());
        store.persistArtifact(artifact);
        return artifact;
    }
}
