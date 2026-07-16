package org.rostilos.codecrow.analysisengine.policy;

import java.util.Objects;
import java.util.OptionalLong;

/**
 * Capability and idempotency boundary that must be crossed before any VCS
 * publication is enqueued. Shadow denial happens before the store is touched.
 */
public final class PublicationFence {
    private final ExecutionControlStore store;

    public PublicationFence(ExecutionControlStore store) {
        this.store = Objects.requireNonNull(store, "store");
    }

    public long claimLatestHeadGeneration(
            String admissionId,
            PublicationKey publicationKey) {
        Objects.requireNonNull(admissionId, "admissionId");
        Objects.requireNonNull(publicationKey, "publicationKey");
        return store.claimLatestHeadGeneration(
                scopeId(publicationKey), admissionId);
    }

    public OptionalLong findLatestHeadGeneration(
            PublicationKey publicationKey) {
        Objects.requireNonNull(publicationKey, "publicationKey");
        return store.findLatestHeadGeneration(scopeId(publicationKey));
    }

    public LatestHeadRegistration registerLatestHead(
            PolicyExecution execution,
            PublicationKey publicationKey,
            long generation) {
        Objects.requireNonNull(execution, "execution");
        Objects.requireNonNull(publicationKey, "publicationKey");
        return store.registerLatestHead(
                scopeId(publicationKey),
                execution.executionId(),
                publicationKey.headRevision(),
                generation);
    }

    public boolean isLatestHead(
            PolicyExecution execution,
            PublicationKey publicationKey) {
        Objects.requireNonNull(execution, "execution");
        Objects.requireNonNull(publicationKey, "publicationKey");
        return store.isLatestHead(
                scopeId(publicationKey),
                execution.executionId(),
                publicationKey.headRevision());
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
        if (!execution.candidatePath()) {
            return store.tryClaimPublication(claimId)
                    ? PublicationReservation.RESERVED
                    : PublicationReservation.DUPLICATE;
        }
        return store.tryClaimLatestPublication(
                scopeId(publicationKey),
                execution.executionId(),
                publicationKey.headRevision(),
                claimId);
    }

    private String scopeId(PublicationKey publicationKey) {
        return sha256(publicationKey.scopeCanonicalValue());
    }

    private String sha256(String value) {
        return PolicyHashing.sha256(value);
    }
}
