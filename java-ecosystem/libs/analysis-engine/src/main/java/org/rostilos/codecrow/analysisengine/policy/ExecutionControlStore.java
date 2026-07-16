package org.rostilos.codecrow.analysisengine.policy;

import java.util.List;
import java.util.Optional;
import java.util.OptionalLong;

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

    /**
     * Atomically creates one immutable artifact identity or returns the value
     * already stored for that identity. Implementations shared by multiple
     * processes must override this method with a distributed atomic claim.
     */
    default ExecutionArtifact createArtifactIfAbsent(
            ExecutionArtifact artifact) {
        synchronized (this) {
            List<ExecutionArtifact> existing = findArtifacts(
                            artifact.executionId(), artifact.namespace())
                    .stream()
                    .filter(value -> value.artifactId().equals(
                            artifact.artifactId()))
                    .toList();
            if (existing.size() > 1) {
                throw new IllegalStateException(
                        "immutable artifact identity has duplicate values");
            }
            if (!existing.isEmpty()) {
                return existing.get(0);
            }
            persistArtifact(artifact);
            return artifact;
        }
    }

    List<ExecutionArtifact> findArtifacts(String executionId, ArtifactNamespace namespace);

    boolean tryClaimPublication(String publicationClaimId);

    /**
     * Claims the durable arrival generation for one pre-acquisition admission.
     * A retry of the same admission must receive the original generation; the
     * admission identity is deliberately distinct from the final execution
     * identity derived from acquired snapshot coordinates.
     */
    default long claimLatestHeadGeneration(
            String publicationScopeId,
            String admissionId) {
        throw new UnsupportedOperationException(
                "latest-head generation claims are unavailable");
    }

    default OptionalLong findLatestHeadGeneration(String publicationScopeId) {
        return OptionalLong.empty();
    }

    /**
     * Binds a provider-verified admission generation to its final immutable
     * execution identity. A lower generation is superseded; an exact binding
     * is a duplicate; reusing one generation for a different final execution
     * must fail closed rather than choose a nondeterministic winner.
     */
    default LatestHeadRegistration registerLatestHead(
            String publicationScopeId,
            String executionId,
            String headRevision,
            long generation) {
        throw new UnsupportedOperationException(
                "latest-head registration is unavailable");
    }

    default boolean isLatestHead(
            String publicationScopeId,
            String executionId,
            String headRevision) {
        return false;
    }

    default PublicationReservation tryClaimLatestPublication(
            String publicationScopeId,
            String executionId,
            String headRevision,
            String publicationClaimId) {
        return PublicationReservation.STALE_HEAD;
    }

    /** Atomically latches the first automatic candidate rollback receipt. */
    default String activateCandidateKillSwitch(String receiptJson) {
        throw new UnsupportedOperationException(
                "automatic candidate rollback is unavailable");
    }

    /** Returns the immutable receipt that keeps new candidate work disabled. */
    default Optional<String> findCandidateKillSwitchReceipt() {
        return Optional.empty();
    }
}
