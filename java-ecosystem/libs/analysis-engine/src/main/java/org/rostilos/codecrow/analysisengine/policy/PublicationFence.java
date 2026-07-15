package org.rostilos.codecrow.analysisengine.policy;

import java.util.Objects;

/**
 * Capability and idempotency boundary that must be crossed before any VCS
 * publication is enqueued. Shadow denial happens before the store is touched.
 */
public final class PublicationFence {
    private final ExecutionControlStore store;

    public PublicationFence(ExecutionControlStore store) {
        this.store = Objects.requireNonNull(store, "store");
    }

    public PublicationReservation reserve(
            PolicyExecution execution,
            PublicationKey publicationKey) {
        Objects.requireNonNull(execution, "execution");
        Objects.requireNonNull(publicationKey, "publicationKey");
        if (execution.mode() == ExecutionMode.SHADOW) {
            return PublicationReservation.SHADOW_DENIED;
        }
        String claimId = sha256(execution.executionId() + '\0' + publicationKey.canonicalValue());
        return store.tryClaimPublication(claimId)
                ? PublicationReservation.RESERVED
                : PublicationReservation.DUPLICATE;
    }

    private String sha256(String value) {
        return PolicyHashing.sha256(value);
    }
}
