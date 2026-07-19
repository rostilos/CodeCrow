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

    /**
     * Whether this disposition fully accounts for a mandatory diff anchor.
     * Unsupported anchors are immutable non-reviewable changes (for example,
     * a binary or a rename-only file section), not unfinished model work.
     */
    public boolean satisfiesMandatoryCoverage() {
        return switch (this) {
            case EXAMINED, UNSUPPORTED, DELETED_RECORDED -> true;
            case PENDING, OWNER_PENDING, INCOMPLETE, FAILED, POLICY_EXCLUDED -> false;
        };
    }
}
