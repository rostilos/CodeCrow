package org.rostilos.codecrow.core.model.rag;

/** Branch-level readiness independent from any individual build attempt. */
public enum RagBranchIndexLifecycleStatus {
    PENDING,
    BUILDING,
    READY,
    FAILED
}

