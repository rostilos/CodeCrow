package org.rostilos.codecrow.analysisengine.policy;

import java.util.List;
import java.util.Optional;

/** Persistence and atomic-claim port for the rollout scaffold. */
public interface ExecutionControlStore {
    Optional<FrozenExecutionPlan> findPlan(String executionId);

    /**
     * Resolves a previously frozen plan when its absence would violate the
     * execution lifecycle invariant.
     */
    default FrozenExecutionPlan requirePlan(String executionId) {
        return findPlan(executionId).orElseThrow(() ->
                new IllegalStateException("no frozen execution plan for " + executionId));
    }

    FrozenExecutionPlan createPlanIfAbsent(FrozenExecutionPlan plan);

    void persistArtifact(ExecutionArtifact artifact);

    List<ExecutionArtifact> findArtifacts(String executionId, ArtifactNamespace namespace);

    boolean tryClaimPublication(String publicationClaimId);
}
