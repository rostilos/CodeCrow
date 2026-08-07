package org.rostilos.codecrow.core.model.rag;

/** Immutable generation publication state. */
public enum RagBranchIndexGenerationStatus {
    BUILDING,
    ACTIVE,
    SUPERSEDED,
    FAILED
}

