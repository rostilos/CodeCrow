package org.rostilos.codecrow.core.model.rag;

/** Ownership and retention policy for a branch index. */
public enum RagBranchIndexKind {
    PRIMARY,
    DURABLE,
    TRANSIENT,
    LEGACY
}

