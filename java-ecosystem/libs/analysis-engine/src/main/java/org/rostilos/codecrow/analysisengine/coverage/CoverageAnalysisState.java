package org.rostilos.codecrow.analysisengine.coverage;

/** Durable truth state derived from the complete anchor ledger. */
public enum CoverageAnalysisState {
    PENDING,
    EMPTY,
    PARTIAL,
    FAILED,
    COMPLETE,
    SUPERSEDED;

    public boolean terminal() {
        return this != PENDING;
    }
}
