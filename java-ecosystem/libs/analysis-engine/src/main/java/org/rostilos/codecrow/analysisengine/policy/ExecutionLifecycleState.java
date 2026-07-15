package org.rostilos.codecrow.analysisengine.policy;

/** Minimal pre-runtime lifecycle used to make kill-switch cancellation explicit. */
public enum ExecutionLifecycleState {
    CREATED,
    RUNNING,
    CANCELLATION_REQUESTED,
    CANCELLED,
    COMPLETED,
    FAILED
}
