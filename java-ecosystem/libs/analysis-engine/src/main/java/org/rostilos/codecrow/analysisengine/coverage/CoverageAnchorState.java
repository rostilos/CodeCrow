package org.rostilos.codecrow.analysisengine.coverage;

/** Per-anchor lifecycle and terminal disposition. */
public enum CoverageAnchorState {
    PENDING,
    OWNER_PENDING,
    EXAMINED,
    INCOMPLETE,
    UNSUPPORTED,
    FAILED,
    POLICY_EXCLUDED,
    DELETED_RECORDED;

    public boolean open() {
        return this == PENDING || this == OWNER_PENDING;
    }
}
