package org.rostilos.codecrow.core.model.rag;

/** Durable branch-index operation state used for restart recovery. */
public enum RagIndexOperationStatus {
    PENDING,
    RUNNING,
    SUCCEEDED,
    FAILED
}

