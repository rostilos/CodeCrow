package org.rostilos.codecrow.analysisapi.usage;

/** Optional host extension for existing review entitlements and durable usage. */
public interface ReviewUsagePolicy {
    Reservation begin(Review review);

    record Review(Long workspaceId, Long projectId, Long pullRequestId,
                  String commitHash, String diffFingerprint, String analysisLockKey) { }

    interface Reservation extends AutoCloseable {
        Reservation NONE = new Reservation() {
            public void completed(Long analysisId) { }
            public void close() { }
        };

        /** Called after a fresh review result has been persisted. */
        void completed(Long analysisId);

        /** Releases an unfinished admission; completion must be idempotent. */
        @Override
        void close();
    }

    /** Only an explicit entitlement decision may reject otherwise valid work. */
    class LimitExceededException extends RuntimeException {
        public LimitExceededException(String message) { super(message); }
    }
}
